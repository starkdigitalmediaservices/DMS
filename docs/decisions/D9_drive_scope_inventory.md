# D-9 — Scope status of the built Drive product

**Backlog item:** "Scope status of the built drive product — folder tree, star, trash, chat, offline and PWA. Accepted change request, or recorded out-of-scope." Owner: Product owner. This document doesn't make that call — it's the concrete inventory the Product owner needs to make it, so "the drive product" stops being a vague blob and becomes six specific, individually-checkable line items.

Compiled 2026-09-02 by directly reading the current code and, where noted, live-testing in a real browser session against a real account this same session.

| Feature | Status | Evidence |
|---|---|---|
| **Folder tree** | Built, real | `frontend/components/drive/FolderTreeSidebar.tsx`. Create/rename/move/delete all wired to real backend endpoints (`backend/app/api/v1/folders.py`), tenant-scoped (`folder.tenant_id != tenant_id` checked on every fetch/mutation — see D-2 review). Live-tested this session: folder creation, nested navigation, breadcrumb. |
| **Star** | Built, real | Bulk-star action in `drive/page.tsx`'s selection action bar, backed by `Document.is_starred`. Live-tested this session via the bulk-select action bar (Star/Download/Move/Move to Bin), confirmed working against a real uploaded document. |
| **Trash / Bin** | Built, real | Trash/restore/permanent-delete wired to `Document.is_trashed`/`trashed_at` and `RetentionClass`. This had a real, severe bug (found and fixed 2026-08-27): `cleanup_expired_trashed_items` had zero tenant scoping, and a separate real bug (2026-09-01) where `retention_class` was never actually flipped on trash, making the 30-day purge permanently inert for every document in the system. Both fixed and live-verified. Bulk "Move to Bin" live-tested this session. |
| **AI Chat** | Built, real | Real backend session/message API (`backend/app/api/v1/chat.py`: create session, list sessions, get session, post message), persisted via `chat_dg_sessions`/`chat_dg_messages`. Frontend: `frontend/components/chat/PersistentChatPanel.tsx`. Not re-tested end-to-end this specific session, but the "AI Chat" nav link and panel are present and wired (confirmed live in the Chrome audit this session — appeared in the left nav next to Home/My Drive/Starred/Bin). |
| **Offline mode** | Built, real, but not a PWA (see below) | `frontend/lib/offlineStore.ts`: a genuine offline-first design — caches drive stats/folder tree/document lists in `localStorage`, queues mutating actions (`create_folder`, `rename_folder`, `delete_folder`, `delete_document`, `rename_document`) and pending uploads (files staged as base64) while the API is unreachable, to sync once connectivity returns. `frontend/components/OfflineBanner.tsx` surfaces the state to the user (its own real bug — low-contrast text on a light background — was found and fixed 2026-08-27). Live-seen this session: the app correctly showed "Offline Mode — You are working offline with cached data" during testing. |
| **PWA** | **Not built at all** | No `manifest.json` anywhere in `frontend/public` or `frontend/app`. No service worker (`sw.js` or equivalent). No `next-pwa`-style config in `next.config.*`. The app is not installable and has no true offline page-caching (Cache API) — "offline mode" above is real but is an in-app localStorage/queue pattern, not a Progressive Web App. If "PWA" in the original scope meant installability + offline-first page loads specifically, that part was never started. |

## What this means for the D-9 decision

Five of the six listed items are genuinely built, tested, and (per this session's live audit) working correctly. The sixth — PWA specifically — doesn't exist in any form. The Product owner's call is really two separate ones:

1. **Folder tree / star / trash / chat / offline**: were these in the original committed scope, or were they built beyond it? Either way they're done and working — this is a bookkeeping/estimate question, not an engineering one.
2. **PWA**: this one needs an actual decision, not just a status note — build it (real scope, real estimate needed), or formally drop it from scope (so it stops silently appearing on lists like this one as if it's pending work).
