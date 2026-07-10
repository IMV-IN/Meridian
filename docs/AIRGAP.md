# Air-gapped install

## On a connected machine

```bash
./scripts/package_airgap.sh ghcr.io/imv-in/meridian:0.9.0
# produces dist/airgap-meridian.tgz
```

Copy `dist/airgap-meridian.tgz` to the offline host (USB, approved drop).

## On the offline host

```bash
tar xzf airgap-meridian.tgz && cd airgap-meridian
./load-images.sh
# Edit configs/ for your backends (IPs on the private network)
docker run --rm -p 8080:8080 \
  -v "$(pwd)/configs/mock_demo.yaml:/app/config.yaml:ro" \
  -e MERIDIAN_CONFIG=/app/config.yaml \
  ghcr.io/imv-in/meridian:0.9.0
```

No registry pulls required after `docker load`.

## Kubernetes air-gap

1. `docker load` the meridian image on each node or your private registry mirror.
2. `helm install` with `image.repository` / `image.tag` pointing at the local mirror.
3. Ensure `imagePullPolicy: IfNotPresent` or `Never`.
