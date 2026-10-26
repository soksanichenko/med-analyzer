# CLAUDE.md

## Project summary

Personal medical lab results tracker: PDF import and dynamics charts

## Project structure

```
med-analyzer/
├── ansible/                        # Deploy to the home server
│   ├── ansible.cfg
│   ├── inventories/home-server/
│   │   ├── hosts.yml
│   │   └── group_vars/all.yml
│   ├── playbooks/
│   │   ├── pre_tasks/infisical.yml
│   │   └── deploy.yml
│   └── roles/med-analyzer/         # builds image, deploys container
├── sources/                        # Docker build context
│   ├── Dockerfile                  # multi-stage almalinux:10, non-root uid 1000
│   ├── entrypoint.sh               # alembic upgrade head, then uvicorn
│   ├── alembic.ini
│   ├── requirements.txt            # runtime dependencies, fully pinned
│   ├── scripts/dump_words.py       # PDF -> anonymised word fixture
│   └── src/med_analyzer/           # FastAPI app: parsing/, routes_*.py, alembic/, templates/, static/
├── tests/                          # pytest (testcontainers PostgreSQL, needs Docker)
├── docs/
│   ├── deployment-checklist.md     # first deploy: infra prerequisites, order, risks, rollback
│   └── superpowers/                # design spec and implementation plan
├── install_dependencies.sh
├── pyproject.toml
├── requirements.txt                # Ansible tooling
├── requirements.yml                # Ansible Galaxy collections
└── .pre-commit-config.yaml
```

## Development

```bash
.venv/bin/pip install -r tests/requirements.txt
.venv/bin/python -m pytest                    # needs a running Docker daemon
docker build -t med-analyzer:test sources     # container image
```

Lab report PDFs (`*.pdf`) are personal data: git-ignored, excluded from the
image and from the Ansible source sync. Never commit them.

## Deployment (Ansible)

```bash
./install_dependencies.sh
cd ansible
ansible-playbook -i inventories/home-server playbooks/deploy.yml
```

Requires env: `INFISICAL_API_URL`, `INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`.

The role also deploys the nginx location (whole app behind the Discord SSO gate) and registers the service with the portal. The first deploy needs infra entries (PostgreSQL user, `host_domains`) first: see `docs/deployment-checklist.md`.
