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
- A generation whose solve runs the worker container out of memory can no
  longer be redelivered forever. When the container ran out of memory, the
  kernel could kill the worker's main process rather than a solver process. The
  in-progress acks then stopped, and the message came back every 10 minutes
  with no limit, because the 3-delivery cap only counts solver crashes. Each
  solver process now sets its own `oom_score_adj` to 1000 when it starts (as
  simulation-key's worker does), so the kernel kills a solver first and the cap
  applies. A solver process that cannot write `/proc/self/oom_score_adj` logs a
  warning on Linux.
- A generation whose deliveries keep ending without a result is no longer
  delivered forever. A transient failure (object storage or the CRM database
  unreachable, a failed write) was retried every 30 seconds without limit, and
  a worker that died or was restarted mid-run got the generation back every 10
  minutes, also without limit. The 10th delivery now marks the generation failed
  with `delivery_limit_reached` without running it again, so an outage longer
  than about four and a half minutes fails the generations waiting on it. As a
  backstop, the `worker-olagsa` and `worker-brute_force` consumers now have
  `max_deliver` 11 instead of unlimited, set in place on existing consumers when
  the worker starts.

### Added

- A limit of 400,000 participants × timesteps per generation (error 2024,
  translated in en/fr/de/nl), checked by the CRM preview and `POST /from-crm`,
  by `POST /` from a count of the uploaded file's rows and columns, and by the
  worker on the parsed data.
- A "Resource sizing" section in the README.

### Changed

- The test workflow installs `requirements/all.txt`, like the lint workflow, so
  tests that need the worker's numerical stack (cvxpy, openpyxl) run in CI.
