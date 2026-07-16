# Perforce Integration Boundary

This Git repository owns the orchestrator service, helper agent, native bridge source, tests, scripts, and documentation.

The Perforce workspace owns the Unreal Engine 5.6/UBA source and receives only the integration artifacts required to build and launch the bridge:

* `UbaCoordinatorOrchestrator.Target.cs`;
* the UBA module source and build file for the bridge;
* the generated `UbaCoordinatorOrchestrator.dll` beside UBA binaries;
* Jenkins and UBA configuration changes required to enable the coordinator.

The source tree mapping is intentionally different from this Git repository layout:

* Copy `coordinator-bridge/Private`, `coordinator-bridge/Public`, and `coordinator-bridge/UbaCoordinatorOrchestrator.Build.cs` to `Engine/Source/Programs/UnrealBuildAccelerator/UbaCoordinatorOrchestrator/`.
* Copy `UbaCoordinatorOrchestrator.Target.cs` to `Engine/Source/Programs/UnrealBuildAccelerator/`.

Do not copy the complete Git repository as a nested UBA source module. In particular, do not place a `Target.cs` below the module directory.

Do not copy the game source into this Git repository. Keep the Git commit identifier in the Jenkins build metadata so a Perforce build can be correlated with the orchestration code version.
