# Two-Machine MVP Runbook

## Start the orchestrator

On the coordinator machine:

```text
python -m venv .venv
.venv\\Scripts\\activate
python -m pip install -r orchestrator\\requirements.txt
python -m uvicorn app.main:app --app-dir orchestrator --host 0.0.0.0 --port 8080
```

The service must be reachable from the helper and initiator on TCP 8080.

## Start one helper

On the Windows helper:

```text
python helper-agent\\agent.py --orchestrator http://<ORCHESTRATOR>:8080 --uba-agent C:\\Path\\To\\UbaAgent.exe --address <HELPER_IP> --listen-port 1346
```

The helper agent registers itself and waits for a lease. When assigned, it launches `UbaAgent.exe -listen=1346` and writes stdout/stderr under `logs/`.

## Configure the initiator

Set these environment variables before starting UBA:

```text
UBA_ORCHESTRATOR_URL=http://<ORCHESTRATOR>:8080
UBA_INITIATOR_ADDRESS=<INITIATOR_IP>
UBA_INITIATOR_PORT=1345
```

Start UBA with `-coordinator=Orchestrator`. The bridge must be deployed as `UbaCoordinatorOrchestrator.dll` beside the UBA binaries. The initiator's UBA network server must listen on TCP 1345.

## Current MVP boundaries

This path supports one initiator and one or more idle helpers, no authentication, in-memory leases, and one UBA agent per helper. The native bridge still needs to be compiled and tested in the UE 5.6 Perforce workspace before Jenkins can use it for a real Unreal build. Follow `docs/integration.md` for the source-to-Engine mapping; do not copy the Git repository as one nested source directory.
