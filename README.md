# med-analyzer

Personal medical lab results tracker: PDF import and dynamics charts

Upload a lab report PDF (Lab Service and Synevo layouts are supported), see the results with reference ranges and out-of-range marks, and follow an analyte over time on the dynamics page. Each Discord user (identified by the `X-Discord-User-Id` header set by the SSO gate in front of the app) sees only their own data.

## Layout

- `sources/` - Docker build context: `src/med_analyzer` (the FastAPI app, Alembic migrations under `alembic/`), `alembic.ini`, `entrypoint.sh`, `Dockerfile`, pinned `requirements.txt`
- `tests/` - pytest suite (needs a running Docker daemon: a throwaway `postgres:18` is started with testcontainers)
- `docs/` - [deployment checklist](docs/deployment-checklist.md) and `superpowers/` (design spec and implementation plan)
- `ansible/` - deploy to the home server

## Local development

```bash
python3 -m venv .venv
.venv/bin/pip install -r tests/requirements.txt
.venv/bin/python -m pytest
```

Run the app against a local PostgreSQL:

```bash
export DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/DB
export STORAGE_DIR=/tmp/med-analyzer-data
cd sources
PYTHONPATH=src ../.venv/bin/alembic upgrade head
PYTHONPATH=src ../.venv/bin/uvicorn --factory med_analyzer.app:create_app --port 8000
```

Every request except `/healthz` needs the `X-Discord-User-Id` header.

## Container image

```bash
docker build -t med-analyzer:test sources
```

The container needs `DATABASE_URL` and `STORAGE_DIR` (`/data`, a volume writable by uid 1000); optional `MAX_UPLOAD_MB` (default 10) and `LOG_LEVEL` (default `INFO`). It runs `alembic upgrade head` and then serves on port 8000 as an unprivileged user.

## Deployment

```bash
./install_dependencies.sh

cd ansible
ansible-playbook -i inventories/home-server playbooks/deploy.yml
```

Requires env: `INFISICAL_API_URL`, `INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`.

The first deploy needs entries in the infra repository (PostgreSQL user, nginx domain and certificate) and an Infisical secret first: see [docs/deployment-checklist.md](docs/deployment-checklist.md).

## License

[MIT](https://opensource.org/licenses/MIT)
