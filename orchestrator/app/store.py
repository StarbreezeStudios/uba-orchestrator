from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import uuid4


def now() -> datetime:
    return datetime.now(timezone.utc)


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
    expires_at: datetime = field(default_factory=lambda: now() + timedelta(seconds=30))
    last_seen: datetime = field(default_factory=now)


class Store:
    def __init__(self) -> None:
        self.lock = RLock()
        self.helpers: dict[str, Helper] = {}
        self.leases: dict[str, Lease] = {}

    def reap(self) -> None:
        timestamp = now()
        for helper in self.helpers.values():
            if timestamp - helper.last_seen > timedelta(seconds=15):
                helper.state = "offline"
                helper.lease_id = None
                helper.agent_ready = False
        for lease in self.leases.values():
            if lease.state not in ("released", "expired") and timestamp > lease.expires_at:
                lease.state = "expired"
                for helper in self.helpers.values():
                    if helper.lease_id == lease.lease_id:
                        helper.lease_id = None
                        helper.state = "idle"
                        helper.agent_ready = False

    def register_helper(self, data: dict) -> Helper:
        with self.lock:
            helper_id = data.get("helper_id")
            if not helper_id:
                matching_helpers = [
                    helper for helper in self.helpers.values()
                    if helper.hostname == data["hostname"]
                    and helper.address == data["address"]
                    and helper.listen_port == data.get("listen_port", 1345)
                ]
                if matching_helpers:
                    # Reuse the newest record so restarting a helper does not
                    # create a second row in the diagnostics view.
                    matching_helpers.sort(key=lambda helper: helper.last_seen, reverse=True)
                    helper_id = matching_helpers[0].helper_id
                    for duplicate in matching_helpers[1:]:
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
            return helper

    def heartbeat_helper(self, helper_id: str, data: dict) -> Helper:
        with self.lock:
            helper = self.helpers[helper_id]
            helper.last_seen = now()
            if data.get("agent_ready") is not None:
                helper.agent_ready = bool(data["agent_ready"])
            if data.get("agent_port"):
                helper.listen_port = int(data["agent_port"])
            if helper.lease_id and helper.agent_ready:
                helper.state = "active"
                lease = self.leases[helper.lease_id]
                lease.state = "active"
                lease.last_seen = now()
                lease.expires_at = lease.last_seen + timedelta(seconds=30)
            return helper

    def create_lease(self, data: dict) -> Lease | None:
        with self.lock:
            self.reap()
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
                return None
            lease = Lease(str(uuid4()), data["initiator_id"], data["initiator_address"], int(data["initiator_port"]),
                          required, [h.helper_id for h in selected])
            self.leases[lease.lease_id] = lease
            for helper in selected:
                helper.state = "reserved"
                helper.lease_id = lease.lease_id
                helper.agent_ready = False
            return lease

    def release_lease(self, lease_id: str) -> Lease:
        with self.lock:
            lease = self.leases[lease_id]
            lease.state = "released"
            for helper in self.helpers.values():
                if helper.lease_id == lease_id:
                    helper.lease_id = None
                    helper.state = "idle"
                    helper.agent_ready = False
            return lease

    def lease_view(self, lease_id: str) -> dict:
        with self.lock:
            self.reap()
            lease = self.leases[lease_id]
            helpers = [self.helpers[i] for i in lease.helper_ids]
            return {"lease_id": lease.lease_id, "state": lease.state, "expires_at": lease.expires_at.isoformat(),
                    "initiator_address": lease.initiator_address, "initiator_port": lease.initiator_port,
                    "helpers": [{"helper_id": h.helper_id, "address": h.address, "port": h.listen_port,
                                  "agent_ready": h.agent_ready, "cores": h.cores} for h in helpers]}

    def list_helpers(self) -> list[dict]:
        with self.lock:
            self.reap()
            return [self.helper_view(helper) for helper in self.helpers.values()]

    def list_initiators(self) -> list[dict]:
        with self.lock:
            self.reap()
            active_leases = [
                lease for lease in self.leases.values()
                if lease.state not in ("released", "expired")
            ]
            return [self.initiator_view(lease) for lease in active_leases]

    @staticmethod
    def helper_view(helper: Helper) -> dict:
        result = asdict(helper)
        result["last_seen"] = helper.last_seen.isoformat()
        return result

    def initiator_view(self, lease: Lease) -> dict:
        return {
            "initiator_id": lease.initiator_id,
            "address": lease.initiator_address,
            "port": lease.initiator_port,
            "lease_id": lease.lease_id,
            "state": lease.state,
            "target_core_count": lease.target_core_count,
            "last_seen": lease.last_seen.isoformat(),
            "expires_at": lease.expires_at.isoformat(),
            "helpers": [
                {
                    "helper_id": helper.helper_id,
                    "hostname": helper.hostname,
                    "address": helper.address,
                    "port": helper.listen_port,
                    "cores": helper.cores,
                    "state": helper.state,
                    "agent_ready": helper.agent_ready,
                }
                for helper in (self.helpers[helper_id] for helper_id in lease.helper_ids)
            ],
        }
