# DMS Email Router (Cloudflare Worker)

Receives mail via Cloudflare Email Routing and forwards each message, raw and
base64-encoded, to the DMS backend's inbound-email webhook, which extracts the
attachments and ingests them as documents.

```
sender → Cloudflare Email Routing → this worker
       → POST {BACKEND_URL}/api/v1/connectors/email-inbound
       → DMS ingestion pipeline
```

## Deploying

Two values must be set. Neither has a usable default, and getting either wrong
fails every delivery rather than failing loudly at deploy time.

**1. `BACKEND_URL`** — the public base URL of the DMS backend, reachable from
Cloudflare's edge. `wrangler.jsonc` ships a deliberately invalid placeholder
(`https://dms.invalid.example`), so override it at deploy:

```bash
wrangler deploy --var BACKEND_URL:https://dms.example.com
```

**2. `WEBHOOK_SECRET`** — must match the backend's `EMAIL_WEBHOOK_SECRET`. It is
the only authentication on that endpoint, so it is stored as an encrypted
secret, never in `wrangler.jsonc`:

```bash
wrangler secret put WEBHOOK_SECRET
```

The backend refuses to start in production while `EMAIL_WEBHOOK_SECRET` is
still the value shipped in this repo, so generate a real one for both sides:

```bash
openssl rand -hex 32
```

Then bind the worker to an address in **Cloudflare dashboard → Email → Email
Routing → Routes**.

## Verifying a deployment

```bash
# Should be 401 — proves the endpoint is reachable and the secret is enforced.
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  https://dms.example.com/api/v1/connectors/email-inbound \
  -H 'Content-Type: application/json' -d '{"raw_email_b64":""}'
```

Then send a real message with an attachment to the routed address and watch
`wrangler tail` alongside the backend log. On success the backend logs
`Email webhook: successfully ingested '<filename>'`; the document appears in
the Drive of whichever account `CONNECTOR_ACTOR_EMAIL` names.

## Known limitations

- Every ingested document is attributed to the single `CONNECTOR_ACTOR_EMAIL`
  account. Routing mail to the tenant it was addressed to is backlog T40.
- A delivery failure is retried by Cloudflare Email Routing, but once retries
  are exhausted the message is dropped — there is no dead-letter queue.
