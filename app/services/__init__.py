"""
Service Configuration - Switch between MongoDB and DynamoDB

This module provides a unified interface to switch between MongoDB and DynamoDB services.
Set USE_DYNAMODB=true in .env to use DynamoDB services.

Usage:
    from app.services import user_service, tenant_service, conversation_service, lead_service
"""

from dotenv import load_dotenv

load_dotenv()

# Read configuration
# Use DynamoDB Services indefinitely as per migration plan
from app.core.config import get_settings as _get_settings
_settings = _get_settings()
_env = _settings.APP_ENV.upper()
print(f"🔄 Using DynamoDB services [{_env}]")
print(f"   Table: {_settings.DYNAMODB_TABLE_NAME} | Qdrant: {_settings.EFFECTIVE_QDRANT_COLLECTION}")

# Import DynamoDB services
from app.services.user.user_service_dynamodb import (
    user_service_dynamodb as user_service,
)
from app.services.tenant.tenant_service_dynamodb import (
    tenant_service_dynamodb as tenant_service,
)
from app.services.lead.lead_service_dynamodb import (
    lead_service_dynamodb as lead_service,
)
from app.services.conversation.conversation_service_dynamodb import (
    conversation_service_dynamodb as conversation_service,
)
from app.services.storage.document_service_dynamodb import (
    document_service_dynamodb as document_service,
)
from app.services.analytics.analytics_service_dynamodb import (
    analytics_service_dynamodb as analytics_service,
)
from app.services.audit.audit_service_dynamodb import (
    audit_service_dynamodb as audit_service,
)

# Export flag for legacy compatibility (though effectively always True now)
USE_DYNAMODB = True


# Export unified interface
__all__ = [
    "user_service",
    "tenant_service",
    "lead_service",
    "conversation_service",
    "document_service",
    "analytics_service",
    "audit_service",
    "USE_DYNAMODB",
]
