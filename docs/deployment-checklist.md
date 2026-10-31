# Deployment checklist

First deploy of med-analyzer to the home server, in the order it has to happen.
Nothing here has been applied: every step that touches another repository, the
server, Infisical, the portal or GitHub is yours to run.

The Ansible role (`ansible/roles/med-analyzer`) builds the image on the server,
runs the container, deploys the nginx location (everything behind the Discord
SSO gate) and registers the service with the portal. It needs the pieces below
to exist first.

## 1. Prerequisites outside this repository

### 1.1 Infisical secret

Create the secret `med-analyzer-db-password` in the same Infisical folder the
inventory already reads (`infisical_path` in
`ansible/inventories/home-server/group_vars/all.yml`, the home server's host
folder). Use a long random value; the role URL-encodes it into `DATABASE_URL`,
so any characters are fine.

The portal registration token needs no action: the inventory already reads
`meow-elite-club-portal-service-registration-token` (variable
`meow_elite_club_portal_service_registration_token`). It exists once the portal's
own role has been deployed.

### 1.2 `postgresql_users` entry (infra)

File: `infra/ansible/inventories/home-server/group_vars/all.yml`, list
`postgresql_users`. The existing entries use this shape (name, password from
Infisical, db); add one next to `bank-analyzer`:

```yaml
  - name: "med-analyzer"
    password: "{{ infisical_secrets.secrets['med-analyzer-db-password'] }}"
    db: "med-analyzer"
```

The `postgresql` role creates the user (or resets its password), creates the
database owned by it, and grants privileges. User and database names must match
`med_analyzer_db_user` and `med_analyzer_db_name` in this role (both
`med-analyzer`).

### 1.3 Domain, DNS and certificate (infra)

What infra does today for `bank-analyzer.zelgray.cherkasy.ua`:

- `host_domains` in the same `group_vars/all.yml` has an entry
  `- domain: 'bank-analyzer.zelgray.cherkasy.ua'` with `external: true`
  (`external: true` skips the default `include locations/<domain>.conf`; the
  service role supplies its own location into
  `<domain>-custom-locations/`).
- DNS needs nothing per service: `infra/terraform/cloudflare/zelgray.cherkasy.ua.tf`
  has a proxied wildcard `*` CNAME to the apex, which already covers
  `med-analyzer.zelgray.cherkasy.ua`.
- The certificate comes from the `certbot` role (`certbot_method: dns`,
  Cloudflare DNS-01) for every domain in `host_domains`.

So only the `host_domains` entry is missing. Add, next to the bank-analyzer one:

```yaml
  - domain: 'med-analyzer.zelgray.cherkasy.ua'
    external: true
```

Then apply it (nginx server block, then certificate and HTTPS block; same tags
infra's CLAUDE.md documents):

```bash
cd infra/ansible
ansible-playbook -i inventories/home-server -vv playbooks/home-server.yml --tags nginx
ansible-playbook -i inventories/home-server -vv playbooks/home-server.yml --tags certbot
```

This also creates `/opt/docker/nginx/conf.d/med-analyzer.zelgray.cherkasy.ua-custom-locations/`,
the directory this role writes its location file into. Without it the nginx
task of this role fails.

## 2. Deploy commands

Credentials for every playbook run (Infisical machine identity):

```bash
export INFISICAL_API_URL=...
export INFISICAL_CLIENT_ID=...
export INFISICAL_CLIENT_SECRET=...
```

In order:

```bash
# 1. Infra: create the PostgreSQL user and database (after 1.1 and 1.2)
cd infra/ansible
ansible-playbook -i inventories/home-server -vv playbooks/home-server.yml --tags postgresql

# 2. Infra: domain, nginx, certificate (1.3), if not done yet

# 3. This repository
cd med-analyzer
./install_dependencies.sh
cd ansible
ansible-playbook -i inventories/home-server playbooks/deploy.yml
```

Run the image build test from section 4 (risk 1) before step 3.

After the first deploy the service is registered with the portal but nobody has
access yet: open the portal's `/admin/services`, find `med-analyzer` and grant
access to yourself (a Discord user or a guild). Until then every request is
refused by the gate.

## 3. Post-deploy verification

1. `docker ps` on the server: `med-analyzer` is up and `healthy`;
   `docker logs med-analyzer` shows both Alembic migrations applied on the first
   start and uvicorn listening.
2. `https://med-analyzer.zelgray.cherkasy.ua/` in a private window: you are
   redirected to the Discord login of the portal, then back to the app.
3. Open the profile page and fill in sex and date of birth (reference ranges for
   a sex or age group depend on them).
4. Upload one PDF of each kind: Lab Service, Synevo, and the second Synevo
   layout. Each upload should redirect to a result page with values, reference
   ranges and out-of-range marks.
5. Open the dynamics page, pick an analyte and check the chart.
6. Upload a PDF larger than 10 MB (any dummy file with a `.pdf` name): the app
   refuses it. A file between 10 and 11 MB reaches the app and is refused by it
   with the size message; above 11 MB nginx answers 413.
7. `curl -I https://med-analyzer.zelgray.cherkasy.ua/healthz` without a session
   redirects to login: there is no ungated path.
8. On the server, `ls -ln /opt/docker/med-analyzer/` shows uploads under a
   numeric per-user directory owned by uid 1000, mode `0700`.

## 4. First-deploy risks

1. **dnf mirror 401.** On the machine this repository was developed on, the
   AlmaLinux mirrorlist resolves to the home server's own mirror path
   (`portal.zelgray.cherkasy.ua/almalinux/...`), which answers 401, so
   `docker build` of `sources/Dockerfile` fails at `dnf install`. The same
   Dockerfile shape is used by discord-meow-bot. Test the build on the server
   before deploying (a PDF-free copy of the build context, throwaway tag).
   `home-server` below is the Ansible inventory name, not necessarily an SSH
   alias: use the real host (`ansible_host` in
   `ansible/inventories/home-server/hosts.yml`) and your login, and `sudo docker`
   if your user is not in the `docker` group:

   ```bash
   rsync -a --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
       --exclude='*.[pP][dD][fF]' --exclude='/tests' --exclude='/docs' \
       sources/ home-server:/tmp/med-analyzer-build-test/
   ssh home-server 'docker build -t med-analyzer:probe /tmp/med-analyzer-build-test \
       && docker rmi med-analyzer:probe; rm -rf /tmp/med-analyzer-build-test'
   ```

   If it fails with a 401/404 from dnf: either open the mirror path to the
   Docker network, or make the Dockerfile use the official `baseurl` repos
   (one `sed` after each `FROM`, switching `mirrorlist=` lines to the
   commented `baseurl=` lines in `/etc/yum.repos.d/*.repo`; this is how the
   image was verified locally).
2. **Infra entries missing.** Until sections 1.1 to 1.3 are applied the
   container cannot log in to PostgreSQL (the migration fails and the container
   exits with code 1) and the nginx task cannot find its directory.
3. **rsync keeps file modes.** The application files are root-owned in the
   image and the container runs as uid 1000, so every file under `sources/`
   must be world-readable. A file with mode `0600` on your machine stays `0600`
   in the build context and in the image, and the app fails to start or import
   it. Check before deploying; the command must print nothing:

   ```bash
   find sources \( -type f ! -perm -o=r \) -o \( -type d ! -perm -o=rx \)
   ```
4. **`no_log` hides container-task errors.** The container task carries the
   database password, so its failures are censored. To debug, run the same
   `docker run` by hand on the server (use `docker inspect med-analyzer` and
   `docker logs med-analyzer`), or temporarily switch `no_log` off in a local
   working copy, never in a committed change.
5. **Old files in `/opt/docker/med-analyzer`.** An earlier version of the role
   synced the sources into the data directory. If that ever ran, delete the
   stale source files by hand (keep only per-user upload directories); the
   build directory is now `/opt/docker/med-analyzer-build`.
6. **Host uid 1000 can read the uploads.** The data directory is owned by uid
   1000 with mode `0700`; whichever host account has uid 1000 can read the lab
   reports. Check with `getent passwd 1000`.
7. **SELinux.** The data directory is a plain bind mount (no `:z`). If
   `getenforce` prints `Enforcing`, the container may get "permission denied" on
   `/data`; the app then fails on the first upload (health stays green). Check
   `ausearch -m avc -ts recent` after the first upload.
8. **No backup of this service (found while reading infra).** infra's `backup`
   playbook targets only the `vds` host group; no backup variables exist in the
   home-server inventory. The `med-analyzer` database and the uploaded PDFs on
   the home server are therefore not covered by the restic backups. Decide
   whether to extend infra's backup to the home server, or take dumps by hand
   (section 5). Minimum recommendation: a nightly cron job on the server that
   runs the `pg_dump` from section 5 and a `tar` of `/opt/docker/med-analyzer`,
   keeping the copies on another machine.
9. **Portal dependency.** The registration task calls the portal's API from the
   server on every deploy and fails the deploy if the portal is down or the
   token is wrong (the error output is hidden by `no_log`; test the token and
   URL by hand if it fails).
10. **Gate scope.** The location covers everything, including `/healthz`. The
    app trusts `X-Discord-User-Id` completely, so the container must never be
    published on a host port or reachable from outside the Docker network
    (the role publishes none).

11. **Mutable collection ref.** `requirements.yml` pins the Infisical Ansible
    collection to a branch (`feat/read-secrets-include-imports`), which can move
    or disappear, so a later `./install_dependencies.sh` may install different
    code. Consider pinning it to a commit SHA. It is not changed here: the
    collection is shared with other projects of the owner.

Other services have bookkeeping in the infra repository that this one does not
yet (read-only look, nothing applied): a row in `README.md`'s services table,
a row in `PROJECTS_DEPLOY.md` (the source list for Semaphore task templates),
an uptime monitor in `terraform/uptimerobot/main.tf`, and a tick in
`docs/portal-architecture.md`'s list of gated services. Add them if you want
parity.

## 5. Rollback and data

| What | Where |
|---|---|
| Container | `med-analyzer` |
| Uploaded PDFs | `/opt/docker/med-analyzer` (`docker_volumes_directory`), mounted at `/data` |
| Docker build context | `/opt/docker/med-analyzer-build` |
| nginx location | `/opt/docker/nginx/conf.d/med-analyzer.zelgray.cherkasy.ua-custom-locations/med-analyzer.conf` |
| Database | database and user `med-analyzer` in the `postgresql` container |

Stop the service (data kept):

```bash
docker stop med-analyzer
```

The shared nginx then answers 502 for the subdomain. To go back to the previous state
completely: stop and remove the container, delete the nginx location file and
reload nginx (`docker exec nginx-server nginx -s reload`), and remove the
service in the portal's `/admin/services`. Keep the database and the data
directory until you are sure.

Dump the database (same method as infra's backup script):

```bash
docker exec postgresql pg_dump -U postgres med-analyzer | gzip > med-analyzer-$(date +%F).sql.gz
```

Copy the uploads together with the dump (`tar -C /opt/docker -czf med-analyzer-data-$(date +%F).tar.gz med-analyzer`;
run as root, the directory is `0700` and owned by uid 1000). Restore: the same
two steps backwards into an empty database and directory, then redeploy.

A bad release: check out the previous commit and run the deploy again. The
container applies `alembic upgrade head` on every start and never downgrades,
so after a release that changed the schema the older image runs against the
newer schema: restore the dump (or run `alembic downgrade` by hand) first.

## 6. What the public repository contains

The repository is public. Facts, and the owner's decisions:

- **Fixtures.** `tests/fixtures/*.json` keep the word layout of real lab
  reports, but every measured value, every date (order, sampling, issue and
  print), the ages and the collection point number are RANDOMISED; the
  printed ▲/▼ flags were recomputed for the new values. The patient name,
  date of birth, order numbers, barcode and performer name are fakes
  (`tests/test_fixtures_clean.py` checks the identity fields); the physician
  and diagnosis fields are empty. What stays as printed is public: analyte
  names, units, reference ranges, the column layout and, in the Synevo
  fixtures, the laboratory's header (name, address, accreditation).
- **Identifiers kept on purpose.** The owner's name and e-mail (git
  authorship, `LICENSE`), the `ansible_user` in `hosts.yml`, the GitHub
  handle in `requirements.yml`, and the hostnames and the
  `infisical_project_id` in the inventory. None are secrets.
- **History.** It was rewritten once, before the first push, to drop real lab
  values; the repository has never contained them on GitHub.
- **Specification and plan.** `docs/superpowers/` describes the project from
  the owner's point of view ("the owner").
- **Local PDFs.** The real lab PDFs in the working copy are excluded by
  `.gitignore`, `sources/.dockerignore` and the rsync excludes of the role (all
  match the extension in any case); `git ls-files '*.[pP][dD][fF]'` should print
  nothing.
