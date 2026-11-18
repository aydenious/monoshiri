"""Read one recorded capability run.

Chat's kept capabilities (web crawl, YouTube, Google search) record each call to the
``runs`` table and cite it as ``run_<uuid>``; the web UI opens that citation through this
one ``GET``. The REST verb doors, run list, live events and cancel routes of the removed
API Playground are gone.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import AuthContext
from app.db import Run, get_async_session
from app.users import get_auth_context
from app.utils.rbac import check_workspace_access


class RunSummary(BaseModel):
    """Metadata of a run (output body + progress log omitted)."""

    id: str
    capability: str
    origin: str
    status: str
    item_count: int
    char_count: int
    duration_ms: int | None
    cost_micros: int | None
    error: str | None
    created_at: datetime


class RunDetail(RunSummary):
    """Full run including input, stored output, and the coarse progress log."""

    thread_id: str | None
    input: dict | None
    output_text: str | None
    progress: list[dict] | None


def _parse_run_uuid(run_id: str) -> uuid.UUID:
    raw = run_id[len("run_") :] if run_id.startswith("run_") else run_id
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Run not found."
        ) from exc


def _to_summary(row: Run) -> RunSummary:
    return RunSummary(
        id=f"run_{row.id}",
        capability=row.capability,
        origin=row.origin,
        status=row.status,
        item_count=row.item_count,
        char_count=row.char_count,
        duration_ms=row.duration_ms,
        cost_micros=row.cost_micros,
        error=row.error,
        created_at=row.created_at,
    )


def _to_detail(row: Run) -> RunDetail:
    return RunDetail(
        **_to_summary(row).model_dump(),
        thread_id=row.thread_id,
        input=row.input,
        output_text=row.output_text,
        progress=row.progress,
    )


def build_run_detail_router() -> APIRouter:
    router = APIRouter(tags=["scrapers"])

    async def get_run(
        workspace_id: int,
        run_id: str,
        session: AsyncSession = Depends(get_async_session),
        auth: AuthContext = Depends(get_auth_context),
    ) -> RunDetail:
        await check_workspace_access(session, auth, workspace_id)
        parsed_id = _parse_run_uuid(run_id)
        row = (
            await session.execute(
                select(Run).where(
                    Run.id == parsed_id, Run.workspace_id == workspace_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Run not found."
            )
        return _to_detail(row)

    router.add_api_route(
        "/workspaces/{workspace_id}/scrapers/runs/{run_id}",
        get_run,
        methods=["GET"],
        response_model=RunDetail,
        name="scraper:get_run",
    )
    return router
