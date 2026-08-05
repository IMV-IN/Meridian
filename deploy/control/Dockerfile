# meridian-control image (the node control plane). Separate deployable from the
# gateway image at the repo root. Runs the control-plane HTTP server; apply
# Alembic migrations first (`meridian-control migrate`) as an init step.
#
#   docker build -f deploy/control/Dockerfile -t meridian-control .
#   docker run -e MERIDIAN_CONTROL_DB_URL=postgresql+psycopg://... \
#              -e MERIDIAN_CONTROL_REQUIRE_MTLS=1 \
#              -v meridian-ca:/var/lib/meridian-control/ca \
#              meridian-control migrate
#   docker run ... meridian-control run --host 0.0.0.0 --port 8443
FROM python:3.12-slim-trixie AS builder

WORKDIR /build
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY meridian/ meridian/
COPY meridian_control/ meridian_control/

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade "pip>=26.1.2" \
    && /opt/venv/bin/pip install --no-cache-dir ".[control]"


FROM python:3.12-slim-trixie AS runtime
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10002 --create-home meridian-control

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# CA material and (for SQLite dev) the DB live here; mount a volume in production.
RUN mkdir -p /var/lib/meridian-control/ca && chown -R meridian-control /var/lib/meridian-control
ENV MERIDIAN_CONTROL_CA_DIR=/var/lib/meridian-control/ca
WORKDIR /var/lib/meridian-control

USER meridian-control
EXPOSE 8443
ENTRYPOINT ["meridian-control"]
CMD ["run", "--host", "0.0.0.0", "--port", "8443"]
