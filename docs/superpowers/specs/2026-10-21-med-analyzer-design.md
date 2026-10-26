# med-analyzer — design

Date: 2026-10-21
Status: draft for approval

## 1. Intent

A small personal web app to keep medical lab results in one place and see how
they change over time. The owner uploads PDF reports from two Ukrainian labs
(Lab Service, Synevo); the app extracts every measured value together with the
lab's own reference range, stores it per user, and shows (a) a list of
uploaded result sets grouped by date and (b) a "dynamics" view where several
analytes are plotted together.

Why: the PDFs are one-off documents. Nobody can see at a glance that
hemoglobin has been below the norm for two years, or that platelets are
coming back into range. The reference ranges printed in every report are what
makes the numbers meaningful, so the app is built around them.

Users: the owner only, today. Family/friends are explicitly not expected, but
data is still stored per user (Discord ID) because that is how every other
service on this infrastructure works and it costs nothing extra.

## 2. Goals

1. Upload a PDF in either of the two supported formats; the app detects the
   format by content, not by filename.
2. Store every extracted result with: analyte, numeric value, unit, the
   reference range printed in the report, and the lab's out-of-range flag.
3. **Results page**: list of result sets (one set = one lab order; PDFs sharing an order number are merged), newest
   first, grouped by collection date. Each set shows lab, order number if
   present, count of values, count out of range, and expands to a table
   "analyte / value / reference".
4. **Dynamics page** (separate tab): the user picks one or more analytes; one
   chart shows all of them over time, plus a table "current value / reference /
   previous value / change".
5. Everything is scoped to the authenticated user; no query ever crosses users.

## 3. Non-goals (first version)

- Manual merge UI for analytes with similar names (the owner wants it
  "later"; see 6.4 for the automatic matching that ships now).
- Unit conversion between labs (g/L vs g/dL).
- Scanned/image PDFs and OCR. Both sample PDFs have a text layer.
- Any lab other than Lab Service and Synevo.
- Sharing between users, an admin area, notifications, data export,
  editing values by hand, interpretation of results.
- Mobile app. The pages should be usable on a phone, but nothing special.

## 4. Anti-goals (a failure even if it "works")

- **A wrong number shown as right.** A misparsed value or reference range is
  worse than a rejected upload. The parser must be strict: anything it cannot
  parse with certainty is reported to the user and not stored as data.
- Applying a reference range meant for another sex/age group.
- One user seeing another user's data.
- Personal data in the repository: the sample PDFs contain a real full name,
  date of birth and order numbers. They must never be committed. Test fixtures
  must be synthetic or fully anonymised (see 9).
- Personal values (results, names) in application logs.

## 5. Decisions made by the owner

- Upload flow: the user uploads a set of analyses; sets are listed by date;
  dynamics live on a separate tab/section.
- Dynamics use the **reference values** from the PDFs, the current values and
  charts. Several analytes can be shown on one chart at once, in
  **"% of norm"** mode (one shared chart, not stacked panels).
- Sex and age are needed to pick the right reference range, so they are part
  of the user profile.
- Authentication: Discord SSO via `meow-elite-club-portal`, same as
  `bank-analyzer` / `hotline-listing`.
- Deployment target: the **home server** (like `bank-analyzer`), not the VDS.
- Stack: as fresh as possible, so there is no backlog of dependency bumps;
  runtime is the system Python of AlmaLinux 10 in an `almalinux:10` image
  (same approach as `discord-meow-bot`).

## 6. Behaviour

### 6.1 Users and profile

The user is identified by the `X-Discord-User-Id` header set by nginx after
the portal's `auth_request`. The app never authenticates anyone itself and
must only be reachable through nginx (the container is not published on a host
port). On first visit a user row is created with `discord_user_id`; the app
asks for **sex** and **date of birth** before the first upload. Age is always
computed **at the collection date of a result set**, never "today".

The patient name printed in a PDF is stored with the set as informational
text. The profile has no name field, so there is nothing to compare it with
by default; the app may remember the name from the first upload and show a
non-blocking warning when a later PDF carries a different one (my call: a
warning, never a rejection). Names are compared by surname plus the first
letter of each following token, so "Surname I. P." (Lab Service) and the full
name (Synevo) count as the same person; the fuller form is the one remembered.

### 6.2 Upload and parsing

- Accepts `application/pdf` only (check magic bytes, not just the extension),
  with a size cap (default 10 MB, configurable). Parsing never executes
  anything from the file. A PDF with more than 30 pages or 50 000 words is
  rejected before further parsing, and the container has a hard memory limit
  (768 MB, swap disabled), so a crafted file cannot exhaust the host.
- The format is detected from content: Lab Service reports have the
  header "Результати досліджень № <number>" with a lab-service layout;
  Synevo reports contain "Сінево (Synevo)". Unknown format → clear error, no
  data stored.
- Parsed output of one PDF = lab, collection date, order number and a list of
  result rows. The order number is the number in the Lab Service title
  ("№ <number>") or, for Synevo, the 10-digit number at the end of the
  birth-date line (both confirmed by the owner). PDFs of one user with the
  same lab and order number are **merged into one result set** (owner's
  decision); a PDF without an order number forms its own set.
- **Collection date**: Lab Service — the order registration / sampling date
  printed under the title (Ukrainian month names, e.g. "09 вересень 2020");
  Synevo — "Дата замовлення". The print date ("Дата друку") is ignored.
- Numbers use a decimal comma ("4,63") or dot ("0.390"). Units such as
  "10^9/л" are kept as text.
- **Lab Service duplicates**: the same analyte (WBC, RBC, HGB, PLT) appears in
  the general block and in the analyzer ("Апаратні показники") block. One row
  per analyte per set is stored; the first occurrence wins, and a differing
  second value is reported as a parse warning in Russian ("Повтор показателя
  «<name>» с другим значением: <first> и <second>"). Parse errors shown to
  the user are Russian too; the English parser message is only logged.
- **Reference ranges** come in many shapes (see Appendix A): a plain
  "130 - 160"; one-sided "до 15", "<20.0", "≤0.4" (upper bound) and "≥1.2"
  (lower bound); and, in Synevo, a multi-line block of **groups**, each with
  a condition (sex, age band, or both, e.g. "Діти: 1-3 роки", "Дорослі:
  Чоловіки", "Чоловіки (20-60 років)", "Дорослі: 18-60 років") and its own
  bounds. The parser extracts every group it recognises as
  `{sex?, age_from?, age_to?, low?, high?}` and keeps the raw block. The app
  picks one group using the user's sex and age at the collection date. If no
  group matches unambiguously, the range is stored as missing (raw text kept)
  and the row is flagged, not guessed.
- **Threshold-style references** (e.g. cholesterol "<5.0 - відсутність
  ризику / <4.5 - для людей з ІХС", LDL by risk group, vitamin D categories)
  depend on a risk category the app does not know. They are stored as raw
  text only, with no numeric range, shown in the results list, and not
  plotted in %-of-norm mode (v1).
- The lab's own out-of-range arrows (▲ ▼ and "!") are stored as printed when
  present. Lab Service prints them; the Synevo samples have none in the text
  layer, so the flag is optional. The app always computes in/out of range
  from the value and the selected range itself; if a printed flag and the
  computed result disagree, a warning is shown.
- Unit text is normalised only for display and grouping (e.g. Synevo's
  wrapped "10^9 клітин/л" and Lab Service's "10^9/л" are the same unit
  string). No numeric conversion is done. Note that the same analyte can
  arrive in different units (MCHC: г/л in Lab Service, г/дл in Synevo); the
  %-of-norm chart still works because each point is normalised by its own
  range, but the tooltip shows each point's own unit.
- Non-numeric results (e.g. "negative", "<0.5") are stored as text, shown in
  the results list, and not plotted.
- Re-uploading the same file (same hash for the same user) is rejected with a
  link to the set that already holds it. A different file with an existing
  lab + order number is merged into that set (see 6.6).
- Original PDFs are kept on disk (per-user directory, content-hash name) so
  sets can be re-parsed after parser improvements. Deleting a set deletes all
  its PDFs and rows.
- After upload the user sees what was extracted (count, warnings, rows that
  could not be parsed). Storing happens immediately; the user can delete a
  set. *(Builder's call: a confirm step before saving is acceptable if it
  stays small.)*

### 6.3 Results page

Sets grouped by collection date, newest first. A set shows lab, order number,
number of values and number out of range; expanding it shows the table with
out-of-range values highlighted. A set can be deleted.

### 6.4 Analytes and matching

Different labs name the same analyte differently ("Концентрація гемоглобіну
(HGB)" in Lab Service, "Гемоглобін" in Synevo). The app keeps a dictionary:
analyte (canonical name + abbreviation) with aliases. On upload, a row is
matched by, in order: the abbreviation in parentheses (HGB, WBC, NEUT%…);
then the normalised name (lower-cased, punctuation and whitespace collapsed)
against known aliases. The dictionary is seeded with the analytes present in
the two sample reports plus the common CBC set. A row that matches nothing
creates a new analyte from its normalised name, so nothing is dropped and the
user can still chart it; merging such duplicates by hand is a later feature.

### 6.5 Dynamics page

- The user picks analytes from the list of those they have at least one
  numeric result for (search box recommended: the list is long).
- **One chart, "% of norm" scale**: for every point,
  `percent = (value − low) / (high − low) × 100` using the reference range
  attached to **that** result (labs and age groups change over time, so each
  point uses its own range). A single shaded band 0–100 % marks the norm.
  Points outside the band are highlighted.
- One-sided ranges: "до X" is treated as `low = 0`. A lower-bound-only range
  ("від X") has no upper bound and therefore no percentage; such a result is
  excluded from the chart and the page says so. *(Builder may show absolute
  values for those as a clearly labelled fallback.)*
- Tooltip shows the real value, unit, the date and the reference range.
- Below the chart: a table with, for each chosen analyte, the current
  (latest) value, its reference range, the previous value, and the change.
- X axis is the collection date; a date range filter is optional.

### 6.6 Edge-case rules (builder-check answers; all are my calls)

- **Choosing a Synevo reference group.** Ages are in whole years from the
  profile's birth date to the collection date. Groups the parser recognises:
  child age bands (months/years) and "Дорослі" with sex sub-groups. A child
  band is half-open `[from, to)`, so age 3 matches "3-5 років", never
  "1-3 роки". From 18 on, the "Дорослі" group is used, with the sex-specific
  line if present, otherwise the sex-neutral one. Age expressions seen so
  far: "<1 року", "1-6 років", ">50 років", "2-12 міс.", "0-6 днів",
  "від 18 років", "Новонароджені" — parsed into days/months/years bounds.
  Nested adult forms ("Дорослі: 18-60 років: …", "Дорослі: Чоловіки: …",
  "Чоловіки (17-50 років): …") are flattened into one group each, with sex
  and age combined. Groups with an unrecognised label ("Вагітні жінки",
  trimester lines, "При дефіциті заліза…", footnote text) are ignored for
  selection but remain in the raw block. If zero or more than one group
  matches, the range is stored as missing with its raw text. Unit tests
  cover boundary ages 0, 1, 3, 17, 18 and 60, a missing sex line, and the
  ESR, glucose and ferritin blocks from `synevo-2.pdf` (anonymised).
- **Profile changes after upload.** Every result stores all parsed reference
  groups as JSON next to the selected `low`/`high`. Saving a changed sex or
  birth date re-selects `low`/`high` for all of that user's results from the
  stored groups; PDFs are not re-parsed.
- **Extreme %-of-norm values.** Data is never clamped. The chart's value axis
  is limited to −100 % … 300 %; points beyond are drawn at the edge with a
  marker, and the tooltip still shows the real value. "до X" uses `low = 0`
  for every analyte; that is a display convention and the tooltip always shows
  the real range.
- **Duplicates and merging.** A file is rejected if the same user already
  has a file with the same hash. A file whose lab + order number matches an
  existing set of that user is merged into it: its results are added, and
  the set keeps its original collection date (a differing date adds a
  warning). If the set already has a result for the same analyte, the new
  row is skipped and, when the values differ, a warning is added (first
  wins, as for the Lab Service duplicate block). With no order number only
  the file hash applies, and every such file is its own set.
- **Profile gate.** Results and Dynamics are reachable without a profile
  (they are simply empty); only upload is blocked, with a banner linking to
  the profile form.
- **Lab Service duplicates.** Two values for one analyte conflict only if they
  differ after rounding both to the lower of the two precisions
  (5.7 vs 5.68 do not conflict).

## 7. Constraints

- **Data model (shape, not schema):** `users` (discord id, sex, birth date);
  `analysis_sets` (user, lab, collection date, order number, patient name text
  as printed, warnings); `set_files` (set, user, file hash, file path; one
  set can have several); `analytes` and `analyte_aliases`;
  `results` (set, analyte, raw name, numeric value or text, unit, low, high,
  raw reference text, lab flag). Reference low/high are numeric and nullable.
  Postgres, migrations with Alembic.
- **Stack** (versions verified on 2026-10-21, pinned exactly in
  `sources/requirements.txt`): Python 3 from the AlmaLinux 10 system
  package (3.12.x), FastAPI 0.159.0, Uvicorn 0.54.0, SQLAlchemy 2.1.6,
  Alembic 1.20.0, psycopg 3.3.6, Pydantic 2.14.1, pydantic-settings 2.16.1,
  pdfplumber 0.11.10, Jinja2 3.1.6, python-multipart 0.0.32, htmx 2.0.11,
  ECharts 6.1.0. htmx and ECharts are vendored into the image (no CDN).
  Config: YAML + Pydantic with env overrides, per the owner's global rules.
- **Image:** multi-stage `almalinux:10` (builder with `python3-devel gcc`,
  runtime with `python3`), runs as a non-root user, same shape as
  `discord-meow-bot/Dockerfile`.
- **Deployment (my call, modelled on `bank-analyzer`):** Ansible, inventory
  `inventories/home-server`, role builds the image on the target and runs
  the container on the shared Docker network; shared `postgresql` container
  with a dedicated `med-analyzer` user/database added to infra's
  `postgresql_users`; secrets from Infisical; PDF storage under
  `{{ docker_volumes_directory }}/med-analyzer`.
- **Address (my call):** own subdomain `med-analyzer.zelgray.cherkasy.ua`
  on the home server, wired through nginx with `/new-nginx-service` (this
  edits infra's `host_domains`). Portal: the service must be registered once
  in `meow-elite-club-portal` (`/admin/services`, slug `med-analyzer`); the
  slug in the role defaults must match, otherwise every request is rejected.
  The home server reaches the portal through its public domain
  (`meow-elite.club`), as `bank-analyzer` does.
- **Repo scaffolding:** **public** GitHub repo `med-analyzer` (owner's
  decision; so no personal data may ever enter the repo or its history),
  default branch `main`, layout from `/new-service-repo`. The generated
  `dependabot.yml` stays enabled (owner's decision). That skill targets the VDS
  (`inventories/zelgray.work`); for this repo it is adapted to the
  home-server layout of `bank-analyzer`.
- **UI language (my call):** Russian interface text; analyte names are shown
  as printed in the PDF (Ukrainian).
- **Security:** PDFs are untrusted input (size cap, magic-byte check,
  parse-only, no shelling out to external tools on user files, generated
  file names). Header trust only behind nginx. Every query filtered by user.
  Logging via the `logging` module without personal values.

## 8. Left to the builder

- Exact table/column names, indexes, Alembic revisions.
- Parser structure (one module per lab, shared helpers for numbers, dates and
  reference ranges), and how detection is implemented.
- Exact Ukrainian name → analyte seed list, within 6.4.
- Page layout and styling, within the two mockups agreed in the brainstorm
  (results list; dynamics with chips, chart, table).
- Whether to add a confirm step after parsing, a date-range filter, a "name
  differs from profile" banner, and an absolute-values fallback for
  lower-bound-only ranges.
- Test layout and tooling (pytest; Postgres via testcontainers as in
  `hotline-listing`).

## 9. Testing and success criteria

- Both sample PDFs parse into the expected rows: Lab Service yields one row
  per analyte with duplicates collapsed and the arrows read correctly;
  Synevo's creatinine yields the adult-male range 62–106 for a male adult.
  From `synevo-2.pdf`, for a male adult: HGB 130–160, ESR "<15" (upper
  bound only), glucose 4.11–5.89, ferritin 30–400, TSH 0.27–4.2, and the
  cholesterol/LDL/vitamin D rows kept as raw text with no numeric range.
- Parser tests run on **anonymised** fixtures (text extracted from the
  samples with name, birth date and order numbers replaced). Real PDFs stay
  out of git (`*.pdf` in `.gitignore`; the two files in the project root are
  not to be committed).
- Reference selection is unit-tested with sex/age at the collection date,
  including a child band and a missing-group case.
- The %-of-norm calculation is unit-tested for two-sided, "до X" and
  lower-bound-only ranges.
- Two different Discord IDs never see each other's sets (test).
- Working end to end: upload both samples as one user, see two dated sets,
  pick hemoglobin and creatinine on the dynamics page, see one chart with
  tooltip values and the delta table.

## Appendix A. The two sample reports (as observed)

The files are in the project root: `lab-service.pdf` (2 pages),
`synevo.pdf` (1 page) and `synevo-2.pdf` (6 pages). Both contain a text layer; `pdftotext -layout` shows
the structure below. They contain personal data and are never committed.

**Lab Service**
- Title "Результати досліджень № <number>", then the date in words
  ("DD <month> YYYY", registration and sampling), then a line with patient
  name, age and sex. The header repeats on page 2.
- Sections: "Основні гематологічні показники" (with "Первинна проба: …") and
  "Апаратні показники (гематологічний аналізатор)". Each has a column header
  "Найменування / Результат / Од. вимір. / Референтні значення".
- A row is: name (may wrap onto a second line; the abbreviation is in
  parentheses), result, optional flag glyph (▲ above, ▼ below) right after the
  result, unit, reference (`130 - 160`, `до 15`, decimal comma). The glyph
  legend (▲ ▼ !) is at the end of the last page. "Дата друку" is a footer.
- WBC, RBC, HGB and PLT appear in both sections with the same values.

**Synevo**
- Produced by Crystal Reports. Header with lab name and address, "Дата
  замовлення: DD.MM.YYYY HH:MM", client full name, birth date, age
  ("<years> Y <months> M"), sex.
- Section title ("Біо/імунохімія"), then rows: name, result, unit, reference
  block. The reference block is multi-line: "Діти:" followed by age bands
  (`Новонароджені`, `2-12 міс.`, `1-3 роки`, … `13-15 років`), then
  "Дорослі:" with "Жінки" and "Чоловіки" lines. A "Примітка" line follows.
- `synevo.pdf` has one analyte (creatinine). `synevo-2.pdf` (6 pages, about
  40 analytes, same patient) shows the real variety:
  - The page header (lab, order date, client, "Показник / Результат / Од. /
    Референтний інтервал") repeats on every page and ends with a boilerplate
    footer ("Результати лабораторних досліджень не є достатньою підставою…",
    "Стор. N з M", "Дата друку"). Parse per page and strip both.
  - Rows are laid out in columns; names, units ("10^9 / клітин/л") and
    reference blocks wrap over several lines. Row boundaries must be taken
    from word x-positions (pdfplumber words), not from text lines.
  - Group headings are interleaved with rows: "Біо/імунохімія", packages
    ("Пакет №4.5 (…)", "Пакет №2 (…)"), "Загальний аналіз крові (…)",
    "Аналіз крові розгорнутий (ЗАК)" (repeated), and method paragraphs.
    These are not results.
  - Under or inside the reference column: "Метод: …", "Реагенти: …",
    "Аналізатор: …", footnotes ("*Згідно наданих рекомендацій…"), pregnancy
    trimester lists, "Виконано на аналізаторі …". None of it is a range.
  - Reference shapes: `<20.0`; `Жінки: ≥1.2 / Чоловіки: ≥1.0`; `Чоловіки:
    <40.0 / Жінки: <32.0`; `Дорослі (20-78 років): 197.0 - 771.0`; `Чоловіки
    (20-60 років): 30.0 - 400.0`; `Діти: <1 року: … / 1-6 років: … /
    Дорослі: Чоловіки: … / Жінки: …`; `0.0 - 1.0`; `0.01 – 0.08` (en
    dash); risk-category lists; vitamin D categories.
  - One result is a text pointer, not a number: "Звіт з описом лабораторних
    показників — Ваш результат готовий і знаходиться в додатковому pdf
    файлі". It becomes a text result (not plotted).
  - Values use a decimal dot here (e.g. "4.71"); Lab Service uses a comma.
  - The same analytes as Lab Service appear with different names but the same
    abbreviation in parentheses: "Лейкоцити (WBC)" vs "Кількість лейкоцитів
    (WBC)", "Гемоглобін (HGB)" vs "Концентрація гемоглобіну (HGB)".
  - No ▲/▼ glyphs in the text layer.
- The parser must fail loudly on anything it does not recognise, and warn on
  every row it dropped or could not read.
