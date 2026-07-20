# Unreal Engine 5.6 UBA patch

This directory contains the experimental UBT integration patch. It is applied on demand to a clean Perforce workspace by Jenkins; it is not submitted to the Unreal Engine depot.

From PowerShell:

```powershell
.\apply-uba-ubt-patch.ps1 -EngineRoot D:\path\to\payday3\trunk
```

The script validates and applies `UBAExecutor.diff` and `ActionGraph.diff`, then copies `UBAAgentCoordinatorOrchestrator.cs` into the UBT source tree. The ActionGraph patch forces the UBA executor only when `UBA_COORDINATOR=Orchestrator`.

The Jenkins build must configure the runtime separately:

```text
UBA_COORDINATOR=Orchestrator
UBA_ORCHESTRATOR_URL=http://helsinki:8080
UBA_TARGET_CORES=128
```

No site-wide Perforce `BuildConfiguration.xml` change is required. Other builds retain their normal executor selection.
