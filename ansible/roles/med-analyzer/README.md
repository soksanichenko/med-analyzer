# med-analyzer

Personal medical lab results tracker: PDF import and dynamics charts

## What it does

1. Creates the build directory `{{ med_analyzer_build_dir }}` and the data directory `{{ med_analyzer_data_dir }}` (owned by the container's unprivileged user, mode `0700`)
2. Syncs `sources/` from the Ansible controller to the build directory (Docker build context), excluding VCS data, virtualenvs, bytecode caches, `tests`, `docs` and every `*.pdf` — previously synced copies of those are deleted on the target too
3. Builds the Docker image locally on the target host from the synced source
4. Starts the container on the shared Docker network, connected to the shared `postgresql` container as the dedicated `med-analyzer` user. The container publishes no host port; the data directory is mounted at `/data`, where the app stores the uploaded PDFs
5. Deploys the nginx location config for `{{ med_analyzer_domain }}` into the shared nginx's `{{ med_analyzer_nginx_custom_locations_path }}` and reloads nginx. The whole app sits behind the Discord SSO gate (`meow-elite-club-portal`: `auth_request` + `/internal/bridge`); nginx forwards `X-Discord-User-Id` and `X-Discord-Avatar-Url` from the gate's answer. The container is reached by name through Docker's resolver, so nginx still starts while it is down. `client_max_body_size` is `med_analyzer_max_upload_mb + 1` MB
6. Registers the service with the portal (`POST /api/services/register`, Bearer token, `no_log`), which creates or updates its `GatedService` row

The image runs the database migrations on every start (`alembic upgrade head`) and serves the app on port 8000. Its `HEALTHCHECK` polls `/healthz`, and the container inherits it.

## Variables

| Variable | Default | Description |
|---|---|---|
| `med_analyzer_container_name` | `med-analyzer` | Docker container name |
| `med_analyzer_image_name` | `med-analyzer` | Image name (built on host) |
| `med_analyzer_image_tag` | `local` | Image tag (built on host) |
| `med_analyzer_image` | `{{ med_analyzer_image_name }}:{{ med_analyzer_image_tag }}` | Image reference used by both the build and the container |
| `med_analyzer_http_port` | `8000` | Port the container listens on (for the nginx upstream; not published on the host) |
| `med_analyzer_data_dir` | `{{ docker_volumes_directory }}/med-analyzer` | Uploaded PDFs on the target, mounted at `/data`. Never touched by the source sync |
| `med_analyzer_uid` / `med_analyzer_gid` | `1000` / `1000` | Owner of the data directory; must match the user in `sources/Dockerfile` |
| `med_analyzer_build_dir` | `{{ docker_volumes_directory }}/med-analyzer-build` | Docker build context on the target. Separate from the data directory because the sync deletes unknown files |
| `med_analyzer_local_source_dir` | `{{ playbook_dir }}/../..` | Project root on the Ansible controller |
| `med_analyzer_db_user` | `med-analyzer` | PostgreSQL user |
| `med_analyzer_db_name` | `med-analyzer` | PostgreSQL database |
| `med_analyzer_db_password` | *(none; set in inventory group_vars from Infisical `med-analyzer-db-password`)* | PostgreSQL password; URL-encoded into `DATABASE_URL` |
| `med_analyzer_max_upload_mb` | `10` | `MAX_UPLOAD_MB`: size limit of one uploaded PDF; nginx's `client_max_body_size` is this plus 1 MB |
| `med_analyzer_log_level` | `INFO` | `LOG_LEVEL` of the app |
| `med_analyzer_memory_limit` | `768m` | Memory limit of the container (`memory:`); `memory_swap:` gets the same value, so swap is not used. Guards the host against a crafted PDF that inflates while being parsed |
| `med_analyzer_domain` | `''` (set to `med-analyzer.zelgray.cherkasy.ua` in inventory) | Public subdomain |
| `med_analyzer_nginx_custom_locations_path` | `{{ nginx_confd_path }}/{{ med_analyzer_domain }}-custom-locations` | Where the location config is deployed |
| `nginx_docker_container_name` | `nginx-server` | Container reloaded after a config change |
| `nginx_confd_path` | `{{ docker_volumes_directory }}/nginx/conf.d` | `conf.d` of the shared nginx |
| `meow_elite_club_portal_public_domain` | `meow-elite.club` | Discord SSO gate: the portal's public domain. This host has no Docker network in common with the portal's VDS, so `/internal/zw-auth` and `/internal/bridge` reach it over the public internet |
| `med_analyzer_service_slug` | `med-analyzer` | `X-Service-Slug` sent to `/auth`; also the `slug` this role self-registers |
| `meow_elite_club_portal_service_registration_token` | *(none; set in inventory group_vars from Infisical `/hosts/shared` `meow-elite-club-portal-service-registration-token`)* | Bearer token for `POST /api/services/register` |

Also required from the inventory: `docker_volumes_directory`, `docker_network`, `postgresql_container_name`, `med_analyzer_domain`.

## Usage

```bash
cd ansible
ansible-playbook -i inventories/home-server playbooks/deploy.yml
```

Force container recreation regardless of source changes:

```bash
ansible-playbook -i inventories/home-server playbooks/deploy.yml \
  -e docker_force_recreate=true
```

## Notes

- The image is built on the **target host** from `sources/` synced by this role. It is not pulled from a registry.
- Connects to the shared `postgresql` container via a dedicated `med-analyzer` user/database (see infra's `postgresql_users`), never the shared superuser account. The scheme is `postgresql+psycopg://` (psycopg 3).
- Untrusted PDFs are bounded twice: the parser rejects a PDF with more than 30 pages or 50 000 words before it is parsed any further, and the container has a hard memory limit (`med_analyzer_memory_limit`) without swap.
- The container task runs with `no_log: true`: its environment carries the database password.
- Lab reports are personal data: the source sync and `sources/.dockerignore` both exclude `*.pdf`, so a PDF in the working copy never reaches the server or the image.
- Before the first deploy, infra's `home-server.yml` must have run (tags `nginx`, `certbot`) for `med-analyzer.zelgray.cherkasy.ua`: it issues the certificate and creates the `{{ med_analyzer_domain }}-custom-locations/` directory this role writes into. See `docs/deployment-checklist.md`.
- The gate enforces per-service access: after the first deploy registered the `GatedService` row, grant yourself access (individual Discord user or guild) under `/admin/services` on the portal. Until then every request is refused.
- There is no ungated location: `/healthz` is reachable only through the gate too. The container's own `HEALTHCHECK` talks to the app directly.
