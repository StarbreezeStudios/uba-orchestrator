# Orchestrator Docker Deployment

The orchestrator runs on the selected Jenkins node, `uba-orchestrator-dev` or `uba-orchestrator`, so that Windows initiators and helpers do not depend on a developer workstation. Docker publishes the backend on TCP port `8080` and nginx on ports `80` and `443` for HTTPS dashboard access.

## Deployment model

* The selected node runs `uba-orchestrator` and `nginx` containers with Docker Compose.
* nginx terminates TLS and proxies the dashboard and its same-origin API requests to `uba-orchestrator:8080`. HTTP on port 80 redirects to HTTPS.
* Windows helpers continue to run `helper-agent/agent.py` on their own machines.
* Initiators and helpers use `http://<PUBLIC_DOMAIN>:8080` for the selected deployment. Existing clients configured with `http://helsinki:8080` must be updated separately when moving to a new host; selecting a deployment node does not change client configuration.
* Uvicorn logs are emitted to the container log stream.

The current Compose file builds directly from the Git checkout. A registry image can be introduced later if the deployment needs immutable image promotion.

## Manual deployment

On the selected host, ensure ports 80, 443 and 8080 are available and the UI FQDN resolves to the host. Install the certificate chain as `starbreeze.com.pem` and its matching private key as `starbreeze.com.key` in a persistent directory outside the checkout. The directory should have mode 0700 and the key mode 0600. Compose mounts the directory read-only into nginx.

From the repository root (use a hostname covered by the certificate):

```bash
export PUBLIC_DOMAIN=uba-orchestrator-dev.starbreeze.com
export TLS_DIRECTORY=/home/jkoperator/jenkins-tls/uba-orchestrator
docker compose -f deploy/docker/compose.yaml up -d --build
docker compose -f deploy/docker/compose.yaml ps
curl http://127.0.0.1:8080/api/v1/health
curl "https://$PUBLIC_DOMAIN/ui"
curl "https://$PUBLIC_DOMAIN/api/v1/health"
```

Expected health response:

```json
{"status":"ok"}
```

The operations dashboard is available at `https://uba-orchestrator-dev.starbreeze.com/ui` or `https://uba-orchestrator.starbreeze.com/ui`, depending on the selected node. Use the corresponding `PUBLIC_DOMAIN` for manual deployments. Confirm DNS and certificate coverage before deployment. It displays helper registration/state and active initiator leases. The underlying diagnostic endpoints are `/api/v1/helpers` and `/api/v1/initiators`.

`JENKINS_BASE_URL` configures dashboard links from helper and initiator names to Jenkins node pages. Compose defaults it to `https://jk3.starbreeze.com`; override it through the deployment environment or Compose `.env` file. Node names are lowercased and URL-encoded in `/computer/<node>/` paths while their displayed text is preserved. Without this variable, standalone deployments display names without links.

To follow logs:

```bash
docker compose -f deploy/docker/compose.yaml logs -f uba-orchestrator nginx
```

## Jenkins deployment

`deploy/jenkins/deploy.groovy` runs directly on the Jenkins node selected by the `node_name` choice parameter. The Jenkins job supplies the repository URL and branch through its SCM configuration.

| `node_name` | `PUBLIC_DOMAIN` |
| --- | --- |
| `uba-orchestrator-dev` | `uba-orchestrator-dev.starbreeze.com` |
| `uba-orchestrator` | `uba-orchestrator.starbreeze.com` |

The pipeline derives `PUBLIC_DOMAIN` from `${params.node_name}.starbreeze.com` and defines `TLS_DIRECTORY` in its environment. It binds Jenkins secret-file credential `cdf92e2d-5e82-4075-a898-20a1ac3d3532` only during certificate installation. It extracts `starbreeze/starbreeze.com.pem` and `starbreeze/starbreeze.com.key`, verifies hostname coverage, expiry and the matching public keys, then installs them outside the workspace with restricted permissions. Secret contents are never printed.

The pipeline runs `docker compose up -d --build --force-recreate` from the Jenkins workspace. Recreating nginx loads updated certificates. It makes up to 30 attempts to check the direct backend health, HTTPS dashboard and proxied health endpoint, with a five-second timeout per request and two seconds between attempts. HTTPS checks use the configured hostname with a local address override and normal certificate verification. On failure it prints the last 100 log lines from both containers. The node requires Docker, the Compose plugin, Bash, tar, OpenSSL, curl, permission to run Docker Compose and write access to the TLS directory. It does not submit Perforce changes or manage Windows helpers.

## Current limitation: in-memory state

The container persists orchestrator state in the `orchestrator-data` Docker volume. On restart, active runtime leases are expired deliberately so initiators can request fresh assignments; helpers that continue heartbeating are revived and can be assigned again. The deployment is still single-instance; do not run multiple orchestrator containers against the same SQLite file.

## Network and security

The service currently has no authentication or authorization. Restrict TCP 80, 443 and 8080 to the build VPN/firewall while the MVP is being tested. TLS protects browser traffic; initiator/helper traffic on 8080 remains HTTP. The backend still serves `/ui` on 8080, and HTTPS also exposes the API required by the dashboard; this is not endpoint-level isolation. Add authentication, authorization and request identity validation before exposing it to a broader network.
