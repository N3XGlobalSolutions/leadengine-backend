"""Product catalog management and search service for outbound sales."""

import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional
from app.models.outbound.outbound_entities import OutboundProduct
from app.services.outbound.outbound_db_service import outbound_db_service
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class OutboundProductService:
    """Outbound Product CRUD and search service."""

    def create_product(self, tenant_id: str, data: Dict[str, Any]) -> OutboundProduct:
        """Create a new product record."""
        product_id = str(uuid.uuid4())
        
        # Parse Decimal price safely
        price_val = data.get("price", 0.0)
        if isinstance(price_val, (int, float)):
            price = Decimal(str(price_val))
        elif isinstance(price_val, str):
            price = Decimal(price_val)
        else:
            price = price_val

        product = OutboundProduct(
            product_id=product_id,
            tenant_id=tenant_id,
            name=data["name"],
            description=data.get("description", ""),
            short_description=data.get("short_description", ""),
            price=price,
            currency=data.get("currency", "USD"),
            images=data.get("images", []),
            gallery=data.get("gallery", []),
            videos=data.get("videos", []),
            features=data.get("features", []),
            benefits=data.get("benefits", []),
            specifications=data.get("specifications", {}),
            category=data["category"],
            tags=data.get("tags", []),
            brochure_pdf=data.get("brochure_pdf"),
            status=data.get("status", "ACTIVE"),
            search_keywords=data.get("search_keywords", []),
        )
        return outbound_db_service.save_product(product)

    def get_product(self, product_id: str) -> Optional[OutboundProduct]:
        """Fetch a specific product by ID."""
        return outbound_db_service.get_product(product_id)

    def update_product(self, product_id: str, data: Dict[str, Any]) -> Optional[OutboundProduct]:
        """Update properties of an existing product."""
        product = self.get_product(product_id)
        if not product:
            return None

        # Build dict from model, update with data, write back
        prod_dict = product.model_dump()
        for key, val in data.items():
            if key in prod_dict:
                if key == "price" and val is not None:
                    prod_dict[key] = Decimal(str(val))
                else:
                    prod_dict[key] = val

        updated_product = OutboundProduct(**prod_dict)
        return outbound_db_service.save_product(updated_product)

    def delete_product(self, product_id: str) -> bool:
        """Remove a product by ID."""
        return outbound_db_service.delete_product(product_id)

    def list_products(self, tenant_id: str) -> List[OutboundProduct]:
        """List all products for tenant."""
        return outbound_db_service.list_tenant_products(tenant_id)

    def search_products(
        self,
        tenant_id: str,
        query: Optional[str] = None,
        category: Optional[str] = None,
        max_price: Optional[float] = None,
        min_price: Optional[float] = None,
    ) -> List[OutboundProduct]:
        """Search products with keyword, category, and price filters.

        Args:
            tenant_id: Tenant context limit
            query: Keyword matched against name, tags, description, keywords
            category: Direct category match filter
            max_price: Maximum price ceiling filter
            min_price: Minimum price floor filter
        """
        all_products = outbound_db_service.list_tenant_products(tenant_id)
        filtered = []

        for p in all_products:
            # Active filter
            if p.status != "ACTIVE":
                continue

            # Category filter
            if category and p.category.lower() != category.lower():
                continue

            # Price limits
            if max_price is not None and p.price > Decimal(str(max_price)):
                continue
            if min_price is not None and p.price < Decimal(str(min_price)):
                continue

            # Text query search matches name, short description, tags, keywords
            if query:
                q = query.lower()
                matches_text = (
                    q in p.name.lower() or
                    q in p.description.lower() or
                    q in p.short_description.lower() or
                    any(q in tag.lower() for tag in p.tags) or
                    any(q in kw.lower() for kw in p.search_keywords) or
                    q in p.category.lower()
                )
                if not matches_text:
                    continue

            filtered.append(p)

        logger.info(f"[OutboundProduct] Product query found {len(filtered)} matches.")
        return filtered


# Singleton product service
outbound_product_service = OutboundProductService()
