"""Unit tests for Chat Products (website chat product cards).

Pure unit tests — no network, no AWS, no DynamoDB, no app settings/env. Modules under
app/services/ are loaded straight from their files so the app.services package
__init__ (which wires up DynamoDB/Redis-backed services) never runs; S3, DynamoDB and
Redis are replaced with in-memory fakes.
"""

import copy
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.conversation import ChatProductCard, ChatResponse  # noqa: E402
from app.models.tenant import (  # noqa: E402
    ChatProduct,
    TenantConfig,
    TenantUpdate,
    is_valid_chat_product_image_id,
)


def _load_module(name: str, relative_path: str):
    """Load a module from its file without importing its parent package __init__."""
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


product_intent = _load_module("_test_chat_products_intent", "app/services/chat/product_intent.py")
product_images = _load_module("_test_chat_products_images", "app/services/chat/product_images.py")

PID_1 = "a" * 32
PID_2 = "b" * 32
PID_3 = "c" * 32
IMG_1 = "0123456789abcdef0123456789abcdef.png"

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP_BYTES = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 64
GIF_BYTES = b"GIF89a" + b"\x00" * 64


def _products(*titles):
    ids = [PID_1, PID_2, PID_3]
    return [ChatProduct(product_id=ids[i], title=title) for i, title in enumerate(titles)]


def _response(answer="How can I help?", transition=None):
    return ChatResponse(
        answer=answer,
        conversation_id="conv-1",
        message_id="msg-1",
        conversational_transition=transition,
    )


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "What products do you have?",
        "Tell me about your product",
        "Can I see the CATALOG",
        "do you have a catalogue?",
        "which models do you make",
        "what builds are available",
        "Any packages?",
        "your offerings please",
        "show the new collection",
        "So what do you sell?",
        "what  do\tyou   offer",
        "show me something",
        "Can you recommend one?",
        "suggest something for me",
        "Do you have a price list?",
        "what's the pricing",
        # Real visitor phrasings: typos and asking for pictures
        "so some produt image",
        "give me image",
        "show some photos",
        "prodcut list please",
        "any prodects?",
        "can I see pictures",
        "send pics",
    ],
)
def test_intent_keywords_positive(message):
    assert product_intent.has_product_intent(message, []) is True


@pytest.mark.parametrize(
    "message",
    [
        "my budget is 80k",
        "hello",
        "Hi there!",
        "My name is Ravi, 9876543210",
        "Yes, schedule a call",
        "Maybe later",
        "I want to productize my idea",  # whole words only
        "That is overpriced",
        "show",
        "we are arranging a meeting",
        # Broad words that appear in ordinary qualification answers must not pop the cards up
        "My price range is 80k",
        "What options are there for financing?",
        "We have no plans yet",
        "I need it for my services business",
        # Image words and look-alike real words in ordinary answers
        "I do image editing",
        "photo editing and gaming",
        "I produce videos",
        "it should protect my data",
        "",
        "   ",
        None,
    ],
)
def test_intent_negative(message):
    assert product_intent.has_product_intent(message, _products("Solar Max 5kW", "Pro")) is False


def test_intent_title_mention_alone_does_not_trigger():
    # Tapping a card sends "I'm interested in <title>"; replying with every card again would loop.
    products = _products("Solar Max 5kW", "Villa Deluxe", "C++ (Gold)")
    assert product_intent.has_product_intent("I'm interested in Solar Max 5kW", products) is False
    assert product_intent.has_product_intent("is VILLA   DELUXE available?", products) is False
    assert product_intent.has_product_intent("price of c++ (gold)?", products) is False


def test_intent_tolerates_malformed_products():
    products = [None, 42, {"title": None}, {"title": "Solar Max"}, object()]
    assert product_intent.has_product_intent("what products do you have?", products) is True
    assert product_intent.has_product_intent("hello", products) is False


# ---------------------------------------------------------------------------
# Card building + attaching to the chat response
# ---------------------------------------------------------------------------


def test_build_product_cards_keeps_admin_order():
    cards = product_intent.build_product_cards(_products("First", "Second", "Third"))
    assert [c.title for c in cards] == ["First", "Second", "Third"]
    assert all(isinstance(c, ChatProductCard) for c in cards)
    assert cards[0].model_dump() == {
        "product_id": PID_1,
        "title": "First",
        "description": "",
        "image_id": None,
    }


def test_build_product_cards_skips_malformed():
    products = [
        {"product_id": PID_1, "title": "  Good  ", "description": " desc ", "image_id": IMG_1},
        {"product_id": PID_2, "title": ""},  # empty title
        {"title": "No id"},  # missing product_id
        None,
        "not a product",
        {"product_id": PID_3, "title": "Bad image", "image_id": "../../etc/passwd"},
    ]
    cards = product_intent.build_product_cards(products)
    assert [(c.product_id, c.title) for c in cards] == [(PID_1, "Good"), (PID_3, "Bad image")]
    assert cards[0].description == "desc"
    assert cards[0].image_id == IMG_1
    assert cards[1].image_id is None  # invalid image dropped, card kept


def test_build_product_cards_caps_at_three():
    products = [{"product_id": f"{i:032x}", "title": f"P{i}"} for i in range(5)]
    assert len(product_intent.build_product_cards(products)) == 3


def test_attach_chat_products_with_intent():
    response = _response("We build homes.")
    result = product_intent.attach_chat_products(response, _products("A", "B"), "what products do you have?", "en")
    assert [c.title for c in result.products] == ["A", "B"]
    assert result.answer == "Here's what we offer — tap one to know more.\n\nWe build homes."


def test_attach_chat_products_without_intent_is_noop():
    response = _response("Great, what's your budget?")
    result = product_intent.attach_chat_products(response, _products("A"), "my budget is 80k", "en")
    assert result.products == []
    assert result.answer == "Great, what's your budget?"


def test_attach_chat_products_without_products_is_noop():
    response = _response("Hi!")
    for products in (None, []):
        result = product_intent.attach_chat_products(response, products, "show me your products", "en")
        assert result.products == []
        assert result.answer == "Hi!"


def test_attach_chat_products_empty_answer():
    result = product_intent.attach_chat_products(_response(""), _products("A"), "products?", "en")
    assert result.answer == "Here's what we offer — tap one to know more."


def test_attach_chat_products_localized_and_fallback():
    hindi = product_intent.attach_chat_products(_response("x"), _products("A"), "products", "hi")
    assert hindi.answer.startswith("यह रहा")
    unknown = product_intent.attach_chat_products(_response("x"), _products("A"), "products", "fr")
    assert unknown.answer.startswith("Here's what we offer")
    no_language = product_intent.attach_chat_products(_response("x"), _products("A"), "products", None)
    assert no_language.answer.startswith("Here's what we offer")


def test_attach_chat_products_keeps_transition_first():
    # The chat client strips conversational_transition from the start of the answer
    transition = "Thanks for sharing!"
    response = _response(f"{transition} What's your budget?", transition=transition)
    result = product_intent.attach_chat_products(response, _products("A"), "and your packages?", "en")
    assert result.answer.startswith(transition)
    assert result.answer[len(transition):].strip() == (
        "Here's what we offer — tap one to know more.\n\nWhat's your budget?"
    )


def test_attach_chat_products_is_idempotent():
    response = _response("Hi")
    product_intent.attach_chat_products(response, _products("A"), "products", "en")
    product_intent.attach_chat_products(response, _products("A"), "products", "en")
    assert response.answer.count("Here's what we offer") == 1


def test_attach_chat_products_never_raises():
    class Exploding:
        def __iter__(self):
            raise RuntimeError("boom")

    response = _response("Hi")
    result = product_intent.attach_chat_products(response, Exploding(), "products", "en")
    assert result is response
    assert result.answer == "Hi"
    assert result.products == []

    # All products malformed -> no cards, answer untouched
    result = product_intent.attach_chat_products(_response("Hi"), [{"title": "X"}, None], "products", "en")
    assert result.products == []
    assert result.answer == "Hi"


def test_chat_response_products_default_empty():
    assert ChatResponse(answer="a", conversation_id="c", message_id="m").products == []


# ---------------------------------------------------------------------------
# ChatProduct / TenantConfig validation
# ---------------------------------------------------------------------------


def test_chat_product_valid_and_normalized():
    product = ChatProduct(
        product_id=PID_1.upper(),
        title="  Solar Max  ",
        description="  Great panel  ",
        image_id=IMG_1,
    )
    assert product.product_id == PID_1
    assert product.title == "Solar Max"
    assert product.description == "Great panel"
    assert product.image_id == IMG_1


def test_chat_product_defaults():
    product = ChatProduct(product_id=PID_1, title="A", description=None, image_id="")
    assert product.description == ""
    assert product.image_id is None


@pytest.mark.parametrize("title", ["", "   ", "x" * 81, None, 5])
def test_chat_product_invalid_title(title):
    with pytest.raises(ValidationError):
        ChatProduct(product_id=PID_1, title=title)


def test_chat_product_title_length_limits():
    assert ChatProduct(product_id=PID_1, title="x" * 80).title == "x" * 80
    assert ChatProduct(product_id=PID_1, title=" " + "x" * 80 + " ").title == "x" * 80  # stripped first


def test_chat_product_description_length_limits():
    assert len(ChatProduct(product_id=PID_1, title="A", description="d" * 300).description) == 300
    with pytest.raises(ValidationError):
        ChatProduct(product_id=PID_1, title="A", description="d" * 301)


@pytest.mark.parametrize("product_id", ["", "abc", "g" * 32, "a" * 31, "a" * 33, "a" * 8 + "-" + "a" * 23, None])
def test_chat_product_invalid_product_id(product_id):
    with pytest.raises(ValidationError):
        ChatProduct(product_id=product_id, title="A")


@pytest.mark.parametrize(
    "image_id",
    [
        "0123456789abcdef0123456789abcdef.gif",
        "0123456789ABCDEF0123456789ABCDEF.png",
        "0123456789abcdef0123456789abcdef.PNG",
        "0123456789abcdef0123456789abcdef.jpeg",
        "0123456789abcdef0123456789abcde.png",
        "../0123456789abcdef0123456789abcdef.png",
        "0123456789abcdef0123456789abcdef.png/x",
        "0123456789abcdef0123456789abcdef",
        "x.png",
    ],
)
def test_chat_product_invalid_image_id(image_id):
    with pytest.raises(ValidationError):
        ChatProduct(product_id=PID_1, title="A", image_id=image_id)


@pytest.mark.parametrize(
    ("image_id", "expected"),
    [
        ("0123456789abcdef0123456789abcdef.png", True),
        ("0123456789abcdef0123456789abcdef.jpg", True),
        ("0123456789abcdef0123456789abcdef.webp", True),
        ("0123456789abcdef0123456789abcdef.jpeg", False),
        ("0123456789abcdef0123456789abcdef.gif", False),
        ("0123456789ABCDEF0123456789ABCDEF.png", False),
        ("0123456789abcdef0123456789abcdef.png\n", False),
        ("../0123456789abcdef0123456789abcdef.png", False),
        ("", False),
        (None, False),
        (123, False),
    ],
)
def test_is_valid_chat_product_image_id(image_id, expected):
    assert is_valid_chat_product_image_id(image_id) is expected


def test_tenant_config_without_chat_products_loads():
    # Existing tenants stored before the feature have no chat_products key
    config = TenantConfig(**{"plan": "S2", "bot_name": "Helper", "url_slug": "acme"})
    assert config.chat_products == []
    assert TenantConfig(chat_products=None).chat_products == []


def test_tenant_config_chat_products_max_three():
    three = [{"product_id": pid, "title": f"P{i}"} for i, pid in enumerate([PID_1, PID_2, PID_3])]
    assert len(TenantConfig(chat_products=three).chat_products) == 3

    four = three + [{"product_id": "d" * 32, "title": "P4"}]
    with pytest.raises(ValidationError):
        TenantConfig(chat_products=four)


def test_tenant_config_chat_products_unique_ids():
    with pytest.raises(ValidationError):
        TenantConfig(chat_products=[{"product_id": PID_1, "title": "A"}, {"product_id": PID_1, "title": "B"}])


def test_tenant_config_chat_products_round_trip():
    config = TenantConfig(chat_products=[{"product_id": PID_1, "title": " A ", "image_id": IMG_1}])
    dumped = config.model_dump(mode="json")
    assert dumped["chat_products"] == [
        {"product_id": PID_1, "title": "A", "description": "", "image_id": IMG_1}
    ]
    assert TenantConfig(**dumped).chat_products == config.chat_products


def test_tenant_update_only_carries_chat_products_when_sent():
    without = TenantUpdate(config={"bot_name": "Helper"}).model_dump(exclude_unset=True, mode="json")
    assert "chat_products" not in without["config"]

    cleared = TenantUpdate(config={"bot_name": "Helper", "chat_products": []}).model_dump(
        exclude_unset=True, mode="json"
    )
    assert cleared["config"]["chat_products"] == []


# ---------------------------------------------------------------------------
# Tenant update keeps / replaces chat_products (DynamoDB + Redis faked)
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_service(monkeypatch):
    """TenantServiceDynamoDB backed by an in-memory item store (no AWS, no Redis, no settings)."""
    store = {}

    def get_item(pk, sk):
        item = store.get((pk, sk))
        return copy.deepcopy(item) if item else None

    def update_item(pk, sk, updates):
        store.setdefault((pk, sk), {}).update(copy.deepcopy(updates))
        return copy.deepcopy(store[(pk, sk)])

    dynamodb_stub = types.ModuleType("app.core.dynamodb")
    dynamodb_stub.get_item = get_item
    dynamodb_stub.update_item = update_item
    for name in ("put_item", "delete_item", "query_items", "get_dynamodb_table"):
        setattr(dynamodb_stub, name, MagicMock(name=name))

    redis_stub = types.ModuleType("app.core.redis_client")
    redis_stub.redis_client = MagicMock(**{"keys.return_value": []})

    encryption_stub = types.ModuleType("app.utils.encryption")
    encryption_stub.encrypt_secret = lambda value: f"enc:{value}"
    encryption_stub.is_encrypted = lambda value: str(value).startswith("enc:")

    config_stub = types.ModuleType("app.core.config")
    config_stub.get_settings = MagicMock(name="get_settings")

    monkeypatch.setitem(sys.modules, "app.core.dynamodb", dynamodb_stub)
    monkeypatch.setitem(sys.modules, "app.core.redis_client", redis_stub)
    monkeypatch.setitem(sys.modules, "app.utils.encryption", encryption_stub)
    if "app.core.config" not in sys.modules:
        monkeypatch.setitem(sys.modules, "app.core.config", config_stub)

    module = _load_module(
        "_test_chat_products_tenant_service", "app/services/tenant/tenant_service_dynamodb.py"
    )
    store[("TENANT#t1", "PROFILE")] = {
        "PK": "TENANT#t1",
        "SK": "PROFILE",
        "tenant_id": "t1",
        "name": "Acme",
        "status": "Active",
        "is_active": True,
        "is_deleted": False,
        "created_at": "2026-01-01T00:00:00+00:00",
        "config": {
            "bot_name": "Helper",
            "api_key": "key-1",
            "chat_products": [
                {"product_id": PID_1, "title": "Solar Max", "description": "Panel", "image_id": IMG_1}
            ],
        },
    }
    return module.TenantServiceDynamoDB(), store


def test_existing_tenant_without_chat_products_loads(tenant_service):
    service, store = tenant_service
    del store[("TENANT#t1", "PROFILE")]["config"]["chat_products"]
    tenant = service.get_tenant("t1")
    assert tenant.config.chat_products == []


def test_update_without_chat_products_keeps_them(tenant_service):
    service, store = tenant_service
    tenant = service.update_tenant("t1", TenantUpdate(config={"bot_name": "Renamed"}))
    assert tenant.config.bot_name == "Renamed"
    assert [p.title for p in tenant.config.chat_products] == ["Solar Max"]
    assert tenant.config.chat_products[0].image_id == IMG_1
    assert tenant.config.api_key == "key-1"


def test_update_with_chat_products_replaces_them(tenant_service):
    service, store = tenant_service
    update = TenantUpdate(
        config={
            "bot_name": "Helper",
            "chat_products": [
                {"product_id": PID_2, "title": "  Villa  ", "description": "3BHK"},
                {"product_id": PID_3, "title": "Plot", "image_id": IMG_1},
            ],
        }
    )
    tenant = service.update_tenant("t1", update)
    assert [(p.product_id, p.title) for p in tenant.config.chat_products] == [(PID_2, "Villa"), (PID_3, "Plot")]
    stored = store[("TENANT#t1", "PROFILE")]["config"]["chat_products"]
    assert stored[0] == {"product_id": PID_2, "title": "Villa", "description": "3BHK"}  # None image_id stripped


def test_update_with_empty_chat_products_clears_them(tenant_service):
    service, _ = tenant_service
    tenant = service.update_tenant("t1", TenantUpdate(config={"bot_name": "Helper", "chat_products": []}))
    assert tenant.config.chat_products == []


def test_update_with_too_many_chat_products_is_rejected():
    products = [{"product_id": f"{i:032x}", "title": f"P{i}"} for i in range(4)]
    with pytest.raises(ValidationError):
        TenantUpdate(config={"chat_products": products})


# ---------------------------------------------------------------------------
# Image upload validation + S3 (mocked)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [(PNG_BYTES, "png"), (JPEG_BYTES, "jpg"), (WEBP_BYTES, "webp"), (GIF_BYTES, None), (b"", None), (b"RIFF", None)],
)
def test_sniff_image_extension(data, expected):
    assert product_images.sniff_image_extension(data) == expected


@pytest.mark.parametrize(
    ("data", "content_type", "expected"),
    [
        (PNG_BYTES, "image/png", "png"),
        (JPEG_BYTES, "image/jpeg", "jpg"),
        (WEBP_BYTES, "image/webp", "webp"),
        (PNG_BYTES, "IMAGE/PNG; charset=binary", "png"),
    ],
)
def test_validate_chat_product_image_ok(data, content_type, expected):
    assert product_images.validate_chat_product_image(data, content_type) == expected


@pytest.mark.parametrize(
    ("data", "content_type"),
    [
        (GIF_BYTES, "image/gif"),  # type not allowed
        (PNG_BYTES, "application/octet-stream"),
        (PNG_BYTES, None),
        (JPEG_BYTES, "image/png"),  # magic bytes don't match declared type
        (b"<svg></svg>", "image/png"),
        (b"", "image/png"),  # empty
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * (2 * 1024 * 1024), "image/png"),  # > 2 MB
    ],
)
def test_validate_chat_product_image_rejects(data, content_type):
    with pytest.raises(product_images.ChatProductImageError):
        product_images.validate_chat_product_image(data, content_type)


def test_validate_chat_product_image_max_size_allowed():
    data = PNG_BYTES[:8] + b"\x00" * (2 * 1024 * 1024 - 8)
    assert product_images.validate_chat_product_image(data, "image/png") == "png"


def test_store_chat_product_image_uploads_to_s3_key():
    storage = MagicMock()
    image_id = product_images.store_chat_product_image(storage, "tenant-1", WEBP_BYTES, "image/webp")

    assert is_valid_chat_product_image_id(image_id)
    assert image_id.endswith(".webp")
    storage.upload_file.assert_called_once()
    file_obj, key = storage.upload_file.call_args.args
    assert key == f"chat-products/tenant-1/{image_id}"
    assert file_obj.read() == WEBP_BYTES
    assert storage.upload_file.call_args.kwargs == {"content_type": "image/webp"}


def test_store_chat_product_image_invalid_does_not_upload():
    storage = MagicMock()
    with pytest.raises(product_images.ChatProductImageError):
        product_images.store_chat_product_image(storage, "tenant-1", GIF_BYTES, "image/gif")
    storage.upload_file.assert_not_called()


def _s3_error(code):
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "HeadObject")


def test_get_chat_product_image_url_presigns_existing_object():
    storage = MagicMock(bucket_name="bucket")
    storage.generate_presigned_url.return_value = "https://s3.example/signed"

    url = product_images.get_chat_product_image_url(storage, "tenant-1", IMG_1)

    assert url == "https://s3.example/signed"
    storage.s3_client.head_object.assert_called_once_with(Bucket="bucket", Key=f"chat-products/tenant-1/{IMG_1}")
    storage.generate_presigned_url.assert_called_once_with(f"chat-products/tenant-1/{IMG_1}", expiration=3600)


@pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound", "403"])
def test_get_chat_product_image_url_missing_object(code):
    storage = MagicMock(bucket_name="bucket")
    storage.s3_client.head_object.side_effect = _s3_error(code)
    assert product_images.get_chat_product_image_url(storage, "tenant-1", IMG_1) is None
    storage.generate_presigned_url.assert_not_called()


def test_get_chat_product_image_url_invalid_id_skips_s3():
    storage = MagicMock(bucket_name="bucket")
    assert product_images.get_chat_product_image_url(storage, "tenant-1", "../secret.png") is None
    storage.s3_client.head_object.assert_not_called()


def test_get_chat_product_image_url_unexpected_errors_raise():
    storage = MagicMock(bucket_name="bucket")
    storage.s3_client.head_object.side_effect = _s3_error("InternalError")
    with pytest.raises(ClientError):
        product_images.get_chat_product_image_url(storage, "tenant-1", IMG_1)

    with pytest.raises(RuntimeError):
        product_images.get_chat_product_image_url(MagicMock(bucket_name=""), "tenant-1", IMG_1)
