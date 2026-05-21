"""
Analytics Service - DynamoDB Implementation

Uses pre-aggregated counter pattern for O(1) analytics queries.
Counters are updated atomically when data changes, providing real-time stats
with minimal read costs.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any

from app.core.dynamodb import (
    get_item,
    put_item,
    get_dynamodb_table  # Changed from using update_item helper
)
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class AnalyticsServiceDynamoDB:
    """
    Analytics service using DynamoDB counter pattern.
    
    Maintains pre-aggregated counters that are updated atomically when data changes.
    Provides O(1) reads for analytics queries with eventual consistency.
    """
    
    def get_tenant_stats(self, tenant_id: str) -> Dict[str, Any]:
        """
        Get tenant statistics from pre-aggregated counters.
        
        Args:
            tenant_id: Tenant identifier
            
        Returns:
            Dict with conversation, lead, and document counts
        """
        result = get_item(
            pk=f"STATS#{tenant_id}",
            sk="COUNTERS"
        )
        
        if not result:
            # Initialize if doesn't exist
            return self._initialize_tenant_stats(tenant_id)
        
        # Calculate conversion rate
        total_conversations = result.get("total_conversations", 0)
        total_leads = result.get("total_leads", 0)
        conversion_rate = round((total_leads / total_conversations) * 100, 2) if total_conversations > 0 else 0.0
        
        return {
            "conversations": int(total_conversations),
            "leads": int(total_leads),
            "documents": int(result.get("total_documents", 0)),
            "leads_by_status": result.get("leads_by_status", {
                "new": 0,
                "contacted": 0,
                "qualified": 0,
                "converted": 0,
                "lost": 0
            }),
            "conversion_rate": float(conversion_rate),
            "generated_at": datetime.now(timezone.utc)
        }
    
    def _initialize_tenant_stats(self, tenant_id: str) -> Dict[str, Any]:
        """Initialize stat counters for a new tenant."""
        initial_stats = {
            "PK": f"STATS#{tenant_id}",
            "SK": "COUNTERS",
            "tenant_id": tenant_id,
            "total_conversations": 0,
            "total_leads": 0,
            "total_documents": 0,
            "active_conversations": 0,
            "leads_by_status": {
                "new": 0,
                "contacted": 0,
                "qualified": 0,
                "converted": 0,
                "lost": 0
            },
            "last_updated": datetime.now(timezone.utc).isoformat()
        }
        
        put_item(initial_stats)
        
        return {
            "conversations": 0,
            "leads": 0,
            "documents": 0,
            "leads_by_status": initial_stats["leads_by_status"],
            "conversion_rate": 0.0,
            "generated_at": datetime.now(timezone.utc)
        }
    
    def increment_counter(
        self,
        tenant_id: str,
        counter_name: str,
        amount: int = 1
    ) -> bool:
        """
        Atomically increment a counter.
        
        Args:
            tenant_id: Tenant identifier
            counter_name: Name of counter field (e.g., "total_conversations")
            amount: Amount to increment (can be negative for decrement)
            
        Returns:
            bool: Success status
        """
        try:
            table = get_dynamodb_table()
            table.update_item(
                Key={"PK": f"STATS#{tenant_id}", "SK": "COUNTERS"},
                UpdateExpression="ADD #counter :amount SET last_updated = :time",
                ExpressionAttributeNames={"#counter": counter_name},
                ExpressionAttributeValues={
                    ":amount": amount,
                    ":time": datetime.now(timezone.utc).isoformat()
                }
            )
            logger.info(f"Incremented {counter_name} by {amount} for tenant {tenant_id}")
            return True
        except Exception as e:
            logger.error(f"Error incrementing counter {counter_name}: {e}")
            return False
    
    def increment_lead_status(
        self,
        tenant_id: str,
        status: str,
        amount: int = 1
    ) -> bool:
        """
        Increment lead status counter (nested map).
        
        Args:
            tenant_id: Tenant identifier
            status: Lead status (new, contacted, qualified, converted, lost)
            amount: Amount to increment
            
        Returns:
            bool: Success status
        """
        try:
            table = get_dynamodb_table()
            table.update_item(
                Key={"PK": f"STATS#{tenant_id}", "SK": "COUNTERS"},
                UpdateExpression="ADD leads_by_status.#status :amount SET last_updated = :time",
                ExpressionAttributeNames={"#status": status},
                ExpressionAttributeValues={
                    ":amount": amount,
                    ":time": datetime.now(timezone.utc).isoformat()
                }
            )
            logger.info(f"Incremented lead status {status} by {amount} for tenant {tenant_id}")
            return True
        except Exception as e:
            # Check if it's the path error (missing map)
            if "The document path provided in the update expression is invalid" in str(e):
                logger.info(f"Initializing leads_by_status map for tenant {tenant_id} and retrying...")
                try:
                    # Initialize the map safely
                    table.update_item(
                        Key={"PK": f"STATS#{tenant_id}", "SK": "COUNTERS"},
                        UpdateExpression="SET leads_by_status = if_not_exists(leads_by_status, :empty_map)",
                        ExpressionAttributeValues={
                            ":empty_map": {
                                "new": 0, "contacted": 0, "qualified": 0, "converted": 0, "lost": 0
                            }
                        }
                    )
                    # Retry the increment
                    table.update_item(
                        Key={"PK": f"STATS#{tenant_id}", "SK": "COUNTERS"},
                        UpdateExpression="ADD leads_by_status.#status :amount SET last_updated = :time",
                        ExpressionAttributeNames={"#status": status},
                        ExpressionAttributeValues={
                            ":amount": amount,
                            ":time": datetime.now(timezone.utc).isoformat()
                        }
                    )
                    logger.info(f"Retry successful: Incremented lead status {status}")
                    return True
                except Exception as retry_e:
                    logger.error(f"Retry failed for lead status {status}: {retry_e}")
                    return False
            
            logger.error(f"Error incrementing lead status {status}: {e}")
            return False
    
    def record_daily_metrics(
        self,
        tenant_id: str,
        date: str,
        metric: str,
        amount: int = 1
    ) -> bool:
        """
        Record daily metric (for time-series analytics).
        
        Args:
            tenant_id: Tenant identifier
            date: Date string (YYYY-MM-DD)
            metric: Metric name (conversations, leads, messages, etc.)
            amount: Value to add
            
        Returns:
            bool: Success status
        """
        try:
            # Auto-delete after 90 days using TTL
            ttl = int((datetime.now(timezone.utc) + timedelta(days=90)).timestamp())
            
            table = get_dynamodb_table()
            table.update_item(
                Key={"PK": f"STATS#{tenant_id}", "SK": f"DAILY#{date}"},
                UpdateExpression="ADD #metric :amount SET #date = :date, #ttl = :ttl",
                ExpressionAttributeNames={
                    "#metric": metric,
                    "#date": "date",
                    "#ttl": "ttl"
                },
                ExpressionAttributeValues={
                    ":amount": amount,
                    ":date": date,
                    ":ttl": ttl
                }
            )
            return True
        except Exception as e:
            logger.error(f"Error recording daily metric {metric}: {e}")
            return False
    
    def get_daily_metrics(
        self,
        tenant_id: str,
        days: int = 7
    ) -> List[Dict[str, Any]]:
        """
        Get daily metrics for last N days.
        
        Args:
            tenant_id: Tenant identifier
            days: Number of days to retrieve
            
        Returns:
            List of daily metric dicts
        """
        # Generate all dates for the range
        results = []
        for i in range(days):
            date = (datetime.now(timezone.utc) - timedelta(days=days - i - 1)).strftime("%Y-%m-%d")
            
            item = get_item(
                pk=f"STATS#{tenant_id}",
                sk=f"DAILY#{date}"
            )
            
            results.append({
                "date": date,
                "conversations": int(item.get("conversations", 0)) if item else 0,
                "leads": int(item.get("leads", 0)) if item else 0,
                "messages": int(item.get("messages", 0)) if item else 0
            })
        
        return results
    
    def get_platform_stats(self) -> Dict[str, Any]:
        """
        Get platform-wide statistics.
        
        Returns:
            Dict with platform-level aggregated stats
        """
        result = get_item(
            pk="STATS#PLATFORM",
            sk="COUNTERS"
        )
        
        if not result:
            return {
                "total_tenants": 0,
                "total_users": 0,
                "total_conversations": 0,
                "total_leads": 0,
                "generated_at": datetime.now(timezone.utc)
            }
        
        total_conversations = result.get("total_conversations", 0)
        total_leads = result.get("total_leads", 0)
        
        return {
            "total_tenants": int(result.get("total_tenants", 0)),
            "total_users": int(result.get("total_users", 0)),
            "total_conversations": int(total_conversations),
            "total_leads": int(total_leads),
            "global_conversion_rate": round((total_leads / total_conversations) * 100, 2) if total_conversations > 0 else 0.0,
            "generated_at": datetime.now(timezone.utc)
        }
    
    def increment_platform_counter(
        self,
        counter_name: str,
        amount: int = 1
    ) -> bool:
        """
        Increment platform-wide counter.
        
        Args:
            counter_name: Counter field name
            amount: Amount to increment
            
        Returns:
            bool: Success status
        """
        try:
            table = get_dynamodb_table()
            table.update_item(
                Key={"PK": "STATS#PLATFORM", "SK": "COUNTERS"},
                UpdateExpression="ADD #counter :amount SET last_updated = :time",
                ExpressionAttributeNames={"#counter": counter_name},
                ExpressionAttributeValues={
                    ":amount": amount,
                    ":time": datetime.now(timezone.utc).isoformat()
                }
            )
            logger.info(f"Incremented platform counter {counter_name} by {amount}")
            return True
        except Exception as e:
            logger.error(f"Error incrementing platform counter {counter_name}: {e}")
            return False


# Singleton instance
analytics_service_dynamodb = AnalyticsServiceDynamoDB()
