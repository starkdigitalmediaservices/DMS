# Decision D-5 — Confidence calibration policy: per-template or global bands

Status: **signed — 2026-08-24**
Blocks (would be unblocked): T20 (real per-field confidence), T51/T52/T54/T55 (Human Verification Workbench), T30 (handwritten/degraded policy), T76 (completeness dashboard's confidence distribution)
Owner: BA + Tech Lead
Reference: docs/planning/backlog.txt T20, T51; Build Design Section 12

---

## Why this has to be agreed before code

T22 (VLM extraction, just shipped) now writes a real confidence score per
fact — sourced from the vision model's own reported confidence for that
field, not a hardcoded constant. That number is meaningless on its own: a
consumer needs to know **what confidence counts as "trust it automatically"
versus "a person must look at this before it's usable."** That line is what
D-5 sets. Until it's drawn, T51's two-lane state machine (`machine →
in-review → human-verified`) has no threshold to route on, and T30 (which
depends on T20) can't decide when a handwritten/degraded field needs
mandatory human capture versus can stand as machine-read.

## What is being decided

Two options were on the table:

1. **One global band set** — e.g. `>=0.9 auto-commit`, `0.6–0.9 review
   queue`, `<0.6 low-confidence flag` — applied identically to every
   template and era.
2. **Per-template/per-corpus bands** — each template (or each corpus
   calibration, per T59) can carry its own thresholds, because a crisp
   1990s typed form and a water-damaged 1920s handwritten register don't
   fail the same way at the same confidence number.

## Decision

**Per-template bands, with a conservative global fallback.**

- `doc_dg_templates.field_schema` gains an optional per-field
  `confidence_bands: {auto_commit: float, review_floor: float}`. A field
  below `review_floor` is always routed to human review regardless of
  anything else; between `review_floor` and `auto_commit` it lands
  `in-review`; at or above `auto_commit` it may auto-commit as `machine`.
- Any template that does not set `confidence_bands` for a field falls back
  to a conservative global default: `auto_commit: 0.85, review_floor: 0.5`.
  Conservative on purpose — a template nobody has calibrated yet should
  default toward "ask a person," not toward "trust it."
- T59's per-corpus calibration attestation is what authorizes moving a
  template's bands *below* the conservative default (i.e., trusting it
  more) — calibration can only make a template stricter or confirm the
  default, never silently loosen it without a human attesting first.

## Why this over a single global number

- The product's own data already disagrees with a single number: a VLM
  reading a clean typed 2004 form and a VLM reading a smudged 1974
  handwritten register report very different confidence distributions for
  equally "actually correct" reads. One global cutoff either lets bad
  1974-era data through or sends good 2004-era data to review for nothing.
- T59 (per-corpus calibration protocol) is already built and unused for
  anything except gating bulk edge-confirmation (T57). Extending it to gate
  confidence bands too means the machinery pays for itself instead of
  sitting next to a parallel, disconnected global-threshold system.

## What this does NOT cover

- OCR/VLM confidence *methodology* (how a field's raw confidence number is
  computed) — that's already decided implicitly by T22's implementation
  (the VLM's own self-reported confidence, averaged across a fact's
  regions). This decision is about the threshold policy applied to that
  number, not how the number itself is produced.
- Retroactively recalibrating facts already written under the old
  hardcoded-0.9 path (legacy `MetadataItem` document-level metadata is a
  separate code path from `doc_dg_facts` and is not affected by this
  decision at all).

## Acceptance test

1. Two templates, one with explicit `confidence_bands`, one without.
2. A field extracted at 0.92 confidence on the template with
   `auto_commit: 0.95` lands `in-review`, not auto-committed — proves
   per-template bands are actually read, not just the global default.
3. A field extracted at 0.4 confidence on any template — with or without
   custom bands — always lands in the review queue, never auto-commits.
4. An uncalibrated template (no T59 attestation, no explicit bands) uses
   the conservative global default, not an unset/undefined threshold.

---

## Sign-off

| Role | Name | Decision | Date |
|---|---|---|---|
| Project decision-maker | (project owner) | ☑ Agree — per-template bands with conservative global fallback | 2026-08-24 |

Signed. T20 (real per-field confidence state), and by extension T51/T30/T76, are unblocked and can proceed.
