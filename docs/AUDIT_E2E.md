# Audit pipeline — production-like end-to-end test

This runs the tamper-evident audit pipeline against a **real** stack: the
Meridian gateway talking to your local Ollama `qwen2.5:0.5b`, publishing audit
events through **Redpanda**, with the consumer signing Merkle roots and
archiving them to **MinIO** under **S3 Object Lock**. A verifier then proves the
archive is authentic and immutable.

```
HOST                                   DOCKER (docker-compose.audit.yaml)
────────────────────────────          ───────────────────────────────────────
Ollama qwen2.5:0.5b :11434             Redpanda  (kafka :19092 ext / :9092 int)
   ▲                                   MinIO     (s3 :9000 / console :9001)
   │                                   createbuckets (mc mb --with-lock)
Meridian gateway (uvicorn :8080)       createtopic   (rpk topic -p 1)
   │  publish AuditEvent ──────────▶   audit-consumer  (sign + archive)
   │     to localhost:19092                  │ hash chain → Merkle → Ed25519
verify_audit_archive.py ◀──────────── s3://meridian-audit (Object Lock COMPLIANCE)
```

## Prerequisites

```bash
ollama pull qwen2.5:0.5b
ollama serve                      # leave running (localhost:11434)
pip install -e ".[dev]"           # installs aiokafka, boto3, cryptography
```

## Run it

```bash
# 1. Generate the Ed25519 signing keypair → audit_keys/ (gitignored).
python scripts/gen_audit_keys.py

# 2. Bring up Redpanda + MinIO (locked bucket + 1-partition topic) + consumer.
docker compose -f docker-compose.audit.yaml up -d --build

# Watch the consumer in a second terminal; look for "Archived to S3: ...".
docker compose -f docker-compose.audit.yaml logs -f audit-consumer

# 3. Start the gateway on the host, pointed at Ollama + the audit bus.
MERIDIAN_CONFIG=configs/local_gpu_audit.yaml \
  uvicorn meridian.api.main:app --host 0.0.0.0 --port 8080

# 4. Generate real traffic through qwen (batch-size is 8; this forces flushes).
python scripts/smoke_test.py --url http://localhost:8080 --model qwen2.5:0.5b
for i in $(seq 1 20); do
  curl -s localhost:8080/v1/chat/completions \
    -H 'content-type: application/json' \
    -d '{"model":"qwen2.5:0.5b","messages":[{"role":"user","content":"ping"}]}' >/dev/null
done

# 5. (optional) Watch events land on the single partition, in order.
docker compose -f docker-compose.audit.yaml exec redpanda \
  rpk topic consume meridian-audit-logs --num 5

# 6. Wait ~10s for a flush, then verify authenticity + immutability.
python scripts/verify_audit_archive.py
```

## What this proves (and which review finding it exercises)

| Step | Property | Finding |
|------|----------|---------|
| `verify_audit_archive.py` checks each root against `audit_public.pem` | Signatures are authentic against a **pinned** key | #1 |
| same script verifies the bundle **fails** against a freshly generated key | Embedded key is not self-trusted | #1 |
| script attempts to delete the locked object **version** → refused | Object Lock COMPLIANCE makes archives immutable | #2 |
| `rpk topic consume` shows one partition | Constant-key routing → deterministic chain order | #8 |
| consumer logs `Archived to S3` only after upload+commit | Reset/commit only after a durable write | #3, #5 |
| events contain `request_id`/`model`/`backend`/`status`/`latency` but **no prompt** | Metadata-only audit logs | repo rule |

A green run prints:

```
✓ AUTHENTICITY: all roots verify against the pinned key; wrong key rejected.
✓ IMMUTABILITY: delete of locked version was refused (AccessDenied) — archived roots are immutable under Object Lock.
ALL CHECKS PASSED — the audit archive is authentic and tamper-evident.
```

## Cleanup

```bash
docker compose -f docker-compose.audit.yaml down -v   # also wipes the MinIO volume
```

> **Note:** Objects are written with COMPLIANCE retention (365 days by default),
> which *cannot* be deleted — even by the root user — until it expires. The only
> way to reclaim that storage is to drop the `minio-data` volume (`down -v`).
> The bucket here is disposable and exists solely for this test.

## Targeting real AWS S3 instead of MinIO

Point the consumer at AWS (drop `--s3-endpoint`, set a real `--s3-region` and a
bucket created with Object Lock), export real AWS creds, and run the verifier
without `--endpoint`:

```bash
aws s3api create-bucket --bucket my-meridian-audit --region us-east-1 \
  --object-lock-enabled-for-bucket
python scripts/verify_audit_archive.py --endpoint https://s3.us-east-1.amazonaws.com \
  --bucket my-meridian-audit --region us-east-1
```
