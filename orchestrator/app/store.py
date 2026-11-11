from __future__ import annotations

import sqlite3
import fcntl
import logging
import json
from collections import deque
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Condition, Event, RLock, Thread
from uuid import uuid4


HELPER_OFFLINE_AFTER = timedelta(seconds=15)
LEASE_DURATION = timedelta(seconds=30)
TERMINAL_LEASE_STATES = ("released", "expired")
PERSIST_INTERVAL = 5.0
MAX_PENDING_HISTORY = 1000
logger = logging.getLogger(__name__)


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
    enabled: bool = True
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
    """Single-coordinator memory state with best-effort periodic SQLite snapshots."""

    def __init__(self, db_path: str | None = None, persist_interval: float = PERSIST_INTERVAL) -> None:
        if persist_interval <= 0:
            raise ValueError("Persistence interval must be positive")
        self.lock = RLock()
        self._condition = Condition(self.lock)
        self._wake = Event()
        self._stop = Event()
        self._closed = False
        self._writer = None
        self._ownership = None
        self._connection = None
        self.db_path = db_path
        self.persist_interval = persist_interval
        self.helpers: dict[str, Helper] = {}
        self.leases: dict[str, Lease] = {}
        self._history: dict[str, Lease] = {}
        self._confirmed_helpers: set[str] = set()
        self._pending_leases: dict[str, Lease] = {}
        self._persisted_helpers: dict[str, Helper] = {}
        self._revision = 0
        self._persisted_revision = 0
        self._last_persisted: str | None = None
        self._persistence_error: str | None = None
        self._dropped_history = 0
        self.events: deque[dict] = deque(maxlen=200)
        self._pending_events: list[dict] = []
        if self.db_path:
            try:
                # SQLite is no longer the allocation lock: reject a second coordinator.
                self._ownership = open(self.db_path + ".lock", "a")
                fcntl.flock(self._ownership, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._connection = sqlite3.connect(self.db_path, timeout=1, check_same_thread=False)
                self._connection.row_factory = sqlite3.Row
                self._create_schema()
                self._load()
                self._persisted_helpers = deepcopy(self.helpers)
                self._begin()
                self._reconcile_restart_locked()
                self._commit()
            except BaseException:
                if self._connection is not None:
                    self._connection.close()
                if self._ownership is not None:
                    self._ownership.close()
                raise
            self._writer = Thread(target=self._persist_loop, name="uba-persistence", daemon=True)
            self._writer.start()

    def close(self, timeout: float = 5.0) -> bool:
        """Attempt a final snapshot without waiting indefinitely for a stuck disk."""
        with self.lock:
            self._closed = True
        self._stop.set()
        self._wake.set()
        if self._writer is not None:
            self._writer.join(timeout)
            stopped = not self._writer.is_alive()
            with self.lock:
                persisted = self._persisted_revision >= self._revision
            if not stopped or not persisted:
                logger.warning("Shutdown left unpersisted runtime state")
            return stopped and persisted
        return True

    def flush(self, timeout: float = 5.0) -> bool:
        """Request persistence and wait at most timeout; never used by request handlers."""
        with self._condition:
            target = self._revision
            if not self.db_path:
                return True
            self._wake.set()
            return self._condition.wait_for(lambda: self._persisted_revision >= target, timeout)

    def persistence_status(self) -> dict:
        with self.lock:
            return {"enabled": bool(self.db_path), "pending": bool(self.db_path) and self._revision > self._persisted_revision,
                    "pending_leases": len(self._pending_leases), "last_success": self._last_persisted,
                    "error": self._persistence_error, "dropped_history": self._dropped_history}

    def _persist_loop(self) -> None:
        try:
            while True:
                self._wake.wait(self.persist_interval)
                self._wake.clear()
                # Capture shutdown before the snapshot so a stop during a write gets a final pass.
                stopping = self._stop.is_set()
                try:
                    self._persist_once()
                except Exception as error:
                    with self.lock:
                        self._persistence_error = type(error).__name__
                    logger.exception("Background persistence failed; runtime state remains in memory")
                if stopping:
                    return
        finally:
            # Only the writer touches SQLite after startup, including during shutdown.
            self._connection.close()
            self._ownership.close()

    def _persist_once(self) -> None:
        with self.lock:
            if self._revision == self._persisted_revision:
                return
            revision = self._revision
            helpers = deepcopy(self.helpers)
            leases = deepcopy(self._pending_leases)
        # No disk operation may hold the runtime lock.
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._save(helpers, leases)
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        with self._condition:
            self._persisted_helpers = helpers
            for lease_id, lease in leases.items():
                if self._pending_leases.get(lease_id) == lease:
                    del self._pending_leases[lease_id]
            self._persisted_revision = revision
            self._last_persisted = now().isoformat()
            self._persistence_error = None
            self._condition.notify_all()

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
                enabled INTEGER NOT NULL DEFAULT 1,
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
            CREATE INDEX IF NOT EXISTS leases_nonterminal ON leases(lease_id)
                WHERE state NOT IN ('released', 'expired');
            CREATE INDEX IF NOT EXISTS lease_helpers_helper ON lease_helpers(helper_id);
            """
        )
        columns = {row["name"] for row in self._connection.execute("PRAGMA table_info(helpers)")}
        if "enabled" not in columns:
            self._connection.execute("ALTER TABLE helpers ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
        self._connection.commit()

    def _load(self) -> None:
        assert self._connection is not None
        self.helpers = {}
        self.leases = {}
        for row in self._connection.execute("SELECT * FROM helpers"):
            self.helpers[row["helper_id"]] = Helper(
                helper_id=row["helper_id"], hostname=row["hostname"], address=row["address"],
                cores=row["cores"], memory_bytes=row["memory_bytes"], platform=row["platform"],
                uba_version=row["uba_version"], listen_port=row["listen_port"], enabled=bool(row["enabled"]),
                state=row["state"],
                last_seen=parse_datetime(row["last_seen"]), lease_id=row["lease_id"],
                agent_ready=bool(row["agent_ready"]),
            )
        memberships: dict[str, list[str]] = {}
        for row in self._connection.execute("SELECT lease_id, helper_id FROM lease_helpers ORDER BY rowid"):
            memberships.setdefault(row["lease_id"], []).append(row["helper_id"])
        for row in self._connection.execute("SELECT * FROM leases"):
            lease = Lease(
                lease_id=row["lease_id"], initiator_id=row["initiator_id"],
                initiator_address=row["initiator_address"], initiator_port=row["initiator_port"],
                target_core_count=row["target_core_count"], helper_ids=memberships.get(row["lease_id"], []),
                state=row["state"], expires_at=parse_datetime(row["expires_at"]),
                last_seen=parse_datetime(row["last_seen"]),
            )
            destination = self._history if lease.state in TERMINAL_LEASE_STATES else self.leases
            destination[lease.lease_id] = lease

    def _get_lease(self, lease_id: str) -> Lease:
        # Historical requests also avoid disk I/O and do not scan the full archive.
        if lease_id not in self.leases:
            lease = self._history[lease_id]
            self._before_leases[lease_id] = deepcopy(lease)
            self.leases[lease_id] = deepcopy(lease)
        return self.leases[lease_id]

    def _save(self, helpers: dict[str, Helper], leases: dict[str, Lease]) -> None:
        removed = [(helper_id,) for helper_id in self._persisted_helpers.keys() - helpers.keys()]
        # Duplicate registration also removes references from retained historical leases.
        self._connection.executemany("DELETE FROM lease_helpers WHERE helper_id = ?", removed)
        self._connection.executemany("DELETE FROM helpers WHERE helper_id = ?", removed)
        for helper in helpers.values():
            if helper == self._persisted_helpers.get(helper.helper_id):
                continue
            self._connection.execute(
                """INSERT INTO helpers
                   (helper_id, hostname, address, cores, memory_bytes, platform, uba_version,
                    listen_port, enabled, state, last_seen, lease_id, agent_ready)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(helper_id) DO UPDATE SET
                    hostname=excluded.hostname, address=excluded.address, cores=excluded.cores,
                    memory_bytes=excluded.memory_bytes, platform=excluded.platform,
                    uba_version=excluded.uba_version, listen_port=excluded.listen_port,
                    enabled=excluded.enabled, state=excluded.state, last_seen=excluded.last_seen,
                    lease_id=excluded.lease_id, agent_ready=excluded.agent_ready""",
                (helper.helper_id, helper.hostname, helper.address, helper.cores, helper.memory_bytes,
                 helper.platform, helper.uba_version, helper.listen_port, int(helper.enabled), helper.state,
                 helper.last_seen.isoformat(), helper.lease_id, int(helper.agent_ready)),
            )
        for lease in leases.values():
            self._connection.execute(
                """INSERT INTO leases VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(lease_id) DO UPDATE SET
                    initiator_id=excluded.initiator_id, initiator_address=excluded.initiator_address,
                    initiator_port=excluded.initiator_port, target_core_count=excluded.target_core_count,
                    state=excluded.state, expires_at=excluded.expires_at, last_seen=excluded.last_seen""",
                (lease.lease_id, lease.initiator_id, lease.initiator_address, lease.initiator_port,
                 lease.target_core_count, lease.state, lease.expires_at.isoformat(), lease.last_seen.isoformat()),
            )
            previous_members = [row[0] for row in self._connection.execute(
                "SELECT helper_id FROM lease_helpers WHERE lease_id = ? ORDER BY rowid", (lease.lease_id,))]
            members = [helper_id for helper_id in lease.helper_ids if helper_id in helpers]
            if members != previous_members:
                self._connection.execute("DELETE FROM lease_helpers WHERE lease_id = ?", (lease.lease_id,))
                self._connection.executemany("INSERT INTO lease_helpers VALUES (?, ?)",
                                             [(lease.lease_id, helper_id) for helper_id in members])

    def _begin(self) -> None:
        if self._closed:
            raise RuntimeError("Store is closed")
        self._before_helpers = deepcopy(self.helpers)
        self._before_confirmed_helpers = self._confirmed_helpers.copy()
        self._before_leases = deepcopy(self.leases)

    def _commit(self) -> None:
        changed = self.helpers != self._before_helpers
        removed = self._before_helpers.keys() - self.helpers.keys()
        if removed:
            self._confirmed_helpers.difference_update(removed)
            for lease_id, lease in self._history.items():
                if removed.intersection(lease.helper_ids):
                    lease = deepcopy(lease)
                    lease.helper_ids = [h for h in lease.helper_ids if h not in removed]
                    self._history[lease_id] = lease
        for lease_id, lease in list(self.leases.items()):
            if lease != self._before_leases.get(lease_id):
                changed = True
                if self.db_path:
                    self._pending_leases[lease_id] = deepcopy(lease)
            if lease.state in TERMINAL_LEASE_STATES:
                self._history[lease_id] = lease
                del self.leases[lease_id]
        # Coalesce repeated heartbeats; drop only excess unpersisted terminal history.
        excess = sum(l.state in TERMINAL_LEASE_STATES for l in self._pending_leases.values()) - MAX_PENDING_HISTORY
        if excess > 0:
            for lease_id, lease in list(self._pending_leases.items()):
                if lease.state in TERMINAL_LEASE_STATES:
                    del self._pending_leases[lease_id]
                    self._dropped_history += 1
                    excess -= 1
                    if not excess:
                        break
        if changed:
            self._revision += 1
        self._publish_events()

    def _rollback(self) -> None:
        self._pending_events.clear()
        self.helpers = self._before_helpers
        self._confirmed_helpers = self._before_confirmed_helpers
        self.leases = {key: lease for key, lease in self._before_leases.items()
                       if lease.state not in TERMINAL_LEASE_STATES}

    def _event(self, event: str, message: str, level: str = "INFO", **details) -> None:
        self._pending_events.append({"timestamp": now().isoformat(), "level": level,
                                     "event": event, "message": message, **details})

    def _lease_event(self, event: str, lease: Lease, message: str, **details) -> None:
        self._event(event, message, lease_id=lease.lease_id, initiator_id=lease.initiator_id,
                    helpers=[self.helpers[i].hostname for i in lease.helper_ids],
                    requested_cores=lease.target_core_count, **details)

    def _publish_events(self) -> None:
        for event in self._pending_events:
            self.events.append(event)
            print(json.dumps(event), flush=True)
        self._pending_events.clear()

    def list_events(self, limit: int = 200) -> list[dict]:
        if not 1 <= limit <= 200:
            raise ValueError("Event limit must be between 1 and 200")
        with self.lock:
            return deepcopy(list(reversed(self.events))[:limit])

    def _reap_locked(self) -> bool:
        timestamp = now()
        changed = False
        for helper in self.helpers.values():
            if timestamp - helper.last_seen > HELPER_OFFLINE_AFTER and helper.state not in ("offline", "disabled"):
                helper.state = "offline" if helper.enabled else "disabled"
                helper.lease_id = None
                helper.agent_ready = False
                self._event("helper_offline", "Helper heartbeat timed out", level="WARNING",
                            helpers=[helper.hostname], helper_id=helper.helper_id)
                changed = True
        for lease in self.leases.values():
            if lease.state in TERMINAL_LEASE_STATES:
                continue
            assignment_lost = any(self.helpers[helper_id].lease_id != lease.lease_id
                                  for helper_id in lease.helper_ids)
            if timestamp > lease.expires_at or assignment_lost:
                lease.state = "expired"
                self._lease_event("lease_expired", lease, "Lease expired", level="WARNING",
                                  reason="initiator_timeout")
                changed = True
                for helper in self.helpers.values():
                    if helper.lease_id == lease.lease_id:
                        helper.lease_id = None
                        helper.state = "idle" if helper.enabled else "disabled"
                        helper.agent_ready = False
        return changed

    def _reconcile_restart_locked(self) -> None:
        """Require fresh helper contact and invalidate all pre-restart assignments."""
        for lease in self.leases.values():
            lease.state = "expired"
            self._lease_event("lease_expired", lease, "Lease expired after coordinator restart",
                              level="WARNING", reason="coordinator_restart")
        for helper in self.helpers.values():
            helper.lease_id = None
            helper.state = "offline" if helper.enabled else "disabled"
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
                                and h.listen_port == data.get("listen_port", 1345)]
                    same_address = [h for h in matching if h.address == data["address"]]
                    reusable = [h for h in matching if h.lease_id is None]
                    candidates = same_address or reusable
                    if candidates:
                        candidates.sort(key=lambda helper: helper.last_seen, reverse=True)
                        helper_id = candidates[0].helper_id
                        for duplicate in matching:
                            if duplicate.helper_id != helper_id and duplicate.lease_id is None:
                                for lease in self.leases.values():
                                    lease.helper_ids = [existing_id for existing_id in lease.helper_ids
                                                        if existing_id != duplicate.helper_id]
                                del self.helpers[duplicate.helper_id]
                helper_id = helper_id or str(uuid4())
                helper = self.helpers.get(helper_id)
                if helper is None:
                    helper = Helper(helper_id=helper_id, hostname=data["hostname"], address=data["address"],
                                    cores=data["cores"], memory_bytes=data.get("memory_bytes", 0),
                                    platform=data.get("platform", "windows"), uba_version=data.get("uba_version", "unknown"),
                                    listen_port=data.get("listen_port", 1345))
                    self.helpers[helper_id] = helper
                    self._event("helper_registered", "Helper registered", helpers=[helper.hostname],
                                helper_id=helper_id, address=helper.address, cores=helper.cores)
                else:
                    for key in ("hostname", "address", "cores", "memory_bytes", "platform", "uba_version", "listen_port"):
                        if key in data:
                            setattr(helper, key, data[key])
                    helper.last_seen = now()
                    if helper.state == "offline" and helper.enabled:
                        helper.state = "idle"
                        self._event("helper_recovered", "Helper reconnected", helpers=[helper.hostname],
                                    helper_id=helper_id)
                self._confirmed_helpers.add(helper.helper_id)
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
                if helper.state == "offline" and helper.enabled:
                    helper.state = "idle"
                    self._event("helper_recovered", "Helper reconnected", helpers=[helper.hostname],
                                helper_id=helper_id)
                if data.get("agent_ready") is not None:
                    helper.agent_ready = bool(data["agent_ready"])
                if data.get("agent_port"):
                    helper.listen_port = int(data["agent_port"])
                if helper.lease_id and helper.agent_ready:
                    if helper.enabled:
                        helper.state = "active"
                    lease = self.leases[helper.lease_id]
                    if lease.state != "active":
                        self._lease_event("lease_active", lease, "Lease activated")
                    lease.state = "active"
                self._confirmed_helpers.add(helper.helper_id)
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
                lease = self._get_lease(lease_id)
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
                self._event("capacity_requested", "Capacity requested", initiator_id=data["initiator_id"],
                            requested_cores=required)
                candidates = [h for h in self.helpers.values() if h.enabled and h.state == "idle" and h.cores > 0]
                candidates.sort(key=lambda h: h.cores, reverse=True)
                selected: list[Helper] = []
                capacity = 0
                for helper in candidates:
                    selected.append(helper)
                    capacity += helper.cores
                    if capacity >= required:
                        break
                if capacity < required:
                    self._event("capacity_unavailable", "Insufficient idle helper capacity", level="WARNING",
                                initiator_id=data["initiator_id"], requested_cores=required,
                                available_cores=capacity)
                    self._commit()
                    return None
                lease = Lease(str(uuid4()), data["initiator_id"], data["initiator_address"],
                              int(data["initiator_port"]), required, [h.helper_id for h in selected])
                self.leases[lease.lease_id] = lease
                for helper in selected:
                    helper.state = "reserved"
                    helper.lease_id = lease.lease_id
                    helper.agent_ready = False
                self._lease_event("lease_created", lease, "Helpers assigned", assigned_cores=capacity)
                self._commit()
                return lease
            except Exception:
                self._rollback()
                raise

    def release_lease(self, lease_id: str) -> Lease:
        with self.lock:
            self._begin()
            try:
                lease = self._get_lease(lease_id)
                if lease.state not in TERMINAL_LEASE_STATES:
                    self._lease_event("lease_released", lease, "Lease explicitly released", reason="explicit_release")
                lease.state = "released"
                for helper in self.helpers.values():
                    if helper.lease_id == lease_id:
                        helper.lease_id = None
                        helper.state = "idle" if helper.enabled else "disabled"
                        helper.agent_ready = False
                self._commit()
                return lease
            except Exception:
                self._rollback()
                raise

    def set_helper_enabled(self, helper_id: str, enabled: bool) -> Helper:
        with self.lock:
            self._begin()
            try:
                helper = self.helpers[helper_id]
                helper.enabled = enabled
                if enabled:
                    if helper.lease_id:
                        helper.state = "active" if helper.agent_ready else "reserved"
                    else:
                        helper.state = "idle" if (helper_id in self._confirmed_helpers and
                                                  now() - helper.last_seen <= HELPER_OFFLINE_AFTER) else "offline"
                elif helper.lease_id:
                    helper.state = "draining"
                else:
                    helper.state = "disabled"
                    helper.agent_ready = False
                self._commit()
                return helper
            except Exception:
                self._rollback()
                raise

    def lease_view(self, lease_id: str) -> dict:
        with self.lock:
            self._begin()
            try:
                self._reap_locked()
                lease = self._get_lease(lease_id)
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
