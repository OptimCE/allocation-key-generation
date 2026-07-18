<p align="center">
  <img src="docs/logo.svg" alt="OptimCE logo" width="160">
</p>

# OptimCE — Allocation Key Generation

[![Website](https://img.shields.io/badge/Website-optimce.be-2e7d32.svg)](https://www.optimce.be/en/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![en](https://img.shields.io/badge/lang-en-43a047.svg)](README.md)
[![fr](https://img.shields.io/badge/lang-fr-lightgrey.svg)](docs/README.fr.md)
[![de](https://img.shields.io/badge/lang-de-lightgrey.svg)](docs/README.de.md)
[![nl](https://img.shields.io/badge/lang-nl-lightgrey.svg)](docs/README.nl.md)

The **allocation key generation** service computes energy-sharing *allocation
keys* (French *clés de répartition*) for renewable energy communities. Given a
community's measurement data — one consumption time series per member plus a
shared injection (production) series — it runs an optimization algorithm to
determine how the shared production should be distributed among members, and
produces candidate allocation keys that a user can review and save.

This service is one part of the OptimCE platform. It is developed within the
[OptimCE development monorepo](https://github.com/OptimCE/monorepo), which runs
the full stack locally; the rest of the platform is available under the
[OptimCE organization](https://github.com/OptimCE). To learn more about the
project, visit [www.optimce.be](https://www.optimce.be/en/).

## Algorithms

Allocation algorithms are **pluggable**: each lives under
`algorithms/algorithms_implemented/` and registers itself in an auto-discovered
registry, exposing lightweight metadata (used by the API) separately from its
heavy implementation (used by the worker). Two algorithms ship today:

| Algorithm | Approach | Notes |
|---|---|---|
| `olagsa` | Genetic algorithm with a convex warm-start (Linear Optimization and Genetic Algorithm with Atypical Speciation) | Tunable hyperparameters (population size, generations, crossover/mutation rates, …) |
| `brute_force` | Exhaustive enumeration of allocation keys | Bounded to a small number of iterations |

Each algorithm declares a Pydantic input schema that is served as JSON Schema so
the frontend can render a matching dynamic form, with field labels localized per
request.

## Architecture

The service is split into two deployables that share one codebase:

- **API** (`main.py`, `api/`) — a FastAPI application. It accepts data uploads,
  validates them, stores the file, persists a generation record, and publishes a
  job on NATS. It also serves the read/list/delete endpoints and the algorithm
  catalog.
- **Worker** (`worker/`) — a long-running NATS JetStream consumer. It downloads
  the file, parses it into matrices, runs the CPU-bound algorithm in a process
  pool, and writes the results (or a recorded failure) back to the database.

Key technologies:

- **FastAPI** + **Uvicorn** (Python 3.12)
- **SQLAlchemy** (async) + **asyncpg** over **PostgreSQL** — two databases: the
  CRM database (read) and the service's own database
- **Pydantic** / **pydantic-settings** for validation and configuration
- **NATS** (JetStream) for API → worker messaging
- **S3-compatible object storage** (MinIO in development) for uploaded files
- **cvxpy**, **NumPy**, **pandas** for the numerical/optimization work (worker)
- **OpenTelemetry** for tracing, metrics, and logs
- **i18n** with locales for English, French, German, and Dutch (`locales/`)

### Authentication

The service trusts an upstream API gateway (KrakenD, in front of Keycloak): it
does not verify tokens itself. Gateway-supplied headers identify the user and
their community, requests are scoped to a single community for multi-tenant
isolation, and access to the generation endpoints requires the community to have
the corresponding feature enabled. The service is not meant to be exposed
directly to the internet.

## API

All generation endpoints are served at the service root (the external path
prefix is added by the gateway). Interactive OpenAPI docs (`/docs`, `/redoc`,
`/openapi.json`) are exposed **only when `ENV=local`**.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | List generations for the caller's community (paginated) |
| `GET` | `/algorithms` | List available algorithms with localized metadata and input schemas |
| `GET` | `/algorithms/{algorithm_name}` | Get one algorithm's localized input schema |
| `GET` | `/key/{id_key}` | Get a single generated allocation key |
| `GET` | `/{id}` | List the keys produced by a generation (paginated) |
| `POST` | `/` | Start a generation (`multipart/form-data`: `file`, `name`, `injection_name`, `algorithm_name`, `inputs`) |
| `POST` | `/save` | Save a generated key back to the CRM |
| `DELETE` | `/generation/{id_generation}` | Delete an entire generation |
| `DELETE` | `/key/{id_key}` | Delete a single key |

Health probes live under `/health`: `GET /health/liveness`,
`GET /health/readiness` (checks the database and NATS), and `GET /health/health`.

## Project Structure

```
allocation-key-generation/
├── main.py            # FastAPI app: middleware, routers, lifespan (NATS + tracing)
├── api/               # HTTP layer (generation endpoints + health probes)
├── algorithms/        # Pluggable algorithm framework + implemented algorithms
├── core/              # Cross-cutting infrastructure (config, db, queue, storage,
│                      #   security, middleware, i18n, tracing, errors)
├── worker/            # Background NATS worker (dispatcher, solver pool, persistence)
├── shared/            # Models, constants, and helpers used by API and worker
├── locales/           # i18n message catalogs (en, fr, de, nl)
├── scripts/           # export_openapi.py + sql/schema.sql (local DB DDL)
├── tests/             # pytest suites and fixtures
├── requirements/      # base / api / worker / development / testing / all
└── Dockerfile*        # API (dev + production) and worker images
```

## Getting Started

### Prerequisites

- **Python 3.12**
- **PostgreSQL**
- **Docker** (used by the test suite; and the simplest way to obtain NATS and
  MinIO for the full flow)
- For the complete pipeline: a **NATS** server and an **S3-compatible** object
  store. Running the [monorepo](https://github.com/OptimCE/monorepo) dev stack
  provides all of these.

### Installation

```bash
git clone https://github.com/OptimCE/allocation-key-generation.git
cd allocation-key-generation

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements/development.txt

cp .env.exemple .env.local       # then edit the values
```

### Configuration

Configuration is read from a `.env.<ENV>` file selected by the `ENV` variable
(e.g. `ENV=local` loads `.env.local`). The authoritative list of settings is in
`core/config.py`; example files are provided for each environment
(`.env.exemple` for local, `.env.staging.exemple`, `.env.production.exemple`, and
`.env.test`).

| Variable | Description |
|---|---|
| `ENV` | `local`, `test`, `staging`, or `production` |
| `CRM_DATABASE_URL` | Async DSN for the CRM database (`postgresql+asyncpg://…`) |
| `LOCAL_DATABASE_URL` | Async DSN for the service's own database |
| `NATS_URL` | NATS server URL (required outside local/test) |
| `STORAGE_ENDPOINT` | S3-compatible endpoint (e.g. MinIO); required outside local/test |
| `STORAGE_BUCKET` | Object storage bucket (default `crm-files`) |
| `STORAGE_ACCESS_KEY` / `STORAGE_SECRET_KEY` | Object storage credentials |
| `STORAGE_REGION` | Object storage region (default `us-east-1`) |
| `ALLOW_ORIGIN` | Comma-separated CORS origins; wildcards are rejected outside local |
| `LOGGING_TOKEN`, `LOGGING_TRACES_URL`, `LOGGING_LOGS_URL`, `LOGGING_METRICS_URL` | OpenTelemetry exporter configuration (required in production) |

Optional connection-pool settings (`*_DB_POOL_SIZE`, `*_DB_MAX_OVERFLOW`,
`*_DB_POOL_RECYCLE`, `*_DB_POOL_TIMEOUT`, `*_DB_SSL`) have sensible defaults.
Pydantic validates the configuration on startup and refuses to start if a
setting required for the current environment is missing.

## Running

### API

```bash
uvicorn main:app --reload --port 8002
```

Then open <http://localhost:8002/docs> (available because `ENV=local`).

### Worker

```bash
python -m worker.main
```

### With Docker

```bash
# development API image (hot-reload)
docker build -t allocation-key-generation .

# worker image
docker build -f Dockerfile.worker -t allocation-key-generation-worker .
```

`Dockerfile.production` builds the production API image. In the monorepo dev
stack the API is reachable on host port **8002**.

## Testing & Quality

```bash
pytest            # test suite (starts a throwaway PostgreSQL container via Docker)
ruff check .      # lint
ruff format --check .
mypy .            # type checking
```

## Contributing

Contributions are welcome! Please read the
[contributing guidelines](CONTRIBUTING.md) and our
[Code of Conduct](CODE_OF_CONDUCT.md) before opening an issue or pull request.

## Security

To report a security vulnerability, please follow the
[security policy](SECURITY.md) — do not open a public issue.

## License

This project is licensed under the [Apache License 2.0](LICENSE).
