#!/usr/bin/env sh
# Online backup of the Meridian Cloud database + node CA + session key.
# Run as root from deploy/cloud (the CA key is readable only by the service
# user), e.g. daily from root's crontab. Keeps the last 14 backups.
#
#   0 3 * * * cd /opt/meridian/deploy/cloud && ./backup.sh >> backup.log 2>&1
set -eu
stamp="$(date +%Y%m%d-%H%M%S)"
mkdir -p backups
docker compose exec -T control meridian-control backup --out "/var/lib/meridian-control/backup-${stamp}.db"
# The CA key and session key are what make existing node certificates and
# sessions valid after a restore; keep them with the database (and private).
tar -czf "backups/meridian-${stamp}.tar.gz" -C data "backup-${stamp}.db" ca
rm -f "data/backup-${stamp}.db"
chmod 600 "backups/meridian-${stamp}.tar.gz"
ls -1t backups/meridian-*.tar.gz | tail -n +15 | xargs -r rm -f
echo "backup written: backups/meridian-${stamp}.tar.gz"
