# Connector Demo — Pitch Script

A ready-to-read script for the ingestion-connectors segment of the demo.
Roughly 3-4 minutes if you do all three beats; 90 seconds if you only
have time for one (use SFTP or Email — they're the most visually
convincing since the file comes from "outside").

Pre-stage everything per the checklist at the bottom **before** you
start talking, so there's zero dead air waiting on file transfers.

---

## The setup line (say this before touching anything)

> "Before I show you search, I want to show you something most document
> systems don't do at all — how documents actually *get in*. Because
> the real problem in most organizations isn't finding documents once
> they're in the system. It's that half of them never make it in, in
> the first place, because someone has to remember to upload them."

> "We built this so a document gets in the moment it exists — no
> matter which system produced it, and no matter who's holding it."

---

## Beat 1 — Auto-sync (Stark Drive)

**Say:**
> "This is a folder on my computer I already use day to day — nothing
> special about it. Watch what happens when I just... save a file into
> it, like I normally would."

**Do:** Drag a pre-picked file into `/home/stark/Stark Drive /` on
screen (or just drop it live — it's fast enough to do live if you're
confident).

**Say while it processes (~20-30 sec):**
> "No upload button, no login required on this folder, nothing. DMS
> checks this folder automatically. In about 20 seconds this shows up
> fully indexed and searchable — same as if I'd uploaded it by hand."

**Do:** Switch to the DMS Drive tab, refresh, point at the file
appearing.

> "That's the whole point — your team doesn't change how they work.
> DMS just quietly keeps up."

---

## Beat 2 — SFTP (bring in an outside device)

**Say:**
> "That folder only works because it's on this machine. But what about
> a vendor, or another office, who doesn't have access to your
> server at all? That's what this is for."

**Do:** Click **+ New → Connect a device** on the Drive page. Switch to
the **Folder / SFTP** tab.

> "This is a self-service panel — anyone on the team can open this and
> hand these details to an outside vendor themselves, no engineer
> needed to issue credentials."

**Do:** Copy the address (or just narrate it), switch to a pre-connected
file manager window showing the SFTP folder, drag a file in.

> "That machine has zero access to anything else in DMS — just this one
> drop folder. Twenty seconds later..."

**Do:** Refresh Drive, show the file indexed.

---

## Beat 3 — Email-in (the universal one)

**Say:**
> "And this is the one I actually think matters most, because it needs
> no client, no server access, no training at all. Everyone already
> knows how to attach a file to an email."

**Do:** Click the **Email** tab in the same "Connect a device" panel,
point at the address.

> "Any system — accounting software, a scanner, a colleague — can just
> email a document to this address."

**Do (off-screen or in a small terminal, pre-positioned):**
```bash
cd "/home/stark/Work Space/DMS"
python3 scripts/send_demo_email.py "/path/to/a/never-uploaded/file.pdf"
```

> "I just sent that as an email a second ago. Give it about ten
> seconds..."

**Do:** Refresh Drive, show it appear.

---

## The close line (say this after any/all beats)

> "Three completely different ways for a document to arrive — a
> folder, another server, an email — and every one of them lands in
> the exact same place, searchable the exact same way, in about the
> same twenty seconds. You don't pick one intake method and live with
> it. Whatever already produces your documents today, this can absorb
> it without changing a thing about how your team works."

---

## If the client asks the hard question

**"Is this using our real email / real folder?"**
> "For this demo, we're running a local test mailbox so the demo
> doesn't depend on real internet email delivery in front of you — in
> your actual deployment, this points at your real company mailbox,
> same underlying mechanism, just a different address."

**"Can different people/departments have different credentials?"**
> "Today it's one shared connector account per deployment. Per-user
> credentials with individual permissions is a natural next step, not
> a rebuild — happy to scope that with you."

**"Does this work with folders-inside-folders, not just flat files?"**
> "Yes — drop a whole folder structure in, and it recreates that same
> structure as real folders inside DMS, not flattened filenames."
(Good one to actually demonstrate live if you have an extra minute —
drag a folder with a subfolder inside it instead of a single file.)

---

## Pre-demo checklist (do this before the client sits down)

- [ ] Confirm all containers up: `docker compose ps` — 9 services, all "Up"
- [ ] Pick 2-3 files you haven't already uploaded through any channel
      tonight (so nothing gets silently skipped as a duplicate on stage)
- [ ] Have a file manager window open and connected to the SFTP folder
      already, so Beat 2 is just a drag, not a connect-live moment
- [ ] Have a terminal open, already `cd`'d into the project folder, for
      Beat 3's email send
- [ ] Do one full silent dry run of all three beats within the hour
      before the demo, using throwaway files, so you know the exact
      timing
