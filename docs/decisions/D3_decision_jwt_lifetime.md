# Decision D-3 — JWT access-token lifetime

Status: **signed — 2026-08-25** (code already matches; this is the missing written record)
Blocks: none currently — D-3 blocks no other task per docs/planning/backlog.txt, but was flagged in the same "start five things this week" item as T33 as something "wrong to ship on an evidence system"
Owner: Tech Lead
Reference: docs/planning/backlog.txt D-3, "T33 and D-3 — silent OCR failure, and the 30-day JWT"; backend/app/config.py; commit 318c0e0

---

## What was being decided

The original access-token lifetime was 30 days, with a 60-day refresh token (`backend/app/config.py`, initial commit `3ea9dde`). For a system storing evidentiary government records, a token that stays valid for a month is a real exposure: a leaked or stolen access token works for as long as a typical employee's leave, with no re-authentication in between.

## Decision

**15-minute access tokens, 7-day refresh tokens.**

```python
jwt_access_token_expire_minutes: int = 15  # 15 minutes
jwt_refresh_token_expire_days: int = 7  # 7 days
```

## Why

- 15 minutes bounds the blast radius of a leaked access token to a single short session rather than a month.
- 7 days for the refresh token keeps normal daily use frictionless (no re-login mid-workday) while still forcing re-authentication at least weekly, rather than bi-monthly.
- This is a standard, conservative pairing for an access-controlled system handling sensitive records — short-lived bearer tokens, a longer-but-still-bounded refresh window.

## History note

This was already implemented in commit `318c0e0` ("fix: correct OCR failure flag, shorten JWT lifetime, add CI coverage floor"), paired with the T33 OCR-failure fix per the backlog's own framing. It was never written up as a signed decision at the time — this document is that missing record, not a new change. No code changes accompany this commit.

---

## Sign-off

| Role | Name | Decision | Date |
|---|---|---|---|
| Tech Lead | (project owner) | ☑ Agree — 15min access / 7-day refresh, as already implemented | 2026-08-25 |

Signed. D-3 is closed; no further action needed.
