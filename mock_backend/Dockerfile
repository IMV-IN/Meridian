FROM python:3.14-slim-trixie

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn

COPY server.py .

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "9000"]
