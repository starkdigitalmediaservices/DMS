# Client Requirements — Connectors Needing Client Credentials / Access

**Purpose of this document:** explain, in plain language, which parts of DMS v1 are
still unfinished specifically because they need an account, credential, or access
approval that only the client (or a third party like Google, Microsoft, or NIC) can
provide — not because of any remaining engineering work.

**The one-line version:** three connectors — Google Drive, SharePoint/OneDrive, and NIC
e-Office — are fully designed and ready to be wired in, but none of them can function
without the client granting access to their own accounts/systems first. This is the
complete list of such items; everything else in the product that the development team
can build on its own has been built.

---

## 1. Why these three, specifically, need the client

All of the other ways documents get into the system today — manual upload, a watched
folder, SFTP, email-in, and a network scanner — only ever touch infrastructure we
control (our own server, our own inbox, our own folder). Nobody's permission is needed
beyond what we already have.

The three connectors below are different: each one has to reach *into an account the
client owns*, sitting inside somebody else's system (Google's, Microsoft's, or the
government's). No amount of coding lets us skip the step where the owner of that account
says "yes, this app may read my files." That approval step is the entire reason these
three are still open.

---

## 2. Google Drive connector

**What it needs from the client:** a Google account (or, if the client is an
organization, a **Google Workspace** business account) to connect to, and permission
granted on that account for our app to read its files.

**Why it's not done:** Google does not allow any outside app to read a user's Drive files
just because the app works correctly. Any app requesting that level of access has to go
through Google's own security review process ("restricted-scope verification") before it
can be used beyond a small closed testing group. This process commonly takes **four to
six weeks** once submitted, and has not been submitted yet, since it needs the client's
go-ahead first (the review shows the client's own organization as the one being connected
to).

**What we need from the client:**
1. Confirm whether the account to connect is a personal Google account or a Google
   Workspace account.
2. If Workspace: identify the workspace administrator who will approve the app once it
   passes Google's review.
3. Give the go-ahead to begin Google's verification submission.

**Status:** never requested yet.

---

## 3. SharePoint / OneDrive connector

**What it needs from the client:** a Microsoft 365 business account (this is what
SharePoint and OneDrive for Business both run on), and an administrator on that account
to approve our app's access.

**Why it's not done:** Microsoft requires someone with **Global Administrator** or
**Application Administrator** rights on the client's own Microsoft 365 / Entra account to
grant "admin consent" for the specific permissions the connector needs (read access to
SharePoint/OneDrive files). Only the client's own IT administrator can grant this — there
is no way to approve it from our side.

**What we need from the client:**
1. Confirm the client uses Microsoft 365 / SharePoint (not just personal OneDrive) as a
   business account.
2. Identify the person with Global Admin or Application Admin rights on that account.
3. Have that administrator click "allow" once our app registration is sent to them for
   approval — normally a single action on a Microsoft-hosted approval screen.

**Status:** never requested yet.

---

## 4. NIC e-Office connector

**What it needs from the client:** a government department to formally request
integration access from NIC (India's National Informatics Centre, which operates
e-Office).

**Why it's not done:** e-Office isn't a commercial product we can sign up for directly —
it's a government system, and NIC only grants integration access when the request comes
from the actual government department that will use the connection, not from a private
vendor acting alone. We are not in a position to request this ourselves.

**What we need from the client:**
1. Identify the specific government department/office this connector is for.
2. Have that department formally submit an integration/API access request to NIC.
3. Once NIC issues credentials, pass them to us — wiring the connector up from there is
   fast, since it follows the same pattern as the other connectors already built.

**Status:** never requested yet.

---

## 5. Summary

| Connector | What's needed from the client | Status |
|---|---|---|
| Google Drive | A Google/Workspace account + Google's app-review approval | Never requested |
| SharePoint / OneDrive | A Microsoft 365 account + admin consent from the client's IT admin | Never requested |
| NIC e-Office | A government department to file an integration access request with NIC | Never requested |

**Bottom line:** the connector framework itself is already built and proven three times
over (watched folder, SFTP, email-in all share the same underlying design), so adding
these three is a small, fast piece of work once access exists. Nothing about them is
stuck on engineering — each one is waiting purely on the client (or, for Google/Microsoft,
the client's own IT administrator) to grant access to an account that only they own.
