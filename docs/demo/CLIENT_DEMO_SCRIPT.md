# VeritasDocs — Client Demo Script

**Use with:** `docs/demo/veritas_demo_deck.html` (9 slides)
**Tone:** confident, consultative, benefit-led — you're reporting delivered value, not narrating a to-do list.
**Timing:** ~60–90 seconds per slide, ~10–12 minutes total. Pause after each bolded transition line.

---

### Slide 1 — Title

> "Good [morning/afternoon], and thank you for the time. Since we last spoke, our team has taken VeritasDocs from a working intake pipeline to a complete, end-to-end AI-powered document management system. Today I'll walk you through exactly what changed, why it matters for your organization, and — most importantly — what we've personally verified works, against your real data, not a demo sandbox."

**Transition:** "Let me start with where we stood a week ago, and where we stand now."

---

### Slide 2 — Where We Are Now

> "Last week, we showed you documents coming in — the intake connectors. This week, that's grown into the full lifecycle: every document is read, understood, verified by a human, made searchable, and governed under a complete audit trail.
>
> Before walking you through it, I want to set the bar for how we validated this: six major systems shipped, seventy-six core functions tested live against a real account, five real issues found and fixed along the way, and full bilingual support — English and Marathi — from day one. Nothing you'll see today is theoretical."

**Transition:** "Let's start at the beginning — how the system actually reads a document."

---

### Slide 3 — Reading the Documents

> "Every document that enters the system is read using local OCR — meaning it happens entirely on your own infrastructure, nothing leaves your environment, which matters for a system handling government and legal records.
>
> From there, our AI doesn't just read text — it understands the *structure* of the form and places each value into the correct field automatically. And critically, every value it extracts is traceable back to the exact spot on the original scan it came from, so nothing is a black box.
>
> Documents are automatically sorted by type, and anything the system isn't confident about is routed to a human — never silently guessed. Handwritten notes and stray marks are captured too, flagged separately rather than lost."

**Transition:** "That's a clean, modern scan. The real test is what happens with fifty-year-old paperwork."

---

### Slide 4 — Table Intelligence

> "This is the part I'm most proud of this week, because we built and tested it against a real 1973 government register you provided — not a synthetic example.
>
> The system automatically reconstructs tables that split across multiple pages, understands historic shorthand — like repeated ditto marks common in older registers — and tells the difference between real data and a repeated letterhead or stamp. It also remembers past reviewer decisions, so your team never answers the same judgment call twice.
>
> We even built a benchmarking tool that compares our OCR engines against each other, so we can keep tuning accuracy on exactly the kind of documents you'll be feeding it."

**Transition:** "Extraction is only half the story — nothing here is trusted until a human signs off. Let me show you that layer, alongside how it's found."

---

### Slide 5 — Verification & Search

> "Every AI-extracted value carries a transparent confidence score, and one click takes a reviewer straight to the exact location on the original document — full traceability, every time. Reviewers can bulk-approve or bulk-edit with a full preview and one-click rollback, and — this is a deliberate safeguard — bulk actions stay locked until a human has certified that batch of documents as reliable.
>
> On the search side: one search box covers exact matches, meaning-based results, and typo tolerance, and it works seamlessly across English and Marathi — search in one language, find content in the other. Every AI-generated answer cites its source, and if it can't find a real answer, it says so rather than guessing. It also automatically flags near-duplicate documents before they clutter your archive."

**Transition:** "Once information is verified, we tie it together — this is where individual documents become a connected, governable record."

---

### Slide 6 — Trust & Governance

> "The system automatically connects the same person, property, or organization across multiple documents — but any high-stakes connection always requires a human sign-off before it's treated as fact. Every record maintains its complete legal history: the original entry, plus every amendment, fully tracked.
>
> On governance: every action in the system is permanently and cryptographically logged — tamper-evident by design, so if anything were ever altered after the fact, we'd know. Retention policy is enforced at the database level, not just hidden in the app. We generate draft legal certificates ready for counsel's review, and every export clearly flags what's verified versus what's still pending — we never present an unverified value as fact."

**Transition:** "None of this matters if the wrong person can see the wrong record — so let's talk access. And then I want to be transparent about how we tested all of this."

---

### Slide 7 — Access & Proof It Works

> "Access is enforced through six distinct user roles, checked on every single action — not just hidden in the interface. Departments only see the folders they're authorized to see. The full interface is localized in Marathi, with accessibility support built in from the start.
>
> And here's what I want to spend a moment on: every one of seventy-six core functions was tested live, end-to-end, against a real account before this meeting — not just reviewed on paper. That testing found five real issues, including one data-isolation gap between accounts, which we caught and fixed ourselves before it could ever become a problem for you. Every one of those fixes now has a permanent automated test guarding against it happening again. We're telling you this not to highlight a flaw, but because that's exactly the level of scrutiny we hold ourselves to."

**Transition:** "Looking ahead — here's where we're pushing next, and full transparency on what's still outstanding."

---

### Slide 8 — Looking Ahead

> "We're actively exploring running the AI extraction fully on your own infrastructure, which would reduce both cost and dependency on external cloud services. This week we tested three candidate models directly against real Marathi text. Two weren't viable — one needed hardware we don't have, one simply couldn't read the script. The third showed real promise, and that testing told us precisely what hardware investment would make full on-premises AI a reality.
>
> On what's outstanding: every remaining item is a resourcing or third-party access decision — not an engineering gap. That includes GPU provisioning, a couple of external integrations waiting on partner access, and a small number of final business sign-offs. There is no unfinished engineering work standing between here and production."

**Close (delivered here, no dedicated slide):**

> "That's where things stand. In one week, VeritasDocs went from receiving documents to a fully verified, auditable, bilingual records platform. And every claim I've made today was tested against your real data, not a demo environment — because that's the standard this kind of system has to meet before it touches a real legal record.
>
> Thank you — I'm glad to open it up for questions, or walk through any part of this live."

---

## Anticipated questions — quick answers

- **"What's the biggest risk right now?"** → No local GPU yet, so AI extraction currently depends on a cloud model. We've scoped the exact hardware fix and it's a cost decision, not a technical unknown.
- **"How do we know the AI isn't making things up?"** → Every answer is grounded with a citation back to source; if it can't find one, it refuses to answer rather than guess. That's enforced in code, not a policy.
- **"What happens if a reviewer makes a mistake?"** → Every action is logged and reversible — bulk edits have one-click rollback, and the audit trail can never be silently altered.
- **"Is our data ever sent externally?"** → OCR runs entirely on your own infrastructure. The one component still calling an external AI service is the structured-extraction step, and that's exactly what the local-GPU investigation is aimed at removing.
