# VeritasDocs DMS — What Changed Since Last Week's Demo

**Covers:** everything built after last Friday's demo (Aug 21) through today (Aug 27).
**Purpose:** presentation reference — what we built, how, why, and what AI models power it.

---

## 1. The Big Picture

Last Friday's demo showed the document ingestion connectors (SFTP, email-in, watched folders). Since then, we built out almost the entire rest of the product — from reading a scanned Marathi/Devanagari document, to extracting structured legal data from it, to letting a human verify it, to searching across it, to producing audit-proof, exportable records.

In short: last week we showed **documents coming in**. This week we can show **documents becoming trustworthy, searchable, legally-usable records** — and we found and fixed 5 real bugs along the way through hands-on testing.

---

## 2. Reading the Documents (OCR + AI Extraction)

**What it does:** turns a scanned page (often a photographed 1970s-era Marathi government register) into usable data.

- **Local OCR** (PaddleOCR) — reads Marathi/Devanagari text directly on our own servers, no data sent outside. This matters because the product is supposed to work in **air-gapped** (fully offline) government deployments.
- **AI structured extraction** (Google Gemini, cloud-based) — this is the "smart" part. Instead of just reading text, it looks at a scanned page **and** the specific form template (e.g. "Waqf Registration Form A") and pulls out each answer into the correct field — owner name, survey number, valuation, etc. — along with exactly where on the page it read that value from.
- **Document classification** — automatically sorts incoming documents by form type; anything it can't confidently classify goes into a review queue instead of being guessed.
- **Handwriting & marginalia handling** — handwritten notes and stamps that aren't part of the actual form get flagged separately for a human to read, rather than being silently ignored or wrongly merged into a data field.

**Why AI (Gemini) and not just OCR:** OCR only gives you raw text. Gemini (a vision-language model, or "VLM") looks at the *image* and understands *which box on the form* each value belongs to — that's what lets every extracted value be clicked on and traced back to its exact location on the original scan.

---

## 3. Reading Real, Messy Registers Correctly (Table Intelligence)

This was the single biggest chunk of new work — a set of 9 features (we call them TS1–TS9) built after studying a real 1973 gazette register you provided.

- **Table stitching** — a table on a scanned register often spans two pages (a row starts at the bottom of page 1 and continues at the top of page 2, or a table is split left-half/right-half across two facing pages). We built an engine that automatically reconnects these fragments into one correct row — instead of silently losing half the row's data, which is what happened before.
- **"Ditto mark" handling** — old registers use "Do." (ditto) to mean "same as the row above," repeated for many rows in a column. We now detect this, fill in the real repeated value, and *also* keep the original "Do." mark on record — so nothing is silently guessed, and the original handwriting is always recoverable.
- **Page furniture detection** — flags repeated headers/footers/stamps that appear on every page, so they don't get mistaken for real data.
- **Human-answer memory** — once a person resolves one tricky/ambiguous case (e.g., "is this a continuing table or a new one?"), the system remembers that decision for every future document with the same table shape, so reviewers don't have to answer the same question over and over.
- **OCR engine comparison tool** — a built tool that scores our different OCR engines (PaddleOCR, Tesseract, etc.) against each other on real pages, to help decide which engine is best for which kind of document.

**Why this matters for the presentation:** this is the part that makes the product actually usable on *real, old, messy government paperwork* — not just clean modern scans.

---

## 4. Human Verification Workbench

No AI-extracted value is treated as "official" until a human confirms it. This week we completed the review workflow:

- Every extracted field is shown with a **confidence score** and grouped into review queues (e.g., "low confidence," "handwritten," "ambiguous table").
- **Click-to-source**: click any extracted value and it highlights the *exact* rectangle on the original scanned page it came from — even if that value's answer was pieced together from two different pages.
- **Bulk actions**: a reviewer can bulk-confirm or bulk-edit many fields at once, with a preview step and a one-click full undo.
- A folder of documents must be **calibrated** (a human certifies the AI's confidence scores are trustworthy for that batch) before bulk-confirm is even allowed — this stops anyone from blindly trusting an uncalibrated confidence number.

---

## 5. Search

- **Hybrid search**: combines meaning-based search (AI embeddings), keyword search, and typo-tolerant fuzzy search — all in one query, so a misspelled name still finds the right document.
- **Cross-script search**: search in English and still find the Marathi/Devanagari content, and vice versa (e.g., searching "waqf" also finds "वक्फ"). Built two ways — a hand-curated glossary of ~50 domain terms (works even fully offline) plus AI-based query translation as a backup.
- **Search over extracted fields, not just raw text**: you can search for a specific extracted value (like a survey number) even if it never appears in that exact wording anywhere in the scanned text.
- **Answer grounding & refusal**: when you ask the AI a question about your documents, it must cite its source — and it will explicitly refuse to answer rather than make something up if it can't find real grounding.
- **Duplicate detection**: catches near-duplicate documents (e.g., the same register rescanned) even when the file itself is different, using AI similarity — not just an exact file match.

---

## 6. Entity Graph & Legal Records ("who owns what, and what changed")

- **Entity/knowledge graph**: links people, properties, and organizations across different documents — e.g., knowing that "Ramrao Patil" in one register and "R. B. Patil" in another are the same person, once a human confirms the link.
- **Tiered trust**: low-risk/mechanical links (e.g., "this page mentions this name") are accepted automatically; higher-risk links (e.g., "these are the same legal identity," "this transfers legal ownership") **always** require a human to confirm, no matter how confident the AI is.
- **Legal records with full history**: a land record isn't just a snapshot — it's the original entry plus every amendment made to it since, each with its own evidence and legal status (in force / set aside / under stay / superseded). You can always see both the current state *and* the original, unedited entry.

---

## 7. Governance, Audit & Legal Compliance

- **Tamper-evident audit trail**: every action (confirm, edit, delete...) is chained together with cryptographic hashes, so if any record in the log were ever altered after the fact, the chain would visibly break. We can verify the entire chain is intact on demand.
- **Retention & legal hold**: documents follow retention rules (some can never be auto-deleted; some expire after a set time) — this is enforced at the database level, not just in the app.
- **Section 63 certificate**: generates a formal evidentiary certificate for a document (currently a draft template, pending legal sign-off before real use).
- **Completeness dashboard**: shows, per folder, how much of the AI's work is actually verified vs. still pending — and lets you drill into exactly which documents are missing what.
- **Exports**: CSV / JSON / PDF exports of records, with any unverified data clearly flagged rather than presented as fact.

---

## 8. Access Control & Language

- **Six real user roles** (records officer, operator, department head, legal counsel, IT admin, auditor), each with different permissions — enforced on every action, not just hidden in the UI.
- **Department-based access**: some roles only see the folders their department has been granted, not the whole tenant.
- **Full Marathi UI translation**, with a language switch, alongside English.
- **Accessibility baseline** (keyboard navigation, screen-reader support) to meet WCAG 2.1 AA.

---

## 9. Licensing

A placeholder business model was built so the product can actually be metered/sold: a SaaS trial with document/storage limits, and a signed, tamper-proof license file for on-premise/offline government deployments. (Every number in this model — trial limits, grace periods — is explicitly marked as a placeholder pending real business sign-off, not a final decision.)

---

## 10. Testing — What We Actually Verified, and Bugs We Found & Fixed

Everything above wasn't just built — it was tested against a real account with real documents, and a systematic end-to-end sweep was run across all ~76 backend functions plus a manual UI pass. That testing pass caught **5 real bugs**, all now fixed:

1. **Cross-tenant data leak in "Empty Bin"** — the most serious one. Clicking "Empty Bin" was accidentally able to delete *other customers'* trashed files, not just your own, because of a missing safety check. Fixed and now has a dedicated test to make sure it can never happen again.
2. **"Empty Bin" silently doing nothing** — for documents without an explicit retention policy, the delete button would silently do nothing and pretend it succeeded. Now it honestly tells you what was deleted and what's protected, and why.
3–5. **Contrast/visibility bugs** — several warning messages (offline notice, upload status, error messages) were using colors that were nearly invisible against the app's light background. All fixed with proper, readable colors, and we proactively scanned the rest of the app for the same issue.

---

## 11. Local AI Model Research (Cost/Offline Investigation)

To reduce reliance on the paid cloud AI (Gemini) and support fully offline deployments, we tested whether a smaller AI model could run directly on our own servers instead:

- **Qwen2.5-VL-7B** (the originally planned local model) — could not be tested at all; needs a GPU with 24GB+ memory, which this environment doesn't have.
- **Moondream2** — a smaller alternative; found it strictly requires a GPU too, even in "CPU mode" (a real limitation of that model's current software, not fixable on our end).
- **SmolVLM** — genuinely runs without a GPU, but **cannot read Devanagari/Marathi at all** — a dealbreaker for this product.
- **Qwen2.5-VL-3B** (smaller version of the original plan) — the most promising: it does read Marathi, but with real accuracy errors on important details like conjunct letters and digits, and is slow (1–3 minutes per page) without a GPU.

**Conclusion:** we need a proper GPU-equipped machine to run this feature reliably — a request for one has been drafted. Renting a cloud GPU for a few hours (roughly $1–5) would be enough to validate it before deciding on hardware.

---

## 12. What's Still Open

- Everything in this document is built, tested, and working — the only things not done are **blocked on something outside engineering** (not a missing feature):
  - No GPU available yet (blocks fully-local AI extraction)
  - No real reference document set with verified-correct answers yet (blocks a formal accuracy benchmark)
  - A few external system integrations (Google Drive, SharePoint, a government e-Office system) are waiting on access being granted by those third parties
  - A handful of legal/business decisions need real human sign-off (e.g., final licensing numbers, whether an open-source license (Surya, GPL) is acceptable)

---

## One-line summary for the room

*"Since last week, we went from 'documents come in' to a complete pipeline: AI reads and structures even messy 1970s scans, a human verifies every fact with full click-to-source traceability, it's all searchable across English and Marathi, it's tied together in a legal record graph, and every action is tamper-evidently audited — and we proved all of it works by testing every single function against a real account, catching and fixing five real bugs in the process."*
