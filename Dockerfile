FROM python:3.11-slim

# Run as non-root user
RUN useradd --create-home --shell /bin/bash geno
USER geno

WORKDIR /app
ENV PATH="/home/geno/.local/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install pinned, hash-verified runtime dependencies first so builds of the
# same commit are reproducible (M-01); then install the package itself without
# re-resolving its dependencies.
COPY --chown=geno:geno requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir --user --require-hashes -r requirements.lock

# Copy package (README.md is kept in the build context so the wheel ships a
# non-empty long description — see .dockerignore).
COPY --chown=geno:geno . /app
RUN pip install --no-cache-dir --user --no-deps .

EXPOSE 8000

# Health check mirrors the docker-compose probe so `docker run` deployments
# (and Kubernetes without explicit probes) get health monitoring (M-01). The
# loopback probe is accepted from the in-container loopback peer even with the
# Host allow-list active (see docs/deploy/hosted.md).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"

# Read-only filesystem is enforced by docker-compose.
# Default entrypoint runs the hosted runtime.
#
# The runtime binds 0.0.0.0 (required for Docker port mapping) and therefore
# requires GENO_API_KEY and GENO_ALLOWED_HOSTS to be set (set GENO_ALLOWED_HOSTS
# to the hostnames clients use, or "*" to disable Host-header validation);
# otherwise it fails fast at startup with an actionable message. See
# docker-compose.yml and docs/deploy/hosted.md.
ENTRYPOINT ["python", "-m", "geno"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
