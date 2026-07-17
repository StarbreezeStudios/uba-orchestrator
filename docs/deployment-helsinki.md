# Helsinki Docker Deployment

The MVP orchestrator can run on `helsinki` so that Windows initiators and helpers do not depend on a developer workstation. Docker publishes the service on TCP port `8080`; that port is currently available on the host.

## Deployment model

* `helsinki` runs one `uba-orchestrator` container with Docker Compose.
* Windows helpers continue to run `helper-agent/agent.py` on their own machines.
* Initiators and helpers use `http://helsinki:8080` or the host address reachable over the VPN.
* Uvicorn logs are emitted to the container log stream.

The current Compose file builds directly from the Git checkout. A registry image can be introduced later if the deployment needs immutable image promotion.

## Manual deployment

On `helsinki`, from the repository root:

```bash
docker compose -f deploy/docker/compose.yaml up -d --build
docker compose -f deploy/docker/compose.yaml ps
curl http://127.0.0.1:8080/api/v1/health
```

Expected health response:

```json
{"status":"ok"}
```

The operations dashboard is available at `http://helsinki:8080/ui`. It displays helper registration/state and active initiator leases. The underlying diagnostic endpoints are `/api/v1/helpers` and `/api/v1/initiators`.

To follow logs:

```bash
docker compose -f deploy/docker/compose.yaml logs -f uba-orchestrator
```

## Jenkins deployment

`deploy/jenkins/deploy.groovy` runs directly on the Jenkins node labeled `helsinki`. The Jenkins job supplies the repository URL and branch through its SCM configuration; the pipeline itself has no deployment parameters.

The pipeline runs `docker compose up -d --build --force-recreate` from the Jenkins workspace and waits up to 60 seconds for the local health endpoint. If the service does not become healthy, the pipeline prints the last 100 container log lines before failing. The node must have Docker, the Compose plugin, and permission to run Docker Compose. It does not submit Perforce changes, manage Windows helpers, or expose the service through a proxy.

## Current limitation: in-memory state

The container does not persist orchestrator state. A restart clears helper registrations and leases. This is acceptable for the MVP pilot only if helpers are restarted or re-registered after a deployment. Before production use, add durable state and restart reconciliation; see [architecture.md](architecture.md#state-persistence-requirement).

## Network and security

The service currently has no authentication or authorization. Restrict TCP 8080 to the build VPN/firewall while the MVP is being tested. Add authentication, authorization, TLS termination, and request identity validation before exposing it to a broader network.
