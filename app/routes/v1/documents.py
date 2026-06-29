"""Document ingestion routes."""

import os
import shutil

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    UploadFile,
    status,
    Response,
)

from app.models.user import UserResponse
from app.services.ai.rag_service import rag_service
from app.services import audit_service
from app.models.audit import AuditLogCreate
from app.utils.dependencies import get_current_user, validate_tenant_access
from app.core.config import get_settings

router = APIRouter(prefix="/api/documents", tags=["Documents"])


def add_cors_headers(response: Response, origin: str = None) -> None:
    """Add CORS headers to response for Lambda/API Gateway compatibility.
    
    When using Lambda proxy integration, CORS middleware headers may not
    propagate correctly. This helper ensures proper CORS headers are set.
    """
    settings = get_settings()
    
    # Determine allowed origin
    allowed_origin = "*"
    if origin and settings.CORS_ORIGINS:
        # Check if origin is in allowed list
        origins_list = [o.strip() for o in settings.CORS_ORIGINS]
        if origin in origins_list:
            allowed_origin = origin
    
    # Set CORS headers explicitly
    response.headers["Access-Control-Allow-Origin"] = allowed_origin
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Tenant-ID, Accept"
    response.headers["Access-Control-Max-Age"] = "3600"


# @router.options("/ingest")
# async def ingest_document_preflight(origin: str = Header(None)):
#     """Handle CORS preflight for document ingestion."""
#     response = Response(status_code=status.HTTP_204_NO_CONTENT)
#     add_cors_headers(response, origin)
#     return response


@router.post("/ingest")
async def ingest_document(
    response: Response,
    file: UploadFile = File(...),
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
    origin: str = Header(None),
    current_user: UserResponse = Depends(get_current_user),
):
    """Ingest a document (DOCX, PDF, or TXT) for a specific tenant."""
    try:
        # Add CORS headers immediately
        add_cors_headers(response, origin)
        
        # Validate tenant access
        await validate_tenant_access(x_tenant_id, current_user)

        # Determine document type
        filename = file.filename.lower()
        if filename.endswith(".docx"):
            doc_type = "docx"
        elif filename.endswith(".pdf"):
            doc_type = "pdf"
        else:
            doc_type = "txt"

        settings = get_settings()

        # ASYNC INGESTION FLOW (Future Cloud Deployment)
        if settings.USE_ASYNC_INGESTION:
            from app.services.storage.storage_service import storage_service
            from app.services.storage.queue_service import queue_service
            from app.services import document_service
            import uuid

            file_uuid = uuid.uuid4()
            s3_key = f"uploads/{x_tenant_id}/{file_uuid}/{file.filename}"

            # 1. Create Metadata (Processing)
            try:
                # Check if exists first to avoid double entries
                existing_doc = document_service.get_document_by_filename(x_tenant_id, file.filename)
                if existing_doc:
                    document_service.update_document_status(existing_doc["document_id"], "processing")
                else:
                    document_service.create_document(
                        tenant_id=x_tenant_id,
                        filename=file.filename,
                        document_type=doc_type,
                        source="upload",
                        s3_key=s3_key
                    )
            except Exception as e:
                print(f"Async metadata creation warning: {e}")

            # 2. Upload to S3
            # CRITICAL: Read file into BytesIO buffer before uploading
            # FastAPI's SpooledTemporaryFile doesn't work correctly with boto3's upload_fileobj
            import io
            
            file.file.seek(0)
            file_content = file.file.read()
            file_buffer = io.BytesIO(file_content)

            # Log file size for debugging
            print(f"Uploading file: {file.filename}, size: {len(file_content)} bytes")
            
            # Set correct content type based on file type
            content_type_map = {
                "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "pdf": "application/pdf",
                "txt": "text/plain"
            }
            content_type = content_type_map.get(doc_type, "application/octet-stream")
            
            storage_service.upload_file(file_buffer, s3_key, content_type=content_type)


            # 3. Send Event to SQS
            payload = {
                "tenant_id": x_tenant_id,
                "s3_key": s3_key,
                "original_filename": file.filename,
                "document_type": doc_type,
            }
            message_id = queue_service.send_message(payload)

            response.status_code = status.HTTP_202_ACCEPTED
            return {
                "status": "accepted",
                "message": f"Document {file.filename} queued for processing",
                "job_id": message_id,
            }

        # SYNC INGESTION FLOW (Local / Current)
        # Save temp file
        file_location = f"temp_{file.filename}"
        with open(file_location, "wb+") as file_object:
            shutil.copyfileobj(file.file, file_object)

        # Ingest and get document_id
        from app.services import document_service
        
        # 1. Create metadata record first (visible to user immediately)
        try:
            # Check if exists first to avoid double entries if retrying
            # (Though create_document allows duplicates, clearer UI if we check)
            existing_doc = document_service.get_document_by_filename(x_tenant_id, file.filename)
            if existing_doc:
                # Update status to processing
                doc_id = existing_doc["document_id"]
                document_service.update_document_status(doc_id, "processing")
            else:
                doc_id = document_service.create_document(
                    tenant_id=x_tenant_id,
                    filename=file.filename,
                    document_type=doc_type,
                    source="upload"
                )
        except Exception as e:
            # Fallback: proceed to ingestion anyway, it will create metadata if needed
            print(f"Metadata creation warning: {e}")
            doc_id = None

        try:
            # 2. Perform Ingestion (CPU intensive)
            # This will update the document status to 'completed' upon success
            final_doc_id = rag_service.ingest_document(
                file_location, x_tenant_id, source=file.filename, document_type=doc_type
            )
            
            # Use the ID returned by RAG service as source of truth
            document_id = final_doc_id
            
        except Exception as e:
            # 3. Handle Failure
            if doc_id:
                document_service.update_document_status(
                    doc_id, 
                    "failed", 
                    error_message=str(e)
                )
            raise e

        # Cleanup
        os.remove(file_location)
        
        try:
            audit_service.log_activity(
                AuditLogCreate(
                    action="DOCUMENT_INGESTED",
                    user_id=current_user.user_id,
                    user_name=current_user.full_name,
                    user_role=current_user.role,
                    tenant_id=x_tenant_id,
                    description=f"Ingested document {file.filename}"
                )
            )
        except Exception as e:
            print(f"Audit log failed: {e}")

        return {
            "status": "success",
            "message": f"Document {file.filename} ingested for tenant {x_tenant_id}",
            "document_id": document_id,
            "filename": file.filename,
            "tenant_id": x_tenant_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@router.get("/tenants/{tenant_id}")
async def list_documents(
    tenant_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    """List all documents for a tenant."""
    await validate_tenant_access(tenant_id, current_user)

    from app.services import document_service
    from app.core.config import get_settings
    
    settings = get_settings()

    # SYNC S3 FILES (Recover missing metadata)
    if settings.USE_ASYNC_INGESTION:
        from app.services.storage.storage_service import storage_service
        import os

        try:
            # Get existing docs first (to check against)
            existing_docs = document_service.list_tenant_documents(tenant_id)
            existing_filenames = {d.get("filename") for d in existing_docs if d.get("filename")}

            # List S3 files
            s3_prefix = f"uploads/{tenant_id}/"
            s3_keys = storage_service.list_files(s3_prefix)

            # Find missing
            for key in s3_keys:
                # key format: uploads/tenant_id/uuid/filename
                # We want the actual filename
                filename = os.path.basename(key)
                
                # Basic validation to ensure it's a file
                if filename and filename not in existing_filenames and not key.endswith('/'):
                    try:
                        # Determine type
                        ext = filename.split('.')[-1].lower() if '.' in filename else 'txt'
                        
                        # Create missing metadata
                        doc_id = document_service.create_document(
                            tenant_id=tenant_id,
                            filename=filename,
                            document_type=ext,
                            source="s3_sync", 
                            s3_key=key
                        )
                        # Mark as 'synced' so we know it came from S3 recovery
                        document_service.update_document_status(doc_id, "synced")
                        
                        existing_filenames.add(filename) 
                    except Exception as e:
                        print(f"Failed to sync S3 file {key}: {e}")
        except Exception as e:
            print(f"S3 Sync error: {e}")

    # Fetch final list
    documents = document_service.list_tenant_documents(tenant_id)
    return documents


@router.get("/{document_id}")
async def get_document(
    document_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    """Get document details by ID."""
    from app.services import document_service

    doc = document_service.get_document(document_id)
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )
    
    # Validate user has access to this tenant
    await validate_tenant_access(doc["tenant_id"], current_user)
    
    return doc


# @router.options("/{document_id}")
# async def delete_document_preflight(document_id: str, origin: str = Header(None)):
#     """Handle CORS preflight for document deletion."""
#     response = Response(status_code=status.HTTP_204_NO_CONTENT)
#     add_cors_headers(response, origin)
#     return response


@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    response: Response,
    tenant_id: str = Header(..., alias="X-Tenant-ID"),
    origin: str = Header(None),
    current_user: UserResponse = Depends(get_current_user),
):
    """Delete a document."""
    # Add CORS headers immediately
    add_cors_headers(response, origin)
    
    await validate_tenant_access(tenant_id, current_user)

    # Platform operators are not allowed to delete documents
    from app.models.user import UserRole
    if current_user.role == UserRole.PLATFORM_OPERATOR:
        from fastapi import status as http_status
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Platform Operators are not permitted to delete resources"
        )

    from app.services import document_service
    from app.services.ai.rag_service import rag_service
    from app.core.config import get_settings

    settings = get_settings()

    # Get document to verify it exists and belongs to tenant
    doc = document_service.get_document(document_id)
    if not doc or doc.get("tenant_id") != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )

    # Delete from Qdrant first (requires source)
    if doc.get("filename"):
        rag_service.delete_document(tenant_id, doc["filename"])

    # Delete from S3 if async ingestion is enabled and s3_key exists
    if settings.USE_ASYNC_INGESTION and doc.get("s3_key"):
        from app.services.storage.storage_service import storage_service
        try:
            storage_service.delete_file(doc["s3_key"])
        except Exception as e:
            # Log but don't fail - S3 file might already be deleted
            print(f"Warning: Failed to delete S3 file {doc['s3_key']}: {e}")

    # Delete metadata (hard delete to prevent S3 sync from recreating)
    success = document_service.hard_delete_document(document_id)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Failed to delete document"
        )
        
    try:
        audit_service.log_activity(
            AuditLogCreate(
                action="DOCUMENT_DELETED",
                user_id=current_user.user_id,
                user_name=current_user.full_name,
                user_role=current_user.role,
                tenant_id=tenant_id,
                description=f"Deleted document {doc.get('filename', document_id)}"
            )
        )
    except Exception as e:
        print(f"Audit log failed: {e}")

    return {"status": "success", "message": "Document deleted"}

