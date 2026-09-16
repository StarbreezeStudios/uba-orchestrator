"""Deploy and validate the UBA helper on a Jenkins Windows node."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse
from xml.sax.saxutils import escape


INSTALL_ROOT = Path(r"C:\ProgramData\Epic\UbaOrchestrator")
AGENT_SCRIPT = INSTALL_ROOT / "helper-agent" / "agent.py"
UBA_AGENT_DIRECTORY = INSTALL_ROOT / "bin"
UBA_AGENT = UBA_AGENT_DIRECTORY / "UbaAgent.exe"
LOG_DIRECTORY = INSTALL_ROOT / "logs" / "helper"
TASK_NAME = "UbaOrchestratorHelper"
ORCHESTRATOR_URL = "http://helsinki:8080"
LISTEN_PORT = 1346
UBA_AGENT_SOURCE = Path(r"\\devopsfs.starbreeze.com\DevOps\Software-Installs\UbaAgent\UbaAgent.exe")


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True, capture_output=True)


def get_python_path() -> str:
    python_path = shutil.which("python.exe")
    if not python_path:
        raise RuntimeError("python.exe was not found in PATH")
    return python_path


def get_helper_address() -> str:
    parsed = urlparse(ORCHESTRATOR_URL)
    if not parsed.hostname:
        raise RuntimeError(f"Invalid orchestrator URL: {ORCHESTRATOR_URL}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.connect((parsed.hostname, port))
        address = connection.getsockname()[0]
    if address == "127.0.0.1":
        raise RuntimeError("Could not determine the helper IPv4 address")
    return address


def powershell_json(script: str) -> list[dict[str, object]]:
    result = run(["powershell.exe", "-NoProfile", "-Command", script])
    if not result.stdout.strip():
        return []
    value = json.loads(result.stdout)
    return value if isinstance(value, list) else [value]


def get_helper_processes() -> list[dict[str, object]]:
    script = f"""
        Get-CimInstance Win32_Process | Where-Object {{
            $_.Name -ine 'powershell.exe' -and $_.CommandLine -and (
                $_.CommandLine -like '*helper-agent*agent.py*' -or
                ($_.Name -ieq 'UbaAgent.exe' -and $_.CommandLine -match '-listen={LISTEN_PORT}')
            )
        }} | Select-Object ProcessId, Name, CommandLine | ConvertTo-Json -Compress
    """
    return powershell_json(script)


def get_task_diagnostics() -> dict[str, object]:
    script = f"""
        $task = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue
        if ($task) {{
            $info = Get-ScheduledTaskInfo -TaskName '{TASK_NAME}'
            [PSCustomObject]@{{ state = [string]$task.State; last_task_result = $info.LastTaskResult }} |
                ConvertTo-Json -Compress
        }}
    """
    result = powershell_json(script)
    return result[0] if result else {}


def get_task_state() -> str | None:
    result = run([
        "powershell.exe", "-NoProfile", "-Command",
        f"$task = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue; if ($task) {{ $task.State }}",
    ], check=False)
    state = result.stdout.strip()
    return state or None


def stop_existing_helper() -> None:
    task_state = get_task_state()
    if task_state == "Running":
        print(f"Stopping existing scheduled task {TASK_NAME}")
        result = run(["schtasks.exe", "/End", "/TN", TASK_NAME], check=False)
        if result.returncode != 0:
            print(f"Warning: Task Scheduler returned exit code {result.returncode} while stopping {TASK_NAME}")
    if task_state is not None:
        print(f"Removing existing scheduled task {TASK_NAME}")
        result = run(["schtasks.exe", "/Delete", "/TN", TASK_NAME, "/F"], check=False)
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"Could not remove scheduled task {TASK_NAME}: {details or result.returncode}")

    deadline = time.monotonic() + 15
    termination_errors: list[str] = []
    while time.monotonic() < deadline:
        remaining = get_helper_processes()
        if not remaining:
            break
        termination_errors.clear()
        for process in remaining:
            process_id = str(process["ProcessId"])
            result = run(["taskkill.exe", "/PID", process_id, "/T", "/F"], check=False)
            if result.returncode != 0:
                details = result.stderr.strip() or result.stdout.strip()
                termination_errors.append(f"PID {process_id}: {details or f'exit code {result.returncode}'}")
        time.sleep(0.25)
    else:
        processes = "; ".join(
            f"PID {process['ProcessId']} ({process.get('Name', 'unknown')}): {process.get('CommandLine', '')}"
            for process in remaining
        )
        details = f" ({'; '.join(termination_errors)})" if termination_errors else ""
        raise RuntimeError(f"Could not stop existing UBA helper processes: {processes}{details}")

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        state = get_task_state()
        if state != "Running":
            return
        time.sleep(0.5)
    raise RuntimeError(f"Scheduled task {TASK_NAME} is still running after its helper processes were stopped")


def install_files(source_script: Path) -> None:
    for directory in (AGENT_SCRIPT.parent, UBA_AGENT_DIRECTORY, LOG_DIRECTORY):
        directory.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_script, AGENT_SCRIPT)
    shutil.copy2(UBA_AGENT_SOURCE, UBA_AGENT)
    print(f"Installed UBA agent to {UBA_AGENT}")


def configure_firewall() -> None:
    result = run([
        "netsh.exe", "advfirewall", "firewall", "add", "rule",
        "name=UBA Orchestrator Helper 1346", "dir=in", "action=allow",
        "protocol=TCP", f"localport={LISTEN_PORT}", "profile=domain,private",
    ], check=False)
    if result.returncode != 0:
        print("Warning: unable to create the firewall rule")


def register_and_start_task(python_path: str, address: str) -> None:
    supervisor_log = LOG_DIRECTORY / "supervisor.log"
    with supervisor_log.open("a", encoding="utf-8") as log_file:
        log_file.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%S%z')}] Starting helper supervisor\n")

    arguments = subprocess.list2cmdline([
        python_path, "-u", str(AGENT_SCRIPT), "--orchestrator", ORCHESTRATOR_URL,
        "--uba-agent", str(UBA_AGENT), "--address", address,
        "--listen-port", str(LISTEN_PORT), "--log-dir", str(LOG_DIRECTORY),
    ])
    command_arguments = f'/d /c "{arguments} 1>> \\"{supervisor_log}\\" 2>&1"'
    task_xml = f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><BootTrigger><Enabled>true</Enabled></BootTrigger></Triggers>
  <Principals><Principal id="System"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
  </Settings>
  <Actions Context="System"><Exec><Command>cmd.exe</Command><Arguments>{escape(command_arguments)}</Arguments><WorkingDirectory>{escape(str(INSTALL_ROOT))}</WorkingDirectory></Exec></Actions>
</Task>'''
    with tempfile.NamedTemporaryFile("w", suffix=".xml", encoding="utf-16", delete=False) as file:
        task_file = Path(file.name)
        file.write(task_xml)
    try:
        result = run(["schtasks.exe", "/Create", "/TN", TASK_NAME, "/XML", str(task_file), "/F"], check=False)
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"Could not create scheduled task {TASK_NAME}: {details or result.returncode}")
    finally:
        task_file.unlink(missing_ok=True)
    run(["schtasks.exe", "/Run", "/TN", TASK_NAME])


def verify_supervisor_started() -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        supervisors = [process for process in get_helper_processes()
                       if str(process.get("Name", "")).lower() != "ubaagent.exe"]
        if supervisors:
            process_ids = ", ".join(str(process["ProcessId"]) for process in supervisors)
            print(f"Helper supervisor started with process ID(s): {process_ids}")
            return
        time.sleep(0.5)
    diagnostics = get_task_diagnostics()
    supervisor_log = LOG_DIRECTORY / "supervisor.log"
    log_tail = supervisor_log.read_text(encoding="utf-8", errors="replace")[-12000:] if supervisor_log.is_file() else "Supervisor log was not created"
    raise RuntimeError(
        f"Scheduled task {TASK_NAME} did not start the helper supervisor "
        f"(state: {diagnostics.get('state', 'missing')}, "
        f"last result: {diagnostics.get('last_task_result', 'unknown')})\n"
        f"Supervisor log:\n{log_tail}"
    )


def get_registered_helper(hostname: str) -> dict[str, object] | None:
    with urllib.request.urlopen(f"{ORCHESTRATOR_URL}/api/v1/helpers", timeout=10) as response:
        helpers = json.load(response)
    matching = [helper for helper in helpers if str(helper.get("hostname", "")).lower() == hostname.lower()]
    return max(matching, key=lambda helper: str(helper.get("last_seen", "")), default=None)


def verify_registration() -> dict[str, object]:
    deadline = time.monotonic() + 180
    initial_heartbeat: str | None = None
    latest_heartbeat: str | None = None
    last_error: Exception | None = None
    hostname = socket.gethostname()
    while time.monotonic() < deadline:
        time.sleep(2)
        try:
            helper = get_registered_helper(hostname)
            last_error = None
            if helper:
                latest_heartbeat = str(helper.get("last_seen", ""))
                if initial_heartbeat is None:
                    initial_heartbeat = latest_heartbeat
                    print(f"Observed helper heartbeat at {initial_heartbeat}; waiting for the next heartbeat")
                elif latest_heartbeat != initial_heartbeat:
                    return helper
        except Exception as error:
            last_error = error

    supervisor_log = LOG_DIRECTORY / "supervisor.log"
    log_tail = supervisor_log.read_text(encoding="utf-8", errors="replace")[-12000:] if supervisor_log.is_file() else "Supervisor log was not created"
    details = f"Last API error: {last_error}" if last_error else (
        f"Last observed heartbeat remained at {latest_heartbeat}" if latest_heartbeat else "No helper record was returned by the API"
    )
    raise RuntimeError(f"Helper did not produce a new heartbeat at {ORCHESTRATOR_URL} within 180 seconds. {details}\nSupervisor log:\n{log_tail}")


def main() -> int:
    source_script = Path(os.environ.get("WORKSPACE", "")) / "helper-agent" / "agent.py"
    if not source_script.is_file():
        raise FileNotFoundError(f"Helper agent source was not found: {source_script}")
    if not UBA_AGENT_SOURCE.is_file():
        raise FileNotFoundError(f"UbaAgent.exe was not found on the software share: {UBA_AGENT_SOURCE}")

    python_path = get_python_path()
    address = get_helper_address()
    print(f"Installing UBA helper on {socket.gethostname()} ({address})")
    print(f"Copying UBA agent from {UBA_AGENT_SOURCE}")
    stop_existing_helper()
    install_files(source_script)
    configure_firewall()
    register_and_start_task(python_path, address)
    verify_supervisor_started()
    helper = verify_registration()
    print(f"Registered helper address: {helper.get('address')}:{helper.get('listen_port')}")
    print(json.dumps(helper, indent=2))
    print("UBA helper deployment completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
