# Jenkins Helper Deployment

`deploy/jenkins/deploy-helper.groovy` installs and starts the UBA helper on one Windows Jenkins node.

The helper runs as a machine-level, service-like Windows Scheduled Task. It starts at system startup, does not require an interactive login, runs as `SYSTEM`, and is configured to restart the supervisor after a failure.

The Jenkins job has one parameter:

* `node_name`: the Windows Jenkins node name, for example `jk-win-039`.

The job runs on the corresponding `${node_name}-service` label. It copies `helper-agent/agent.py` to `C:\ProgramData\Epic\UbaOrchestrator`, locates the local Unreal `UbaAgent.exe`, opens TCP port `1346`, and registers a scheduled task named `UbaOrchestratorHelper` for the `SYSTEM` account.

The orchestrator URL, helper port, and Unreal binary path layout are deployment constants. The helper registers against `http://helsinki:8080` and the pipeline waits up to 180 seconds until a helper with the target hostname appears in `/api/v1/helpers`. The address returned by the orchestrator is logged for diagnostics because Windows can select a different local interface than the deployment script's route discovery.

Helper registration is idempotent for the same hostname, address, and listen port. Restarting or redeploying a helper reuses its existing orchestrator record instead of creating a duplicate row. Inactive duplicate records from earlier deployments are consolidated when the helper registers again.

The target machine must already have:

* The Windows Jenkins service node configured as `${node_name}-service`.
* Python available as `python.exe`.
* The Unreal Build Accelerator binaries under `D:\jkws\<COMPUTERNAME>\payday3\trunk`.
* Administrator rights for Jenkins to create a machine-level scheduled task and firewall rule.

The initiator setup remains separate. This deployment only manages the helper process and its local `UbaAgent.exe`.
