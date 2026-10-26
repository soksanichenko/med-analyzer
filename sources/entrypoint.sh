#!/bin/sh
# Container entrypoint: apply database migrations, then serve the app.
set -eu

# A failed migration aborts the container instead of serving a stale schema.
/app/venv/bin/alembic upgrade head

exec /app/venv/bin/uvicorn --factory med_analyzer.app:create_app \
    --host 0.0.0.0 --port 8000 --no-server-header
