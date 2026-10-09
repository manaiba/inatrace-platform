#!/bin/sh
# Puts a backup back: the database and the uploads as they were at <time>,
# replacing what is there now. Run it here, next to compose.yaml:
#
#   ./restore.sh                 list the backups
#   ./restore.sh <time>          restore one, e.g. 20261008T120000Z
#
# The backend is stopped meanwhile and started again at the end.
set -eu
cd "$(dirname "$0")"

if [ $# -eq 0 ]; then
    echo "Backups (restore one with ./restore.sh <time>):"
    ls -1 backups/*-db.sql.gz 2>/dev/null | sed 's|backups/||; s|-db.sql.gz||' || echo "  none"
    exit 0
fi
stamp=$1
db="backups/$stamp-db.sql.gz"
storage="backups/$stamp-storage.tar.gz"
for f in "$db" "$storage"; do
    [ -f "$f" ] || { echo "restore: $f not found (./restore.sh lists them)" >&2; exit 1; }
done

echo "restore: stopping the backend"
docker compose stop backend
docker compose up -d --wait mysql

echo "restore: database <- $db"
gunzip -c "$db" | docker compose exec -T -u mysql mysql sh -c '
    mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "DROP DATABASE IF EXISTS inatrace" &&
    exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD"'

echo "restore: uploads <- $storage"
docker compose run --rm -T --no-deps tools sh -c \
    'find /data/storage -mindepth 1 -delete && tar -C /data/storage -xzf -' < "$storage"

echo "restore: starting the backend"
docker compose up -d
echo "restore: done, $stamp"
