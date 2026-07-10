# Multi-stage runtime image.
# - Builder: install Meridian into a venv, drop pip/setuptools/wheel.
# - Runtime: Debian trixie slim + security upgrades; strip base-image packaging
#   tools so Trivy does not report HIGH CVEs from pip/wheel left on the PATH.

FROM python:3.12-slim-trixie AS builder

WORKDIR /build

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY meridian/ meridian/

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade "pip>=26.1.2" \
    && /opt/venv/bin/pip install --no-cache-dir . \
    && /opt/venv/bin/pip uninstall -y pip setuptools wheel \
    && rm -rf \
        /opt/venv/lib/python*/site-packages/pip* \
        /opt/venv/lib/python*/site-packages/setuptools* \
        /opt/venv/lib/python*/site-packages/wheel* \
        /opt/venv/lib/python*/site-packages/pkg_resources* \
        /opt/venv/lib/python*/site-packages/jaraco* \
        /opt/venv/bin/pip* \
        /opt/venv/bin/wheel \
    || true


FROM python:3.12-slim-trixie AS runtime

# Distro security updates, then drop packages Meridian never uses at runtime.
# perl-base is Essential on Debian but unused by the gateway; removing it clears
# CRITICAL Archive::Tar / regex CVEs that have no fixed version yet.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get purge -y --allow-remove-essential perl-base \
    && apt-get autoremove -y --allow-remove-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /usr/share/perl* /usr/lib/*/perl* /usr/bin/perl* 2>/dev/null || true

# Official python images ship pip/setuptools/wheel under /usr/local — remove them
# from the runtime layer (gateway never needs them after install).
RUN rm -rf \
      /usr/local/lib/python*/site-packages/pip \
      /usr/local/lib/python*/site-packages/pip-*.dist-info \
      /usr/local/lib/python*/site-packages/setuptools \
      /usr/local/lib/python*/site-packages/setuptools-*.dist-info \
      /usr/local/lib/python*/site-packages/_distutils_hack \
      /usr/local/lib/python*/site-packages/distutils-precedence.pth \
      /usr/local/lib/python*/site-packages/pkg_resources \
      /usr/local/lib/python*/site-packages/wheel \
      /usr/local/lib/python*/site-packages/wheel-*.dist-info \
      /usr/local/bin/pip \
      /usr/local/bin/pip3 \
      /usr/local/bin/pip3.* \
      /usr/local/bin/wheel \
      /usr/local/bin/easy_install* \
    && find /usr/local/lib -type d -name 'jaraco*' -exec rm -rf {} + 2>/dev/null || true \
    && find /usr/local/lib -type d -name 'wheel*' -exec rm -rf {} + 2>/dev/null || true

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin meridian

COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY --chown=meridian:meridian config.yaml .

USER meridian

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/meridian/status', timeout=2)" \
    || exit 1

# Explicit asyncio loop — avoids uvloop edge cases in constrained containers.
CMD ["uvicorn", "meridian.api.main:app", "--host", "0.0.0.0", "--port", "8080", "--loop", "asyncio"]
