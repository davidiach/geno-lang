# Deploying Geno Hosted Applications

## Overview

The Geno hosted runtime (`geno serve`) provides an HTTP server with
health checks, metrics, a `/run` endpoint for executing Geno programs,
and a `/constrain` endpoint for prefix validation and next-token guidance.

## Starting the Server

```bash
# Local development (loopback only, no auth required)
geno serve

# Production (replace geno.example.com with the public proxy hostname)
GENO_API_KEY="$(openssl rand -hex 32)" \
GENO_ALLOWED_HOSTS="geno.example.com" \
geno serve --host 0.0.0.0 --port 8000
```

Non-loopback binding without `GENO_API_KEY` is refused by default.
Production deployments must also set `GENO_ALLOWED_HOSTS` to the exact public
hostname or hostnames accepted from their trusted reverse proxy.
Pass `--allow-insecure` to explicitly opt in to unauthenticated access
(not recommended for production).

### Options

| Flag               | Default     | Description                              |
|--------------------|-------------|------------------------------------------|
| `--host`           | `127.0.0.1` | Bind address                            |
| `--port`           | `8000`      | Listen port                             |
| `--service`        | `geno-api`  | Service name (for metrics)              |
| `--revision`       | (none)      | Deployment revision identifier           |
| `--allow-insecure` | (off)       | Allow non-loopback without API key auth  |

Environment variables: `GENO_SERVICE`, `GENO_REVISION`, `GENO_API_KEY`,
`GENO_ALLOWED_HOSTS`, `GENO_CORS_ALLOWED_ORIGINS`,
`GENO_REQUIRE_AUTH_FOR_METRICS`, and `GENO_REQUIRE_AUTH_FOR_PLAYGROUND`.

Host validation is enabled by default. Loopback servers accept `localhost`,
`127.0.0.1`, and `[::1]` for the bound port; production deployments should set
comma-separated `GENO_ALLOWED_HOSTS` to the public hostnames accepted by the
reverse proxy.

Browser CORS responses are fail-closed by default: the server reflects an
`Origin` only when it appears in comma-separated `GENO_CORS_ALLOWED_ORIGINS`.
Set this variable to the exact playground or application origins that should
call hosted endpoints from a browser.

## Endpoints

| Endpoint      | Method | Description                             |
|---------------|--------|-----------------------------------------|
| `/healthz`    | GET    | Health check — returns `ok`             |
| `/metrics`    | GET    | Prometheus-format metrics               |
| `/run`        | POST   | Execute Geno source, returns JSON result |
| `/constrain`  | POST   | Validate a Geno prefix and return allowed-next-token guidance |

`/healthz` is intentionally public for load balancers. When `GENO_API_KEY` is
configured, unauthenticated health checks return only the overall status; send
the same API-key headers to receive the full diagnostic health payload. Metrics
require API-key auth by default when `GENO_API_KEY` is configured. Set
`GENO_REQUIRE_AUTH_FOR_METRICS=0` only when a trusted proxy already protects
`/metrics`, and set `GENO_REQUIRE_AUTH_FOR_PLAYGROUND=1` to require auth for
`/` and `/playground`.

### POST /run

```bash
curl -X POST http://localhost:8000/run \
  -H 'Content-Type: application/json' \
  -d '{"source": "func main() -> Int\n  return 42\nend func"}'
```

Response (success, HTTP 200):

```json
{
  "ok": true,
  "value": 42,
  "output": "",
  "diagnostics": [],
  "timing": {
    "total_ms": 12.5,
    "lex_ms": 0.1,
    "parse_ms": 0.3,
    "typecheck_ms": 0.5,
    "run_ms": 11.6
  },
  "steps_used": 15
}
```

Response (error, HTTP 400):

```json
{
  "ok": false,
  "value": null,
  "output": "",
  "diagnostics": [
    {
      "code": "E300",
      "message": "Return type mismatch: expected Int, got String",
      "severity": "error",
      "location": {
        "line": 2,
        "column": 5,
        "filename": "<http>"
      }
    }
  ],
  "timing": { "total_ms": 1.2, "lex_ms": 0.1, "parse_ms": 0.2, "typecheck_ms": 0.9, "run_ms": 0.0 },
  "steps_used": 0
}
```

### POST /constrain

```bash
curl -X POST http://localhost:8000/constrain \
  -H 'Content-Type: application/json' \
  -d '{"prefix": "func "}'
```

Response:

```json
{
  "valid": true,
  "allowed_next": {
    "allow_identifier": true
  }
}
```

## Docker

```dockerfile
FROM python:3.11-slim
RUN pip install geno-lang
ENV GENO_API_KEY=""
ENV GENO_ALLOWED_HOSTS="localhost:8000"
EXPOSE 8000
CMD ["geno", "serve", "--host", "0.0.0.0", "--port", "8000"]
```

Build and run:

```bash
docker build -t geno-server .
docker run \
  -e GENO_API_KEY="$(openssl rand -hex 32)" \
  -e GENO_ALLOWED_HOSTS="localhost:8000" \
  -p 8000:8000 geno-server
```

## Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: geno-server
spec:
  replicas: 2
  selector:
    matchLabels:
      app: geno-server
  template:
    metadata:
      labels:
        app: geno-server
    spec:
      containers:
        - name: geno
          image: geno-server:latest
          ports:
            - containerPort: 8000
          livenessProbe:
            httpGet:
              path: /healthz
              port: 8000
              httpHeaders:
                - name: Host
                  value: geno.example.com
            initialDelaySeconds: 5
            periodSeconds: 10
          readinessProbe:
            httpGet:
              path: /healthz
              port: 8000
              httpHeaders:
                - name: Host
                  value: geno.example.com
            initialDelaySeconds: 3
            periodSeconds: 5
          env:
            - name: GENO_SERVICE
              value: "geno-api"
            - name: GENO_API_KEY
              valueFrom:
                secretKeyRef:
                  name: geno-api-key
                  key: api-key
            - name: GENO_ALLOWED_HOSTS
              value: "geno.example.com"
---
apiVersion: v1
kind: Service
metadata:
  name: geno-server
spec:
  selector:
    app: geno-server
  ports:
    - port: 80
      targetPort: 8000
```

## Cloud Platforms

### AWS ECS / Fargate

1. Push Docker image to ECR
2. Create a task definition with port 8000 and use the image's Python health
   check: `python -c "import sys, urllib.request; sys.exit(0 if `
   `urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status `
   `== 200 else 1)"`.
3. Create a Network Load Balancer with a TCP target-group health check on port
   8000. An ALB's HTTP health check uses the target's private IP in `Host`, which
   intentionally fails Geno's allow-list; use a local reverse-proxy health
   sidecar that supplies the configured Host if an ALB is required.
4. Terminate TLS at the NLB and pass `GENO_API_KEY` as a task secret.
5. Set `GENO_ALLOWED_HOSTS` to the NLB's public DNS name (or your custom
   domain).

### Google Cloud Run

```bash
gcloud run deploy geno-server \
  --image gcr.io/PROJECT/geno-server \
  --port 8000 \
  --set-secrets GENO_API_KEY=geno-api-key:latest \
  --set-env-vars GENO_ALLOWED_HOSTS=geno.example.com \
  --allow-unauthenticated
```

### Fly.io

```toml
# fly.toml
app = "geno-server"
[env]
  GENO_ALLOWED_HOSTS = "geno.example.com"

[http_service]
  internal_port = 8000
  force_https = true

[[http_service.checks]]
  path = "/healthz"
  interval = 10000
  timeout = 2000

[http_service.checks.headers]
  Host = "geno.example.com"
```

```bash
fly deploy
```
Replace `geno.example.com` in the managed-platform examples with the exact
public hostname clients use. Local loopback `/healthz` probes may use the bound
loopback Host value; managed probes must send an explicitly allowed Host. Every
execution endpoint requires an allowed Host value.

## Security

Production deployments that bind to a non-loopback address must use
`GENO_API_KEY` and terminate TLS at the edge (for example with an ingress, ALB,
Caddy, nginx, Fly.io, or platform-managed HTTPS). An API key sent over plaintext
HTTP is not a production security boundary.

The hosted runtime executes user-provided Geno code in a sandboxed
environment with:

- **Step limit**: Prevents infinite loops
- **Memory guards**: Operator-fixed collection and integer magnitude limits
- **Capability restrictions**: No filesystem or network access by default
- **Timeout**: Per-request execution timeout capped by server configuration
- **API key authentication**: Required for non-loopback bindings. Set
  `GENO_API_KEY` env var. Accepts `Authorization: Bearer <key>` or
  `X-API-Key: <key>` headers. Uses constant-time comparison.
- **GET endpoint auth controls**: `/healthz` stays public, but authenticated
  deployments expose only the overall status unless callers provide a valid API
  key. `/metrics` requires API-key auth by default when `GENO_API_KEY` is set;
  use `GENO_REQUIRE_AUTH_FOR_METRICS=0` only behind a trusted protected metrics
  path. Set `GENO_REQUIRE_AUTH_FOR_PLAYGROUND=1` to require auth for the
  playground GET endpoints.
- **Per-IP rate limiting**: Sliding-window throttle, configurable via
  `GENO_RATE_LIMIT_REQUESTS` (default: 60/min) and
  `GENO_RATE_LIMIT_WINDOW_SECONDS` (default: 60s). Active client buckets
  are capped by `GENO_RATE_LIMIT_MAX_BUCKETS` (default: 4096) to bound
  memory use when many distinct client keys appear. Returns HTTP 429 when
  exceeded. Set requests to 0 to disable.
  **Behind a reverse proxy you must set `GENO_TRUSTED_PROXY`** (see below) —
  otherwise every external client is keyed by the proxy's IP and shares one
  bucket, so a single client can exhaust the whole service's budget and the
  per-client protection effectively does not exist.

### Running behind a reverse proxy

Every deployment topology in this document (ALB, Kubernetes ingress, Cloud
Run, Fly.io) terminates client connections at a proxy, so the server's TCP
peer is the proxy, not the client. Set `GENO_TRUSTED_PROXY` to the proxy's
address (the immediate upstream peer) so the server trusts the client IP from
the `X-Forwarded-For` header for rate-limiting and logging. Without it, per-IP
rate limiting keys on the proxy IP (one shared bucket for all clients). Only
set it when a trusted proxy actually fronts the server — trusting
`X-Forwarded-For` from an untrusted peer lets clients spoof their IP.

`GENO_TRUSTED_PROXY` also enables the public scheme from
`X-Forwarded-Proto` for same-origin browser checks. The immediate proxy must
overwrite that header with exactly `http` or `https`; duplicate or malformed
values fail closed. Without a matching trusted immediate peer, forwarded scheme
headers do not affect security decisions.

### Operator environment variables

Beyond the request-facing variables listed under [Options](#options), the
server reads these operator knobs (all optional; defaults in parentheses):

| Variable | Purpose |
| --- | --- |
| `GENO_TRUSTED_PROXY` | Immediate upstream proxy address; enables strict `X-Forwarded-For` client-IP trust and `X-Forwarded-Proto` scheme trust (unset — peer IP and connection scheme used) |
| `GENO_ALLOWED_ENV_NAMES` / `GENO_ALLOWED_ENV_PREFIXES` | Allowlist for the `env` capability; without one, granting `env` on `/run` is rejected |
| `GENO_SKIP_STARTUP_CHECKS` | Skip fail-closed startup checks (`/healthz` then reports checks as skipped) |
| `GENO_MAX_REQUEST_BODY_BYTES` | Cap on `/run` and `/constrain` request body size |
| `GENO_MAX_REQUEST_HEADER_BYTES` | Aggregate request-line and header size cap before request dispatch (64 KiB) |
| `GENO_MAX_RESPONSE_BODY_BYTES` | Maximum serialized JSON response body (8 MiB); oversized results receive a fixed `413` response |
| `GENO_MAX_JSON_NESTING_DEPTH` | Maximum object/array nesting depth accepted in JSON request bodies (128) |
| `GENO_REQUEST_TIMEOUT_SECONDS` | Per-request wall-clock ceiling |
| `GENO_DEFAULT_MAX_STEPS` / `GENO_MAX_STEPS` | Default and hard cap for interpreter steps |
| `GENO_MAX_MODULES` / `GENO_MAX_MODULE_SOURCE_BYTES` | Multi-module submission limits |
| `GENO_CONSTRAIN_WALL_CLOCK_SECONDS` | Wall-clock ceiling for `/constrain` |
| `GENO_SANDBOX_DEBUG` | Attach the sandbox worker traceback to server logs for compiler-bug diagnosis (off by default; log-only, never returned to clients) |

The repeatable `--allow-capability` CLI flag controls which capabilities
`POST /run` may grant (see `geno serve --help`).
- **Worker startup grace**: Spawned worker processes get a separate startup
  grace period before user-code wall time starts. Configure with
  `GENO_WORKER_STARTUP_GRACE_SECONDS` (default: 10s) for slower cold-start
  environments.
- **Worker process limits**: On platforms with `resource.setrlimit`, spawned
  workers apply operator-configured OS limits before hosted code runs:
  `GENO_WORKER_MAX_MEMORY_BYTES` (default: 268435456), `GENO_WORKER_MAX_CPU_TIME`
  (default: `GENO_MAX_WALL_CLOCK_SECONDS`), `GENO_WORKER_MAX_FILE_SIZE_BYTES`
  (default: 0, meaning no file writes), and `GENO_WORKER_MAX_PROCESSES`
  (default: 1). Set memory or CPU limits to 0 to disable that specific cap.
  On Darwin, the memory value is a VM address-space growth budget above the
  already-spawned trusted worker bootstrap, because XNU rejects an absolute
  limit below the existing VM map. Any stricter inherited hard limit remains
  authoritative.
- **Bounded concurrency**: Limits simultaneous executions via
  `GENO_MAX_CONCURRENT_REQUESTS` (default: 16)

Hosted `/run` clients may set only these resource controls:

- `timeout`: positive seconds, capped by `GENO_MAX_TIMEOUT_SECONDS`
- `max_steps`: positive interpreter steps, capped by `GENO_MAX_STEPS`

Other sandbox resource limits are operator-controlled and fixed for the hosted
service: `max_memory_bytes`, `max_cpu_time`, `max_file_size_bytes`,
`max_processes`, `max_recursion_depth`, `max_output_length`,
`max_collection_size`, and `max_integer_bits`. Requests that include those
fields are rejected with HTTP 400 so clients cannot weaken or accidentally
depend on deployment-specific guardrails.
