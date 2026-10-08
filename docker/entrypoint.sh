#!/bin/sh
# Apply database migrations, then start the given command (uvicorn by default).
# Set RUN_MIGRATIONS=false when migrations are run separately (e.g. multiple replicas).
set -e

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    alembic upgrade head
fi

exec "$@"
