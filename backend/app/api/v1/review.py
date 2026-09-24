"""Review screen API -- see app/services/review_service.py for the model.

Every mutating call needs If-Match: <review version> (the `version`/`etag`
of the last GET) and returns the full updated review document, so the UI
always shows exactly what was saved. A stale version is a 409."""
import uuid
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...deps import get_tenant_db, require_role
from ...schemas.auth import TokenPayload
from ...services import review_service as rs

router = APIRouter(prefix="/documents/{document_id}/review", tags=["Review"])
# Workbench "Documents" tab: every document with something to review.
list_router = APIRouter(prefix="/review", tags=["Review"])

_read = require_role(*rs.READ_ROLES)
_edit = require_role(*rs.EDIT_ROLES)
_verify = require_role(*rs.VERIFY_ROLES)
_revert_all = require_role(*rs.REVERT_ALL_ROLES)


def _if_match(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    raw = value.strip().removeprefix("W/").strip('"')
    try:
        return int(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="If-Match must be the review version, e.g. \"3\"")


def _ctx(user: TokenPayload):
    return uuid.UUID(user.tenant_id), uuid.UUID(user.sub), user.role


def _respond(response: Response, doc: dict) -> JSONResponse:
    # The review document is already plain JSON types. Returning it straight
    # skips FastAPI's generic jsonable_encoder walk, which took longer than
    # building the document itself for a ~6,300-cell register (3.6 MB).
    return JSONResponse(content=doc, headers={"ETag": doc["etag"]})


class TextEdit(BaseModel):
    text: str = Field(max_length=100_000)


class CellEdit(BaseModel):
    value: str = Field(max_length=20_000)
    fact_version: Optional[int] = None


class RevertRequest(BaseModel):
    block_id: str
    row_id: Optional[str] = None
    col: Optional[int] = None


class AddRow(BaseModel):
    after_row_id: Optional[str] = None


class AddBlock(BaseModel):
    type: str
    text: str = Field(default="", max_length=100_000)
    after_block_id: Optional[str] = None
    page: Optional[int] = None


class VerifyRequest(BaseModel):
    block_id: str
    row_id: Optional[str] = None
    verified: bool = True
    fact_versions: Optional[Dict[str, int]] = None


@router.get("")
async def get_review(document_id: uuid.UUID, response: Response,
                     user: TokenPayload = Depends(_read), db: AsyncSession = Depends(get_tenant_db)):
    tenant_id, _, role = _ctx(user)
    return _respond(response, await rs.get_review_document(db, tenant_id, document_id, role))


@router.get("/pages/{page_number}/image")
async def get_page_image(document_id: uuid.UUID, page_number: int,
                         user: TokenPayload = Depends(_read), db: AsyncSession = Depends(get_tenant_db)):
    tenant_id, _, _ = _ctx(user)
    png = await rs.get_page_image(db, tenant_id, document_id, page_number)
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/export")
async def export_review(document_id: uuid.UUID, format: str = "xlsx",
                        user: TokenPayload = Depends(_read), db: AsyncSession = Depends(get_tenant_db)):
    """The corrected version, with each value's status (checked / corrected /
    added / machine-extracted). Anyone who can view the review can export it."""
    from ...services import review_export

    tenant_id, actor, role = _ctx(user)
    body, media_type, disposition = await review_export.export_document(db, tenant_id, document_id, actor, role, format)
    return Response(content=body, media_type=media_type, headers={"Content-Disposition": disposition})


@router.get("/history")
async def get_history(document_id: uuid.UUID, block_id: str, row_id: Optional[str] = None, col: Optional[int] = None,
                      user: TokenPayload = Depends(_read), db: AsyncSession = Depends(get_tenant_db)):
    tenant_id, _, _ = _ctx(user)
    return {"entries": await rs.get_history(db, tenant_id, document_id, block_id, row_id, col)}


@router.patch("/blocks/{block_id}")
async def edit_text_block(document_id: uuid.UUID, block_id: str, body: TextEdit, response: Response,
                          if_match: Optional[str] = Header(None),
                          user: TokenPayload = Depends(_edit), db: AsyncSession = Depends(get_tenant_db)):
    tenant_id, actor, role = _ctx(user)
    return _respond(response, await rs.edit_text_block(db, tenant_id, document_id, actor, role, _if_match(if_match), block_id, body.text))


@router.patch("/blocks/{block_id}/rows/{row_id}/cells/{col}")
async def edit_cell(document_id: uuid.UUID, block_id: str, row_id: str, col: int, body: CellEdit, response: Response,
                    if_match: Optional[str] = Header(None),
                    user: TokenPayload = Depends(_edit), db: AsyncSession = Depends(get_tenant_db)):
    tenant_id, actor, role = _ctx(user)
    return _respond(response, await rs.edit_cell(
        db, tenant_id, document_id, actor, role, _if_match(if_match), block_id, row_id, col, body.value, body.fact_version,
    ))


@router.post("/revert")
async def revert(document_id: uuid.UUID, body: RevertRequest, response: Response,
                 if_match: Optional[str] = Header(None),
                 user: TokenPayload = Depends(_edit), db: AsyncSession = Depends(get_tenant_db)):
    tenant_id, actor, role = _ctx(user)
    return _respond(response, await rs.revert(
        db, tenant_id, document_id, actor, role, _if_match(if_match), body.block_id, body.row_id, body.col,
    ))


@router.post("/blocks/{block_id}/rows")
async def add_row(document_id: uuid.UUID, block_id: str, body: AddRow, response: Response,
                  if_match: Optional[str] = Header(None),
                  user: TokenPayload = Depends(_edit), db: AsyncSession = Depends(get_tenant_db)):
    tenant_id, actor, role = _ctx(user)
    return _respond(response, await rs.add_row(db, tenant_id, document_id, actor, role, _if_match(if_match), block_id, body.after_row_id))


@router.delete("/blocks/{block_id}/rows/{row_id}")
async def delete_row(document_id: uuid.UUID, block_id: str, row_id: str, response: Response,
                     if_match: Optional[str] = Header(None),
                     user: TokenPayload = Depends(_edit), db: AsyncSession = Depends(get_tenant_db)):
    tenant_id, actor, role = _ctx(user)
    return _respond(response, await rs.delete_row(db, tenant_id, document_id, actor, role, _if_match(if_match), block_id, row_id))


@router.post("/blocks")
async def add_block(document_id: uuid.UUID, body: AddBlock, response: Response,
                    if_match: Optional[str] = Header(None),
                    user: TokenPayload = Depends(_edit), db: AsyncSession = Depends(get_tenant_db)):
    tenant_id, actor, role = _ctx(user)
    return _respond(response, await rs.add_block(
        db, tenant_id, document_id, actor, role, _if_match(if_match), body.type, body.text, body.after_block_id, body.page,
    ))


@router.delete("/blocks/{block_id}")
async def delete_block(document_id: uuid.UUID, block_id: str, response: Response,
                       if_match: Optional[str] = Header(None),
                       user: TokenPayload = Depends(_edit), db: AsyncSession = Depends(get_tenant_db)):
    tenant_id, actor, role = _ctx(user)
    return _respond(response, await rs.delete_block(db, tenant_id, document_id, actor, role, _if_match(if_match), block_id))


@router.post("/revert-all")
async def revert_all(document_id: uuid.UUID, response: Response,
                     if_match: Optional[str] = Header(None),
                     user: TokenPayload = Depends(_revert_all), db: AsyncSession = Depends(get_tenant_db)):
    tenant_id, actor, role = _ctx(user)
    return _respond(response, await rs.revert_all(db, tenant_id, document_id, actor, role, _if_match(if_match)))


@router.post("/verify")
async def verify(document_id: uuid.UUID, body: VerifyRequest, response: Response,
                 if_match: Optional[str] = Header(None),
                 user: TokenPayload = Depends(_verify), db: AsyncSession = Depends(get_tenant_db)):
    tenant_id, actor, role = _ctx(user)
    return _respond(response, await rs.set_verified(
        db, tenant_id, document_id, actor, role, _if_match(if_match),
        body.block_id, body.row_id, body.verified, body.fact_versions,
    ))


@list_router.get("/documents")
async def list_review_documents(q: Optional[str] = None, limit: int = 50, offset: int = 0,
                                user: TokenPayload = Depends(_read), db: AsyncSession = Depends(get_tenant_db)):
    tenant_id, _, _ = _ctx(user)
    return await rs.list_review_documents(db, tenant_id, q=q, limit=limit, offset=offset)
