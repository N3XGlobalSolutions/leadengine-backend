"""Campaign executor service — orchestrates batch message dispatch."""

import asyncio
import time
from typing import List, Optional

from app.models.outbound.campaign import CampaignStatus
from app.models.outbound.outbound_lead import MessageStatus, OutboundLead
from app.models.outbound.audit import CampaignAuditAction, CampaignAuditCreate
from app.models.outbound.message import OutboundMessageStatus
from app.services.outbound.campaign_service import campaign_service
from app.services.outbound.outbound_lead_service import outbound_lead_service
from app.services.outbound.message_service import message_service
from app.services.outbound.campaign_audit_service import campaign_audit_service
from app.services.outbound.whatsapp_sender import whatsapp_sender
from app.services import tenant_service
from app.core.logging_config import get_logger

logger = get_logger(__name__)

MAX_RETRIES = 3


class CampaignExecutorService:
    """Orchestrates campaign execution — batch processing, sending, status tracking."""

    async def execute_campaign(self, campaign_id: str) -> None:
        """Execute a campaign by processing leads in batches.

        This is the main entry point called by the worker.
        """
        campaign = campaign_service.get_campaign(campaign_id)
        if not campaign:
            logger.error(f"Campaign {campaign_id} not found")
            return

        if campaign.status != CampaignStatus.RUNNING:
            logger.warning(f"Campaign {campaign_id} is not RUNNING (status: {campaign.status})")
            return

        logger.info(f"Starting execution of campaign {campaign_id}: {campaign.name}")

        try:
            batch_size = campaign.batch_size
            message_delay = campaign.message_delay_seconds
            batch_delay = campaign.batch_delay_seconds

            while True:
                # Re-fetch campaign to check for pause/stop
                campaign = campaign_service.get_campaign(campaign_id)
                if not campaign or campaign.status != CampaignStatus.RUNNING:
                    logger.info(f"Campaign {campaign_id} no longer RUNNING, stopping execution")
                    break

                # Get next batch of pending leads
                pending_leads = self._get_pending_leads_batch(campaign_id, batch_size)

                if not pending_leads:
                    # All leads processed — mark as completed
                    logger.info(f"Campaign {campaign_id} completed - all leads processed")
                    campaign_service.update_campaign_status(campaign_id, CampaignStatus.COMPLETED)
                    campaign_audit_service.log_action(CampaignAuditCreate(
                        campaign_id=campaign_id,
                        tenant_id=campaign.tenant_id,
                        action=CampaignAuditAction.CAMPAIGN_COMPLETED,
                        description="Campaign completed — all leads processed",
                    ))
                    break

                # Process batch
                await self._process_batch(campaign, pending_leads, message_delay)

                # Delay between batches
                logger.info(f"Batch complete. Waiting {batch_delay}s before next batch...")
                await asyncio.sleep(batch_delay)

        except Exception as e:
            logger.error(f"Campaign {campaign_id} execution failed: {e}")
            campaign_service.update_campaign_status(campaign_id, CampaignStatus.FAILED)
            campaign_audit_service.log_action(CampaignAuditCreate(
                campaign_id=campaign_id,
                tenant_id=campaign.tenant_id,
                action=CampaignAuditAction.CAMPAIGN_FAILED,
                description=f"Campaign execution failed: {str(e)}",
            ))

    async def _process_batch(
        self, campaign, leads: List[OutboundLead], message_delay: int
    ) -> None:
        """Process a single batch of leads — send messages one by one."""
        for lead in leads:
            # Check if campaign is still running before each message
            fresh_campaign = campaign_service.get_campaign(campaign.campaign_id)
            if not fresh_campaign or fresh_campaign.status != CampaignStatus.RUNNING:
                logger.info("Campaign paused/stopped mid-batch")
                return

            await self._send_to_lead(campaign, lead)

            # Delay between messages
            if message_delay > 0:
                await asyncio.sleep(message_delay)

    async def _send_to_lead(self, campaign, lead: OutboundLead) -> bool:
        """Send a message to a single lead with retry logic."""
        # 1. Load tenant
        tenant = tenant_service.get_tenant(campaign.tenant_id)
        if not tenant:
            logger.error(f"Tenant {campaign.tenant_id} not found for campaign {campaign.campaign_id}")
            return False

        # 2. Load tenant WhatsApp configuration - already in tenant.config

        # 3. Personalize message
        rendered = whatsapp_sender.render_template(
            campaign.message_template,
            {"name": lead.name or "", "phone": lead.phone},
        )

        # 4. Normalize phone number
        normalized_phone = whatsapp_sender.normalize_phone(lead.phone)
        if not normalized_phone:
            logger.warning(
                f"Invalid or un-parseable phone number '{lead.phone}' for lead {lead.lead_id} in campaign {campaign.campaign_id}. Skipping."
            )
            # Skip lead: update lead message status to FAILED and increment campaign counter
            outbound_lead_service.update_lead_message_status(
                campaign.campaign_id, lead.lead_id, MessageStatus.FAILED, error_reason="Invalid phone number"
            )
            campaign_service.increment_campaign_counter(campaign.campaign_id, "failed_count")
            return False

        # Mark as SENDING
        outbound_lead_service.update_lead_message_status(
            campaign.campaign_id, lead.lead_id, MessageStatus.SENDING
        )

        # 5. Create message record (provider is "whatsapp", phone is normalized_phone)
        msg = message_service.create_message(
            campaign_id=campaign.campaign_id,
            lead_id=lead.lead_id,
            tenant_id=campaign.tenant_id,
            phone=normalized_phone,
            rendered_message=rendered,
            provider="whatsapp",
        )

        # Try sending with retries
        for attempt in range(1, MAX_RETRIES + 1):
            result = await whatsapp_sender.send_message(
                to_phone=normalized_phone,
                message=rendered,
                tenant_config=tenant.config,
            )

            success = False
            provider_msg_id = None
            error = None

            if isinstance(result, dict):
                status = result.get("status")
                if status == "simulated":
                    success = True
                    provider_msg_id = f"sim_{normalized_phone}"
                elif status == "sent":
                    success = True
                    provider_msg_id = result.get("message_id")
                else:
                    success = False
                    error = result.get("error", "Unknown WhatsApp error")
            else:
                logger.error(f"Unexpected result from whatsapp_sender: {result}")
                error = "Unexpected result structure"

            if success:
                # Update statuses
                outbound_lead_service.update_lead_message_status(
                    campaign.campaign_id, lead.lead_id, MessageStatus.SENT
                )
                message_service.update_message_status(
                    msg.message_id,
                    OutboundMessageStatus.SENT,
                    provider_message_id=provider_msg_id,
                )
                campaign_service.increment_campaign_counter(campaign.campaign_id, "sent_count")
                return True
            else:
                logger.warning(
                    f"Send attempt {attempt}/{MAX_RETRIES} failed for lead {lead.lead_id}: {error}"
                )
                if attempt < MAX_RETRIES:
                    # Exponential backoff
                    await asyncio.sleep(2 ** attempt)

        # All retries exhausted — mark as FAILED
        outbound_lead_service.update_lead_message_status(
            campaign.campaign_id, lead.lead_id, MessageStatus.FAILED, error_reason=error
        )
        message_service.update_message_status(
            msg.message_id,
            OutboundMessageStatus.FAILED,
            error_message=error,
        )
        campaign_service.increment_campaign_counter(campaign.campaign_id, "failed_count")
        return False

    def _get_pending_leads_batch(self, campaign_id: str, batch_size: int) -> List[OutboundLead]:
        """Get the next batch of PENDING leads for processing."""
        return outbound_lead_service.list_leads(
            campaign_id=campaign_id,
            status=MessageStatus.PENDING.value,
            limit=batch_size,
        )


# Singleton
campaign_executor_service = CampaignExecutorService()
