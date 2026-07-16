"""Small helper-side process supervisor for the UBA orchestrator MVP."""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import subprocess
import time
import urllib.request
from pathlib import Path


def request(url: str, method: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.load(response)


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
    helper = request(base + "/api/v1/helpers/register", "POST", identity)
    helper_id = helper["helper_id"]
    process: subprocess.Popen | None = None
    log_handles = None
    lease_id: str | None = None
    try:
        while True:
            payload = {"agent_ready": bool(process and process.poll() is None),
                       "agent_port": args.listen_port, "pid": process.pid if process else None}
            current = request(f"{base}/api/v1/helpers/{helper_id}/heartbeat", "POST", payload)
            if current.get("lease_id") and current["lease_id"] != lease_id and process is None:
                lease_id = current["lease_id"]
                Path(args.log_dir).mkdir(parents=True, exist_ok=True)
                stdout = open(Path(args.log_dir) / f"uba-agent-{lease_id}.stdout.log", "a", encoding="utf-8")
                stderr = open(Path(args.log_dir) / f"uba-agent-{lease_id}.stderr.log", "a", encoding="utf-8")
                log_handles = (stdout, stderr)
                process = subprocess.Popen([args.uba_agent, f"-listen={args.listen_port}"],
                                            stdout=stdout, stderr=stderr, text=True)
            if process and process.poll() is not None:
                if log_handles:
                    for handle in log_handles:
                        handle.close()
                    log_handles = None
                process = None
                lease_id = None
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        if log_handles:
            for handle in log_handles:
                handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
