FROM python:3.11-slim-bookworm

WORKDIR /app

# Non-root runtime user (Milestone K).
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin meridian

COPY pyproject.toml README.md ./
COPY meridian/ meridian/

RUN pip install --no-cache-dir . \
    && chown -R meridian:meridian /app

COPY --chown=meridian:meridian config.yaml .

USER meridian

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/meridian/status', timeout=2)" \
    || exit 1

CMD ["uvicorn", "meridian.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
