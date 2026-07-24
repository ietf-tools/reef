#!/bin/bash -e

# Bare call to trap helps TERM signals exit during sleep
trap

if ! ./manage.py migrate --check; then
    echo "Unapplied migrations found, waiting to start..."
    sleep 5
    while ! ./manage.py migrate --check; do
        echo "... still waiting for migrations..."
        sleep 5
    done
fi

echo "Starting Pink API server..."

cleanup () {
    if [[ -n "${gunicorn_pid}" ]]; then
        echo "Terminating gunicorn..."
        kill -TERM "${gunicorn_pid}"
        wait "${gunicorn_pid}"
    fi
}
trap 'trap "" TERM; cleanup' TERM

gunicorn \
    -c /workspace/gunicorn.conf.py \
    --workers "${PINK_GUNICORN_WORKERS:-5}" \
    --max-requests "${PINK_GUNICORN_MAX_REQUESTS:-0}" \
    --timeout "${PINK_GUNICORN_TIMEOUT:-180}" \
    --bind :8000 \
    --log-level "${PINK_GUNICORN_LOG_LEVEL:-info}" \
    --capture-output \
    --access-logfile - \
    ${PINK_GUNICORN_EXTRA_ARGS} \
    pink.wsgi:application &
gunicorn_pid=$!
wait "${gunicorn_pid}"
