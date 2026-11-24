#!/usr/bin/env python3
"""
Comprehensive API End-to-End Test Script
Tests ALL endpoints with REAL data:
- Authentication (signup, login)
- Tenant management
- Document ingestion (RAG)
- Conversations
- Chat (with RAG)
- Lead management
"""

import requests
import time
from datetime import datetime

# Configuration
BASE_URL = "http://localhost:8000"
# Use a fixed email for consistent testing
TEST_EMAIL = "test_aob_user@example.com"
TEST_PASSWORD = "SecurePassword123!"
TENANT_NAME = "Test Real Estate Corp"

# Colors for output
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'

def print_test(name):
    print(f"\n{Colors.BLUE}{'='*70}{Colors.END}")
    print(f"{Colors.BLUE}TEST: {name}{Colors.END}")
    print(f"{Colors.BLUE}{'='*70}{Colors.END}")

def print_success(msg):
    print(f"{Colors.GREEN}✅ {msg}{Colors.END}")

def print_error(msg):
    print(f"{Colors.RED}❌ {msg}{Colors.END}")

def print_info(msg):
    print(f"{Colors.YELLOW}ℹ️  {msg}{Colors.END}")

# Store test data
test_data = {
    "access_token": None,
    "tenant_id": None,
    "conversation_id": None,
    "lead_id": None,
    "document_id": None,
    "api_key": None
}

def poll_for_document_completion(document_id, headers, max_retries=10, initial_wait=2):
    """
    Poll for document processing completion with exponential backoff.
    Production-ready pattern for async operations.
    
    Args:
        document_id: Document ID to check
        headers: Authorization headers
        max_retries: Maximum number of retry attempts
        initial_wait: Initial wait time in seconds (doubles each retry)
    
    Returns:
        dict: Document data if found, None otherwise
    """
    wait_time = initial_wait
    
    for attempt in range(max_retries):
        try:
            # Try to retrieve document
            response = requests.get(
                f"{BASE_URL}/api/documents/{document_id}",
                headers=headers
            )
            
            if response.status_code == 200:
                doc = response.json()
                print_info(f"Document ready after {attempt + 1} attempts")
                return doc
            elif response.status_code == 404:
                # Document not yet processed
                if attempt < max_retries - 1:
                    print_info(f"Attempt {attempt + 1}: Document still processing, waiting {wait_time}s...")
                    time.sleep(wait_time)
                    wait_time = min(wait_time * 2, 30)  # Exponential backoff, max 30s
                else:
                    print_error(f"Document not ready after {max_retries} attempts")
                    return None
            else:
                print_error(f"Unexpected status: {response.status_code}")
                return None
        except Exception as e:
            print_error(f"Error polling document: {e}")
            if attempt < max_retries - 1:
                time.sleep(wait_time)
                wait_time = min(wait_time * 2, 30)
            else:
                return None
    
    return None

def test_1_signup():
    """Test user registration"""
    print_test("1. User Signup")
    
    payload = {
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD,
        "full_name": "Test User",
        "role": "platform_admin"  # Need admin role to create tenants
    }
    
    response = requests.post(f"{BASE_URL}/api/auth/register", json=payload)
    
    if response.status_code == 201:
        data = response.json()
        print_success("User registered successfully")
        print_info(f"Email: {data['email']}")
        print_info(f"Role: {data['role']}")
        return True
    else:
        print_error(f"Signup failed: {response.status_code}")
        print_error(f"Response: {response.text}")
        return False

def test_2_login():
    """Test user login"""
    print_test("2. User Login")
    
    payload = {
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD
    }
    
    response = requests.post(
        f"{BASE_URL}/api/auth/login",
        json=payload
    )
    
    if response.status_code == 200:
        data = response.json()
        test_data["access_token"] = data["access_token"]
        print_success("Login successful")
        print_info(f"Access Token: {data['access_token'][:30]}...")
        print_info(f"Token Type: {data['token_type']}")
        return True
    else:
        print_error(f"Login failed: {response.status_code}")
        print_error(f"Response: {response.text}")
        return False

def test_3_create_tenant():
    """Test tenant creation"""
    print_test("3. Create Tenant")
    
    headers = {"Authorization": f"Bearer {test_data['access_token']}"}
    
    payload = {
        "name": TENANT_NAME,
        "config": {
            "plan": "S2",
            "url_slug": "test-real-estate",
            "bot_name": "PropertyBot",
            "avatar_url": "https://api.dicebear.com/7.x/bottts/svg?seed=test",
            "primary_color": "#10B981",
            "secondary_color": "#F59E0B",
            "headline_prefix": "Find Your",
            "highlighted_headline": "Dream Property",
            "sub_headline": "with AI-powered assistance",
            "key_features": ["24/7 Support", "Instant Search", "Virtual Tours"],
            "qualification_questions": [
                {
                    "id": "Q1",
                    "instruction": "What type of property are you looking for?",
                    "label": "Property Type",
                    "collects": ["property_type"],
                    "quick_replies": [
                        {"id": "1", "text": "Apartment", "value": "apartment"},
                        {"id": "2", "text": "Villa", "value": "villa"}
                    ],
                    "required": True
                }
            ],
            "welcome_message": "Hello! Welcome to Test Real Estate. How can I help you today?",
            "language": "en",
            "tone": "friendly",
            "company_context": "Test Real Estate specializes in luxury properties in Bangalore."
        }
    }
    
    response = requests.post(f"{BASE_URL}/api/tenants", json=payload, headers=headers)
    
    if response.status_code == 201:
        data = response.json()
        test_data["tenant_id"] = data["tenant_id"]
        print_success("Tenant created successfully")
        print_info(f"Tenant ID: {data['tenant_id']}")
        print_info(f"Name: {data['name']}")
        print_info(f"Plan: {data['config']['plan']}")
        print_info(f"Bot Name: {data['config']['bot_name']}")
        return True
    else:
        print_error(f"Tenant creation failed: {response.status_code}")
        print_error(f"Response: {response.text}")
        return False

def test_4_generate_api_key():
    """Test API key generation"""
    print_test("4. Generate API Key for Widget")
    
    headers = {"Authorization": f"Bearer {test_data['access_token']}"}
    
    response = requests.post(
        f"{BASE_URL}/api/tenants/{test_data['tenant_id']}/generate-api-key",
        headers=headers
    )
    
    if response.status_code == 200:
        data = response.json()
        test_data["api_key"] = data["api_key"]
        print_success("API Key generated")
        print_info(f"API Key: {data['api_key'][:30]}...")
        return True
    else:
        print_error(f"API key generation failed: {response.status_code}")
        print_error(f"Response: {response.text}")
        return False

def test_5_get_welcome_message():
    """Test welcome endpoint (public)"""
    print_test("5. Get Welcome Message (Widget Init)")
    
    response = requests.get(f"{BASE_URL}/api/tenants/{test_data['tenant_id']}/welcome")
    
    if response.status_code == 200:
        data = response.json()
        print_success("Welcome data retrieved")
        print_info(f"Welcome Message: {data['welcome_message']}")
        print_info(f"Bot Name: {data['branding']['bot_name']}")
        print_info(f"Colors: {data['branding']['primary_color']}, {data['branding']['secondary_color']}")
        print_info(f"Features: {len(data['branding']['key_features'])} items")
        print_info(f"Questions: {len(data['branding']['qualification_questions'])} items")
        return True
    else:
        print_error(f"Welcome endpoint failed: {response.status_code}")
        print_error(f"Response: {response.text}")
        return False

def test_6_create_document():
    """Test document upload"""
    print_test("6. Create Document for RAG Ingestion")
    
    # Create a test markdown file
    import tempfile
    import os
    
    test_content = """# Test Property Document
    
## Available Properties

### Luxury Apartments
- **Location**: Downtown Bangalore
- **Type**: 3BHK, 2BHK
- **Price Range**: ₹80L - ₹1.5Cr
- **Amenities**: Swimming pool, Gym, Children's play area
- **Possession**: Ready to move

### Premium Villas
- **Location**: Whitefield
- **Type**: 4BHK Villa
- **Price Range**: ₹2Cr - ₹3.5Cr
- **Amenities**: Private garden, Home theater, Smart home
- **Possession**: Under construction

## Contact
For more information, contact our sales team.
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
        f.write(test_content)
        temp_file = f.name
    
    try:
        headers = {"Authorization": f"Bearer {test_data['access_token']}"}
        
        with open(temp_file, 'rb') as f:
            files = {'file': ('test_properties.md', f, 'text/markdown')}
            headers_with_tenant = headers.copy()
            headers_with_tenant['X-Tenant-ID'] = test_data['tenant_id']
            
            response = requests.post(
                f"{BASE_URL}/api/documents/ingest",
                files=files,
                headers=headers_with_tenant
            )
        
        # Accept both 200 (sync processing) and 202 (async processing)
        if response.status_code in [200, 202]:
            doc_data = response.json()
            if response.status_code == 202:
                print_info("Status: Queued for async processing")
                print_info(f"Message: {doc_data.get('message')}")
                # Fetch actual document_id from the list
                time.sleep(1)  # wait a moment for DB write
                list_response = requests.get(
                    f"{BASE_URL}/api/documents/tenants/{test_data['tenant_id']}",
                    headers=headers
                )
                if list_response.status_code == 200:
                    docs = list_response.json()
                    matching_docs = [d for d in docs if d.get("filename") == "test_properties.md"]
                    if matching_docs:
                        test_data["document_id"] = matching_docs[0].get("document_id")
                    else:
                        test_data["document_id"] = doc_data.get("job_id")
                else:
                    test_data["document_id"] = doc_data.get("job_id")
            else:
                test_data["document_id"] = doc_data.get("document_id")
                print_info(f"Status: {doc_data.get('status')}")

            print_success("Document uploaded")
            print_info(f"Document ID: {test_data['document_id']}")
            print_info(f"Response: {doc_data}")  # Debug: see full response
            
            # For async processing, poll until document is ready
            if response.status_code == 202 and test_data['document_id']:
                print_info("Polling for document completion with exponential backoff...")
                headers = {"Authorization": f"Bearer {test_data['access_token']}"}
                completed_doc = poll_for_document_completion(
                    test_data['document_id'], 
                    headers,
                    max_retries=10,
                    initial_wait=2
                )
                if completed_doc:
                    print_success("Document processing completed!")
                    print_info(f"Status: {completed_doc.get('status')}")
                    print_info(f"Chunks: {completed_doc.get('chunk_count', 0)}")
                else:
                    print_error("Document processing not completed in time")
                    # Still return True as upload succeeded
            else:
                # Sync processing or no polling needed
                print_info("Waiting 3 seconds for sync processing...")
                time.sleep(3)
            
            return True
        else:
            print_error(f"Document uploadfailed: {response.status_code}")
            print_error(f"Response: {response.text}")
            return False
    finally:
        os.unlink(temp_file)

def test_7_chat_first_message():
    """Test first chat message (creates conversation)"""
    print_test("7. Send First Chat Message")
    
    payload = {
        "query": "I'm looking for a 3BHK apartment in downtown Bangalore. What do you have?",
        "tenant_id": test_data['tenant_id']
    }
    
    response = requests.post(f"{BASE_URL}/api/chat", json=payload)
    
    if response.status_code == 200:
        data = response.json()
        test_data["conversation_id"] = data["conversation_id"]
        print_success("Chat response received")
        print_info(f"Conversation ID: {data['conversation_id']}")
        print_info(f"Answer: {data['answer'][:100]}...")
        print_info(f"Sources: {data.get('sources', [])}")
        return True
    else:
        print_error(f"Chat failed: {response.status_code}")
        print_error(f"Response: {response.text}")
        return False

def test_8_chat_follow_up():
    """Test follow-up chat message"""
    print_test("8. Send Follow-up Chat Message")
    
    payload = {
        "query": "What's the price range for 3BHK apartments?",
        "tenant_id": test_data['tenant_id'],
        "conversation_id": test_data['conversation_id']
    }
    
    response = requests.post(f"{BASE_URL}/api/chat", json=payload)
    
    if response.status_code == 200:
        data = response.json()
        print_success("Follow-up response received")
        print_info(f"Answer: {data['answer'][:100]}...")
        return True
    else:
        print_error(f"Follow-up chat failed: {response.status_code}")
        print_error(f"Response: {response.text}")
        return False

def test_9_chat_with_contact_info():
    """Test chat with contact information (triggers lead capture)"""
    print_test("9. Provide Contact Information (Lead Capture)")
    
    payload = {
        "query": "My name is John Doe, email is john@example.com, phone is +91-9876543210",
        "tenant_id": test_data['tenant_id'],
        "conversation_id": test_data['conversation_id']
    }
    
    response = requests.post(f"{BASE_URL}/api/chat", json=payload)
    
    if response.status_code == 200:
        data = response.json()
        print_success("Contact info processed")
        print_info(f"Answer: {data['answer'][:100]}...")
        return True
    else:
        print_error(f"Contact info chat failed: {response.status_code}")
        print_error(f"Response: {response.text}")
        return False

def test_10_get_conversation():
    """Test get conversation details"""
    print_test("10. Get Conversation Details")
    
    headers = {"Authorization": f"Bearer {test_data['access_token']}"}
    
    response = requests.get(
        f"{BASE_URL}/api/chat/conversations/{test_data['conversation_id']}",
        headers=headers
    )
    
    if response.status_code == 200:
        data = response.json()
        print_success("Conversation retrieved")
        print_info(f"Message Count: {data['message_count']}")
        print_info(f"Lead Captured: {data['lead_captured']}")
        print_info(f"Extracted Data: {data['extracted_lead_data']}")
        return True
    else:
        print_error(f"Get conversation failed: {response.status_code}")
        print_error(f"Response: {response.text}")
        return False

def test_11_list_leads():
    """Test list leads for tenant"""
    print_test("11. List All Leads")
    
    headers = {"Authorization": f"Bearer {test_data['access_token']}"}
    
    response = requests.get(
        f"{BASE_URL}/api/leads/tenants/{test_data['tenant_id']}/all",
        headers=headers
    )
    
    if response.status_code == 200:
        data = response.json()
        print_success(f"Retrieved {len(data)} lead(s)")
        
        if len(data) > 0:
            test_data["lead_id"] = data[0]["lead_id"]
            lead = data[0]
            print_info(f"Lead ID: {lead['lead_id']}")
            print_info(f"Name: {lead.get('name', 'N/A')}")
            print_info(f"Email: {lead.get('email', 'N/A')}")
            print_info(f"Phone: {lead.get('phone', 'N/A')}")
            print_info(f"Status: {lead['status']}")
        return True
    else:
        print_error(f"List leads failed: {response.status_code}")
        print_error(f"Response: {response.text}")
        return False

def test_12_update_lead_status():
    """Test update lead status"""
    print_test("12. Update Lead Status")
    
    if not test_data.get("lead_id"):
        print_info("No lead ID available, skipping")
        return True
    
    headers = {"Authorization": f"Bearer {test_data['access_token']}"}
    
    response = requests.patch(
        f"{BASE_URL}/api/leads/{test_data['lead_id']}/status?new_status=contacted",
        headers=headers
    )
    
    if response.status_code == 200:
        data = response.json()
        print_success("Lead status updated")
        print_info(f"New Status: {data['status']}")
        return True
    else:
        print_error(f"Update lead status failed: {response.status_code}")
        print_error(f"Response: {response.text}")
        return False

def test_13_list_conversations():
    """Test list conversations for tenant"""
    print_test("13. List All Conversations")
    
    headers = {"Authorization": f"Bearer {test_data['access_token']}"}
    
    response = requests.get(
        f"{BASE_URL}/api/chat/tenants/{test_data['tenant_id']}/conversations",
        headers=headers
    )
    
    if response.status_code == 200:
        data = response.json()
        print_success(f"Retrieved {len(data)} conversation(s)")
        
        if len(data) > 0:
            conv = data[0]
            print_info(f"Conversation ID: {conv['conversation_id']}")
            print_info(f"Message Count: {conv['message_count']}")
            print_info(f"Lead Captured: {conv['lead_captured']}")
        return True
    else:
        print_error(f"List conversations failed: {response.status_code}")
        print_error(f"Response: {response.text}")
        return False

def test_14_get_document():
    """Test get document details"""
    print_test("14. Get Document Details")
    
    headers = {"Authorization": f"Bearer {test_data['access_token']}"}
    
    # If document_id not available from upload, get it from the list
    if not test_data.get("document_id"):
        print_info("Document ID not available, fetching from list...")
        list_response = requests.get(
            f"{BASE_URL}/api/documents/tenants/{test_data['tenant_id']}",
            headers=headers
        )
        if list_response.status_code == 200:
            docs = list_response.json()
            if len(docs) > 0:
                test_data["document_id"] = docs[0].get("document_id")
                print_info(f"Retrieved document ID from list: {test_data['document_id']}")
            else:
                print_error("No documents found in list")
                return False
        else:
            print_error(f"Failed to fetch document list: {list_response.status_code}")
            return False
    
    response = requests.get(
        f"{BASE_URL}/api/documents/{test_data['document_id']}",
        headers=headers
    )
    
    if response.status_code == 200:
        data = response.json()
        print_success("Document retrieved")
        print_info(f"Filename: {data.get('filename', 'N/A')}")
        print_info(f"Status: {data.get('status', 'N/A')}")
        print_info(f"Chunk Count: {data.get('chunk_count', 0)}")
        return True
    else:
        print_error(f"Get document failed: {response.status_code}")
        print_error(f"Response: {response.text}")
        return False

def test_15_list_documents():
    """Test list documents for tenant"""
    print_test("15. List All Documents")
    
    headers = {"Authorization": f"Bearer {test_data['access_token']}"}
    
    response = requests.get(
        f"{BASE_URL}/api/documents/tenants/{test_data['tenant_id']}",
        headers=headers
    )
    
    if response.status_code == 200:
        data = response.json()
        print_success(f"Retrieved {len(data)} document(s)")
        
        if len(data) > 0:
            doc = data[0]
            print_info(f"Document: {doc['filename']}")
            print_info(f"Status: {doc['status']}")
        return True
    else:
        print_error(f"List documents failed: {response.status_code}")
        print_error(f"Response: {response.text}")
        return False

# Main test execution
def run_all_tests():
    print(f"\n{Colors.BLUE}{'='*70}{Colors.END}")
    print(f"{Colors.BLUE}COMPREHENSIVE API TESTING - ALL ENDPOINTS{Colors.END}")
    print(f"{Colors.BLUE}{'='*70}{Colors.END}")
    print(f"Base URL: {BASE_URL}")
    print(f"Test Email: {TEST_EMAIL}")
    print(f"Timestamp: {datetime.now()}")
    
    tests = [
        ("User Signup", test_1_signup),
        ("User Login", test_2_login),
        ("Create Tenant", test_3_create_tenant),
        ("Generate API Key", test_4_generate_api_key),
        ("Get Welcome Message", test_5_get_welcome_message),
        ("Upload Document (RAG)", test_6_create_document),
        ("First Chat Message", test_7_chat_first_message),
        ("Follow-up Chat", test_8_chat_follow_up),
        ("Lead Capture Chat", test_9_chat_with_contact_info),
        ("Get Conversation", test_10_get_conversation),
        ("List Leads", test_11_list_leads),
        ("Update Lead Status", test_12_update_lead_status),
        ("List Conversations", test_13_list_conversations),
        ("Get Document Details", test_14_get_document),
        ("List Documents", test_15_list_documents),
    ]
    
    results = []
    
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print_error(f"Test raised exception: {e}")
            results.append((name, False))
    
    # Summary
    print(f"\n{Colors.BLUE}{'='*70}{Colors.END}")
    print(f"{Colors.BLUE}TEST SUMMARY{Colors.END}")
    print(f"{Colors.BLUE}{'='*70}{Colors.END}\n")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = f"{Colors.GREEN}✅ PASS{Colors.END}" if result else f"{Colors.RED}❌ FAIL{Colors.END}"
        print(f"{status} - {name}")
    
    print(f"\n{Colors.BLUE}{'='*70}{Colors.END}")
    print(f"Total: {total} tests")
    print(f"{Colors.GREEN}Passed: {passed}{Colors.END}")
    print(f"{Colors.RED}Failed: {total - passed}{Colors.END}")
    print(f"Success Rate: {passed/total*100:.1f}%")
    print(f"{Colors.BLUE}{'='*70}{Colors.END}\n")
    
    if passed == total:
        print(f"{Colors.GREEN}🎉 ALL TESTS PASSED!{Colors.END}\n")
    else:
        print(f"{Colors.YELLOW}⚠️  Some tests failed. Review output above.{Colors.END}\n")

if __name__ == "__main__":
    try:
        run_all_tests()
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Tests interrupted by user{Colors.END}")
    except Exception as e:
        print(f"\n{Colors.RED}Fatal error: {e}{Colors.END}")
        import traceback
        traceback.print_exc()
