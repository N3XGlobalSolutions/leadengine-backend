"""
Document Metadata Service - DynamoDB Implementation

Manages document metadata in DynamoDB while vector embeddings stay in Qdrant.
Provides efficient access patterns for document listing, filename lookup, and tenant filtering.
"""

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from app.core.dynamodb import (
    get_dynamodb_table,
    put_item,
    get_item,
    update_item,
    query_items,
    delete_item
)
from app.core.logging_config import get_logger

# Import directly from module to avoid circular import
from app.services.analytics.analytics_service_dynamodb import analytics_service_dynamodb

logger = get_logger(__name__)


class DocumentServiceDynamoDB:
    """Manages document metadata using DynamoDB with optimized access patterns."""
    
    def create_document(
        self,
        tenant_id: str,
        filename: str,
        document_type: str,
        source: str = "upload",
        file_size: Optional[int] = None,
        s3_key: Optional[str] = None,
        extra_info: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Create a new document metadata entry.
        
        Args:
            tenant_id: Tenant identifier
            filename: Original filename
            document_type: Type of document (pdf, docx, txt, url)
            source: Source of document (upload, url, api)
            file_size: Size in bytes
            s3_key: S3 key if file stored in S3
            extra_info: Additional metadata
            
        Returns:
            document_id: Unique identifier for the document
        """
        document_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        
        # Check if filename already exists for this tenant
        existing = self.get_document_by_filename(tenant_id, filename)
        if existing:
            logger.warning(f"Document with filename {filename} already exists for tenant {tenant_id}")
            # Could either error or return existing ID
            # For now, we'll allow duplicates with different IDs
        
        item = {
            # Primary keys
            "PK": f"DOC#{document_id}",
            "SK": "METADATA",
            
            # GSI1: Tenant documents listing (sorted by upload time, newest first)
            "GSI1PK": f"TENANT#{tenant_id}#DOCS",
            "GSI1SK": f"DOC#{timestamp}#{document_id}",
            
            # GSI2: Filename lookup (for checking duplicates)
            "GSI2PK": f"TENANT#{tenant_id}#FILE",
            "GSI2SK": f"NAME#{filename}",
            
            # Document data
            "document_id": document_id,
            "tenant_id": tenant_id,
            "filename": filename,
            "source": source,
            "document_type": document_type,
            "status": "processing",  # processing, completed, failed
            "uploaded_at": timestamp,
            "chunk_count": 0,
            "qdrant_point_ids": [],  # Vector embedding IDs in Qdrant
            "error_message": None,
            "extra_info": extra_info or {},
        }
        
        # Add optional fields if provided
        if file_size is not None:
            item["file_size"] = file_size
        if s3_key:
            item["s3_key"] = s3_key
        
        put_item(item)
        logger.info(f"Created document metadata: {document_id} for tenant {tenant_id}")
        
        # Update analytics counters
        try:
            analytics_service_dynamodb.increment_counter(tenant_id, "total_documents")
        except Exception as e:
            logger.warning(f"Failed to update analytics: {e}")
        
        return document_id
    
    def get_document(self, document_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve document metadata by ID.
        
        Args:
            document_id: Document identifier
            
        Returns:
            Document metadata dict or None if not found
        """
        item = get_item(
            pk=f"DOC#{document_id}",
            sk="METADATA"
        )
        
        if not item:
            return None
        
        # Remove DynamoDB keys from response
        clean_item = {k: v for k, v in item.items() 
                     if not k.startswith("PK") and not k.startswith("SK") 
                     and not k.startswith("GSI")}
        
        return clean_item
    
    def update_document_status(
        self,
        document_id: str,
        status: str,
        chunk_count: Optional[int] = None,
        point_ids: Optional[List[str]] = None,
        error_message: Optional[str] = None
    ) -> bool:
        """
        Update document status after vector ingestion.
        
        Args:
            document_id: Document identifier
            status: New status (processing, completed, failed)
            chunk_count: Number of chunks created
            point_ids: List of Qdrant point IDs
            error_message: Error message if status is failed
            
        Returns:
            bool: True if successful
        """
        updates = {
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        if chunk_count is not None:
            updates["chunk_count"] = chunk_count
        
        if point_ids is not None:
            updates["qdrant_point_ids"] = point_ids
        
        if error_message:
            updates["error_message"] = error_message
        
        try:
            update_item(
                pk=f"DOC#{document_id}",
                sk="METADATA",
                updates=updates
            )
            logger.info(f"Updated document {document_id} status to {status}")
            return True
        except Exception as e:
            logger.error(f"Error updating document {document_id}: {e}")
            return False
    
    def list_tenant_documents(
        self,
        tenant_id: str,
        limit: int = 100,
        status_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        List all documents for a tenant, sorted by upload time (newest first).
        
        Args:
            tenant_id: Tenant identifier
            limit: Maximum number of documents to return
            status_filter: Optional filter by status
            
        Returns:
            List of document metadata dicts
        """
        items = query_items(
            pk=f"TENANT#{tenant_id}#DOCS",
            sk_prefix="DOC#",
            index_name="GSI1",
            limit=limit,
            scan_forward=False  # Newest first
        )
        
        # Remove DynamoDB keys and apply status filter
        documents = []
        for item in items:
            clean_item = {k: v for k, v in item.items() 
                         if not k.startswith("PK") and not k.startswith("SK") 
                         and not k.startswith("GSI")}
            
            # Filter out deleted documents
            status = clean_item.get("status")
            if status == "deleted":
                continue
            
            # Apply status filter if specified
            if status_filter is None or status == status_filter:
                documents.append(clean_item)
        
        return documents
    
    def get_document_by_filename(
        self,
        tenant_id: str,
        filename: str
    ) -> Optional[Dict[str, Any]]:
        """
        Find document by filename within a tenant.
        
        Args:
            tenant_id: Tenant identifier
            filename: Filename to search for
            
        Returns:
            Document metadata dict or None if not found
        """
        items = query_items(
            pk=f"TENANT#{tenant_id}#FILE",
            sk_prefix=f"NAME#{filename}",
            index_name="GSI2",
            limit=1
        )
        
        if not items:
            return None
        
        # Get the full document data
        doc_id = items[0].get("document_id")
        return self.get_document(doc_id)
    
    def delete_document(self, document_id: str) -> bool:
        """
        Delete document metadata (soft delete by setting status).
        
        Note: This does NOT delete vector embeddings from Qdrant.
        Use rag_service.delete_document() for complete cleanup.
        
        Args:
            document_id: Document identifier
            
        Returns:
            bool: True if successful
        """
        try:
            # Soft delete by updating status
            self.update_document_status(
                document_id=document_id,
                status="deleted"
            )
            logger.info(f"Soft deleted document {document_id}")
            return True
        except Exception as e:
            logger.error(f"Error deleting document {document_id}: {e}")
            return False
    
    def hard_delete_document(self, document_id: str) -> bool:
        """
        Permanently delete document metadata from DynamoDB.
        
        WARNING: This does NOT clean up Qdrant vectors or S3 files.
        
        Args:
            document_id: Document identifier
            
        Returns:
            bool: True if successful
        """
        try:
            delete_item(
                pk=f"DOC#{document_id}",
                sk="METADATA"
            )
            logger.info(f"Hard deleted document {document_id}")
            return True
        except Exception as e:
            logger.error(f"Error hard deleting document {document_id}: {e}")
            return False
    
    def count_tenant_documents(self, tenant_id: str) -> int:
        """
        Count total documents for a tenant.
        
        Args:
            tenant_id: Tenant identifier
            
        Returns:
            int: Document count
        """
        # Query with Select=COUNT for efficient counting
        items = query_items(
            pk=f"TENANT#{tenant_id}#DOCS",
            sk_prefix="DOC#",
            index_name="GSI1",
            limit=1000  # Will need pagination for larger counts
        )
        
        return len(items)


# Singleton instance
document_service_dynamodb = DocumentServiceDynamoDB()
