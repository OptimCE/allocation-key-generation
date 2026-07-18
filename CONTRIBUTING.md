# Contributing to OptimCE — Allocation Key Generation

Thank you for your interest in contributing! Issues and pull requests are
welcome from everyone. By participating in this project, you agree to abide by
our [Code of Conduct](CODE_OF_CONDUCT.md).

## Where to Contribute

This repository holds the **allocation key generation** service — one of several
services that make up the OptimCE platform under the
[OptimCE organization](https://github.com/OptimCE). It is normally developed as
part of the [development monorepo](https://github.com/OptimCE/monorepo), which
runs the full stack (gateway, authentication, databases, messaging, object
storage) with Docker Compose, but it can also be run standalone for focused work
on the service itself.

If your change concerns another service (the CRM, billing, document generation,
etc.), please open your issue or pull request in that service's repository.

## Setting Up a Development Environment

The service targets **Python 3.12**. It needs a PostgreSQL database, and — for
the full flow — a NATS server and an S3-compatible object store (MinIO). The
easiest way to get all of those is to run the
[monorepo](https://github.com/OptimCE/monorepo) dev stack.

To work on the service on its own:

```bash
git clone https://github.com/OptimCE/allocation-key-generation.git
cd allocation-key-generation

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements/development.txt

cp .env.exemple .env.local       # then edit the values for your machine
```

See the [README](README.md) for the full configuration reference and for how to
run the API and the worker.

## Reporting Bugs and Suggesting Features

Open a
[GitHub issue](https://github.com/OptimCE/allocation-key-generation/issues). For
bugs, include what you did, what you expected, and what happened instead — logs
and reproduction steps help a lot.

For security vulnerabilities, **do not open a public issue**; follow the
[security policy](SECURITY.md) instead.

## Submitting Pull Requests

1. Fork the repository and create a feature branch from `main`.
2. Make your changes. Keep each pull request focused on a single topic.
3. Make sure the checks pass locally:

   ```bash
   ruff check .
   ruff format --check .
   mypy .
   pytest
   ```

   (Tool configuration lives in `pyproject.toml`. The test suite starts a
   throwaway PostgreSQL container, so Docker needs to be available.)
4. Open a pull request against `main`, describing **what** you changed and
   **why**.

Small documentation fixes are welcome as direct pull requests; for larger
changes, opening an issue first to discuss the approach can save you time.

## Commit Messages

Use short, imperative commit messages, preferably following the
[Conventional Commits](https://www.conventionalcommits.org/) style used across
OptimCE:

```
feat: add a new allocation algorithm to the registry
fix: correct surplus rounding in the brute-force generator
chore: bump nats-py to 2.14.0
docs: document the generation endpoints
```

## License

OptimCE is licensed under the [Apache License 2.0](LICENSE). By contributing,
you agree that your contributions will be licensed under the same license.
