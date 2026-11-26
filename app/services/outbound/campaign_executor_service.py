"""Campaign executor service: sends campaign messages batch by batch.

Two ways to run:
- Production (SQS): each "campaign_batch" job sends one batch (within a time
  budget so it fits the worker Lambda's limit) and queues the next batch with an
  SQS delay. No Lambda ever waits out a batch delay.
- Local development (no queue): execute_campaign() loops in-process.
"""

import asyncio
import time
from typing import List, Optional

from app.models.outbound.campaign import Campaign, CampaignStatus
from app.models.outbound.outbound_lead import MessageStatus, OutboundLead
from app.models.outbound.audit import CampaignAuditAction, CampaignAuditCreate
from app.models.outbound.message import OutboundMessageStatus
from app.services.outbound.campaign_service import campaign_service
from app.services.outbound.outbound_lead_service import outbound_lead_service
from app.services.outbound.message_service import message_service
from app.services.outbound.campaign_audit_service import campaign_audit_service
from app.services.outbound.whatsapp_sender import whatsapp_sender
from app.services.outbound import outbound_queue
from app.services import tenant_service
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# Worker Lambda limit is 300 s; stop starting new sends after this and requeue.
WORKER_TIME_BUDGET_SECONDS = 240
SIMULATION_ERROR = "WhatsApp is not enabled or configured for this client, so nothing was sent."


def resolve_template_params(campaign: Campaign, name: str, phone: str) -> List[str]:
    """Values for a template's {{1}}, {{2}}..: "{{name}}" / "{{phone}}" are filled per lead."""
    values = []
    for raw in campaign.template_params:
        value = (raw or "").replace("{{name}}", name or "there").replace("{{phone}}", phone or "")
        values.append(value.strip() or "-")
    return values


def campaign_message_text(campaign: Campaign, name: str, phone: str) -> str:
    """The exact text this lead received (used for message records and AI conversation context)."""
    if campaign.message_type == "template" and campaign.template_name:
        params = resolve_template_params(campaign, name, phone)
        return whatsapp_sender.fill_numbered_template(campaign.template_body, params) or f"[Template: {campaign.template_name}]"
    return whatsapp_sender.render_template(campaign.message_template or "", {"name": name or "", "phone": phone})


class CampaignExecutorService:
    """Orchestrates campaign execution: batching, sending and status tracking."""

    # ── Entry points ───────────────────────────────────────────────────

    async def execute_campaign(self, campaign_id: str) -> None:
        """Local mode: run the whole campaign in-process (no SQS configured)."""
        while True:
            try:
                delay = await self.process_next_batch(campaign_id)
            except Exception as e:
                self._mark_failed(campaign_id, e)
                return
            if delay is None:
                return
            logger.info(f"Batch complete. Waiting {delay}s before next batch...")
            await asyncio.sleep(delay)

    async def run_batch_job(self, campaign_id: str) -> None:
        """SQS worker: send one batch, then queue the next one with the batch delay."""
        try:
            delay = await self.process_next_batch(campaign_id, deadline=time.monotonic() + WORKER_TIME_BUDGET_SECONDS)
        except Exception as e:
            self._mark_failed(campaign_id, e)
            return
        if delay is None:
            return
        if not outbound_queue.enqueue(outbound_queue.MSG_CAMPAIGN_BATCH, {"campaign_id": campaign_id}, delay):
            self._mark_failed(campaign_id, RuntimeError("Could not queue the next batch (SQS unavailable)."))

    def start(self, campaign_id: str) -> bool:
        """Kick off sending. True if queued on SQS; False means the caller must run it locally."""
        return outbound_queue.enqueue(outbound_queue.MSG_CAMPAIGN_BATCH, {"campaign_id": campaign_id})

    # ── Batch processing ───────────────────────────────────────────────

    async def process_next_batch(self, campaign_id: str, deadline: Optional[float] = None) -> Optional[int]:
        """Send up to batch_size messages.

        Returns the seconds to wait before calling again, or None when the
        campaign is finished, paused or stopped.
        """
        campaign = campaign_service.get_campaign(campaign_id)
        if not campaign or campaign.status != CampaignStatus.RUNNING:
            logger.info(f"Campaign {campaign_id} is not RUNNING; nothing to send.")
            return None

        pending = self._get_pending_leads_batch(campaign_id, campaign.batch_size)
        if not pending:
            self._mark_completed(campaign)
            return None

        tenant = tenant_service.get_tenant(campaign.tenant_id)
        if not tenant:
            raise RuntimeError(f"Tenant {campaign.tenant_id} not found")

        logger.info(f"🚀 Campaign {campaign_id}: sending batch of {len(pending)}")
        for index, lead in enumerate(pending):
            fresh = campaign_service.get_campaign(campaign_id)
            if not fresh or fresh.status != CampaignStatus.RUNNING:
                logger.info(f"Campaign {campaign_id} paused/stopped mid-batch")
                return None

            await self._send_to_lead(campaign, lead, tenant)

            is_last = index == len(pending) - 1
            if not is_last:
                if deadline is not None and time.monotonic() + campaign.message_delay_seconds > deadline:
                    # Out of worker time: continue with the rest of this batch in a new job.
                    return campaign.message_delay_seconds
                if campaign.message_delay_seconds > 0:
                    await asyncio.sleep(campaign.message_delay_seconds)

        if not self._get_pending_leads_batch(campaign_id, 1):
            self._mark_completed(campaign)
            return None
        return campaign.batch_delay_seconds

    async def _send_to_lead(self, campaign: Campaign, lead: OutboundLead, tenant) -> bool:
        """Send the campaign message to one lead. Returns True if WhatsApp accepted it."""
        # Claim first so overlapping runs can never message the same lead twice.
        if not outbound_lead_service.claim_lead_for_sending(campaign.campaign_id, lead.lead_id):
            logger.info(f"Lead {lead.lead_id} already claimed by another run; skipping.")
            return False

        normalized_phone = whatsapp_sender.normalize_phone(lead.phone)
        if not normalized_phone:
            self._finish_failed(campaign, lead, None, "Invalid phone number")
            return False

        # Respect opt-outs (STOP / unsubscribe) across all campaigns of this client.
        from app.services.outbound.outbound_db_service import outbound_db_service
        if outbound_db_service.is_opted_out(campaign.tenant_id, normalized_phone):
            outbound_lead_service.update_lead_message_status(
                campaign.campaign_id, lead.lead_id, MessageStatus.SKIPPED,
                error_reason="Opted out of messages (replied STOP earlier)"
            )
            campaign_service.increment_campaign_counter(campaign.campaign_id, "skipped_count")
            return False

        rendered = campaign_message_text(campaign, lead.name or "", normalized_phone)
        msg = message_service.create_message(
            campaign_id=campaign.campaign_id,
            lead_id=lead.lead_id,
            tenant_id=campaign.tenant_id,
            phone=normalized_phone,
            rendered_message=rendered,
            provider="whatsapp",
        )

        if campaign.message_type == "template" and campaign.template_name:
            result = await whatsapp_sender.send_template(
                to_phone=normalized_phone,
                template_name=campaign.template_name,
                language=campaign.template_language or "en_US",
                tenant_config=tenant.config,
                body_params=resolve_template_params(campaign, lead.name or "", normalized_phone),
            )
        else:
            result = await whatsapp_sender.send_message(normalized_phone, rendered, tenant.config)

        status = result.get("status") if isinstance(result, dict) else None
        if status == "sent":
            wamid = result.get("message_id")
            outbound_lead_service.update_lead_message_status(campaign.campaign_id, lead.lead_id, MessageStatus.SENT)
            message_service.update_message_status(msg.message_id, OutboundMessageStatus.SENT, provider_message_id=wamid)
            campaign_service.increment_campaign_counter(campaign.campaign_id, "sent_count")
            if wamid:
                outbound_db_service.save_wamid_link(wamid, {
                    "tenant_id": campaign.tenant_id,
                    "campaign_id": campaign.campaign_id,
                    "lead_id": lead.lead_id,
                    "message_id": msg.message_id,
                })
            return True

        # Simulation mode used to be counted as "sent" although nothing was sent.
        error = SIMULATION_ERROR if status == "simulated" else (
            result.get("error", "Unknown WhatsApp error") if isinstance(result, dict) else "Unexpected sender result"
        )
        self._finish_failed(campaign, lead, msg.message_id, error)
        return False

    # ── Helpers ────────────────────────────────────────────────────────

    def _finish_failed(self, campaign: Campaign, lead: OutboundLead, message_id: Optional[str], error: str) -> None:
        logger.warning(f"Campaign {campaign.campaign_id}: lead {lead.lead_id} failed: {error}")
        outbound_lead_service.update_lead_message_status(
            campaign.campaign_id, lead.lead_id, MessageStatus.FAILED, error_reason=error
        )
        if message_id:
            message_service.update_message_status(message_id, OutboundMessageStatus.FAILED, error_message=error)
        campaign_service.increment_campaign_counter(campaign.campaign_id, "failed_count")

    def _mark_completed(self, campaign: Campaign) -> None:
        try:
            campaign_service.update_campaign_status(campaign.campaign_id, CampaignStatus.COMPLETED)
        except ValueError:
            return  # already completed/paused by another run
        logger.info(f"✅ Campaign {campaign.campaign_id} completed — all leads processed")
        campaign_audit_service.log_action(CampaignAuditCreate(
            campaign_id=campaign.campaign_id,
            tenant_id=campaign.tenant_id,
            action=CampaignAuditAction.CAMPAIGN_COMPLETED,
            description="Campaign completed — all leads processed",
        ))

    def _mark_failed(self, campaign_id: str, error: Exception) -> None:
        logger.error(f"Campaign {campaign_id} execution failed: {error}")
        campaign = campaign_service.get_campaign(campaign_id)
        try:
            campaign_service.update_campaign_status(campaign_id, CampaignStatus.FAILED)
        except Exception:
            pass
        if campaign:
            campaign_audit_service.log_action(CampaignAuditCreate(
                campaign_id=campaign_id,
                tenant_id=campaign.tenant_id,
                action=CampaignAuditAction.CAMPAIGN_FAILED,
                description=f"Campaign execution failed: {str(error)}",
            ))

    def _get_pending_leads_batch(self, campaign_id: str, batch_size: int) -> List[OutboundLead]:
        """Get the next batch of PENDING leads for processing."""
        return outbound_lead_service.list_leads(
            campaign_id=campaign_id,
            status=MessageStatus.PENDING.value,
            limit=batch_size,
        )


# Singleton
campaign_executor_service = CampaignExecutorService()
