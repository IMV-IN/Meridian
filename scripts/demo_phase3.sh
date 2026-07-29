#!/usr/bin/env bash
# Phase 3 flagship demo — isolation modes, key lifecycle API, canary rollback.
# Usage: scripts/demo_phase3.sh          (seeds keys, brings compose up, walk-through)
set -euo pipefail

BASE="http://localhost:${DEMO_PORT:-8181}"
CONFIG="configs/phase3_demo.yaml"
KEYDIR=".phase3_demo_keys"

ADMIN_KEY="mrdn_DemoAdmin000000000000000000aa"
ACME_KEY="mrdn_DemoAcme0000000000000000000aa"
GLOBEX_KEY="mrdn_DemoGlobex000000000000000aa"

step() { echo; echo "═══ $1 ═══"; }
quiet() { "$@" > /dev/null 2>&1; }

# ── 0. Seed the writable key store and bring the stack up ────────────────
# chmod 777: the container runs as uid 10001 (non-root hardening); a bind
# mount owned by your local user must be writable by that uid for the key
# lifecycle API to persist keys. Local demo dir only.
mkdir -p "$KEYDIR"
# Remove a previous run's file first — the container may have rewritten it as
# uid 10001, making it unwritable by your local user.
rm -f "$KEYDIR/keys.yaml"
chmod 777 "$KEYDIR"
cat > "$KEYDIR/keys.yaml" <<EOF
keys:
  - key: "$ADMIN_KEY"
    org_id: "ops"
    role: "admin"
  - key: "$ACME_KEY"
    org_id: "acme"
    role: "viewer"
  - key: "$GLOBEX_KEY"
    org_id: "globex"
    role: "viewer"
EOF

step "Bringing up gateway + 4 mock backends"
# Always recreate: the canary controller clock and rollback window live in the
# gateway process — reusing a previous run's container makes the timeline stale.
docker compose -f docker-compose.phase3.yaml up -d --build --force-recreate
echo -n "waiting for gateway"
until quiet curl -sf "$BASE/meridian/version"; do echo -n "."; sleep 1; done
echo " up."

canary_weight() {
  curl -s "$BASE/meridian/status" -H "Authorization: Bearer $ADMIN_KEY" \
    | grep -o '"canary": *{[^}]*}' | grep -o '"weight":[0-9.]*' | head -1 | cut -d: -f2
}

chat() {  # chat <api_key> [extra body fields]
  local key="$1" extra="${2:-}"
  curl -si "$BASE/v1/chat/completions" \
    -H "Content-Type: application/json" -H "Authorization: Bearer $key" \
    -d "{\"model\":\"demo-model\",\"messages\":[{\"role\":\"user\",\"content\":\"demo\"}]$extra}"
}

backends_of() { grep -o 'x-meridian-backend: [^[:space:]]*' | awk '{print $2}' | sort | uniq -c; }

# ── 1. Tenancy: no key → 401 ──────────────────────────────────────────────
step "1. Tenancy: request without a key → 401"
curl -si -X POST "$BASE/v1/chat/completions" -H "Content-Type: application/json" \
  -d '{"model":"demo-model","messages":[{"role":"user","content":"hi"}]}' | head -1

# ── 2. Isolation: acme is pinned to its own pool ──────────────────────────
step "2. Isolation: acme's requests ONLY land on acme-dedicated"
for i in $(seq 1 10); do chat "$ACME_KEY"; done | backends_of

# ── 3. Isolation: globex floods — never starves acme's pool ───────────────
step "3. Isolation: globex (unlisted) can never land on acme's backends"
for i in $(seq 1 12); do chat "$GLOBEX_KEY"; done | backends_of
echo "      ^ no acme-dedicated above — the pool is invisible to unlisted orgs"

# ── 4. Key lifecycle API: create → use → list → delete → dead ─────────────
step "4. Key lifecycle: create a key via API, use it instantly"
NEW_RESP=$(curl -si -X POST "$BASE/meridian/keys" \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ADMIN_KEY" \
  -d '{"org_id":"globex","role":"viewer"}')
NEW_KEY=$(echo "$NEW_RESP" | grep -o '"key": *"mrdn_[^"]*"' | head -1 | cut -d'"' -f4)
NEW_ID=$(echo "$NEW_RESP" | grep -o '"key_id": *"[^"]*"' | cut -d'"' -f4)
echo "   created (server returned it ONCE): $NEW_ID"
echo "   authenticate with it immediately:"
chat "$NEW_KEY" | head -1
echo "   GET /meridian/keys shows it redacted (id=$NEW_ID):"
curl -s "$BASE/meridian/keys" -H "Authorization: Bearer $ADMIN_KEY" \
  | grep -o "\"key_id\": *\"$NEW_ID\".*\"source\": *\"[^\"]*\""
echo "   deleting it…"
curl -s -X DELETE "$BASE/meridian/keys/$NEW_ID" -H "Authorization: Bearer $ADMIN_KEY" > /dev/null
echo "   next request with it:"
chat "$NEW_KEY" | head -1

# ── 5. Canary: 15% of globex's traffic rides the canary pool ──────────────
step "5. Canary rollout at 15%: watch the split over 20 requests"
for i in $(seq 1 20); do chat "$GLOBEX_KEY"; done | backends_of
echo "      ^ ~3 of 20 on canary-1 (random per request; run bigger loops to converge)"

# ── 6. Time-based promotion: the schedule raises canary to 100% itself ────
step "6. Canary schedule promotes 15% → 100% (time-based, no operator input)"
echo "   current weight: $(canary_weight)%  — waiting for the schedule to advance…"
deadline=$((SECONDS + 75))
until [ "$(canary_weight)" = "100.0" ] || [ $SECONDS -ge $deadline ]; do
  sleep 2; echo -n "."
done
echo
echo "   weight after schedule advance: $(canary_weight)%"
for i in $(seq 1 6); do chat "$GLOBEX_KEY"; done | backends_of

# ── 7. Canary health goes bad → automatic rollback ───────────────────────
# Alternating fail/success: a *flaky* rollout. Consecutive 5xx would trip the
# health checker's passive-failure ejection instead of the canary's error
# window — a failing rollout is interleaved 5xx+200, which is exactly the
# signal the rollback window exists for.
step "7. Canary starts serving intermittent 5xx → Meridian rolls it back"
for i in $(seq 1 10); do
  chat "$GLOBEX_KEY" ', "mock_fail": true' > /dev/null
  chat "$GLOBEX_KEY" ', "mock_fail": true' > /dev/null
  chat "$GLOBEX_KEY" > /dev/null
done
echo "   forced 20 failures interleaved with successes; waiting two ticks…"
sleep 7
echo "   /meridian/status → canary block:"
curl -s "$BASE/meridian/status" -H "Authorization: Bearer $ADMIN_KEY" \
  | grep -o '"canary": *{[^}]*}' | head -1
echo "   traffic now (weight forced to 0):"
for i in $(seq 1 6); do chat "$GLOBEX_KEY"; done | backends_of
echo "   rollback counter:"
curl -s "$BASE/metrics" | grep meridian_canary_rollbacks_total || true

step "Demo done. Durable artifacts:"
echo "  keys written by the API are in $KEYDIR/keys.yaml"
echo "  tear down with: docker compose -f docker-compose.phase3.yaml down"
