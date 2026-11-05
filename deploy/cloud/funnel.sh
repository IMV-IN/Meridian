#!/usr/bin/env sh
# Publish (or unpublish) Meridian Cloud with Tailscale Funnel — free HTTPS on
# your *.ts.net name, no port forwarding, works behind CGNAT / mobile data.
#
#   ./funnel.sh on     expose 443 (console + API) and 8443 (GPU nodes)
#   ./funnel.sh off
#   ./funnel.sh status
#
# Raw TCP forwarding keeps TLS end-to-end to Caddy, which is what lets GPU
# nodes authenticate with client certificates. PROXY v2 passes the real
# client address through for per-IP rate limits.
set -eu
case "${1:-status}" in
  on)
    tailscale funnel --bg --proxy-protocol=2 --tcp=443 tcp://127.0.0.1:8441
    tailscale funnel --bg --proxy-protocol=2 --tcp=8443 tcp://127.0.0.1:8442
    tailscale funnel status
    ;;
  off)
    tailscale funnel --tcp=443 off || true
    tailscale funnel --tcp=8443 off || true
    ;;
  status)
    tailscale funnel status
    ;;
  *)
    echo "usage: $0 on|off|status" >&2
    exit 2
    ;;
esac
