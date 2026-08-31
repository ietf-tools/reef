#!/bin/bash
#
# Environment config:
#   CONTAINER_ROLE - backend, beat, celery, or migrations
#
case "${CONTAINER_ROLE:-backend}" in
    backend)
        exec ./backend-start.sh
        ;;
    beat)
        exec ./celery-start.sh beat --loglevel=INFO
        ;;
    celery)
        # Both queues: precompute is separated so a long precomputer run cannot sit
        # in front of subscription mail, not so that it goes unserved.
        exec ./celery-start.sh worker --loglevel=INFO --queues=celery,precompute
        ;;
    migrations)
        exec ./migration-start.sh
        ;;
    *)
        echo "Unknown role '${CONTAINER_ROLE}'"
        exit 255
        ;;
esac
