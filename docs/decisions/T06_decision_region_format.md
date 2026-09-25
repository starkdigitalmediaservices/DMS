# Decision T06 — How a source location is written down

Status: **signed — 2026-08-24**
Blocks (now unblocked): T04, T05, the verification workbench, the 360° page viewer, citation click-through in search & Q&A
Owner: signed off by the project decision-maker on 2026-08-24
Reference: Build Design v0.3, Section 2 ("Where each fact came from")

---

## Why this has to be agreed before code

Three separate parts of the product draw a highlighted box on a scanned page from the same stored numbers: the verification workbench, the 360° document viewer, and citations under a search answer. If the extractor, the API, and the viewer don't agree on what a number in a region means, the three will disagree on where the box goes — and nobody will notice until an operator is looking at a highlight on the wrong row of a 50-year-old register.

This is a four-line schema decision. It has to be made once, in writing, and then never revisited per-feature.

---

## What is being decided

### 1. Where (0,0) sits
The top-left corner of the page is `(0,0)`. `x` grows right, `y` grows **downward** — matching how the scanned image itself is read, not mathematical convention (which would grow upward).

### 2. What the stored numbers mean
A region is stored as `x0, y0, x1, y1`, each a value from `0` to `1` — a **fraction of page width/height**, not a pixel count.
Reason: a fraction stays correct at any zoom level and any screen size. A pixel offset breaks the moment the image is re-rendered at a different resolution.

### 3. Crooked scans
Rotation and skew are stored **per page**, not per region. The viewer applies the page's rotation/skew when it draws a region's box.
Reason: many of these registers were photographed crooked. A region box computed as if the page were perfectly upright will land on the wrong row once the viewer straightens (or fails to straighten) the image.

### 4. Facts that span more than one location
A fact's region is stored as a **list** of regions, not a single one, from day one — even though most facts will only ever have one entry.
Reason: Handler 3 (continuation-row merge) produces facts whose value is only complete when read across two pages. The column has to be a list from the first migration, not retrofitted later.

---

## Data shape this implies

```
document
  ├─ file hash, source office, date, document type
  └─ page[]
       ├─ page number
       ├─ width, height (px, as scanned)
       ├─ rotation, skew
       └─ fact[]
            ├─ field name, value, confidence
            └─ region[]                 ← list, not single
                 ├─ x0, y0, x1, y1       ← normalised 0–1
                 └─ (rotation is read from the parent page, not stored again here)
```

A fact with an empty region list is invalid and must be rejected at write time — not just hidden in the UI. A fact nobody can point at on the page is a fact nobody can check.

---

## What this decision does NOT cover
- OCR/VLM confidence scoring methodology (separate, pipeline-level decision)
- Which of the four handlers produces multi-region facts and when (Section 4 — handler-specific)
- Storage engine/column types for the region list (implementation detail once this shape is agreed)

---

## Acceptance test (from Section 2)

1. Load a crooked (rotated/skewed) scan.
2. Confirm every extracted fact has at least one region, and every region's coordinates fall inside its page's bounds.
3. Click a fact in the field list → the correct area of the image highlights, accounting for that page's rotation/skew.
4. Attempt to save a fact with an empty region list → the write must fail, not silently succeed.

---

## Sign-off

| Role | Name | Decision | Date |
|---|---|---|---|
| Project decision-maker | (project owner) | ☑ Agree | 2026-08-24 |

Signed. T04/T05 (source-location columns + write-time enforcement) are unblocked and can start as part of Phase 1.
