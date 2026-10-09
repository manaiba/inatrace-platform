#!/bin/sh
# Dumps MySQL and archives the uploads into backups/, as <time>-db.sql.gz and
# <time>-storage.tar.gz, then removes those older than INATRACE_BACKUP_DAYS days (from
# .env, default 7): the one just made always stays. Run it here, next to compose.yaml,
# with the stack up:
#
#   ./backup.sh
#   ./backup.sh --scheduled   what cron runs (INATRACE_BACKUP_SCHEDULE): with the stack
#                             down, it skips, rather than fail every time
#
# The copies stay on this server: copying them elsewhere is up to you.
set -eu
cd "$(dirname "$0")"

days=$(sed -n 's/^INATRACE_BACKUP_DAYS=//p' .env 2>/dev/null | tail -n 1)
days=${days:-7}
case $days in
    ''|*[!0-9]*|0) echo "backup: INATRACE_BACKUP_DAYS must be a number of days, 1 or more" >&2; exit 1 ;;
esac
stamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p backups
[ "${1:-}" = --scheduled ] && echo "backup: scheduled, $(date -u '+%Y-%m-%d %H:%M UTC')"
if [ -z "$(docker compose ps -q --status running mysql 2>/dev/null)" ]; then
    echo "backup: MySQL is not running (inatrace deploy up starts it)"
    [ "${1:-}" = --scheduled ] && exit 0
    exit 1
fi

echo "backup: database -> backups/$stamp-db.sql.gz"
docker compose exec -T -u mysql mysql sh -c \
    'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines --triggers --databases inatrace' \
    | gzip > "backups/$stamp-db.sql.gz.part"
mv "backups/$stamp-db.sql.gz.part" "backups/$stamp-db.sql.gz"

echo "backup: uploads -> backups/$stamp-storage.tar.gz"
docker compose run --rm -T --no-deps tools tar -C /data/storage -czf - . \
    > "backups/$stamp-storage.tar.gz.part"
mv "backups/$stamp-storage.tar.gz.part" "backups/$stamp-storage.tar.gz"

# Names start with their time, so older is smaller as text.
cutoff=$(date -u -d "@$(( $(date +%s) - days * 86400 ))" +%Y%m%dT%H%M%SZ)
ls -1 backups | awk -v cutoff="$cutoff" '/-(db\.sql|storage\.tar)\.gz$/ && substr($0, 1, 16) < cutoff' |
    while read -r old; do
        echo "backup: removing backups/$old (older than $days days)"
        rm -f "backups/$old"
    done
echo "backup: done, $stamp"
