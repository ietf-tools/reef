#!/bin/bash
set -e -x -o pipefail

echo "Drop the DB if it exists..."
dropdb -U "$POSTGRES_USER" --if-exists "$POSTGRES_DB"

echo "Create an empty DB..."
createdb -U "$POSTGRES_USER" -T template0 "$POSTGRES_DB"

if [ -z "$DUMPFILE" ]; then
    echo "DUMPFILE not set, starting with an empty database."
elif [ -f "$DUMPFILE" ]; then
    echo "Import the DB dump into $POSTGRES_DB..."
    pg_restore --clean --if-exists --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$DUMPFILE"
else
    echo "No file to import, starting with an empty database."
fi

echo "Done!"
