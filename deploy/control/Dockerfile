# meridian-control image (the node control plane, and Meridian Cloud when
# MERIDIAN_CONTROL_CLOUD=1). Separate deployable from the gateway image at the
# repo root. Runs the control-plane HTTP server; apply Alembic migrations first
# (`meridian-control migrate`) as an init step. Multi-arch (amd64 + arm64).
#
#   docker build -f deploy/control/Dockerfile -t meridian-control .
#   docker run -e MERIDIAN_CONTROL_DB_URL=postgresql+psycopg://... \
#              -e MERIDIAN_CONTROL_REQUIRE_MTLS=1 \
#              -v meridian-ca:/var/lib/meridian-control/ca \
#              meridian-control migrate
#   docker run ... meridian-control run --host 0.0.0.0 --port 8443
# The console (web/) is served by the control plane in cloud mode.
FROM --platform=$BUILDPLATFORM node:22-slim AS console

WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# vite.config.ts writes to ../meridian/ui/dist → /src/meridian/ui/dist
RUN npm run build


# Python 3.14 is the project's runtime everywhere (images, CI, docs). 3.15 is
# not used yet: PyYAML has no prebuilt 3.15 wheels and this slim image has no
# compiler.
FROM python:3.14-slim-trixie AS builder

WORKDIR /build
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY meridian/ meridian/
COPY meridian_control/ meridian_control/
COPY --from=console /src/meridian/ui/dist meridian/ui/dist

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade "pip>=26.1.2" \
    && /opt/venv/bin/pip install --no-cache-dir ".[control]"

# The gateway package (with the built console) that the one-line installer
# downloads from this server: /install.sh → /download/meridian-*.whl.
RUN /opt/venv/bin/pip wheel --no-cache-dir --no-deps -w /downloads . \
    && cd /downloads && sha256sum meridian-*.whl > meridian.whl.sha256


FROM python:3.14-slim-trixie AS runtime
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10002 --create-home meridian-control

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /downloads /opt/meridian/downloads
ENV PATH="/opt/venv/bin:$PATH"

# CA material and (for SQLite dev) the DB live here; mount a volume in production.
RUN mkdir -p /var/lib/meridian-control/ca && chown -R meridian-control /var/lib/meridian-control
ENV MERIDIAN_CONTROL_CA_DIR=/var/lib/meridian-control/ca
WORKDIR /var/lib/meridian-control

USER meridian-control
EXPOSE 8443
ENTRYPOINT ["meridian-control"]
CMD ["run", "--host", "0.0.0.0", "--port", "8443"]
