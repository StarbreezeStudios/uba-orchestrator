"""Exercise a loopback HTTP server using a disposable copy of an existing SQLite DB."""

import argparse
import hashlib
import json
import os
import socket
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path


def retained_history(connection, ids):
    digest = hashlib.sha256()
    for table, order in (("leases", "lease_id"), ("lease_helpers", "lease_id, helper_id")):
        for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order}"):
            if row[0] in ids:
                digest.update(json.dumps(tuple(row)).encode())
    return digest.hexdigest()


def validate(args):
    with tempfile.TemporaryDirectory(prefix="uba-load-") as directory:
        database = Path(directory) / "history.db"
        source = sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)
        target = sqlite3.connect(database)
        try:
            source.backup(target)
        finally:
            source.close()
        terminal_ids = {row[0] for row in target.execute(
            "SELECT lease_id FROM leases WHERE state IN ('released', 'expired')")}
        history_before = retained_history(target, terminal_ids)
        original_leases = target.execute("SELECT count(*) FROM leases").fetchone()[0]
        original_helpers = target.execute("SELECT count(*) FROM helpers").fetchone()[0]
        # All mutations and synthetic identities below exist only in the temporary copy.
        target.execute("UPDATE helpers SET enabled = 0")
        target.commit()
        target.close()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def request(path, method="GET", payload=None):
            data = None if payload is None else json.dumps(payload).encode()
            req = urllib.request.Request(base + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
            with opener.open(req, timeout=args.timeout) as response:
                body = response.read()
                return json.loads(body) if "application/json" in response.headers.get("Content-Type", "") else None

        environment = {**os.environ, "UBA_ORCHESTRATOR_DB": str(database), "PYTHONDONTWRITEBYTECODE": "1"}
        arm = Path(directory) / "arm-storage-fault"
        entered = Path(directory) / "storage-fault-entered"
        release = Path(directory) / "release-storage-fault"
        runner = Path(directory) / "server.py"
        # Fault injection exists only in this disposable loopback harness.
        runner.write_text("""
import sqlite3
import sys
import time
from pathlib import Path
import uvicorn

app_dir, port, mode, delay, arm, entered, release = sys.argv[1:]
sys.path.insert(0, app_dir)
arm, entered, release = Path(arm), Path(entered), Path(release)
connect = sqlite3.connect

class FaultConnection(sqlite3.Connection):
    def commit(self):
        if arm.exists():
            entered.touch()
            if mode == "slow":
                time.sleep(float(delay))
            elif mode == "blocked":
                while not release.exists():
                    time.sleep(.01)
        return super().commit()

def fault_connect(*args, **kwargs):
    kwargs["factory"] = FaultConnection
    return connect(*args, **kwargs)

if mode != "normal":
    sqlite3.connect = fault_connect
import app.main
sqlite3.connect = connect
uvicorn.run(app.main.app, host="127.0.0.1", port=int(port), access_log=False)
""")
        with open(Path(directory) / "server.log", "w+") as log:
            process = subprocess.Popen(
                [sys.executable, str(runner), str(args.app_dir.resolve()), str(port), args.storage_mode,
                 str(args.commit_delay), str(arm), str(entered), str(release)],
                env=environment, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 30
                while True:
                    try:
                        request("/api/v1/health")
                        break
                    except (OSError, urllib.error.URLError):
                        if process.poll() is not None or time.monotonic() >= deadline:
                            log.seek(0)
                            raise RuntimeError("Local server did not start: " + log.read()[-2000:])
                        time.sleep(.1)
                helpers = [request("/api/v1/helpers/register", "POST", {
                    "hostname": f"validation-helper-{i}", "address": "127.0.0.2", "cores": 8,
                })["helper_id"] for i in range(args.clients // 4)]
                if args.storage_mode != "normal":
                    arm.touch()
                    deadline = time.monotonic() + 15
                    while not entered.exists():
                        if process.poll() is not None or time.monotonic() >= deadline:
                            raise RuntimeError("Storage fault was not exercised")
                        time.sleep(.01)
                barrier = threading.Barrier(args.clients)

                def client(index):
                    samples = []
                    barrier.wait(timeout=30)

                    def measure(kind, path, method="GET", payload=None, capacity_conflict=False):
                        started = time.monotonic()
                        outcome = "ok"
                        result = None
                        try:
                            result = request(path, method, payload)
                        except urllib.error.HTTPError as error:
                            outcome = "capacity_conflict" if capacity_conflict and error.code == 409 else f"http_{error.code}"
                        except (OSError, urllib.error.URLError) as error:
                            outcome = type(error).__name__
                        samples.append((kind, time.monotonic() - started, outcome))
                        return result

                    for iteration in range(args.iterations):
                        role = index % 4
                        if role == 0:
                            measure("helper_heartbeat", f"/api/v1/helpers/{helpers[index // 4]}/heartbeat",
                                    "POST", {"agent_ready": True})
                        elif role == 1:
                            lease = measure("allocate", "/api/v1/leases", "POST", {
                                "initiator_id": f"validation-build-{index}", "initiator_address": "127.0.0.3",
                                "initiator_port": 1345, "target_core_count": 8,
                            }, capacity_conflict=True)
                            if lease:
                                path = f"/api/v1/leases/{lease['lease_id']}"
                                measure("lease_heartbeat", path + "/heartbeat", "POST")
                                measure("lease_view", path)
                                measure("release", path, "DELETE")
                        elif role == 2:
                            path = ("/api/v1/helpers", "/api/v1/initiators", "/api/v1/events")[iteration % 3]
                            measure("read", path)
                        else:
                            path = "/api/v1/health" if iteration % 2 == 0 else "/ui"
                            measure("health" if iteration % 2 == 0 else "ui", path)
                    return samples

                started = time.monotonic()
                with ThreadPoolExecutor(max_workers=args.clients) as executor:
                    samples = [sample for batch in executor.map(client, range(args.clients)) for sample in batch]
                elapsed = time.monotonic() - started
                status_during_fault = request("/api/v1/health")["persistence"]
            finally:
                release.touch()
                process.terminate()
                try:
                    process.wait(timeout=max(8, args.commit_delay * 2 + 3))
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

        connection = sqlite3.connect(database)
        try:
            preserved = retained_history(connection, terminal_ids) == history_before
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            overlapping = connection.execute(
                """SELECT helper_id FROM lease_helpers JOIN leases USING (lease_id)
                   WHERE state NOT IN ('released', 'expired') GROUP BY helper_id HAVING count(*) > 1""").fetchall()
            persisted_validation_leases = connection.execute(
                "SELECT count(*) FROM leases WHERE initiator_id LIKE 'validation-build-%'"
            ).fetchone()[0]
            unfinished = connection.execute(
                "SELECT count(*) FROM leases WHERE initiator_id LIKE 'validation-build-%' AND state != 'released'"
            ).fetchone()[0]
            expired = connection.execute(
                "SELECT count(*) FROM leases WHERE initiator_id LIKE 'validation-build-%' AND state = 'expired'"
            ).fetchone()[0]
        finally:
            connection.close()
        grouped = defaultdict(list)
        errors = Counter()
        for kind, duration, outcome in samples:
            grouped[kind].append(duration)
            if outcome not in ("ok", "capacity_conflict"):
                errors[f"{kind}:{outcome}"] += 1
        latency = {}
        for kind, durations in grouped.items():
            durations.sort()
            latency[kind] = {"count": len(durations), "p95_ms": round(durations[int((len(durations) - 1) * .95)] * 1000, 2),
                             "max_ms": round(durations[-1] * 1000, 2)}
        allocations = sum(kind == "allocate" and outcome == "ok" for kind, _, outcome in samples)
        persistence_complete = persisted_validation_leases == allocations
        fault_exercised = args.storage_mode == "normal" or entered.exists()
        passed = (not errors and preserved and integrity == "ok" and not foreign_keys and not overlapping
                  and not unfinished and persistence_complete and fault_exercised
                  and process.returncode in (0, -signal.SIGTERM))
        report = {"passed": passed, "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                  "original_leases": original_leases, "retained_terminal_leases": len(terminal_ids),
                  "original_helpers": original_helpers, "clients": args.clients, "iterations": args.iterations,
                  "requests": len(samples), "seconds": round(elapsed, 3), "errors": dict(errors),
                  "storage_mode": args.storage_mode, "commit_delay_seconds": args.commit_delay,
                  "fault_exercised": fault_exercised, "status_during_fault": status_during_fault,
                  "server_exit_code": process.returncode, "successful_allocations": allocations,
                  "persisted_validation_leases": persisted_validation_leases,
                  "capacity_conflicts": sum(outcome == "capacity_conflict" for _, _, outcome in samples),
                  "latency": latency, "terminal_history_preserved": preserved, "integrity": integrity,
                  "foreign_key_violations": len(foreign_keys), "overlapping_assignments": len(overlapping),
                  "expired_validation_leases": expired, "unfinished_validation_leases": unfinished}
        print(json.dumps(report, indent=2))
        if args.output:
            args.output.write_text(json.dumps(report, indent=2) + "\n")
        return 0 if passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True, help="Local SQLite snapshot; opened read-only")
    parser.add_argument("--app-dir", type=Path, default=Path(__file__).resolve().parents[1] / "orchestrator")
    parser.add_argument("--clients", type=int, default=64)
    parser.add_argument("--iterations", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=2)
    parser.add_argument("--storage-mode", choices=("normal", "slow", "blocked"), default="normal")
    parser.add_argument("--commit-delay", type=float, default=2, help="Delay before each commit in slow mode")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if arguments.clients < 4 or arguments.clients % 4 or arguments.iterations < 1 or arguments.timeout <= 0 or arguments.commit_delay <= 0:
        parser.error("clients must be a positive multiple of four; iterations, timeout and commit-delay must be positive")
    raise SystemExit(validate(arguments))
