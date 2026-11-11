# Incremental persistence validation

## Behavior

Each transaction retains `BEGIN IMMEDIATE` and reloads helpers plus indexed nonterminal leases so separate SQLite connections cannot allocate the same capacity. The store compares this bounded working set against its transaction snapshot and writes only changed rows. Lease membership is rewritten only when that lease's membership changes. Read paths still reconcile expired state, but write nothing when no state has changed.

Terminal lease records and memberships remain in SQLite. Historical lookup, heartbeat and release load the requested lease by primary key. They do not populate the routine working set with the full history. Duplicate-helper registration removes only that helper's historical membership references, preserving existing behavior. Restart reconciliation and rollback semantics are retained.

The existing database schema receives two indexes on startup: a partial index for nonterminal leases and an index for membership cleanup by helper ID. The first startup builds these indexes once; no data purge or separate migration command is required. The service still assumes one running orchestrator instance.

## Reproduce

Use the existing virtual environment and a consistent, local SQLite snapshot. Do not copy only the database file from a running WAL database; use SQLite's backup API. Do not run this load against production.

```bash
.venv/bin/python -m unittest discover -s tests -p test_persistence.py -v
.venv/bin/python scripts/validate-persistence.py \
  --database /path/to/local-snapshot.db \
  --clients 64 --iterations 8 --output /tmp/uba-http-results.json
```

The validator opens the input read-only and uses SQLite backup to create a disposable database. Only in that copy, it disables original helpers and registers synthetic loopback helpers. It starts Uvicorn on `127.0.0.1` and exercises helper heartbeats, allocation, lease heartbeat/read/release, dashboard reads, health and UI requests. It stops its local server and checks history digests, SQLite integrity, foreign keys, assignment exclusion and unexpected lease expiry. The input database is never passed to the server. No new dependencies are required beyond the service requirements.

To compare another implementation, pass `--app-dir /path/to/other/orchestrator`. Capacity-conflict responses to allocation are expected; other HTTP errors and two-second request timeouts fail validation. Timing is reported, not used as a machine-specific unit-test assertion.

## Results: 2026-11-11

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
