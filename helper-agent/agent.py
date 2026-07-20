"""Small helper-side process supervisor for the UBA orchestrator MVP."""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


def request(url: str, method: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.load(response)


def close_logs(log_handles) -> None:
    if log_handles:
        for handle in log_handles:
            handle.close()


def stop_agent_process(process: subprocess.Popen | None, log_handles) -> None:
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    close_logs(log_handles)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orchestrator", required=True)
    parser.add_argument("--uba-agent", required=True)
    parser.add_argument("--address", default=socket.gethostbyname(socket.gethostname()))
    parser.add_argument("--listen-port", type=int, default=1346)
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--interval", type=float, default=3.0)
    args = parser.parse_args()
    base = args.orchestrator.rstrip("/")
    identity = {"hostname": socket.gethostname(), "address": args.address, "cores": os.cpu_count() or 1,
                "memory_bytes": 0, "platform": platform.system().lower(), "uba_version": "unknown",
                "listen_port": args.listen_port}
    helper_id: str | None = None
    process: subprocess.Popen | None = None
    log_handles = None
    lease_id: str | None = None
    try:
        while True:
            if helper_id is None:
                try:
                    helper = request(base + "/api/v1/helpers/register", "POST", identity)
                    helper_id = helper["helper_id"]
                    print(f"Registered helper {identity['hostname']} as {helper_id}", flush=True)
                except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
                    print(f"Unable to register with orchestrator: {error}; retrying", flush=True)
                    time.sleep(args.interval)
                    continue

            payload = {"agent_ready": bool(process and process.poll() is None),
                       "agent_port": args.listen_port, "pid": process.pid if process else None}
            try:
                current = request(f"{base}/api/v1/helpers/{helper_id}/heartbeat", "POST", payload)
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    print("Orchestrator lost helper registration; registering again", flush=True)
                    stop_agent_process(process, log_handles)
                    process = None
                    log_handles = None
                    lease_id = None
                    helper_id = None
                else:
                    print(f"Helper heartbeat failed: {error}; retrying", flush=True)
                time.sleep(args.interval)
                continue
            except (urllib.error.URLError, TimeoutError) as error:
                print(f"Orchestrator is unavailable: {error}; retrying", flush=True)
                time.sleep(args.interval)
                continue

            assigned_lease_id = current.get("lease_id")
            if process and lease_id and assigned_lease_id != lease_id:
                print(f"Lease {lease_id} was released or replaced; stopping UbaAgent", flush=True)
                stop_agent_process(process, log_handles)
                process = None
                log_handles = None
                lease_id = None
            if assigned_lease_id and assigned_lease_id != lease_id and process is None:
                lease_id = assigned_lease_id
                Path(args.log_dir).mkdir(parents=True, exist_ok=True)
                stdout = open(Path(args.log_dir) / f"uba-agent-{lease_id}.stdout.log", "a", encoding="utf-8")
                stderr = open(Path(args.log_dir) / f"uba-agent-{lease_id}.stderr.log", "a", encoding="utf-8")
                log_handles = (stdout, stderr)
                process = subprocess.Popen([args.uba_agent, f"-listen={args.listen_port}"],
                                            stdout=stdout, stderr=stderr, text=True)
                print(f"Started UbaAgent for lease {lease_id}", flush=True)
            if process and process.poll() is not None:
                close_logs(log_handles)
                log_handles = None
                process = None
                lease_id = None
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        stop_agent_process(process, log_handles)


if __name__ == "__main__":
    raise SystemExit(main())
