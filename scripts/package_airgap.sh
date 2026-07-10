#!/usr/bin/env bash
# Build offline install bundle: images + compose + docs.
# Usage: ./scripts/package_airgap.sh [image:tag]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAG="${1:-ghcr.io/imv-in/meridian:0.9.0}"
OUT="${ROOT}/dist/airgap-meridian"
mkdir -p "${OUT}"

echo "Saving image ${TAG} ..."
docker pull "${TAG}" || docker build -t "${TAG}" "${ROOT}"
docker save "${TAG}" -o "${OUT}/meridian-image.tar"

# Mock backends optional for air-gapped demo
if docker image inspect meridian-mock:local >/dev/null 2>&1; then
  docker save meridian-mock:local -o "${OUT}/mock-backend-image.tar" || true
fi

cp "${ROOT}/docker-compose.yml" "${OUT}/"
cp -a "${ROOT}/configs" "${OUT}/configs"
cp "${ROOT}/docs/AIRGAP.md" "${OUT}/" 2>/dev/null || true
cp "${ROOT}/docs/DEPLOY.md" "${OUT}/" 2>/dev/null || true
cp "${ROOT}/scripts/smoke_test.py" "${OUT}/" 2>/dev/null || true

cat > "${OUT}/load-images.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
docker load -i "${DIR}/meridian-image.tar"
[[ -f "${DIR}/mock-backend-image.tar" ]] && docker load -i "${DIR}/mock-backend-image.tar"
echo "Images loaded. See AIRGAP.md / DEPLOY.md"
EOF
chmod +x "${OUT}/load-images.sh"

( cd "${OUT}/.." && tar czf airgap-meridian.tgz airgap-meridian )
echo "Bundle: ${ROOT}/dist/airgap-meridian.tgz"
