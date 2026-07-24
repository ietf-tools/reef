#!/bin/bash -e
#
# Startup script for the celery container.
# Usage: celery-start.sh <worker|beat> [args...]
# Do not pass --app; set CELERY_APP in the environment.
#
CELERY=/usr/local/bin/celery
MANAGE_PY=./manage.py

trap

migrations_all_applied () {
    $MANAGE_PY migrate --check --database default
}

if ! migrations_all_applied; then
    echo "Unapplied migrations found, waiting to start..."
    sleep 5
    while ! migrations_all_applied; do
        echo "... still waiting for migrations..."
        sleep 5
    done
fi

echo "Starting Pink celery container..."

cleanup () {
  if [[ -n "${celery_pid}" ]]; then
    echo "Gracefully terminating celery process..."
    kill -TERM "${celery_pid}"
    wait "${celery_pid}"
  fi
}
trap 'trap "" TERM; cleanup' TERM

$CELERY --app="${CELERY_APP:-pink}" "$@" &
celery_pid=$!
wait "${celery_pid}"
