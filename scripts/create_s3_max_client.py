"""Create a single S3_MAX tier Client / Tenant in DynamoDB.

Includes:
- Plan: S3_MAX
- Full Lead Qualification Criteria (LQC)
- Structured Qualification Questions with Quick Replies
- Pre-generated Question Bank for S3 MAX LangGraph Agent
- Complete Bot & UI Customizations, Branding, and Domain Configuration

Usage:
    python scripts/create_s3_max_client.py
"""

import sys
import os
import secrets
import json
from datetime import datetime, timezone

# Ensure parent path is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.models.tenant import (
    TenantCreate,
    TenantConfig,
    TenantStatus,
    Plan,
    QualificationQuestion,
    QuickReply,
)
from app.services.tenant.tenant_service_dynamodb import TenantServiceDynamoDB


def create_s3_max_client():
    """Creates a single comprehensive S3_MAX tier client."""
    print("🚀 Initializing S3_MAX Client creation process...")

    # 1. Define Lead Qualification Criteria (LQC)
    lqc = (
        "Qualifying Criteria:\n"
        "1. Budget: Minimum ₹1.5 Cr ($180,000+) up to ₹10 Cr+.\n"
        "2. Timeline: Planning to purchase immediately or within 6 months.\n"
        "3. Property Type: 3 BHK Apartment, 4 BHK Villa, or 5 BHK Penthouse.\n"
        "4. Location Preference: Prime Metro Corridor, Financial District, or Bayfront.\n"
        "5. Decision Authority: Primary Buyer or Joint Family Decision Maker.\n"
        "6. Purpose: Personal Residence (Self-Use) or High-Yield Real Estate Investment."
    )

    # 2. Define Qualification Questions (5 core structured questions)
    qualification_questions = [
        QualificationQuestion(
            id="budget",
            label="Budget Range",
            instruction="Ask what budget range they are targeting for their property purchase.",
            collects=["budget"],
            quick_replies=[
                QuickReply(id="1", text="₹1.5 Cr - ₹2.5 Cr"),
                QuickReply(id="2", text="₹2.5 Cr - ₹5 Cr"),
                QuickReply(id="3", text="₹5 Cr+"),
            ],
            required=True,
        ),
        QualificationQuestion(
            id="timeline",
            label="Purchase Timeline",
            instruction="Ask when they plan to buy or finalize the property decision.",
            collects=["timeline"],
            quick_replies=[
                QuickReply(id="1", text="Immediately (Ready to Buy)"),
                QuickReply(id="2", text="Within 3 Months"),
                QuickReply(id="3", text="6+ Months"),
            ],
            required=True,
        ),
        QualificationQuestion(
            id="property_type",
            label="Property Layout",
            instruction="Ask what configuration/layout of property they are looking for.",
            collects=["property_type"],
            quick_replies=[
                QuickReply(id="1", text="3 BHK Luxury Apartment"),
                QuickReply(id="2", text="4 BHK Gated Villa"),
                QuickReply(id="3", text="5 BHK Penthouse"),
            ],
            required=True,
        ),
        QualificationQuestion(
            id="location",
            label="Preferred Location",
            instruction="Ask about their preferred location or zone in the city.",
            collects=["location"],
            quick_replies=[
                QuickReply(id="1", text="Financial District Corridor"),
                QuickReply(id="2", text="Golf Course Road Corridor"),
                QuickReply(id="3", text="Coastal Bayfront"),
            ],
            required=True,
        ),
        QualificationQuestion(
            id="purpose",
            label="Purchase Purpose",
            instruction="Ask whether this purchase is for self-use residence or real estate investment.",
            collects=["purpose"],
            quick_replies=[
                QuickReply(id="1", text="Self-Use Residence"),
                QuickReply(id="2", text="High-Yield Investment"),
            ],
            required=True,
        ),
    ]

    # 3. Define Pre-generated S3_MAX Question Bank
    question_bank = [
        {
            "id": "qb_001",
            "question": "What is your target budget range for your next luxury home?",
            "dimension": "budget",
            "priority": "high",
            "quick_replies": ["₹1.5 Cr - ₹2.5 Cr", "₹2.5 Cr - ₹5 Cr", "₹5 Cr+"],
        },
        {
            "id": "qb_002",
            "question": "How soon are you planning to finalize your property purchase?",
            "dimension": "timeline",
            "priority": "high",
            "quick_replies": ["Immediately", "Within 3 Months", "6+ Months"],
        },
        {
            "id": "qb_003",
            "question": "Which property type and layout fits your living requirements best?",
            "dimension": "preference",
            "priority": "high",
            "quick_replies": ["3 BHK Apartment", "4 BHK Villa", "5 BHK Penthouse"],
        },
        {
            "id": "qb_004",
            "question": "Which prime neighborhood or region do you prefer for your residence?",
            "dimension": "preference",
            "priority": "medium",
            "quick_replies": ["Financial District", "Golf Course Road", "Bayfront"],
        },
        {
            "id": "qb_005",
            "question": "Will this property serve as your primary home or an investment property?",
            "dimension": "use_case",
            "priority": "medium",
            "quick_replies": ["Self-Use Residence", "High-Yield Investment"],
        },
        {
            "id": "qb_006",
            "question": "Are you the primary decision-maker for this purchase, or evaluating together with family?",
            "dimension": "authority",
            "priority": "medium",
            "quick_replies": ["Primary Decision Maker", "Joint Family Decision", "Advising / Agent"],
        },
    ]

    # 4. Generate API Key & Allowed Origins
    api_key = secrets.token_hex(16)
    client_domain = "https://auraluxuryhomes.com"

    # 5. Build TenantConfig
    config = TenantConfig(
        plan=Plan.S3_MAX,
        url_slug="aura-luxury-homes",
        bot_name="Aura S3 Max Assistant",
        company_name="Aura Luxury Homes",
        avatar_url="https://images.unsplash.com/photo-1600585154340-be6161a56a0c?w=128&q=80",
        primary_color="#1E3A8A",
        secondary_color="#D97706",
        headline_prefix="Welcome to",
        highlighted_headline="Aura Luxury Homes",
        sub_headline="AI-Powered Virtual Concierge for Premium Villas & Residences",
        key_features=[
            "24/7 Virtual AI Assistant",
            "Instant Price & Plan Calculator",
            "VIP Private Tour Booking",
        ],
        welcome_message="Hello! Welcome to Aura Luxury Homes. I am your AI concierge. How can I assist you with your luxury property search today?",
        company_context=(
            "Aura Luxury Homes is an award-winning real estate developer offering luxury "
            "3 BHK apartments, 4 BHK duplex villas, and 5 BHK sky penthouses. Located across prime "
            "metropolitan hubs, our properties feature Italian marble flooring, private infinity pools, "
            "smart home automation, and 5-star concierge services starting at ₹1.5 Crore."
        ),
        lead_form_enabled=True,
        lead_form_fields=["name", "phone", "email"],
        language="en",
        tone="professional",
        lead_qualification_criteria=lqc,
        qualification_questions=qualification_questions,
        question_bank=question_bank,
        client_domain=client_domain,
        allowed_origins=[client_domain],
        api_key=api_key,
    )

    # 6. Build TenantCreate payload
    tenant_data = TenantCreate(
        name="Aura Luxury Homes",
        config=config,
        status=TenantStatus.ACTIVE,
        created_by="System Provisioner",
    )

    # 7. Persist to DynamoDB via TenantService
    service = TenantServiceDynamoDB()
    new_tenant = service.create_tenant(tenant_data)

    print("\n✅ Successfully Created 1 S3_MAX Client!\n")
    print("=" * 65)
    print(f"  Tenant ID   : {new_tenant.tenant_id}")
    print(f"  Client Name : {new_tenant.name}")
    print(f"  Plan Tier   : {new_tenant.config.plan.value}")
    print(f"  URL Slug    : {new_tenant.config.url_slug}")
    print(f"  Status      : {new_tenant.status.value}")
    print(f"  API Key     : {new_tenant.config.api_key}")
    print(f"  Domain      : {new_tenant.config.client_domain}")
    print("=" * 65)

    print("\n📋 Lead Qualification Criteria (LQC):")
    print("-" * 65)
    print(new_tenant.config.lead_qualification_criteria)
    print("-" * 65)

    print(f"\n❓ Qualification Questions ({len(new_tenant.config.qualification_questions)} questions):")
    for idx, q in enumerate(new_tenant.config.qualification_questions, 1):
        opts = ", ".join([qr.text for qr in q.quick_replies])
        print(f"  {idx}. [{q.id.upper()}] {q.label}: \"{q.instruction}\" -> Options: ({opts})")

    print(f"\n🏦 Pre-generated Question Bank ({len(new_tenant.config.question_bank)} questions):")
    for idx, qb in enumerate(new_tenant.config.question_bank, 1):
        print(f"  {idx}. [{qb['dimension']}] ({qb['priority']} priority) {qb['question']}")

    print("=" * 65)
    return new_tenant


if __name__ == "__main__":
    create_s3_max_client()
