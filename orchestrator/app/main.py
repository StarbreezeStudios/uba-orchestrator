from __future__ import annotations

from .store import Store

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, RedirectResponse
except ImportError:  # The pure store remains testable without optional server dependencies.
    FastAPI = None


store = Store()

if FastAPI is not None:
    app = FastAPI(title="UBA Orchestrator", version="0.1.0")

    @app.get("/api/v1/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/ui")

    @app.get("/api/v1/helpers")
    def list_helpers() -> list[dict]:
        return store.list_helpers()

    @app.get("/api/v1/initiators")
    def list_initiators() -> list[dict]:
        return store.list_initiators()

    @app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
    def ui() -> str:
        return """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UBA Orchestrator</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; }
    body { background: #111827; color: #e5e7eb; margin: 0; padding: 24px; }
    h1 { margin-top: 0; }
    h2 { margin-top: 32px; }
    .meta { color: #9ca3af; margin-bottom: 16px; }
    .table-wrap { overflow-x: auto; }
    table { border-collapse: collapse; min-width: 760px; width: 100%; background: #1f2937; }
    th, td { border-bottom: 1px solid #374151; padding: 10px 12px; text-align: left; white-space: nowrap; }
    th { background: #374151; color: #f9fafb; }
    .idle, .active { color: #86efac; }
    .reserved, .pending { color: #fde68a; }
    .offline, .expired { color: #fca5a5; }
    code { color: #bfdbfe; }
  </style>
</head>
<body>
  <h1>UBA Orchestrator</h1>
  <div class="meta">Refreshing every 3 seconds · Last update: <span id="updated">never</span></div>
  <h2>Helpers</h2>
  <div class="table-wrap"><table>
    <thead><tr><th>Hostname</th><th>Address</th><th>Cores</th><th>State</th><th>Agent</th><th>Lease</th><th>Last heartbeat</th></tr></thead>
    <tbody id="helpers"><tr><td colspan="7">Loading...</td></tr></tbody>
  </table></div>
  <h2>Initiators</h2>
  <div class="table-wrap"><table>
    <thead><tr><th>Initiator</th><th>Address</th><th>Requested cores</th><th>State</th><th>Helpers</th><th>Lease</th><th>Expires</th></tr></thead>
    <tbody id="initiators"><tr><td colspan="7">Loading...</td></tr></tbody>
  </table></div>
  <script>
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const state = value => `<span class="${esc(value)}">${esc(value)}</span>`;
    const refresh = async () => {
      try {
        const [helpersResponse, initiatorsResponse] = await Promise.all([
          fetch('/api/v1/helpers'), fetch('/api/v1/initiators')
        ]);
        const helpers = await helpersResponse.json();
        const initiators = await initiatorsResponse.json();
        document.querySelector('#helpers').innerHTML = helpers.length ? helpers.map(h => `
          <tr><td>${esc(h.hostname)}</td><td><code>${esc(h.address)}:${esc(h.listen_port)}</code></td>
          <td>${esc(h.cores)}</td><td>${state(h.state)}</td><td>${h.agent_ready ? 'ready' : 'not ready'}</td>
          <td><code>${esc(h.lease_id || '-')}</code></td><td>${esc(h.last_seen)}</td></tr>`).join('')
          : '<tr><td colspan="7">No helpers registered</td></tr>';
        document.querySelector('#initiators').innerHTML = initiators.length ? initiators.map(i => `
          <tr><td>${esc(i.initiator_id)}</td><td><code>${esc(i.address)}:${esc(i.port)}</code></td>
          <td>${esc(i.target_core_count)}</td><td>${state(i.state)}</td>
          <td>${i.helpers.map(h => `${esc(h.hostname)} (${esc(h.cores)})`).join(', ')}</td>
          <td><code>${esc(i.lease_id)}</code></td><td>${esc(i.expires_at)}</td></tr>`).join('')
          : '<tr><td colspan="7">No active initiators</td></tr>';
        document.querySelector('#updated').textContent = new Date().toLocaleString();
      } catch (error) {
        document.querySelector('#updated').textContent = `error: ${error}`;
      }
    };
    refresh();
    setInterval(refresh, 3000);
  </script>
</body>
</html>
"""

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
            lease = store.heartbeat_lease(lease_id)
            if lease.state in ("released", "expired"):
                raise HTTPException(409, "Lease is no longer active")
            return store.lease_view(lease.lease_id)
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
