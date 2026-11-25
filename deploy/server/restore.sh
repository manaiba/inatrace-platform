#!/bin/bash
# Puts a backup back: the database and the uploads as they were at <time>,
# replacing what is there now. Run it here, next to compose.yaml:
#
#   ./restore.sh                 list the backups
#   ./restore.sh <time>          restore one, e.g. 20261008T120000Z
#
# The backend is stopped meanwhile and started again at the end.
set -eu
set -o pipefail
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

engine=$(sed -n 's/^INATRACE_DATABASE=//p' .env | tail -n 1)
engine=${engine:-mysql}
case "$engine" in mysql) dbservice=mysql ;; postgresql) dbservice=postgres ;; *) echo "restore: unknown database engine" >&2; exit 1 ;; esac
gzip -t "$db"
tar -tzf "$storage" >/dev/null
header=$(gzip -cd "$db" | sed -n '1p')
case "$header" in
    '-- INATRACE_DATABASE=mysql') backup_engine=mysql ;;
    '-- INATRACE_DATABASE=postgresql') backup_engine=postgresql ;;
    '-- INATRACE_DATABASE='*) echo "restore: unknown backup database engine" >&2; exit 1 ;;
    *) backup_engine=mysql ;; # backups created before engine selection
esac
if [ "$engine" != "$backup_engine" ]; then
    echo "restore: backup uses $backup_engine but this instance uses $engine" >&2
    exit 1
fi

echo "restore: stopping the backend"
docker compose stop backend
docker compose up -d --wait "$dbservice"

echo "restore: database <- $db"
if [ "$engine" = postgresql ]; then
    docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS inatrace WITH (FORCE)" -c "CREATE DATABASE inatrace"'
    gunzip -c "$db" | docker compose exec -T postgres sh -c 'exec psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d inatrace'
else
    gunzip -c "$db" | docker compose exec -T -u mysql mysql sh -c '
        mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "DROP DATABASE IF EXISTS inatrace" &&
        exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD"'
fi

echo "restore: uploads <- $storage"
docker compose run --rm -T --no-deps tools sh -c \
    'find /data/storage -mindepth 1 -delete && tar -C /data/storage -xzf -' < "$storage"

echo "restore: starting the backend"
docker compose up -d backend
echo "restore: done, $stamp"
