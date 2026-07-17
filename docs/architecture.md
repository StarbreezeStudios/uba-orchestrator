# UBA Orchestrator MVP Architecture

This document describes the first two-machine implementation for this checkout of Unreal Build Accelerator. The local tree is the standalone UBA source tree under `src/`; the Unreal Engine `Engine/Source/Programs/...` path and the Horde coordinator implementation are not present here.

## Components

* `coordinator-bridge/` is a small native coordinator DLL. It implements the exact `uba::Coordinator` interface and uses WinHTTP to call the Python service. The service URL is supplied through `UBA_ORCHESTRATOR_URL`; the existing host configuration maps its `Coordinator.Uri` value to `UE_HORDE_URL`, which the bridge also accepts for compatibility. `src/UbaCoordinatorOrchestrator/` is the UBT-discoverable module wrapper for this source.
* `orchestrator/` is a FastAPI service. The MVP keeps helpers and leases in memory; SQLite is intentionally deferred until the HTTP contract is exercised.
* `helper-agent/` registers a Windows helper, polls its heartbeat, starts `UbaAgent.exe -listen=<port>`, and reports readiness.

The native source is intentionally separate from Epic source. The UBT target file is `src/UbaCoordinatorOrchestrator.Target.cs`; the bridge module should be placed in the UBA program source tree when this checkout is integrated into the full Engine tree.

## Verified UBA integration facts

The current source establishes the following behavior:

1. On Windows, `CoordinatorWrapper` constructs `binariesDir + "UbaCoordinator" + coordinatorType + ".dll"`. Therefore `-coordinator=Orchestrator` requires `UbaCoordinatorOrchestrator.dll`. Linux uses `libUbaCoordinatorOrchestrator.so`.
2. The loader requires undecorated exports named `UbaCreateCoordinator` and `UbaDestroyCoordinator`. Their exact signatures are `Coordinator*(const CoordinatorCreateInfo&)` and `void(Coordinator*)`.
3. `CoordinatorCreateInfo` contains `workDir`, `binariesDir`, `pool`, `maxCoreCount`, and `logging`. It does not contain URI, credentials, initiator identity, or a cancellation callback. In the host API, `Coordinator.Uri` is copied to `UE_HORDE_URL` before the coordinator is created; this is the existing configuration handoff available to a native coordinator.
4. `CoordinatorWrapper` starts a dedicated thread. That thread calls `SetAddClientCallback` once, then calls `SetTargetCoreCount` immediately and every three seconds until destruction. If a scheduler is present, the requested count is capped by the currently runnable remote process count.
5. The callback has signature `bool(void*, const tchar*, u16)` and is forwarded to `NetworkServer::AddClient`. The boolean is the result of `AddClient`; its implementation returns before connection success is known, so it is an enqueue/acceptance result, not proof that the helper connected.
6. The callback receives an address and TCP port for a server that the initiator should connect to. Consequently the coordinator path uses `UbaAgent.exe -listen=<port>`. The manually validated `-host=<initiator>:1345` mode is the inverse, direct-connect path and is not used by `AddClient`.
7. There is no “no more agents” method in `Coordinator`. Destruction is the only lifecycle signal exposed to the coordinator; the bridge releases its lease from its destructor. UBA itself disables remote execution through `SessionServer::DisableRemoteExecution`, but that is not part of the coordinator interface.
8. The local checkout contains `UbaCoordinatorHorde.Target.cs` and references to Horde, but not the corresponding implementation source. Its exact URI, pool, credential handling, and build dependencies therefore remain to be checked in the full Engine branch.

## Build sequence

1. Jenkins starts UBT/Build.bat on the initiator and starts UBA's network server on TCP 1345.
2. UBA loads `UbaCoordinatorOrchestrator.dll` when `-coordinator=Orchestrator` is supplied.
3. The bridge requests a lease for the current target core count.
4. The orchestrator atomically selects idle helpers and marks them reserved.
5. The helper agent observes the assignment, starts `UbaAgent.exe -listen=<port>`, and heartbeats readiness.
6. The bridge polls the lease until active and invokes UBA's add-client callback with the helper address and listening port.
7. UBA owns remote process scheduling, CAS, file transfer, and results. The orchestrator does not inspect or reimplement those protocols.
8. When the UBA coordinator is destroyed, the bridge deletes the lease. The helper agent terminates its UbaAgent process and returns to idle.

## HTTP contract

The MVP endpoints are:

* `POST /api/v1/helpers/register`: helper metadata; returns `helper_id`.
* `POST /api/v1/helpers/{helper_id}/heartbeat`: readiness, PID, and port.
* `POST /api/v1/leases`: initiator identity, port, and requested cores; returns a lease and selected helpers.
* `POST /api/v1/leases/{lease_id}/heartbeat`: extends the lease expiry.
* `GET /api/v1/leases/{lease_id}`: state and selected helper endpoints.
* `DELETE /api/v1/leases/{lease_id}`: release reservation.
* `GET /api/v1/health`: liveness.

Lease states are `pending`, `active`, `released`, and `expired`. Helper states are `idle`, `reserved`, `active`, and `offline`. The initiator state is represented by the lease: pending until a helper reports readiness, active while heartbeats continue, and released/expired after completion or timeout.

## Timeouts and errors

* Helper liveness expires after 15 seconds without a heartbeat.
* A lease expires 30 seconds after creation or its last heartbeat.
* Native HTTP requests have a finite WinHTTP operation timeout inherited from the process configuration and always fail closed; no bridge call waits indefinitely.
* A failed reservation leaves UBA without a client and logs through the normal UBA coordinator path. It does not claim capacity.

## State persistence requirement

The MVP store is in-memory. Restarting the orchestrator loses helper registrations, lease state, and the relationship between helpers and active leases. Helpers can re-register, but active initiators do not have durable coordination state to recover from a service restart.

Before production use, replace the in-memory store with a persistent transactional store, preferably SQLite for a single-host deployment or PostgreSQL for high availability. The implementation must persist helpers, leases, heartbeats, and state transitions; expire stale leases atomically; support restart reconciliation; and make helper registration idempotent. Docker deployment does not solve this limitation by itself.
* Authentication, multiple pools, priorities, durable state, dashboard, and multi-initiator fairness are intentionally out of scope for this MVP.

## Open questions

The full UE 5.6 branch must still verify the actual UBT coordinator configuration path, the Horde module source and target dependencies, the intended helper port policy, and the packaging/reconciliation step used by that branch. The CLI help advertises `-uri` and `-oidc`, but the inspected `UbaCli.cpp` parser does not consume either option in this checkout. The host configuration path does consume `Coordinator.Uri` and exports it as `UE_HORDE_URL`. Credentials are not represented in the checked-in coordinator interface, so environment configuration is used for the first implementation.
