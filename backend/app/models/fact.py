from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy import ForeignKey, Float, Text, Boolean, CheckConstraint, Integer
from datetime import datetime
import uuid
from typing import TYPE_CHECKING, Any, List, Optional

from app.database import Base

if TYPE_CHECKING:
    from app.models.fact_region import FactRegion


class Fact(Base):
    """One value pulled out of a document — a name, an area, a date (Section 0).

    Every fact must resolve to at least one region on a page. That's enforced
    at commit time by the doc_dg_facts_require_region trigger (migration
    0010), not just hidden in the UI — a fact nobody can point at on the page
    is a fact nobody can check.
    """

    __tablename__ = "doc_dg_facts"
    __table_args__ = (
        CheckConstraint("status IN ('machine', 'in_review', 'verified')", name="ck_doc_dg_facts_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam_dg_tenants.id"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doc_dg_documents.id", ondelete="CASCADE"), index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doc_dg_document_versions.id", ondelete="CASCADE"), index=True)
    field_name: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # T-rowgroup — every field written from the same logical table row (one
    # merged_row in vlm_extraction.py's main loop, or one result.pairs
    # entry in _extract_spread_facts) shares this id. Real bug found live
    # 2026-09-07: with no row identity ever stored, fact_service.py's
    # table-view endpoint had to *guess* which facts belonged together
    # from FactRegion.y0 alone — broke badly on any page laid out as
    # side-by-side entry-columns (Wardha.pdf page 2: one entry's own
    # fields span nearly the full page height, while unrelated entries'
    # same-named fields share a y-band), silently overwriting 7 of every
    # 8 real entries' values. NULL for facts that were never part of a
    # tabular row (page_header fields, _marginalia, _join_mismatch) and
    # for anything extracted before this column existed — those still
    # fall back to the old y0-heuristic in get_table_view_for_document.
    row_group_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    # T20/D-5 — 'machine' (auto-committed, confidence cleared its band,
    # never promoted further — permanent, same as tier1/2 entity edges) or
    # 'in_review' (needs a human to reach 'verified', same as tier3/4
    # 'held' edges). Only 'in_review' facts can be confirmed (T51).
    status: Mapped[str] = mapped_column(Text, nullable=False, default="machine")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    # Review screen (migration 0055) -- bumped by bump_edit_version() on
    # every change to value or review status, never on claim/release. The
    # Workbench and the review screen both send the version they loaded;
    # a mismatch is a 409 instead of a silent overwrite.
    edit_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    # T30/T55 — nothing sets this True yet (the handwritten/degraded
    # capture policy, T30, isn't built), but bulk_confirm_facts (T54)
    # already refuses to auto-promote a handwritten fact once something
    # does — only single-fact confirm_fact() can verify one.
    is_handwritten: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # T52 — claim/release so two operators don't work the same queue item
    # at once. Cleared by release_fact(); confirm_fact() does not require
    # a claim first (a claim is a courtesy lock, not a hard gate).
    claimed_by_actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("iam_dg_users.id"), nullable=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    # T51/T54 — who/what promoted this fact to 'verified', and how. Single
    # confirm_fact() calls leave the threshold/corpus/policy/batch fields
    # NULL; only bulk_confirm_facts() sets them, mirroring T57's edges.
    verified_by_actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("iam_dg_users.id"), nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    verified_threshold: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    verified_corpus_folder_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("doc_dg_folders.id", ondelete="SET NULL"), nullable=True)
    verified_via_policy_version: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    verified_batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)

    regions: Mapped[List["FactRegion"]] = relationship(
        "FactRegion", back_populates="fact", cascade="all, delete-orphan"
    )


def bump_edit_version(fact: "Fact") -> None:
    fact.edit_version = (fact.edit_version or 1) + 1


def check_edit_version(fact: "Fact", expected: Optional[int]) -> None:
    """409 when a caller's view of this fact is stale. `expected=None`
    skips the check (older API clients that do not send a version)."""
    if expected is not None and expected != fact.edit_version:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stale_fact",
                "message": "This value was changed by someone else since you loaded it. Reload to see the latest.",
                "fact_id": str(fact.id),
                "expected_version": expected,
                "current_version": fact.edit_version,
            },
        )
