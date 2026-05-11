# Production Roadmap

Punch list of work required before this service can be considered production-ready. Items are grouped by severity. Audit performed 2026-05-08.

## Hard blockers — ship-stopping

- [ ] **Register the service in KrakenD.** No entry exists in `monorepo/krakend/config/root.yaml` or `krakend-builder.yaml`. The frontend cannot reach this service through the gateway. Add the upstream and route at minimum: `GET /subscriptions`, `GET /subscribe`, `GET /unsubscribe`, all `/generation/**` endpoints. Forward `Authorization` and the `x-user-id` / `x-community-id` / `x-user-role` headers exactly as the existing `crm-backend` routes do.
- [ ] **Include the service in `docker-compose.dev.yml`.** Add the API container (port 8000 dev / 8002 prod) and the worker container (separate process, see below) on the same network as KrakenD and Postgres.
- [x] **Enforce required env vars at startup.** `core/config.py:45` defaults `NATS_URL: str = ""` with no validator. Add a Pydantic validator that rejects empty `NATS_URL`, `CRM_DATABASE_URL`, and `LOCAL_DATABASE_URL` when `ENV != local`. Today the API starts "healthy" with no broker, accepts `POST /generation/`, and generations silently queue forever.
- [x] **Stand up CI.** Three workflows under `.github/workflows/`:
  - `lint.yml` — `ruff check .`, `ruff format --check .`, `mypy .`
  - `test.yml` — `pytest` against a Postgres 18 service container (`test_db_be` on 5433), `ENV=test` so `core/config.Settings` loads `.env.test`
  - `build.yml` — matrix-builds `Dockerfile.production` (suffix `-prod`) and `Dockerfile.worker` (suffix `-worker`), pushes both to GHCR on `main`/tag pushes (PR runs verify-only, no push)
  - All three trigger on push to `main` and PRs against `main`.
- [x] **Deploy the worker.** `Dockerfile.production` runs only the API (`uvicorn main:app`). The worker entrypoint is `worker/main.py` and must be supervised as a separate container/process. Add a `Dockerfile.worker` (or a CMD override on the existing image) and wire it into the prod orchestrator.

## Important — fix before first real traffic

- [x] **Populate `locales/fr.json`.** Currently `{}`. All user-facing errors return raw keys (`ERRORS.SUBSCRIPTION.NOT_SUBSCRIBED`). Add at least the auth + subscription + generation error keys. Add `en.json` if the frontend supports English.
- [x] **Migration tooling.** Covered externally — not this service's responsibility.
- [x] **Add tests for health endpoints.** `api/health/routes.py` exposes `/liveness`, `/readiness`, and a `/health` alias. The readiness probe actively checks DB + Redis + NATS. There are zero tests covering it; a regression in the probe would only surface in production via failed K8s liveness checks.
- [x] **Static analysis as part of CI.** `mypy` (with the official `pydantic.mypy` plugin) runs as a step in `lint.yml`. Config in `pyproject.toml` excludes `tests/`, `scripts/`, `.venv/`, and `_impl/` (legacy algorithm internals being ported from the former project). First-pass cleanup deleted three dead modules (`core/security/auth0.py`, `core/security/bearer.py`, `core/helpers/auth0/`), added missing Redis fields to `Settings`, fixed async generator return types, and migrated FastAPI routes to the modern `Annotated[T, Depends(...)]` pattern. Repo is `mypy`-clean (0 errors over 63 source files).

## Operational hardening

- [x] **Per-IP / per-user rate limiting.** Handled by KrakenD at the gateway layer — this service does not need its own request-rate limiter. `RequestLimitsMiddleware` continues to handle body size and per-request timeout locally.
- [x] **Structured request logging audit.** `RequestIdFilter` (`core/logging.py`) now stamps `request_id`, `user_id`, `community_id`, and `user_role` onto every log record — including the OTLP `LoggingHandler` shipping to Loki (filter attached in `core/tracing.py`). The 14+ in-service raise sites no longer need per-site logging; the central `error_exception_handler` (`core/errors/handlers.py`) emits the full context automatically.
- [x] **Observability beyond traces.** Application metrics live in `core/metrics.py` and flow through the existing OTLP → Mimir pipeline: `generations.created.total`, `generations.completed.total{status}`, `generation.duration.seconds{status}`, `worker.messages.total{outcome}`, `health.checks.total{component,ok}`, and an observable gauge `nats.queue.depth{algorithm}` fed by a 15 s background poller in `worker/main.py`.
- [x] **Lock down CORS in non-local.** `core/config.py:48` defaults `ALLOW_ORIGIN` to `*` for local. Validation rejects `*` in staging/prod (good), but make sure the prod env file is explicit (no implicit fallback) and document the allowed origins.
- [x] **Confirm worker idempotency under retry.** NATS JetStream redelivers on consumer failure. `worker/persistence.py` opens its own session per save and commits atomically — verify that retried events do not double-write generations or keys (likely needs an idempotency check on `generation_id` before `INSERT`).
- [x] **Graceful NATS reconnection.** If the broker goes down mid-flight, the worker should reconnect and resume; the API should fail `POST /generation/` fast (and not 500-loop) rather than silently buffering. Trace the `init_nats` reconnect strategy.

## Tests / coverage gaps

- [x] **Untested routes**: `/health/*` (3 endpoints, 0 tests).
- [x] **Algorithm registry**: `algorithms/autodiscover` runs at startup and logs failures per algorithm but does not raise. Add a test that fails CI if any registered algorithm has an invalid input schema or duplicate name.
- [x] **Worker failure paths**: confirm tests exist for algorithm-raises, malformed-event, and DB-unavailable scenarios.
- [x] **Multi-tenant isolation**: every read route should have an "across two communities" test (subscription routes already do; spot-check generation list/delete).

## Documentation

- [ ] **README**: add a real one. Onboarding currently relies on `CLAUDE.md` which is agent-oriented.
- [ ] **Migration runbook**: how to apply `scripts/sql/migrations/*.sql` to staging and prod. Until the runner exists, document the manual process.
- [ ] **Deployment runbook**: API + worker topology, env vars, secret sourcing, NATS stream creation (`streams.json`), KrakenD route registration.
- [ ] **i18n contract**: which error keys must exist in `locales/*.json`, and how missing keys behave (`core/i18n.py:42-43` falls back to the key — document this).

## Latent bugs (already noted, track to closure)

- [x] `errors.auth.AUTHORIZATION_MISSING` referenced but undefined — fixed in `shared/custom_errors.py:11`.
- [x] `/unsubscribe` enforced subscription only at service layer — now gated at the dependency layer (403 + code 1003).

## Suggested order

1. KrakenD + compose wiring (one PR each, ~1 day).
2. Pydantic validators on required env vars (small PR).
3. Bare-minimum CI: ruff + pytest (one PR).
4. Worker deployable + idempotency check (one PR).
5. Migration runner decision + implementation.
6. Populate i18n + add health tests + add static analysis to CI.
7. Rate limiting, metrics, runbooks.

Items 1–3 are roughly a day each. The rest is ~1–2 weeks of focused polish. Without 1 and 2 specifically, the frontend integration cannot work end-to-end.
