# Unreal Engine 5.6 UBA patch

This directory contains the experimental UBT integration patch. It is applied on demand to a clean Perforce workspace by Jenkins; it is not submitted to the Unreal Engine depot.

From PowerShell:

```powershell
.\apply-uba-ubt-patch.ps1 -EngineRoot D:\path\to\payday3\trunk
```

The script validates and applies `UBAExecutor.diff`, then copies `UBAAgentCoordinatorOrchestrator.cs` into the UBT source tree. It fails if the diff no longer applies cleanly, which protects the build from silently patching a changed Engine revision.

The Jenkins build must configure the runtime separately:

```text
UBA_COORDINATOR=Orchestrator
UBA_ORCHESTRATOR_URL=http://helsinki:8080
UBA_TARGET_CORES=128
```

XGE should be disabled through the build command/configuration used by Jenkins (`-NoXGE`); the site-wide Perforce `BuildConfiguration.xml` change is intentionally not part of this patch.
