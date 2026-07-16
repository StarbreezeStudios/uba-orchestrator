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

Run the standard-library test suite with:

```bash
PYTHONPATH=orchestrator python -m unittest discover -s tests -v
```

The native bridge is compiled later from the UE 5.6/UBA Perforce integration workspace and packaged beside the UBA binaries.

See [docs/mvp-runbook.md](docs/mvp-runbook.md) for the two-machine startup sequence.
