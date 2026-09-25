# Decision D-8 — Escrowed links in evidence exports: exclude, or include with a flag

Status: **signed — 2026-08-24**
Blocks (would be unblocked): T65 (Section 63 certificate — also still gated on A3, see below), T67 (verified-layer boundary enforcement)
Owner: Legal counsel
Reference: docs/planning/backlog.txt T65, T67; "T67 is what makes tier-3 escrow safe. §11 requires it demonstrated on an evidence export that actually contains escrowed links."

---

## Why this has to be agreed before code

Tier-3 (identity) and tier-4 (legal) entity links always land as `held` —
escrowed, unconfirmed, requiring explicit human confirmation regardless of
confidence (T56, already built and signed off on this session). The open
question is narrower than "should unconfirmed data ever leave the
system" — it's specifically: when a legal/evidence export is generated,
does an escrowed link ever appear in it, and if so, how is it marked so
nobody downstream mistakes a machine suggestion for a confirmed fact.

## What the backlog itself already answers

The backlog's own acceptance note for T67 rules out one of the two
options before this decision starts: **"§11 requires it demonstrated on
an evidence export that actually contains escrowed links."** That's only
satisfiable if escrowed links can appear in an export at all — full
exclusion would make T67's own acceptance test impossible to pass. So
this decision is really about the *labeling contract*, not a binary
include/exclude choice.

## Decision (proposed)

**Include, always explicitly flagged — never indistinguishable from
verified fact.**

- An evidence export may include escrowed (tier-3/tier-4, `held`-status)
  links, but every such link carries an explicit, unmissable marker in
  the export payload: `"confirmation_status": "unconfirmed_machine_suggested"`
  (as opposed to `"human_verified"` for confirmed links) plus the
  confidence score and creating policy/actor.
- T67's query-layer boundary is what's actually being built here: the
  enforcement point isn't "strip escrowed links," it's "never let an
  escrowed link's status field go missing or get silently coerced to
  look verified" — enforced once at the query layer (per T67's own
  wording), not re-implemented per export format (CSV/JSON/XLSX/PDF, T78).
- Section 63 certificates (T65) are a stricter case than a general
  export: a certificate is a stronger legal assertion than an export
  listing, so certificates exclude escrowed links entirely rather than
  flag them — a certificate has no room for a "this might not be true"
  footnote. General exports (T78) may include flagged escrowed links;
  certificates (T65) never do.

## What this does NOT resolve

- **A3 — legal counsel review of the Section 63 certificate template
  itself.** That's a separate, still-unresolved external dependency: this
  document settles *whether escrowed links can appear in certificates*
  (they cannot, decided above), not whether the certificate template's
  actual wording/format is legally sound. T65 stays blocked on A3
  regardless of this decision. D-8 unblocks T67 (and, through T67, T78 —
  general exports); it does not unblock T65 by itself.
- This is a genuine legal-consequence call in a system marketed on
  evidentiary integrity, being proposed here as a project-decision-maker
  sign-off rather than actual outside legal review, because backlog
  labels D-8's owner as "Legal counsel" while A3 is the item explicitly
  called out as needing that outside review. Flag this to real legal
  counsel before this policy ships to a real customer — this document
  unblocks engineering work, it is not a substitute for that review.

## Acceptance test

1. Generate an export containing at least one escrowed (tier-3) link —
   confirms T67's own acceptance requirement is satisfiable.
2. Every escrowed link in that export carries
   `confirmation_status: unconfirmed_machine_suggested`, distinguishable
   at the schema level from `human_verified` — not just in prose text
   that a script could ignore.
3. Attempt to include an escrowed link in a Section 63 certificate — this
   must be rejected/excluded at generation time, not just recommended
   against.

---

## Sign-off

| Role | Name | Decision | Date |
|---|---|---|---|
| Project decision-maker | (project owner) | ☑ Agree — flag in exports, exclude from certificates | 2026-08-24 |

Signed. T67 (verified-layer boundary) is unblocked and can proceed. T65 (Section 63 certificate) remains blocked on A3 — see "What this does NOT resolve" above.

Note: recorded here as a project-decision-maker sign-off standing in for
"Legal counsel" per this session's established pattern (D-1, D-5). Real
legal counsel review before production use is still recommended given the
evidentiary stakes — see "What this does NOT resolve" above.
