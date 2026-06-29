import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from app.utils.encryption import encrypt_secret, decrypt_secret, is_encrypted
from app.services.outbound.whatsapp_sender import whatsapp_sender
from app.models.tenant import TenantConfig


def test_encryption_helpers():
    plaintext = "super-secret-whatsapp-token"
    
    # 1. Encrypt and decrypt should be symmetric
    ciphertext = encrypt_secret(plaintext)
    assert ciphertext != plaintext
    assert decrypt_secret(ciphertext) == plaintext
    
    # 2. is_encrypted validation
    assert is_encrypted(ciphertext) is True
    assert is_encrypted(plaintext) is False
    assert is_encrypted("") is False
    assert is_encrypted(None) is False


def test_phone_number_normalization():
    # Test IN E.164 normalization examples
    assert whatsapp_sender.normalize_phone("9876543210") == "+919876543210"
    assert whatsapp_sender.normalize_phone("919876543210") == "+919876543210"
    assert whatsapp_sender.normalize_phone("+919876543210") == "+919876543210"
    
    # Test spaces, formatting, and international formats
    assert whatsapp_sender.normalize_phone(" +91 98765 43210 ") == "+919876543210"
    assert whatsapp_sender.normalize_phone("+1 650 253 0000") == "+16502530000"
    
    # Invalid formats
    assert whatsapp_sender.normalize_phone("12345") is None
    assert whatsapp_sender.normalize_phone("invalid-phone") is None
    assert whatsapp_sender.normalize_phone("") is None


@pytest.mark.anyio
async def test_whatsapp_sender_simulation_mode():
    config = TenantConfig()
    
    # By default, config has whatsapp_enabled = False, so should trigger Simulation Mode
    res = await whatsapp_sender.send_message("+919876543210", "Hello", config)
    assert res == {
        "status": "simulated",
        "provider": "whatsapp"
    }


@pytest.mark.anyio
async def test_whatsapp_sender_real_delivery_success():
    config = TenantConfig(
        whatsapp_enabled=True,
        whatsapp_access_token="encrypted-token",
        whatsapp_phone_number_id="12345"
    )
    
    # Mock decrypt_secret so it returns "decrypted-token"
    with patch("app.services.outbound.whatsapp_sender.decrypt_secret", return_value="decrypted-token"), \
         patch("httpx.AsyncClient.post") as mock_post:
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "messages": [{"id": "wamid.success_id"}]
        }
        mock_post.return_value = mock_response
        
        res = await whatsapp_sender.send_message("+919876543210", "Hello message", config)
        
        assert res["status"] == "sent"
        assert res["message_id"] == "wamid.success_id"
        
        # Verify mock_post call details
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://graph.facebook.com/v23.0/12345/messages"
        assert kwargs["headers"]["Authorization"] == "Bearer decrypted-token"
        assert kwargs["json"]["to"] == "+919876543210"
        assert kwargs["json"]["text"]["body"] == "Hello message"
