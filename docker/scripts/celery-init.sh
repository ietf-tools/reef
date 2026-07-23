#!/bin/bash
#
# Startup script for the celery container.
#

cd /workspace

# Install Python dependencies
echo "Installing dependencies from requirements.txt..."
pip3 --disable-pip-version-check --no-cache-dir install --user --no-warn-script-location -r requirements.txt

CELERY=/home/dev/.local/bin/celery

# A bare trap helps TERM signals exit cleanly during sleep
trap

# Wait for the DB container
echo "Waiting for the DB container to come online..."
/usr/local/bin/wait-for db:5432 -- echo "PostgreSQL ready"

cleanup () {
  if [[ -n "${celery_pid}" ]]; then
    echo "Gracefully terminating the celery worker."
    kill -TERM "${celery_pid}"
    wait "${celery_pid}"
  fi
}
trap 'trap "" TERM; cleanup' TERM

echo "Starting the celery worker..."
watchmedo auto-restart \
          --patterns '*.py' \
          --directory . \
          --recursive \
          --debounce-interval 5 \
          -- \
          $CELERY --app="${CELERY_APP:-pink}" worker --loglevel=INFO "$@" &
celery_pid=$!

wait "${celery_pid}"
