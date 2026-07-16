from __future__ import annotations

from datetime import timedelta

from .store import Store, now

try:
    from fastapi import FastAPI, HTTPException
except ImportError:  # The pure store remains testable without optional server dependencies.
    FastAPI = None


store = Store()

if FastAPI is not None:
    app = FastAPI(title="UBA Orchestrator", version="0.1.0")

    @app.get("/api/v1/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/api/v1/helpers/register")
    def register_helper(payload: dict) -> dict:
        return store.helper_view(store.register_helper(payload))

    @app.post("/api/v1/helpers/{helper_id}/heartbeat")
    def helper_heartbeat(helper_id: str, payload: dict) -> dict:
        try:
            helper = store.heartbeat_helper(helper_id, payload)
            result = store.helper_view(helper)
            result["lease_id"] = helper.lease_id
            return result
        except KeyError as error:
            raise HTTPException(404, "Unknown helper") from error

    @app.post("/api/v1/leases")
    def create_lease(payload: dict) -> dict:
        lease = store.create_lease(payload)
        if lease is None:
            raise HTTPException(409, "Insufficient idle helper capacity")
        return store.lease_view(lease.lease_id)

    @app.post("/api/v1/leases/{lease_id}/heartbeat")
    def lease_heartbeat(lease_id: str) -> dict:
        try:
            store.leases[lease_id].last_seen = now()
            store.leases[lease_id].expires_at = store.leases[lease_id].last_seen + timedelta(seconds=30)
            return store.lease_view(lease_id)
        except KeyError as error:
            raise HTTPException(404, "Unknown lease") from error

    @app.delete("/api/v1/leases/{lease_id}")
    def release_lease(lease_id: str) -> dict:
        try:
            return store.lease_view(store.release_lease(lease_id).lease_id)
        except KeyError as error:
            raise HTTPException(404, "Unknown lease") from error

    @app.get("/api/v1/leases/{lease_id}")
    def get_lease(lease_id: str) -> dict:
        try:
            return store.lease_view(lease_id)
        except KeyError as error:
            raise HTTPException(404, "Unknown lease") from error
else:
    app = None
