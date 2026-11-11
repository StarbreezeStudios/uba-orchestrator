# UBA Orchestrator

Standalone orchestration components for Unreal Build Accelerator. The repository contains custom code only; Unreal Engine and game source remain in the Perforce integration workspace.

## Repository layout

* `orchestrator/`: central HTTP service.
* `helper-agent/`: lightweight service running on helper machines.
* `coordinator-bridge/`: native UBA coordinator bridge.
* `tests/`: unit and integration tests.
* `docs/`: architecture and integration notes.
* `scripts/`: local and deployment helpers.

## Local Python setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r orchestrator/requirements.txt
PYTHONPATH=orchestrator python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

The service stores state in `orchestrator.db` by default. Set `UBA_ORCHESTRATOR_DB` to choose another path. The Docker Compose deployment sets this to `/var/lib/uba-orchestrator/orchestrator.db` and persists that directory in the `orchestrator-data` volume.

Run the standard-library test suite with:

```bash
PYTHONPATH=orchestrator python -m unittest discover -s tests -v
```

The native bridge is compiled later from the UE 5.6/UBA Perforce integration workspace and packaged beside the UBA binaries.

See [docs/mvp-runbook.md](docs/mvp-runbook.md) for the two-machine startup sequence.

For developer workstation trials, see [Build with UBA from Visual Studio instead of XGE](docs/visual-studio-uba-guide.md).

When the service is running, open `/ui` for the local operations dashboard. It shows registered helpers and active initiator leases; the tables refresh every three seconds.

Runtime state is authoritative in memory. One background writer persists changed records to SQLite every five seconds; requests never wait for SQLite after startup. Acknowledged changes, including administrative settings, may be lost if the process stops before persistence succeeds. Restart loads the last saved records, expires previous leases, and requires fresh helper registration or heartbeat before allocation.

Run exactly one orchestrator process/worker. A nonblocking lock on `<database>.lock` rejects another coordinator using the same database path; this is not distributed leader election. Existing history is loaded once into a separate in-memory archive so historical requests also avoid disk I/O. SQLite history is not pruned, and archive memory grows with retained history. Pending writes are coalesced by record, with at most 1,000 pending terminal lease records; excess terminal persistence is discarded while live assignments remain protected. `/api/v1/health` continues returning `status: ok` for runtime liveness and adds `persistence` diagnostics (`pending`, `pending_leases`, `last_success`, `error`, `dropped_history`, `enabled`). Monitor these fields for persistence lag or loss. Shutdown attempts a final save for at most five seconds. Startup still needs readable/writable SQLite; this change does not protect against a stalled VM, filesystem, or logging sink. See [persistence validation](docs/persistence-validation.md) for guarantees, limitations and local fault-injection tests.

The Jenkins helper deployment installs `UbaOrchestratorHelper` as a machine-level Scheduled Task that starts at Windows boot and restarts the helper supervisor after failures.
