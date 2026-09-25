# Running the Project — Command Reference

Practical day-to-day commands for this repo (Docker Compose based). All commands
assume you're in the repo root: `cd "/home/stark/Work Space/DMS"`.

Verified working as of 2026-09-03 — all 9 services up, backend health check
passing, full backend test suite green (275/275).

---

## First-time setup

```bash
cp backend/.env.example backend/.env
# edit backend/.env — fill in passwords, JWT secret, and AI provider API keys
docker compose up -d --build
```

---

## Everyday start / stop

```bash
# Start everything (detached)
docker compose up -d

# Stop everything (keeps containers/volumes, just stops them)
docker compose stop

# Stop and remove containers (volumes/data persist)
docker compose down

# Check what's running
docker compose ps
```

---

## After changing code or config

Which command you need depends on what changed:

```bash
# Backend Python code changes — bind-mounted, picked up automatically.
# Only restart if the process itself needs a fresh start (rare):
docker compose restart backend worker

# Changed backend/.env — restart does NOT reload env vars.
# You must recreate the containers:
docker compose up -d --force-recreate backend worker

# Frontend code changes — it's a production build, so a plain restart
# rebuilds it (takes ~25-100s):
docker compose restart frontend
```

---

## Health checks / is it actually working

```bash
# Backend health (checks DB, Redis, embeddings provider)
curl http://localhost:8000/api/v1/health

# Frontend responding
curl -o /dev/null -w "%{http_code}\n" http://localhost:3000/login

# Watch logs for a specific service
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs -f worker
```

---

## Running tests

```bash
# Full backend test suite
docker compose exec -T backend python -m pytest tests/ -q --no-cov

# One test file
docker compose exec -T backend python -m pytest tests/test_table_stitch.py -q --no-cov

# Filter by keyword
docker compose exec -T backend python -m pytest tests/ -q --no-cov -k "stitch or spread"
```

---

## Access points

| Service | URL |
|---|---|
| App (frontend) | http://localhost:3000 |
| Backend API docs (Swagger) | http://localhost:8000/api/docs |
| Backend health | http://localhost:8000/api/v1/health |
| MinIO console (object storage) | http://localhost:9001 |
| Flower (Celery task monitor) | http://localhost:5555 |

**Default seeded login:**
- Email: `admin@example.com`
- Password: `changeme`

---

## Running a one-off Python script inside the backend container

Useful for debugging/testing pipeline code directly against the real DB:

```bash
docker compose cp my_script.py backend:/tmp/my_script.py
docker compose exec -T backend sh -c "PYTHONPATH=/app python3 /tmp/my_script.py"
```

---

## Database access

```bash
docker compose exec -T postgres psql -U docsearch -d docsearch
```

---

## Troubleshooting

- **Container up but app broken**: check `docker compose logs <service>` first —
  most failures show up there immediately (bad env var, migration failure, etc.)
- **Changed `.env` but nothing changed**: you needed `--force-recreate`, a plain
  `restart` does not reload `env_file` values.
- **Frontend build fails on a font/CDN fetch**: usually transient — just retry
  `docker compose restart frontend` once.
