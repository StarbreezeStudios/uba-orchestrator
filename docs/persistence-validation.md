# Background persistence validation

## Behavior and durability contract

One coordinator owns runtime state in memory. Each operation uses a short process-local lock and an in-memory rollback snapshot of helpers and active leases. SQLite is never accessed by request handlers after startup. Historical leases are loaded once into a separate dictionary and are looked up by ID, so routine operations do not scan the complete archive. The full historical archive remains in RAM and grows with retained history; this design trades memory and startup time for disk-independent request handling.

A single daemon writer wakes every five seconds. It takes a coherent copy of helpers and pending changed leases under the runtime lock, releases that lock, and writes one SQLite transaction. Repeated changes to a lease coalesce into its latest version. A successful commit acknowledges only the captured versions; newer concurrent changes remain pending. Failed commits roll back SQLite and leave runtime state and pending changes intact for retry. Lease memberships are written only when changed. Duplicate-helper cleanup updates both in-memory history and persisted membership references.

Pending terminal lease writes are capped at 1,000 records, in addition to active lease updates. Overflow drops the oldest pending terminal writes and increments `dropped_history`; active assignments are never dropped by this limit. There can also be one already captured batch in flight. Dropped history may remain absent or stale on disk. Existing SQLite history is not pruned. This is best-effort historical durability, not a durable acknowledgment per request.

On startup the existing schema/index migration and history loading still require SQLite. All previous assignments expire and helpers remain offline/disabled until fresh registration or heartbeat, even if an administrator toggles enabled state. Shutdown requests a final snapshot and waits at most five seconds. If I/O remains blocked, shutdown reports unsaved state and may lose all updates since the last successful save, including administrative settings. The background writer retains database ownership until it actually finishes or the process exits.

Exactly one process/worker must coordinate the helpers. A nonblocking advisory lock on `<database>.lock` rejects another coordinator using that same file path. Do not delete that file while the process runs. This is not distributed leadership or coordination between independent databases. `BEGIN IMMEDIATE` protects background SQLite transactions; it no longer arbitrates assignments between processes.

`GET /api/v1/health` remains an HTTP 200 runtime-liveness response with `status: ok` and adds:

- `persistence.enabled`: whether a database is configured.
- `persistence.pending`: changes remain unacknowledged by the writer.
- `persistence.pending_leases`: coalesced pending lease records.
- `persistence.last_success`: UTC timestamp of the last successful runtime snapshot, or null.
- `persistence.error`: last write exception class, cleared by a successful retry.
- `persistence.dropped_history`: cumulative terminal writes discarded by the pending limit.

A stalled commit need not raise an exception, so monitor pending work together with last-success age. A healthy runtime response does not promise durable history. This change cannot protect against an unresponsive VM/OS, blocked logging output, or storage failure during startup.

## Reproduce safely

Use the existing virtual environment and a consistent local SQLite snapshot. Do not copy only the database file from a running WAL database; use SQLite's backup API. Never run these loads or fault injections against production.

```bash
.venv/bin/python -B -m unittest discover -s tests -v
.venv/bin/python -B scripts/validate-persistence.py \
  --database /path/to/local-snapshot.db --clients 64 --iterations 32 \
  --storage-mode normal --output /tmp/uba-normal.json
.venv/bin/python -B scripts/validate-persistence.py \
  --database /path/to/local-snapshot.db --clients 64 --iterations 32 \
  --storage-mode slow --commit-delay 2 --output /tmp/uba-slow.json
.venv/bin/python -B scripts/validate-persistence.py \
  --database /path/to/local-snapshot.db --clients 64 --iterations 32 \
  --storage-mode blocked --output /tmp/uba-blocked.json
```

The validator opens the input read-only and creates a disposable copy using SQLite backup. It disables original helpers only in that copy and registers synthetic loopback helpers. A temporary harness starts Uvicorn on 127.0.0.1. Slow mode delays each commit by two seconds; blocked mode holds commit until all HTTP traffic finishes. The harness verifies that the fault was entered before launching clients, releases the fault before shutdown, and checks that all successful allocations were persisted as released afterward. Fault injection code exists only in the temporary harness, not in the application.

Each run exercises helper heartbeats, allocation, lease heartbeat/read/release, diagnostics, health and UI. Capacity conflicts are allowed; other HTTP errors and two-second timeouts fail validation. History digests, integrity, foreign keys, final assignment overlap and unexpected expiry are checked. Unit tests additionally verify allocation exclusion while a writer is blocked, real SQLite write-lock contention, transaction rollback/retry, preservation of newer changes, pending-history limits, restart confirmation, periodic writes without explicit flush, and bounded shutdown. A SQLite authorizer rejects any runtime SQL access in the disk-independence regression.

The simulated commit stalls validate application isolation, not NAS kernel behavior or production capacity. Local timing is reported rather than asserted as a machine-specific unit-test threshold.

## Background-persistence results: 2026-11-11

The background-persistence implementation passed all **35 unit/regression tests**. The same local production snapshot contained 11,355 leases (11,354 terminal) and 22 helpers. Each HTTP scenario used its own disposable copy, 64 clients and 32 iterations per client:

| Scenario | Requests | HTTP workload (s) | Health p95 (ms) | Helper heartbeat p95 (ms) | Maximum request (ms) | Errors/timeouts |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Normal local disk | 3,584 | 3.120 | 53.73 | 62.28 | 84.12 | 0 |
| 2-second commit delay | 3,584 | 3.000 | 44.02 | 53.81 | 85.24 | 0 |
| Commit blocked throughout HTTP traffic | 3,584 | 2.992 | 42.40 | 56.35 | 82.29 | 0 |

Across all three runs: 10,752 requests, zero errors/timeouts, no unexpected expiry, intact original terminal-history/membership digests, valid SQLite integrity/foreign keys, and no final overlapping assignments. Each run created 512 leases; all 512 were persisted as released after fault release and shutdown. In the blocked scenario, persistence remained pending with no successful runtime snapshot throughout the workload, while HTTP continued to respond. The fault was entered before traffic and released only afterward. Uvicorn returned SIGTERM after the requested shutdown; the validator accepts that normal signal outcome and separately verifies final database contents.

The input snapshot SHA-256 was unchanged before and after validation: `bb1535c0b30c91b3653c61438346bcdd66aa9a90ead62ebda60106e00dcb98f5`.

These results isolate the application from simulated SQLite commit delays and a held commit. They are not a real NAS fault test, production benchmark, or Unreal build validation. No deployment or production traffic was performed.

## Busy-helper reactivation follow-up: 2026-11-11

Re-enabling a leased helper now preserves its lease and restores reserved/active state according to readiness. A deterministic regression first reproduced the old idle-state bug in four cases (pending/active lease, with/without a preceding disable). The corrected behavior prevents a replacement allocation until the original lease is released. A second regression runs 16 concurrent reactivation/allocation callers and verifies that only the original lease owns the helper.

All 23 Store tests and the complete **37-test suite** passed after this fix (full suite: 1.985 seconds). The full suite includes the background persistence failure, blocked-writer, restart and shutdown regressions. `git diff --check` passed. The earlier HTTP load runs above were not repeated for this focused state-transition fix. No deployment was performed.

## Earlier incremental-persistence baseline: 2026-11-11

A consistent read-only backup of the production history contained 11,355 leases (11,354 terminal) and 22 helpers. Both versions used independent temporary copies of this same snapshot, 16 synthetic helpers, 64 concurrent clients and eight iterations per client. Successful allocations trigger three additional lifecycle requests, so completed request totals differ.

| Measurement | Previous implementation | Incremental persistence |
| --- | ---: | ---: |
| HTTP requests attempted | 512 | 896 |
| Request timeouts | 474 | 0 |
| Workload elapsed | 16.04 s | 1.52 s |
| Health p95 | 2,001.80 ms | 21.51 ms |
| Helper heartbeat p95 | 2,006.30 ms | 88.07 ms |
| Allocation p95 | 2,007.09 ms | 140.20 ms |
| Unexpected validation lease expirations | 4 | 0 |
| Terminal history and memberships preserved | Yes | Yes |
| Integrity / foreign keys / overlapping assignments | Pass | Pass |

An extended run of the final repository code used 64 clients and 32 iterations: 3,584 requests in 4.90 seconds, zero errors, health p95 20.45 ms, and maximum latency across endpoints 167.26 ms. History, integrity, foreign keys and exclusive assignments passed again, with no unexpected lease expirations. The local runtime was Python 3.14.7, FastAPI 0.139.0, Uvicorn 0.51.0 and AnyIO 4.14.2.

These are local measurements, not production capacity guarantees. The workload reproduces the backlog under the previous implementation and validates the correction with the accumulated history. Production was not restarted, modified or deployed.

Seven new regression tests use 12,000 terminal leases and cover bounded SQL work, row-level write counts, historical access, restart, rollback after partial writes, failed reload cleanup and allocation exclusion across four connections with 48 concurrent callers.

The final complete suite passes all 33 tests. The two pre-existing test failures found during baseline validation were corrected:

- `test_existing_enabled_column_is_preserved_on_restart_and_save` now uses the `enabled` column already created by the schema instead of adding it again. Its restart and persistence assertions are unchanged.
- `test_capacity_failure_timeout_recovery_and_initiator_expiry` now verifies both `helper_offline` and the subsequent `lease_expired` event in newest-first order.

Both corrected tests passed individually, then `.venv/bin/python -B -m unittest discover -s tests -v` passed all 33 tests in 0.941 seconds. These test corrections do not change service behavior. The historical duplicate-helper test verifies the public lease view instead of requiring historical leases to remain in the internal working dictionary.
