"""
RBAC (Role-Based Access Control) routes the single-user app still reads.

Endpoints:
- /workspaces/{workspace_id}/members - list a workspace's members (chat page,
  comment mentions, automation permission gates)
- /workspaces/{workspace_id}/my-access - the caller's own role and permissions
- /permissions - list every permission value

Roles, membership changes and workspace invite links are gone: Monoshiri is
single-user, so there is nobody to invite, promote or remove.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.auth.context import AuthContext
from app.db import Permission, User, Workspace, WorkspaceMembership, get_async_session
from app.schemas import (
    MembershipRead,
    PermissionInfo,
    PermissionsListResponse,
    UserWorkspaceAccess,
)
from app.users import get_auth_context
from app.utils.rbac import (
    check_permission,
    check_workspace_access,
    get_user_permissions,
)

router = APIRouter()


# ============ Permissions Endpoints ============

# Human-readable descriptions for each permission
PERMISSION_DESCRIPTIONS = {
    # Documents
    "documents:create": "Add new documents, files, and content to the workspace",
    "documents:read": "View and search documents in the workspace",
    "documents:update": "Edit existing documents and their metadata",
    "documents:delete": "Remove documents from the workspace",
    # Artifacts
    "artifacts:create": "Create generated artifacts in the workspace",
    "artifacts:read": "View and download artifacts in the workspace",
    "artifacts:update": "Revise existing artifacts in the workspace",
    "artifacts:delete": "Remove artifacts from the workspace",
    # Chats
    "chats:create": "Start new AI chat conversations",
    "chats:read": "View chat history and conversations",
    "chats:update": "Edit chat titles and settings",
    "chats:delete": "Delete chat conversations",
    # Comments
    "comments:create": "Add comments and annotations to documents",
    "comments:read": "View comments on documents",
    "comments:delete": "Remove comments from documents",
    # LLM Configs
    "llm_configs:create": "Add new AI model configurations",
    "llm_configs:read": "View AI model settings and configurations",
    "llm_configs:update": "Modify AI model configurations",
    "llm_configs:delete": "Remove AI model configurations",
    # Podcasts
    "podcasts:create": "Generate new AI podcasts from content",
    "podcasts:read": "Listen to and view generated podcasts",
    "podcasts:update": "Edit podcast settings and metadata",
    "podcasts:delete": "Remove generated podcasts",
    # Connectors
    "connectors:create": "Set up new data source integrations",
    "connectors:read": "View configured data sources and their status",
    "connectors:update": "Modify data source configurations",
    "connectors:delete": "Remove data source integrations",
    # Logs
    "logs:read": "View activity logs and audit trail",
    "logs:delete": "Clear activity logs",
    # Members
    "members:invite": "Send invitations to new team members",
    "members:view": "View the list of team members",
    "members:remove": "Remove members from the workspace",
    "members:manage_roles": "Assign and change member roles",
    # Roles
    "roles:create": "Create new custom roles",
    "roles:read": "View available roles and their permissions",
    "roles:update": "Modify role permissions",
    "roles:delete": "Remove custom roles",
    # Settings
    "settings:view": "View workspace settings",
    "settings:update": "Modify workspace settings",
    "settings:delete": "Delete the entire workspace",
    # API access
    "api_access:manage": "Enable or disable programmatic API access for a workspace",
    # Automations
    "automations:create": "Create automations from chat or JSON",
    "automations:read": "View automations, their triggers, and run history",
    "automations:update": "Edit automations and manage their triggers",
    "automations:delete": "Remove automations from the workspace",
    "automations:execute": "Manually fire automations",
    # Full access
    "*": "Full access to all features and settings",
}


@router.get("/permissions", response_model=PermissionsListResponse)
async def list_all_permissions(
    auth: AuthContext = Depends(get_auth_context),
):
    """
    List every permission value the app checks.
    """
    permissions = []
    for perm in Permission:
        # Extract category from permission value (e.g., "documents:read" -> "documents")
        category = perm.value.split(":")[0] if ":" in perm.value else "general"
        description = PERMISSION_DESCRIPTIONS.get(
            perm.value, f"Permission for {perm.value}"
        )

        permissions.append(
            PermissionInfo(
                value=perm.value,
                name=perm.name,
                category=category,
                description=description,
            )
        )

    return PermissionsListResponse(permissions=permissions)


# ============ Membership Endpoints ============


@router.get(
    "/workspaces/{workspace_id}/members",
    response_model=list[MembershipRead],
)
async def list_members(
    workspace_id: int,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(get_auth_context),
):
    """
    List all members of a workspace.
    Requires MEMBERS_VIEW permission.
    """
    try:
        await check_permission(
            session,
            auth,
            workspace_id,
            Permission.MEMBERS_VIEW.value,
            "You don't have permission to view members",
        )

        result = await session.execute(
            select(WorkspaceMembership)
            .options(selectinload(WorkspaceMembership.role))
            .filter(WorkspaceMembership.workspace_id == workspace_id)
        )
        memberships = result.scalars().all()

        # Fetch user emails for each membership
        response = []
        for membership in memberships:
            user_result = await session.execute(
                select(User).filter(User.id == membership.user_id)
            )
            member_user = user_result.scalars().first()

            membership_dict = {
                "id": membership.id,
                "user_id": membership.user_id,
                "workspace_id": membership.workspace_id,
                "role_id": membership.role_id,
                "is_owner": membership.is_owner,
                "joined_at": membership.joined_at,
                "created_at": membership.created_at,
                "role": membership.role,
                "user_email": member_user.email if member_user else None,
                "user_display_name": member_user.display_name if member_user else None,
                "user_avatar_url": member_user.avatar_url if member_user else None,
                "user_last_login": member_user.last_login if member_user else None,
            }
            response.append(membership_dict)

        return response

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch members: {e!s}"
        ) from e


# ============ User Access Info ============


@router.get(
    "/workspaces/{workspace_id}/my-access",
    response_model=UserWorkspaceAccess,
)
async def get_my_access(
    workspace_id: int,
    session: AsyncSession = Depends(get_async_session),
    auth: AuthContext = Depends(get_auth_context),
):
    user = auth.user
    """
    Get the current user's access info for a workspace.
    """
    try:
        membership = await check_workspace_access(session, auth, workspace_id)

        # Get workspace name
        result = await session.execute(
            select(Workspace).filter(Workspace.id == workspace_id)
        )
        workspace = result.scalars().first()

        # Get permissions
        permissions = await get_user_permissions(session, user.id, workspace_id)

        return UserWorkspaceAccess(
            workspace_id=workspace_id,
            workspace_name=workspace.name if workspace else "",
            is_owner=membership.is_owner,
            role_name=membership.role.name if membership.role else None,
            permissions=permissions,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to get access info: {e!s}"
        ) from e
