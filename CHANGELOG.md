# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- OLAGSA no longer gets its solver process OOM-killed on long periods. The
  allocation is now a vector times one production row, compiled without DPP, so
  memory grows linearly with participants × timesteps: 20 meters over 92 days of
  quarter-hours peaks at 0.5 GB instead of being killed at 2 GB, and 31 days
  drops from 1.3 GB to 0.26 GB. Keys are unchanged up to float rounding: on
  full runs with a fixed seed, the same keys came out with percentages within
  4e-12 and surplus totals within 5e-10 kWh.
- A generation that runs longer than 10 minutes is no longer solved twice. The
  worker tells JetStream every minute that the generation is still in progress,
  including while it waits for a free solver process, so the message is no
  longer redelivered at `ack_wait` and queued behind its own first run.
- A solver process that is OOM-killed or crashes no longer fails the generation
  at once as `algorithm_failed: solver pool broken`, contrary to what the code
  documented. The crash breaks the whole pool, so every generation solving at
  that moment is delivered again; a crash on a generation's third delivery or
  later marks it failed with `solver_crashed (likely out of memory)`.
- brute_force no longer runs out of memory with 3 iterations. Its result tree
  kept every iteration of every candidate key with its full time series (2,574
  iterations for 1,782 keys); a node now keeps only the figures its key reports.
  With 20 meters, 3 iterations over 92 days of quarter-hours peak at 0.12 GB,
  where 7 days used to exceed 1.5 GB, and 2 iterations over 92 days drop from
  1.3 GB to 0.1 GB. The keys are identical.
- The worker's JetStream consumer settings now reach a consumer an earlier
  build created. nats-py applies `ack_wait` and `max_ack_pending` only when it
  creates the durable, so `max_ack_pending` (the solver pool size) never took
  effect on an existing deployment: the dev consumers, created in May, still
  let JetStream hand out 1000 generations at once. The worker now updates the
  durable in place when it starts and logs the old and new values.

### Added

- A limit of 400,000 participants × timesteps per generation (error 2024,
  translated in en/fr/de/nl), checked by the CRM preview and `POST /from-crm`,
  by `POST /` from a count of the uploaded file's rows and columns, and by the
  worker on the parsed data.
- A "Resource sizing" section in the README.

### Changed

- The test workflow installs `requirements/all.txt`, like the lint workflow, so
  tests that need the worker's numerical stack (cvxpy, openpyxl) run in CI.
