#!/usr/bin/env bash
# Local Linux / WSL2 alternative to Docker. Does not modify any existing instance.
set -euo pipefail
port="${SEEKDB_PORT:-2881}"
base="${SEEKDB_BASE_DIR:-$HOME/.local/share/localseek-seekdb}"
command -v seekdb >/dev/null || { echo 'Install seekdb 1.4 first: https://github.com/oceanbase/seekdb'; exit 1; }
command -v mysql >/dev/null || { echo 'Install a MySQL client first (Ubuntu: sudo apt install default-mysql-client).'; exit 1; }
export MYSQL_PWD="${SEEKDB_PASSWORD:-}"
sql=(mysql --no-defaults --protocol=TCP --host=127.0.0.1 --port="$port" --user="${SEEKDB_USER:-root}" --connect-timeout=2 --batch)
if "${sql[@]}" --execute='SELECT VERSION(), 1+1; CREATE DATABASE IF NOT EXISTS stepfun;' 2>/dev/null; then
  echo "Reusing SQL instance on port $port. Check that VERSION identifies seekdb 1.4+."
  exit 0
fi
if ss -lnt | awk '{print $4}' | grep -q ":${port}$"; then echo "Port $port is occupied; refusing to start another instance."; exit 1; fi
mkdir -p "$base"
seekdb --nodaemon --base-dir "$base" --port "$port" \
  --parameter memory_limit=3G --parameter datafile_size=2G \
  --parameter datafile_maxsize=5G --parameter log_disk_size=3G --parameter cpu_count=0 --log-level WARN &
db_pid=$!
for attempt in $(seq 1 120); do
  if "${sql[@]}" --execute='SELECT VERSION(), 1+1; CREATE DATABASE IF NOT EXISTS stepfun;' 2>/dev/null; then
    echo "seekdb ready: $base (port $port). Keep this terminal open."
    wait "$db_pid"
    exit $?
  fi
  kill -0 "$db_pid" 2>/dev/null || { echo "seekdb exited. Inspect $base/log/seekdb.log"; exit 1; }
  sleep 2
done
echo "SQL startup timed out. Inspect $base/log/seekdb.log; instance has not been removed."
exit 1
