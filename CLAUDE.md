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
│   └── src/med_analyzer/           # FastAPI app: parsing/, routes_*.py, alembic/, templates/, static/,
│                                   #   i18n.py + messages.py + analyte_names.py (interface language)
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

## Interface language and header

The UI is Ukrainian, English or Russian (`LANGUAGES` in `i18n.py`). The language
is the `lang` cookie set by the picker in the header (`static/header.js`), else
the browser's `Accept-Language`, else Ukrainian; pages are `no-store`, so it never
leaks through a cache. Texts live in `messages.py` (`{0}` placeholders; a test
keeps the three catalogs identical in keys and placeholders); the `js.*` entries
are handed to `dynamics.js` as a JSON block. Messages created away from a request
(upload errors, warnings stored in `analysis_sets.warnings`) are `Msg` values; a
stored warning is the JSON of one and a plain string (older rows) is shown as is.
Analyte names are Ukrainian in the database; English and Russian ones come from
`analyte_names.py` (a test checks it covers the seed).

The header has the portal link, the language picker and the user's Discord avatar
with a log-out button (`POST /logout` drops the portal's `zw_session` cookie of
the parent domain; the portal re-bridges while its own session is valid). The page
uses the portal's dark palette.

Static files are linked with `static_url()`, which adds `?v=<first 8 hex digits of
the MD5 of the file>`, so a changed file is never served stale from a browser or
Cloudflare. Use it for every CSS/JS/image link in a template.

## Deployment (Ansible)

```bash
./install_dependencies.sh
cd ansible
ansible-playbook -i inventories/home-server playbooks/deploy.yml
```

Requires env: `INFISICAL_API_URL`, `INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`.

The role also deploys the nginx location (whole app behind the Discord SSO gate) and registers the service with the portal. The first deploy needs infra entries (PostgreSQL user, `host_domains`) first: see `docs/deployment-checklist.md`.
