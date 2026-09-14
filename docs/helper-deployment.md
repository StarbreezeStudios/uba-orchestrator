# Jenkins Helper Deployment

`deploy/jenkins/deploy-helper.groovy` orchestrates installation of the UBA helper on one Windows Jenkins node. Its PowerShell implementation lives in `deploy/jenkins/deploy-helper.ps1`.

The helper runs as a machine-level, service-like Windows Scheduled Task. It starts at system startup, does not require an interactive login, runs as `SYSTEM`, and is configured to restart the supervisor after a failure.

The Jenkins job has one parameter:

* `node_name`: the Windows Jenkins node name, for example `jk-win-039`.

The job runs on the corresponding `${node_name}-service` label. It copies `helper-agent/agent.py` and `UbaAgent.exe` to `C:\ProgramData\Epic\UbaOrchestrator`, opens TCP port `1346`, and registers a scheduled task named `UbaOrchestratorHelper` for the `SYSTEM` account.

The orchestrator URL, helper port, and UBA agent source are deployment constants. The job copies `\\devopsfs.starbreeze.com\DevOps\Software-Installs\UbaAgent\UbaAgent.exe` to `C:\ProgramData\Epic\UbaOrchestrator\bin\UbaAgent.exe`; the helper never executes the binary directly from the share. The supervisor's stdout and stderr are appended to `C:\ProgramData\Epic\UbaOrchestrator\logs\helper\supervisor.log`. The helper registers against `http://helsinki:8080` and the pipeline waits up to 180 seconds until a helper with the target hostname appears in `/api/v1/helpers`. The address returned by the orchestrator is logged for diagnostics because Windows can select a different local interface than the deployment script's route discovery.

Helper registration is idempotent for the same hostname and listen port. Restarting or redeploying a helper reuses an unleased orchestrator record and updates its address, so an IP change does not create a duplicate row. Unleased duplicate records from earlier deployments are consolidated when the helper registers again.

The target machine must already have:

* The Windows Jenkins service node configured as `${node_name}-service`.
* Python available as `python.exe`.
* Read access to `\\devopsfs.starbreeze.com\DevOps\Software-Installs\UbaAgent\UbaAgent.exe` for the Jenkins service account.
* Administrator rights for Jenkins to create a machine-level scheduled task and firewall rule.

The initiator setup remains separate. This deployment only manages the helper process and its local `UbaAgent.exe`.

## Building and publishing UbaAgent

`deploy/jenkins/build-uba-agent.groovy` orchestrates the synchronization, build, and publication of the Windows `UbaAgent` package. Its PowerShell implementation lives in `deploy/jenkins/sync-uba-agent.ps1`, `deploy/jenkins/build-uba-agent.ps1`, and `deploy/jenkins/publish-uba-agent.ps1`. Configure Jenkins to load the pipeline from the repository and run it on nodes labeled `uba-helper`.

The job builds `UbaAgent Win64 Development` from the machine-local Perforce workspace at `D:\\jkws\\<COMPUTERNAME>\\payday3\\trunk`. It publishes a new immutable package for every Jenkins build to:

`\\devopsfs.starbreeze.com\devops\Software-Installs\UnrealBuildAccelerator\UbaAgent\build-<BUILD_NUMBER>`

Each package contains `UbaAgent.exe`, all sibling DLLs from its UBA binaries directory, and a `manifest.json` with SHA-256 hashes. The job never overwrites an existing package and removes a partially copied package if publishing fails.

The selected `uba-helper` node must have a configured Perforce workspace, the Unreal build prerequisites, `p4.exe` available to the Jenkins service account, and write access to the DevOps software share. The pipeline synchronizes that workspace to its latest mapped changelist before every build.
