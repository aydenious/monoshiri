"""
Pydantic schemas for the read-only RBAC endpoints.

Roles are still modelled (every membership carries one and the live database keeps
the table), but nobody creates, edits or deletes them any more.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

# ============ Role Schemas ============


class RoleBase(BaseModel):
    """Base schema for roles."""

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    permissions: list[str] = Field(default_factory=list)
    is_default: bool = False


class RoleRead(RoleBase):
    """Schema for reading a role."""

    id: int
    workspace_id: int
    is_system_role: bool
    created_at: datetime

    class Config:
        from_attributes = True


# ============ Membership Schemas ============


class MembershipRead(BaseModel):
    """Schema for reading a membership."""

    id: int
    user_id: UUID
    workspace_id: int
    role_id: int | None
    is_owner: bool
    joined_at: datetime
    created_at: datetime
    # Nested role info
    role: RoleRead | None = None
    # User details (populated separately)
    user_email: str | None = None
    user_display_name: str | None = None
    user_avatar_url: str | None = None
    user_last_login: datetime | None = None

    class Config:
        from_attributes = True


# ============ Permission Schemas ============


class PermissionInfo(BaseModel):
    """Schema for permission information."""

    value: str
    name: str
    category: str
    description: str


class PermissionsListResponse(BaseModel):
    """Response schema for listing all available permissions."""

    permissions: list[PermissionInfo]


# ============ User Access Info ============


class UserWorkspaceAccess(BaseModel):
    """Schema for user's access info in a workspace."""

    workspace_id: int
    workspace_name: str
    is_owner: bool
    role_name: str | None
    permissions: list[str]
