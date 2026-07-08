"""Document ingestion routes."""

import io
import os
import shutil
import tempfile
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)

from app.core.logging_config import get_logger
from app.middleware.rate_limiter import limiter
from app.models.user import UserResponse
from app.services.ai.rag_service import rag_service
from app.services import audit_service
from app.models.audit import AuditLogCreate
from app.utils.dependencies import (
    get_current_user,
    validate_tenant_access,
    validate_tenant_write_access,
)
from app.core.config import get_settings

logger = get_logger(__name__)

router = APIRouter(prefix="/api/documents", tags=["Documents"])


def _validate_and_resolve_upload(file: UploadFile) -> tuple[str, str]:
    """Validate an uploaded file and return (safe_basename, doc_type).

    Guards against path traversal (CWE-22) by stripping any directory
    components from the client-supplied filename, and enforces an extension
    allowlist so arbitrary file types cannot be stored/ingested.
    """
    settings = get_settings()

    raw_name = file.filename or ""
    # Strip any path components the client may have injected (../, absolute, etc.)
    safe_name = os.path.basename(raw_name.replace("\\", "/")).strip()
    if not safe_name or safe_name in {".", ".."}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid filename")

    ext = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
    allowed = {e.strip().lower() for e in settings.ALLOWED_UPLOAD_EXTENSIONS.split(",") if e.strip()}
    if ext not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '.{ext}'. Allowed: {', '.join(sorted(allowed))}",
        )

    doc_type = ext if ext in {"docx", "pdf"} else "txt"
    return safe_name, doc_type


def _read_upload_with_limit(file: UploadFile) -> bytes:
    """Read an upload fully into memory, enforcing the configured size cap."""
    settings = get_settings()
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    file.file.seek(0)
    content = file.file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum size of {settings.MAX_UPLOAD_SIZE_MB} MB",
        )
    return content


@router.post("/ingest")
@limiter.limit(get_settings().RATE_LIMIT_INGEST)
async def ingest_document(
    request: Request,
    file: UploadFile = File(...),
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
    current_user: UserResponse = Depends(get_current_user),
):
    """Ingest a document (DOCX, PDF, or TXT) for a specific tenant."""
    try:
        # Validate tenant access
        await validate_tenant_access(x_tenant_id, current_user)

        # Validate filename (path-traversal safe) and file type
        safe_filename, doc_type = _validate_and_resolve_upload(file)

        settings = get_settings()

        # ASYNC INGESTION FLOW (Future Cloud Deployment)
        if settings.USE_ASYNC_INGESTION:
            from app.services.storage.storage_service import storage_service
            from app.services.storage.queue_service import queue_service
            from app.services import document_service
            import uuid

            file_uuid = uuid.uuid4()
            s3_key = f"uploads/{x_tenant_id}/{file_uuid}/{safe_filename}"

            # 1. Create Metadata (Processing)
            try:
                # Check if exists first to avoid double entries
                existing_doc = document_service.get_document_by_filename(x_tenant_id, safe_filename)
                if existing_doc:
                    document_service.update_document_status(existing_doc["document_id"], "processing")
                else:
                    document_service.create_document(
                        tenant_id=x_tenant_id,
                        filename=safe_filename,
                        document_type=doc_type,
                        source="upload",
                        s3_key=s3_key
                    )
            except Exception as e:
                logger.warning("Async metadata creation warning", exc_info=True)

            # 2. Upload to S3 (size-limited read into an in-memory buffer)
            file_content = _read_upload_with_limit(file)
            file_buffer = io.BytesIO(file_content)

            logger.info(
                "Uploading document to S3",
                extra={"extra_data": {"filename": safe_filename, "size_bytes": len(file_content)}},
            )

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
                "original_filename": safe_filename,
                "document_type": doc_type,
            }
            message_id = queue_service.send_message(payload)

            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=status.HTTP_202_ACCEPTED,
                content={
                    "status": "accepted",
                    "message": f"Document {safe_filename} queued for processing",
                    "job_id": message_id,
                },
            )

        # SYNC INGESTION FLOW (Local / Current)
        # Save to a server-controlled temp path (never the client-supplied name)
        file_content = _read_upload_with_limit(file)
        temp_dir = tempfile.mkdtemp(prefix="leadengine_ingest_")
        file_location = os.path.join(temp_dir, f"{uuid.uuid4()}_{safe_filename}")
        with open(file_location, "wb") as file_object:
            file_object.write(file_content)

        # Ingest and get document_id
        from app.services import document_service

        try:
            # 1. Create metadata record first (visible to user immediately)
            try:
                # Check if exists first to avoid double entries if retrying
                existing_doc = document_service.get_document_by_filename(x_tenant_id, safe_filename)
                if existing_doc:
                    # Update status to processing
                    doc_id = existing_doc["document_id"]
                    document_service.update_document_status(doc_id, "processing")
                else:
                    doc_id = document_service.create_document(
                        tenant_id=x_tenant_id,
                        filename=safe_filename,
                        document_type=doc_type,
                        source="upload"
                    )
            except Exception:
                # Fallback: proceed to ingestion anyway, it will create metadata if needed
                logger.warning("Metadata creation warning", exc_info=True)
                doc_id = None

            try:
                # 2. Perform Ingestion (CPU intensive)
                # This will update the document status to 'completed' upon success
                final_doc_id = rag_service.ingest_document(
                    file_location, x_tenant_id, source=safe_filename, document_type=doc_type
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
                raise
        finally:
            # Cleanup temp file/dir regardless of success
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                logger.warning("Temp cleanup failed", exc_info=True)

        try:
            audit_service.log_activity(
                AuditLogCreate(
                    action="DOCUMENT_INGESTED",
                    user_id=current_user.user_id,
                    user_name=current_user.full_name,
                    user_role=current_user.role,
                    tenant_id=x_tenant_id,
                    description=f"Ingested document {safe_filename}"
                )
            )
        except Exception:
            logger.warning("Audit log failed", exc_info=True)

        return {
            "status": "success",
            "message": f"Document {safe_filename} ingested for tenant {x_tenant_id}",
            "document_id": document_id,
            "filename": safe_filename,
            "tenant_id": x_tenant_id,
        }
    except HTTPException:
        raise
    except Exception:
        logger.error("Document ingestion failed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document ingestion failed. Please try again later.",
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
                    except Exception:
                        logger.warning("Failed to sync S3 file", exc_info=True)
        except Exception:
            logger.warning("S3 sync error", exc_info=True)

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
    tenant_id: str = Header(..., alias="X-Tenant-ID"),
    current_user: UserResponse = Depends(get_current_user),
):
    """Delete a document."""
    await validate_tenant_write_access(tenant_id, current_user)

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
        except Exception:
            # Log but don't fail - S3 file might already be deleted
            logger.warning("Failed to delete S3 file", exc_info=True)

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
    except Exception:
        logger.warning("Audit log failed", exc_info=True)

    return {"status": "success", "message": "Document deleted"}

