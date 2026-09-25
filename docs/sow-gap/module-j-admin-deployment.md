# Module (j) — Admin & Deployment

SoW 3.10 · Status as of 2026-09-08, commit `e1af8ff` · See [ROADMAP.md](ROADMAP.md) for the whole-system picture.

- [ ] **T81** — Licensing enforcement — **blocked on A5**
  Plan definitions and signed on-prem licence-file verification both exist (migration `0033`). Placeholder pending the licensing model sign-off — see `docs/decisions/T81_licensing_assumptions.md`.

- [ ] **T90** — Local model provider on a 24GB GPU — **blocked on A2**
  Local VLM, OCR and embeddings/rerank all exist. No local LLM, so `airgapped.py` correctly refuses to start on those surfaces and names T90 as the reason, rather than silently falling back to a hosted API.

- [x] **T91** — API-versus-local toggle that fails closed
  Evidence: `enforce_local()` raises rather than silently falling back; `egress_guard.py` blocks known external AI hosts at the transport layer.

- [x] **T92** — Egress-zero verification and CI coverage
  Evidence: a CI job blocks all outbound traffic except loopback at the OS level, proves the block holds by failing to connect, then runs the guard tests.

- [x] **T93** — Helm charts and air-gapped install runbook
  Evidence: full chart with an air-gapped values file, `docs/AIRGAPPED_INSTALL_RUNBOOK.md`. **Was "never executed by anyone who didn't write it" as of 04-Sep — that was always its exit condition.** Fixed this pass: run for real via a local `kind` cluster, 4 real bugs found and fixed (missing `APP_POSTGRES_URL` wiring, unwired `DATALAB_API_KEY`, backend OOMKilled under the real memory limit, missing HuggingFace-cache PVC), plus documented the Postgres-Helm-hooks upgrade-restart behaviour that isn't worth fixing.

- [x] **T94** — Project / Collection container level
  Evidence: closed by D-1 — recursive folders stay, scoping by department RBAC.

- [ ] **T97** — Performance pass on real corpus volumes — **blocked on A1**
  `perf_benchmark.py` drives the real API and Celery pipeline rather than micro-benchmarking internals, and says openly it's a starting baseline, not a validated one — real volumes need the reference corpus.

**Open items in this module:** 3, all external (T81, T90, T97 — see [ROADMAP.md](ROADMAP.md#the-10-open-tasks-grouped-by-what-blocks-them)).
