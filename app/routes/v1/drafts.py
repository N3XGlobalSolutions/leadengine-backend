"""Draft autosave routes for client creation and client settings editing.

Endpoints are scoped to the authenticated user — draft ownership is enforced
by extracting user_id from the JWT token. Users can never access another
user's drafts.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.models.draft import DraftCreate, DraftResponse, DraftUpdate
from app.models.user import UserResponse
from app.services import draft_service
from app.utils.dependencies import get_current_user

router = APIRouter(prefix="/api/admin/client-drafts", tags=["Client Drafts"])


@router.post(
    "",
    response_model=DraftResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_draft(
    body: DraftCreate,
    current_user: UserResponse = Depends(get_current_user),
):
    """Create a new draft for the current user.

    If a draft of the same type already exists, it is overwritten.
    Each user can have at most one draft per draft_type.
    """
    try:
        return draft_service.create_draft(
            user_id=current_user.user_id,
            draft_type=body.draft_type,
            draft_data=body.draft_data,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create draft: {str(e)}",
        )


@router.patch(
    "",
    response_model=DraftResponse,
)
async def update_draft(
    body: DraftUpdate,
    draft_type: str = Query("client_creation", description="Type of draft to update"),
    current_user: UserResponse = Depends(get_current_user),
):
    """Update an existing draft for the current user.

    Refreshes updated_at and extends expires_at by 7 days.
    Returns 404 if no draft exists or draft has expired.
    """
    result = draft_service.update_draft(
        user_id=current_user.user_id,
        draft_type=draft_type,
        draft_data=body.draft_data,
    )

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No draft found",
        )

    return result


@router.get(
    "/latest",
    response_model=DraftResponse,
)
async def get_latest_draft(
    draft_type: str = Query("client_creation", description="Type of draft to retrieve"),
    current_user: UserResponse = Depends(get_current_user),
):
    """Get the current user's latest draft.

    Returns 404 if no draft exists or the draft has expired.
    Expired drafts are treated as non-existent (application-level check)
    even if DynamoDB TTL hasn't cleaned them up yet.
    """
    result = draft_service.get_latest_draft(
        user_id=current_user.user_id,
        draft_type=draft_type,
    )

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No draft found",
        )

    return result


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_draft(
    draft_type: str = Query("client_creation", description="Type of draft to delete"),
    current_user: UserResponse = Depends(get_current_user),
):
    """Delete the current user's draft.

    Used after successful client creation or when the user explicitly
    discards their draft. Always returns 204 even if no draft existed.
    """
    draft_service.delete_draft(
        user_id=current_user.user_id,
        draft_type=draft_type,
    )
