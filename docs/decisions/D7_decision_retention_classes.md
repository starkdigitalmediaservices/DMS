# Decision D-7 — Retention classes and defaults

Status: **signed — 2026-08-24**
Blocks (would be unblocked): T66 (retention policy engine, replacing the 30-day trash purge as the only retention behavior)
Owner: BA
Reference: docs/planning/backlog.txt T66; Governance module (h)

---

## Why this has to be agreed before code

Today there is exactly one retention rule in the whole product: trashed
items purge after 30 days (`trash_retention_days` config, T66's own
description calls this out as "the only retention behaviour"). A record
class engine needs to know what classes exist and what each one's default
period is before it can replace that single rule — otherwise T66 has
nothing to be data-driven *about*.

## What is being decided

This decision settles the **retention engine's shape and safe defaults**,
not the final legally-authoritative retention schedule for every record
type — that number properly belongs to records management / legal policy
input this project doesn't have (same category of gap as A1's missing
reference corpus). What's being decided here is narrow: build the engine
now, on defaults that can never be legally wrong in the dangerous
direction (deleting something too early), and let the real retention
years be corrected later by editing config, not by re-architecting.

## Decision (proposed)

**Retention classes are data-driven, not hardcoded, with a conservative
starting set:**

- New `sys_dg_retention_classes` table: `class_name`, `retention_years`
  (nullable = permanent/never purge), `applies_to` (document type or
  record type pattern), `description`.
- Every document/record is assigned a retention class at ingest (default
  `unclassified_permanent` — retention_years = NULL — if nothing more
  specific matches). **Nothing is ever purged by default** unless it's
  explicitly assigned a class with a finite period.
- Starting classes seeded conservatively:
  - `unclassified_permanent` (NULL — never auto-purge; the safe default)
  - `operational_trash` (30 days — the existing, unchanged trash-purge
    behavior, kept as its own named class rather than the only rule)
  - `statutory_record` (NULL — permanent; explicitly for anything tied to
    a property/entity record, T60/T61 — these should never be
    engine-purged regardless of age)
- The engine enforces whatever's configured; it does not ship an opinion
  on what a "correct" statutory retention period is for any given form
  type. That number is a config edit, not a deploy, whenever real
  records-retention policy input arrives.

## Why this over guessing real statutory numbers

- Guessing a wrong *short* retention period on evidence-grade government
  records is the one mistake this system cannot recover from. A
  conservative "permanent unless proven otherwise" default is the only
  safe starting position without real policy input.
- Making retention data-driven and class-based (rather than a single
  global number) means the eventual real numbers are a data change, not
  an engineering change — this decision only has to happen once.

## What this does NOT cover

- The actual legally-correct retention period for any specific statutory
  form type or record class — that remains genuinely externally owned,
  same as A1/A3, and should be corrected via config once real input
  arrives, not treated as settled by this document.

## Acceptance test

1. A newly ingested document with no explicit class assignment defaults
   to `unclassified_permanent` — never silently purged.
2. A document explicitly trashed by a user still purges after 30 days
   (`operational_trash`), unchanged from today's behavior.
3. A record (T60) is never purged by the retention engine regardless of
   its class or age — `statutory_record` is a hard exception, not just a
   long default.

---

## Sign-off

| Role | Name | Decision | Date |
|---|---|---|---|
| Project decision-maker | (project owner) | ☑ Agree — conservative defaults | 2026-08-24 |

Signed. T66 (retention policy engine) is unblocked and can proceed.
