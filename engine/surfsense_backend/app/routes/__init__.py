from fastapi import APIRouter

# Imported for their registration side effects: the kept capability verbs chat calls
# (web crawl, YouTube, Google search).
import app.capabilities.google_search
import app.capabilities.web
import app.capabilities.youtube  # noqa: F401
from app.artifacts.access.authenticated import build_authenticated_artifact_router
from app.automations.api import router as automations_router
from app.capabilities.core.access.run_detail import build_run_detail_router
from app.file_storage.api import router as file_storage_router
from app.knowledge_store.remote.api import router as git_remotes_router
from app.monoshiri.admin_routes import router as admin_router
from app.notifications.api import router as notifications_router
from app.podcasts.api import router as podcasts_router

from .agent_action_log_route import router as agent_action_log_router
from .agent_flags_route import router as agent_flags_router
from .agent_permissions_route import router as agent_permissions_router
from .agent_revert_route import router as agent_revert_router
from .artifacts_routes import router as artifacts_router
from .chat_comments_routes import router as chat_comments_router
from .deliverable_jobs_routes import router as deliverable_jobs_router
from .document_files_routes import router as document_files_router
from .documents_routes import router as documents_router
from .editor_routes import router as editor_router
from .export_routes import router as export_router
from .folders_routes import router as folders_router
from .logs_routes import router as logs_router
from .memory_routes import router as memory_router
from .model_connections_routes import router as model_connections_router
from .model_list_routes import router as model_list_router
from .new_chat_routes import router as new_chat_router
from .notes_routes import router as notes_router
from .personal_access_tokens_routes import router as personal_access_tokens_router
from .prompts_routes import router as prompts_router
from .rbac_routes import router as rbac_router
from .sandbox_routes import router as sandbox_router
from .team_memory_routes import router as team_memory_router
from .url_documents_routes import router as url_documents_router
from .workspaces_routes import router as workspaces_router

router = APIRouter()

router.include_router(workspaces_router)
router.include_router(git_remotes_router)
router.include_router(rbac_router)  # RBAC routes for roles, members, invites
router.include_router(editor_router)
router.include_router(export_router)
router.include_router(documents_router)
router.include_router(url_documents_router)  # POST /documents/url (save a URL)
router.include_router(document_files_router)
router.include_router(artifacts_router)
router.include_router(deliverable_jobs_router)
router.include_router(folders_router)
router.include_router(notes_router)
router.include_router(new_chat_router)  # Chat with assistant-ui persistence
router.include_router(agent_revert_router)  # POST /threads/{id}/revert/{action_id}
router.include_router(agent_action_log_router)  # GET /threads/{id}/actions
router.include_router(
    agent_permissions_router
)  # CRUD for /workspaces/{id}/agent/permissions/rules
router.include_router(agent_flags_router)  # GET /agent/flags
router.include_router(sandbox_router)  # Sandbox file downloads (Daytona)
router.include_router(chat_comments_router)
router.include_router(podcasts_router)  # Podcast task status and audio
router.include_router(personal_access_tokens_router)  # Personal access token manager
router.include_router(model_connections_router)  # Connection-centric model catalog
router.include_router(model_list_router)  # Dynamic model catalogue from OpenRouter
router.include_router(logs_router)
router.include_router(notifications_router)  # Notifications with Zero sync
router.include_router(admin_router)  # Monoshiri: admin user management (superuser)
router.include_router(prompts_router)
router.include_router(memory_router)  # User personal memory (memory.md style)
router.include_router(team_memory_router)  # Workspace team memory
router.include_router(automations_router)  # Automations CRUD + run history
router.include_router(file_storage_router)  # Original file metadata + download
router.include_router(build_run_detail_router())  # GET one run (chat run citations)
router.include_router(
    build_authenticated_artifact_router()
)  # Authenticated artifact generation (dev API)
