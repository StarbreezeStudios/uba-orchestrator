from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import uuid4


HELPER_OFFLINE_AFTER = timedelta(seconds=15)
LEASE_DURATION = timedelta(seconds=30)
TERMINAL_LEASE_STATES = ("released", "expired")


def now() -> datetime:
    return datetime.now(timezone.utc)


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass
class Helper:
    helper_id: str
    hostname: str
    address: str
    cores: int
    memory_bytes: int
    platform: str
    uba_version: str
    listen_port: int
    state: str = "idle"
    last_seen: datetime = field(default_factory=now)
    lease_id: str | None = None
    agent_ready: bool = False


@dataclass
class Lease:
    lease_id: str
    initiator_id: str
    initiator_address: str
    initiator_port: int
    target_core_count: int
    helper_ids: list[str]
    state: str = "pending"
    expires_at: datetime = field(default_factory=lambda: now() + LEASE_DURATION)
    last_seen: datetime = field(default_factory=now)


class Store:
    """Thread-safe orchestration state with optional SQLite durability.

    ``db_path=None`` retains the in-memory behavior used by isolated unit tests.
    A file path enables durable state and transactional lease allocation.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self.lock = RLock()
        self.db_path = db_path
        self.helpers: dict[str, Helper] = {}
        self.leases: dict[str, Lease] = {}
        if self.db_path:
            self._connection = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._create_schema()
            self._load()
            self._reconcile_restart_locked()
            self._reap_locked()
            self._save()
            self._connection.commit()
        else:
            self._connection = None

    def close(self) -> None:
        with self.lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def _create_schema(self) -> None:
        assert self._connection is not None
        self._connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS helpers (
                helper_id TEXT PRIMARY KEY,
                hostname TEXT NOT NULL,
                address TEXT NOT NULL,
                cores INTEGER NOT NULL,
                memory_bytes INTEGER NOT NULL,
                platform TEXT NOT NULL,
                uba_version TEXT NOT NULL,
                listen_port INTEGER NOT NULL,
                state TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                lease_id TEXT,
                agent_ready INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS leases (
                lease_id TEXT PRIMARY KEY,
                initiator_id TEXT NOT NULL,
                initiator_address TEXT NOT NULL,
                initiator_port INTEGER NOT NULL,
                target_core_count INTEGER NOT NULL,
                state TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS lease_helpers (
                lease_id TEXT NOT NULL REFERENCES leases(lease_id) ON DELETE CASCADE,
                helper_id TEXT NOT NULL REFERENCES helpers(helper_id),
                PRIMARY KEY (lease_id, helper_id)
            );
            """
        )
        self._connection.commit()

    def _load(self) -> None:
        assert self._connection is not None
        self.helpers = {}
        self.leases = {}
        for row in self._connection.execute("SELECT * FROM helpers"):
            self.helpers[row["helper_id"]] = Helper(
                helper_id=row["helper_id"], hostname=row["hostname"], address=row["address"],
                cores=row["cores"], memory_bytes=row["memory_bytes"], platform=row["platform"],
                uba_version=row["uba_version"], listen_port=row["listen_port"], state=row["state"],
                last_seen=parse_datetime(row["last_seen"]), lease_id=row["lease_id"],
                agent_ready=bool(row["agent_ready"]),
            )
        helper_ids: dict[str, list[str]] = {}
        for row in self._connection.execute("SELECT lease_id, helper_id FROM lease_helpers ORDER BY rowid"):
            helper_ids.setdefault(row["lease_id"], []).append(row["helper_id"])
        for row in self._connection.execute("SELECT * FROM leases"):
            self.leases[row["lease_id"]] = Lease(
                lease_id=row["lease_id"], initiator_id=row["initiator_id"],
                initiator_address=row["initiator_address"], initiator_port=row["initiator_port"],
                target_core_count=row["target_core_count"], helper_ids=helper_ids.get(row["lease_id"], []),
                state=row["state"], expires_at=parse_datetime(row["expires_at"]),
                last_seen=parse_datetime(row["last_seen"]),
            )

    def _save(self) -> None:
        if self._connection is None:
            return
        self._connection.execute("DELETE FROM lease_helpers")
        self._connection.execute("DELETE FROM leases")
        self._connection.execute("DELETE FROM helpers")
        for helper in self.helpers.values():
            self._connection.execute(
                "INSERT INTO helpers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (helper.helper_id, helper.hostname, helper.address, helper.cores, helper.memory_bytes,
                 helper.platform, helper.uba_version, helper.listen_port, helper.state,
                 helper.last_seen.isoformat(), helper.lease_id, int(helper.agent_ready)),
            )
        for lease in self.leases.values():
            self._connection.execute(
                "INSERT INTO leases VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (lease.lease_id, lease.initiator_id, lease.initiator_address, lease.initiator_port,
                 lease.target_core_count, lease.state, lease.expires_at.isoformat(), lease.last_seen.isoformat()),
            )
            for helper_id in lease.helper_ids:
                self._connection.execute("INSERT INTO lease_helpers VALUES (?, ?)", (lease.lease_id, helper_id))

    def _begin(self) -> None:
        if self._connection is not None:
            self._connection.execute("BEGIN IMMEDIATE")
            self._load()

    def _commit(self) -> None:
        if self._connection is not None:
            self._save()
            self._connection.commit()

    def _rollback(self) -> None:
        if self._connection is not None:
            self._connection.rollback()
            self._load()

    def _reap_locked(self) -> bool:
        timestamp = now()
        changed = False
        for helper in self.helpers.values():
            if timestamp - helper.last_seen > HELPER_OFFLINE_AFTER and helper.state != "offline":
                helper.state = "offline"
                helper.lease_id = None
                helper.agent_ready = False
                changed = True
        for lease in self.leases.values():
            if lease.state not in TERMINAL_LEASE_STATES and timestamp > lease.expires_at:
                lease.state = "expired"
                changed = True
                for helper in self.helpers.values():
                    if helper.lease_id == lease.lease_id:
                        helper.lease_id = None
                        helper.state = "idle"
                        helper.agent_ready = False
        return changed

    def _reconcile_restart_locked(self) -> None:
        """Invalidate runtime assignments that cannot survive a service restart."""
        for lease in self.leases.values():
            if lease.state in TERMINAL_LEASE_STATES:
                continue
            lease.state = "expired"
            for helper in self.helpers.values():
                if helper.lease_id == lease.lease_id:
                    helper.lease_id = None
                    helper.state = "idle"
                    helper.agent_ready = False

    def reap(self) -> None:
        with self.lock:
            self._begin()
            try:
                self._reap_locked()
                self._commit()
            except Exception:
                self._rollback()
                raise

    def register_helper(self, data: dict) -> Helper:
        with self.lock:
            self._begin()
            try:
                helper_id = data.get("helper_id")
                if not helper_id:
                    matching = [h for h in self.helpers.values() if h.hostname == data["hostname"]
                                and h.address == data["address"]
                                and h.listen_port == data.get("listen_port", 1345)]
                    if matching:
                        matching.sort(key=lambda helper: helper.last_seen, reverse=True)
                        helper_id = matching[0].helper_id
                        for duplicate in matching[1:]:
                            if duplicate.lease_id is None:
                                del self.helpers[duplicate.helper_id]
                helper_id = helper_id or str(uuid4())
                helper = self.helpers.get(helper_id)
                if helper is None:
                    helper = Helper(helper_id=helper_id, hostname=data["hostname"], address=data["address"],
                                    cores=data["cores"], memory_bytes=data.get("memory_bytes", 0),
                                    platform=data.get("platform", "windows"), uba_version=data.get("uba_version", "unknown"),
                                    listen_port=data.get("listen_port", 1345))
                    self.helpers[helper_id] = helper
                else:
                    for key in ("hostname", "address", "cores", "memory_bytes", "platform", "uba_version", "listen_port"):
                        if key in data:
                            setattr(helper, key, data[key])
                    helper.last_seen = now()
                    if helper.state == "offline":
                        helper.state = "idle"
                self._commit()
                return helper
            except Exception:
                self._rollback()
                raise

    def heartbeat_helper(self, helper_id: str, data: dict) -> Helper:
        with self.lock:
            self._begin()
            try:
                helper = self.helpers[helper_id]
                helper.last_seen = now()
                if helper.state == "offline":
                    helper.state = "idle"
                if data.get("agent_ready") is not None:
                    helper.agent_ready = bool(data["agent_ready"])
                if data.get("agent_port"):
                    helper.listen_port = int(data["agent_port"])
                if helper.lease_id and helper.agent_ready:
                    helper.state = "active"
                    self.leases[helper.lease_id].state = "active"
                self._commit()
                return helper
            except Exception:
                self._rollback()
                raise

    def heartbeat_lease(self, lease_id: str) -> Lease:
        with self.lock:
            self._begin()
            try:
                self._reap_locked()
                lease = self.leases[lease_id]
                if lease.state not in TERMINAL_LEASE_STATES:
                    lease.last_seen = now()
                    lease.expires_at = lease.last_seen + LEASE_DURATION
                self._commit()
                return lease
            except Exception:
                self._rollback()
                raise

    def create_lease(self, data: dict) -> Lease | None:
        with self.lock:
            self._begin()
            try:
                self._reap_locked()
                required = int(data["target_core_count"])
                candidates = [h for h in self.helpers.values() if h.state == "idle" and h.cores > 0]
                candidates.sort(key=lambda h: h.cores, reverse=True)
                selected: list[Helper] = []
                capacity = 0
                for helper in candidates:
                    selected.append(helper)
                    capacity += helper.cores
                    if capacity >= required:
                        break
                if capacity < required:
                    self._commit()
                    return None
                lease = Lease(str(uuid4()), data["initiator_id"], data["initiator_address"],
                              int(data["initiator_port"]), required, [h.helper_id for h in selected])
                self.leases[lease.lease_id] = lease
                for helper in selected:
                    helper.state = "reserved"
                    helper.lease_id = lease.lease_id
                    helper.agent_ready = False
                self._commit()
                return lease
            except Exception:
                self._rollback()
                raise

    def release_lease(self, lease_id: str) -> Lease:
        with self.lock:
            self._begin()
            try:
                lease = self.leases[lease_id]
                lease.state = "released"
                for helper in self.helpers.values():
                    if helper.lease_id == lease_id:
                        helper.lease_id = None
                        helper.state = "idle"
                        helper.agent_ready = False
                self._commit()
                return lease
            except Exception:
                self._rollback()
                raise

    def lease_view(self, lease_id: str) -> dict:
        with self.lock:
            self._begin()
            try:
                self._reap_locked()
                lease = self.leases[lease_id]
                helpers = [self.helpers[i] for i in lease.helper_ids]
                self._commit()
                return {"lease_id": lease.lease_id, "state": lease.state, "expires_at": lease.expires_at.isoformat(),
                        "initiator_address": lease.initiator_address, "initiator_port": lease.initiator_port,
                        "helpers": [{"helper_id": h.helper_id, "address": h.address, "port": h.listen_port,
                                     "agent_ready": h.agent_ready, "cores": h.cores} for h in helpers]}
            except Exception:
                self._rollback()
                raise

    def list_helpers(self) -> list[dict]:
        with self.lock:
            self._begin()
            try:
                self._reap_locked()
                result = [self.helper_view(helper) for helper in self.helpers.values()]
                self._commit()
                return result
            except Exception:
                self._rollback()
                raise

    def list_initiators(self) -> list[dict]:
        with self.lock:
            self._begin()
            try:
                self._reap_locked()
                result = [self.initiator_view(lease) for lease in self.leases.values()
                          if lease.state not in TERMINAL_LEASE_STATES]
                self._commit()
                return result
            except Exception:
                self._rollback()
                raise

    @staticmethod
    def helper_view(helper: Helper) -> dict:
        result = asdict(helper)
        result["last_seen"] = helper.last_seen.isoformat()
        return result

    def initiator_view(self, lease: Lease) -> dict:
        return {"initiator_id": lease.initiator_id, "address": lease.initiator_address,
                "port": lease.initiator_port, "lease_id": lease.lease_id, "state": lease.state,
                "target_core_count": lease.target_core_count, "last_seen": lease.last_seen.isoformat(),
                "expires_at": lease.expires_at.isoformat(),
                "helpers": [{"helper_id": h.helper_id, "hostname": h.hostname, "address": h.address,
                             "port": h.listen_port, "cores": h.cores, "state": h.state,
                             "agent_ready": h.agent_ready} for h in (self.helpers[i] for i in lease.helper_ids)]}
