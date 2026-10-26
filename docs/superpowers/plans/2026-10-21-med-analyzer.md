# med-analyzer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A per-user web app that ingests Lab Service and Synevo PDF reports, stores every result with its reference range, lists result sets by date, and plots several analytes at once in "% of norm".

**Architecture:** One FastAPI service (sync SQLAlchemy + psycopg 3, Jinja2 + htmx pages, ECharts chart) behind nginx and the Discord SSO portal; identity comes from the `X-Discord-User-Id` header. A pure-Python parsing layer turns pdfplumber words into a `ParsedSet` (no DB, no web), which an upload service stores per user; reference groups are kept as JSON so a profile change re-selects ranges without re-parsing. Deployed to the home server with Ansible, modelled on `bank-analyzer`.

**Tech Stack:** Python 3.12 (AlmaLinux 10 system package), FastAPI 0.159.0, Uvicorn 0.54.0, SQLAlchemy 2.1.6, Alembic 1.20.0, psycopg 3.3.6, Pydantic 2.14.1, pydantic-settings 2.16.1, pdfplumber 0.11.10, Jinja2 3.1.6, python-multipart 0.0.32, htmx 2.0.11, ECharts 6.1.0; tests: pytest 9.1.1, httpx 0.28.1, testcontainers 4.15.0.

**Spec:** `docs/superpowers/specs/2026-10-21-med-analyzer-design.md` (read it first; section numbers below refer to it).

## Global Constraints

- Versions above are pinned exactly in `sources/requirements.txt` (tests in `tests/requirements.txt`). Re-run the PyPI/npm lookup before pinning and keep newer versions if they exist; a major bump needs the owner's OK.
- Runtime image: multi-stage `almalinux:10` (builder: `python3 python3-devel gcc`; runtime: `python3`), non-root user, same shape as `discord-meow-bot/Dockerfile`. htmx and ECharts are vendored under `sources/src/med_analyzer/static/`, no CDN.
- Container HTTP port `8000`; domain `med-analyzer.zelgray.cherkasy.ua`; portal service slug `med-analyzer`; Ansible inventory `inventories/home-server`; Postgres user/db `med-analyzer` on the shared `postgresql` container.
- Python style (owner's global rules): single quotes (docstrings `"""`), `logging` via `get_logger(name)` with %-formatting, type annotations everywhere, Google-style docstrings on public items, PEP 8 at 88 columns, config from YAML via Pydantic with env overrides.
- YAML files start with `---` and end with `...` plus a blank line.
- The GitHub repo is **public** (owner's decision) and `dependabot.yml` stays enabled. Never commit the real PDFs (`*.pdf` in `.gitignore`) or any real name, birth date or order number, in any commit or in history. Fixtures are anonymised JSON (Task 5).
- No result values, names or PDF text in logs.
- Every DB query that touches sets/results filters by `user_id`.
- UI text is Russian; analyte names are shown as printed.
- Commits are local only; **never `git push`, never touch infra, never deploy without asking the owner first.** Commit messages are English and end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Reference bounds are treated as inclusive when computing in/out of range (my call; the spec is silent at the equality point).

## Review Focus

Inputs the spec implies but a happy-path test would miss; each has a test in the named task.

1. Same PDF uploaded twice by one user (rejected, links to the existing set) and by two users (both succeed); two different PDFs with one order number (merged into one set, no duplicated analytes) — Task 11.
2. Non-PDF, oversized, text-less (scanned) or unknown-lab PDF — clean error, nothing stored on disk or in DB — Task 11.
3. Cyrillic look-alike letters in abbreviations (`НСТ`, `МСНС`, `РСТ` in Synevo vs `HCT`, `MCHC`, `PCT`) must match the same analyte — Task 9.
4. Text results, missing references and a single data point must not crash the results page or the dynamics table (no "previous", no percentage) — Tasks 10, 13, 14.
5. Collection date before the birth date, no profile yet, and a profile edited after upload — no crash, no wrong range — Tasks 4, 11.

---

## File Structure

```
sources/src/med_analyzer/
  config.py          AppConfig (YAML + env)
  log.py             get_logger
  db.py              engine/session factory, get_db dependency
  models.py          User, AnalysisSet, Analyte, AnalyteAlias, Result
  alembic/           env.py + versions/ (one initial revision)
  parsing/
    types.py         Word, ReferenceGroup, ParsedReference, ParsedResult, ParsedSet, ParseError
    primitives.py    numbers, dates, units, abbreviations, ages
    reference.py     parse_reference(), select_reference()
    words.py         pdf_to_pages()
    lab_service.py   parse_lab_service()
    synevo.py        parse_synevo()
    __init__.py      detect_lab(), parse_pdf()
  analytes.py        normalize_name(), match_analyte(), seed_analytes()
  analyte_seed.py    SEED: list[SeedAnalyte]
  norm.py            percent_of_norm(), is_out_of_range()
  storage.py         save_pdf(), delete_pdf()
  uploads.py         store_upload(), recompute_references(), UploadRejected
  deps.py            current_user dependency
  app.py             FastAPI app + routers
  templates/ static/ (htmx.min.js, echarts.min.js, style.css)
sources/scripts/dump_words.py   PDF -> anonymised words JSON (dev tool)
tests/ (conftest.py, fixtures/*.json, test_*.py)
```

---

### Task 1: Repository scaffold (home-server variant)

**Files:** everything `/new-service-repo` produces, plus `.gitignore` additions; keep `docs/` as is.

**Interfaces:** Produces: git repo on branch `main`, GitHub remote `med-analyzer`, skeleton under `ansible/`, `sources/`, root tooling.

- [ ] **Step 1:** `git init -b main` in the project root; add `*.pdf` and `.superpowers/` to `.gitignore` **before** the first `git add`. Run `git status` and confirm no PDF is listed.
- [ ] **Step 2:** Run `/new-service-repo med-analyzer`. Answers: description "Personal medical lab results tracker: PDF import and dynamics charts"; Python `3.12`; port `8000`; Postgres yes; Redis no; repo visibility **public** (owner's decision; keeps `codeql.yml`; the skill's Step 7 will require the `CodeQL` check). At its Step 1 it will see the PDFs and `docs/`; tell it that is expected.
- [ ] **Step 3:** Adapt the generated skeleton to the home server: rename `ansible/inventories/zelgray.work` to `home-server` with the same host entry as `bank-analyzer/ansible/inventories/home-server/hosts.yml`; `group_vars/all.yml` per `bank-analyzer` (`infisical_path: /hosts/zelgray-cherkasy`, `med_analyzer_domain`, portal registration token). Replace `sources/Dockerfile` with the AlmaLinux 10 multi-stage form (Task 15 finishes it). Update README/CLAUDE.md command lines to `-i inventories/home-server`.
- [ ] **Step 4:** Do **not** run the skill's Step 4 (infra edit) or Step 7 (GitHub ruleset) now; they are outward-facing and handled in Task 16 after the owner agrees. Run `pre-commit run --all-files`; expect pass.
- [ ] **Step 5:** `git add -A && git commit -m "chore: scaffold repository"` (check `git show --stat` lists no `.pdf`). Ask the owner before `gh repo create` / any push.

### Task 2: Config, logging, DB models, migration, test DB

**Files:** create `config.py`, `log.py`, `db.py`, `models.py`, `alembic.ini`, `alembic/env.py`, `alembic/versions/<rev>_initial.py`, `tests/conftest.py`, `tests/test_models.py`; `sources/requirements.txt`, `tests/requirements.txt`; `pyproject.toml` gets `[tool.pytest.ini_options] pythonpath = ["sources/src"]`.

**Interfaces:**
- Produces: `get_logger(name: str) -> logging.Logger`; `class AppConfig(BaseSettings)` with `database_url: str`, `storage_dir: Path`, `max_upload_mb: int = 10`, `log_level: str = 'INFO'`, loaded from YAML at `MED_ANALYZER_CONFIG` (optional) with env vars overriding; `get_config() -> AppConfig` (cached); `make_session_factory(url: str) -> sessionmaker[Session]`; `get_db()` FastAPI dependency yielding a `Session`.
- Models (SQLAlchemy 2 `Mapped[...]`): `User(id, discord_user_id unique, sex: 'M'|'F'|None, birth_date: date|None, last_patient_name: str|None)`; `Analyte(id, code unique, name)`; `AnalyteAlias(id, analyte_id FK, alias unique)`; `AnalysisSet(id, user_id FK, lab: 'lab_service'|'synevo', collected_on: date, order_number: str|None, patient_name: str|None, warnings: JSONB list[str], created_at)` with partial unique `(user_id, lab, order_number) WHERE order_number IS NOT NULL` (the merge key); `SetFile(id, set_id FK ON DELETE CASCADE, user_id FK, sha256, file_path, uploaded_at)` with unique `(user_id, sha256)`; `Result(id, set_id FK ON DELETE CASCADE, analyte_id FK, raw_name, value: Numeric|None, value_text: str|None, unit: str|None, flag: 'high'|'low'|'attention'|None, ref_raw: str|None, ref_groups: JSONB list, ref_low: Numeric|None, ref_high: Numeric|None)`.
- Conftest produces fixtures `pg_url` (session scope, testcontainers `postgres:18`, `alembic upgrade head`), `db` (function scope, session rolled back / tables truncated per test), `make_user(discord_id='u1', sex='M', birth_date=date(1989, 1, 1)) -> User`.

- [ ] **Step 1:** Write `test_models.py`: `test_file_hash_unique_per_user` (same user+hash twice → `IntegrityError`; other user same hash → ok); `test_order_number_unique_only_when_present` (two NULL order numbers ok, same non-null number for same user+lab → `IntegrityError`, same number for another user or lab → ok); `test_deleting_set_cascades_results_and_files`.
- [ ] **Step 2:** Run `pytest tests/test_models.py -v`; expect FAIL (modules missing).
- [ ] **Step 3:** Implement the interfaces; hand-write the initial Alembic revision from the models (do not autogenerate against a dirty DB); `env.py` reads `get_config().database_url`.
- [ ] **Step 4:** Run `pytest tests/test_models.py -v`; expect PASS (needs Docker).
- [ ] **Step 5:** Commit `feat: config, models and initial migration`.

### Task 3: Parsing primitives

**Files:** create `parsing/types.py`, `parsing/primitives.py`, `tests/test_primitives.py`.

**Interfaces:** Produces (in `types.py`): `@dataclass(frozen=True) Word(text: str, x0: float, x1: float, top: float, bottom: float)`; `Page = list[Word]`; `@dataclass(frozen=True) ReferenceGroup(sex: str | None, age_from_months: int | None, age_to_months: int | None, low: Decimal | None, high: Decimal | None, label: str)` (age band is half-open `[from, to)`); `@dataclass(frozen=True) ParsedReference(raw: str, groups: list[ReferenceGroup])`; `@dataclass ParsedResult(raw_name, abbreviation: str | None, value: Decimal | None, value_text: str | None, unit: str | None, flag: str | None, reference: ParsedReference)`; `@dataclass ParsedSet(lab, collected_on: date, order_number: str | None, patient_name: str | None, results: list[ParsedResult], warnings: list[str])`; `class ParseError(Exception)`.
In `primitives.py`: `parse_decimal(s: str) -> Decimal` (comma or dot; raises `ValueError`); `parse_ua_date(s: str) -> date` (`'09 вересень 2020'` and `'14.07.2023 07:32'` and `'14.07.2023'`); `normalize_unit(s: str) -> str` (collapses whitespace; `'10^9 клітин/л'` and `'10^9/л'` → `'10^9/л'`; likewise `10^12`); `extract_abbreviation(name: str) -> str | None` (last parenthesised, comma-separated token that is Latin letters/digits/`#%-`; `'Нейтрофіли (NEUT%)'` → `'NEUT%'`, `'… (АСТ, AST)'` → `'AST'`, `'Лімфоцити (на 100 лейкоцитів)'` → `None`); `age_in_months(birth: date, on: date) -> int` (whole months elapsed, negative if `on < birth`).

- [ ] **Step 1:** Tests with the spec's values: `parse_decimal('4,63') == Decimal('4.63')`, `parse_decimal('0.390')`, `parse_decimal('abc')` raises; `parse_ua_date('14 жовтень 2021') == date(2021, 10, 14)` (all twelve Ukrainian genitive/nominative month forms occurring as `жовтень`/`жовтня` etc.); `age_in_months(date(1990, 5, 15), date(2024, 5, 15)) == 408` and one day earlier `== 407`; `age_in_months` for `on < birth` is negative.
- [ ] **Step 2:** Run `pytest tests/test_primitives.py -v`; expect FAIL.
- [ ] **Step 3:** Implement. Approach: month-name table keyed by lower-cased stem; whole months via year/month/day arithmetic, not 30-day division.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat: parsing primitives and types`.

### Task 4: Reference parsing and group selection

**Files:** create `parsing/reference.py`, `tests/test_reference.py`.

**Interfaces:**
- Consumes: Task 3 types and `parse_decimal`.
- Produces: `parse_reference(text: str) -> ParsedReference` (`text` is the reference cell joined with `\n`; `raw` keeps it); `select_reference(ref: ParsedReference, sex: str | None, age_months: int | None) -> tuple[Decimal | None, Decimal | None]`.

Rules to implement (spec 6.2, 6.6): bounds `a - b` / `a – b`; `<x`, `≤x`, `до x` → `high`; `≥x`, `>x`, `від x` → `low`. Group headers `Діти:` → `[0, 216)` months, `Дорослі:` → `[216, None)`; `Новонароджені`; `Чоловіки`/`Жінки` set sex; age expressions `<1 року`, `1-6 років`, `>50 років`, `2-12 міс.`, `0-6 днів`, `від 18 років`, `18-60 років`; combined forms `Чоловіки (17-50 років): <15.0` and nested `Дорослі:` + `Жінки:` lines. A header line that starts with none of `Діти|Дорослі|Чоловіки|Жінки|Новонароджені` and ends with `:` (e.g. `При дефіциті заліза…:`, `**Вагітні жінки:`), and every line after it, is ignored. If every non-empty line has the form `<threshold> - <words>` (risk categories, vitamin D) → `groups == []`. Selection: age `None`/negative or `sex` needed but `None` → `(None, None)`; exactly one matching group → its bounds; zero or several → `(None, None)`; a sex-specific group wins over a sex-neutral one that also matches only when both cover the age (then it is one match after dropping the neutral).

- [ ] **Step 1:** Tests (each `parse_reference(text)` then `select_reference(ref, sex, age_months)`; ages in months): `'130 - 160'` → `(130, 160)`; `'до 15'` → `(None, 15)`; creatinine block from the spec, male 34y (408) → `(62, 106)`, female → `(44, 80)`; age 3y (36) on a `1-3 роки`/`3-5 років` block → picks `3-5` (`27–42`), age 35 months picks `1-3`; ages 0, 17y (204) and 18y (216) on the TSH-style `Діти:` / `Дорослі:` block; glucose block male 34 → `(4.11, 5.89)`, age 60y (720) → `(4.56, 6.38)`; ESR block male 34 → `(None, 15.0)`, male 55 → `(None, 20.0)`, female 40 → `(None, 20.0)`; ferritin: male 34 → `(30, 400)`, female 25 → `(13, 150)`, male 11 (132) → `(None, None)` (the `>200` line sits under an ignored header); HDL `Жінки: ≥1.2\nЧоловіки: ≥1.0` male → `(1.0, None)`; cholesterol block, LDL block, vitamin D block → `groups == []` and `select_reference` → `(None, None)`; `'<20.0'` → `(None, 20.0)`; negative age → `(None, None)`; `sex=None` on a sex-split block → `(None, None)`.
- [ ] **Step 2:** Run `pytest tests/test_reference.py -v`; expect FAIL.
- [ ] **Step 3:** Implement as a small line-oriented state machine over the stripped lines (current section header, pending sex/age context); normalise en dash and NBSP first.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat: reference range parsing and selection`.

### Task 5: Word extraction and anonymised fixtures

**Files:** create `parsing/words.py`, `sources/scripts/dump_words.py`, `tests/fixtures/{lab_service,synevo,synevo_2}.json`, `tests/test_fixtures_clean.py`. Fake order numbers must differ between fixtures: `9000001` (Lab Service, after `№`), `1000000001` (`synevo.json`), `1000000002` (`synevo_2.json`).

**Interfaces:** Produces: `pdf_to_pages(path: Path) -> list[Page]` (pdfplumber `extract_words(keep_blank_chars=False, use_text_flow=False)`, one `Page` per PDF page, `Word` coordinates as pdfplumber gives them); `pages_to_json(pages) -> str` and `pages_from_json(s: str) -> list[Page]`; `dump_words.py <pdf> <out.json>` writes the **anonymised** JSON.

Anonymisation: replace patient name words (after `Клієнт:` / `ПІБ`), birth date (after `Дата народження:`), the order number words (the number after `№` in the Lab Service title; the 10-digit number on the Synevo birth-date line), and the bracketed barcode token with fixed fakes of the same shape (e.g. `Іваненко Іван Іванович`, `01.01.1989`, `1000000001`) — **keep ages, sex and every analyte row untouched**, since they drive range selection.

- [ ] **Step 1:** Write `test_fixtures_clean.py`: `test_fixture_has_no_pdf_personal_markers` loads each fixture and asserts the fake name appears and that the words `Клієнт:`/`ПІБ` are followed by it; `test_fixtures_roundtrip` (`pages_from_json(pages_to_json(p)) == p`). Real surname / birth date are **not** written into the test.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement `words.py` and the script; run the script locally on `lab-service.pdf`, `synevo.pdf`, `synevo-2.pdf`.
- [ ] **Step 4:** `grep -r` the fixtures for the real surname, first name and birth date printed in the PDFs (look them up locally; do not paste them into any file); expect no matches. Run `pytest tests/test_fixtures_clean.py -v`; expect PASS.
- [ ] **Step 5:** Commit `test: anonymised PDF word fixtures`.

### Task 6: Lab Service parser

**Files:** create `parsing/lab_service.py`, `tests/test_lab_service.py`.

**Interfaces:** Consumes Task 3–5. Produces `parse_lab_service(pages: list[Page]) -> ParsedSet` (`lab='lab_service'`, `order_number` from `№ <number>` (`'9000001'` in the anonymised fixture), `collected_on=date(2020, 9, 9)`, `patient_name` from the `ПІБ` line, `results` de-duplicated).

Approach: group words into visual rows by `top`; locate column x-ranges from the header row ("Найменування…", "Результат", "Од. вимір.", "Референтні значення") on each page; a name that wraps joins with the next row when the next row has no result; flag glyph `▲`→`high`, `▼`→`low`, `!`→`attention`. Duplicate analyte (same abbreviation): first wins, warning only if the values differ after rounding both to the lower precision.

- [ ] **Step 1:** Tests on the `lab_service.json` fixture: `len(result.results) == 27`; HGB `value == Decimal('191')`, `unit == 'г/л'`, `flag == 'high'`, reference groups give `(130, 160)`; RBC `Decimal('3.37')`, `flag == 'low'`; ESR `value == 8`, `abbreviation is None`, ref `(None, 15)`; `MCH` name rejoined from two lines; exactly 6 results have a flag; `warnings == []`; a modified copy of the fixture with the second HGB changed to 181 yields one warning and keeps the first value.
- [ ] **Step 2:** Run `pytest tests/test_lab_service.py -v`; expect FAIL.
- [ ] **Step 3:** Implement. Any row that has a number in the result column but cannot be fully parsed → `ParseError` naming the row index, not silent skip; non-numeric result text → `value=None, value_text=<text>`.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat: Lab Service parser`.

### Task 7: Synevo parser

**Files:** create `parsing/synevo.py`, `tests/test_synevo.py`.

**Interfaces:** Produces `parse_synevo(pages: list[Page]) -> ParsedSet` (`lab='synevo'`). `collected_on` from `Дата замовлення`; `patient_name` from `Клієнт:`; `order_number` = the 10-digit number at the end of the `Дата народження` line (confirmed by the owner); the tests assert `'1000000001'` for `synevo.json` and `'1000000002'` for `synevo_2.json`.

Approach: per page, drop everything above the "Показник / Результат / Од. / Референтний інтервал" header row and everything from the "Результати лабораторних досліджень не є…" footer down; locate the four columns by x-position of that header; a result row starts at a line whose name column is non-empty **and** whose result column holds a value; reference text is all lines in the reference column until the next result row (minus lines starting `Метод:`, `Реагенти:`, `Аналізатор:`, `Виконано на`, and `*`-footnote lines); lines in the name column without a result (package/section headings, method paragraphs) are headings, not rows; wrapped name/unit lines attach to the current row.

- [ ] **Step 1:** Tests: `synevo.json` → one result, creatinine, `Decimal('97')`, `мкмоль/л`, adult-male range `(62, 106)`, `collected_on == date(2023, 7, 14)`; `synevo_2.json` (`collected_on == date(2025, 10, 13)`): HGB `155` / `г/л` / `(130, 160)`; WBC unit `'10^9/л'` (wrapped unit joined); ESR `8` / `(None, 15)`; glucose `4.71` / `(4.11, 5.89)`; ferritin `341.7` / `(30, 400)`; TSH `2.20` / `(0.27, 4.2)`; cholesterol `8.05` with `reference.groups == []` and `ref raw` non-empty; the "Звіт з описом…" row has `value is None` and `value_text` non-empty; no result name starts with `Пакет` or contains `Метод:`; no result is repeated across the page break; every result with a numeric value has a unit or `unit is None` only for the text row. Use `select_reference` with male, age computed from the fake birth date in the fixture.
- [ ] **Step 2:** Run `pytest tests/test_synevo.py -v`; expect FAIL.
- [ ] **Step 3:** Implement; a page with no header row or a row that cannot be split → `ParseError`.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat: Synevo parser`.

### Task 8: Format detection and `parse_pdf`

**Files:** modify `parsing/__init__.py`; create `tests/test_parse_pdf.py`.

**Interfaces:** Produces `detect_lab(pages: list[Page]) -> str` (`'synevo'` if page 1 contains `Synevo`/`Сінево`, `'lab_service'` if it has `Результати досліджень №` and the Lab Service column header, else raises `ParseError('unknown format')`); `parse_pdf(path: Path) -> ParsedSet` (raises `ParseError` if no words at all, i.e. a scanned PDF).

- [ ] **Step 1:** Tests: both detect results on the three fixtures; `detect_lab([[]])` raises; a pages list with only unrelated words raises `ParseError`; `parse_pdf` on a blank one-page PDF (a minimal hand-written PDF byte string kept in the test, no extra dependency) raises `ParseError`.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement. **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat: lab detection and parse_pdf`.

### Task 9: Analyte dictionary and matching

**Files:** create `analytes.py`, `analyte_seed.py`, `tests/test_analytes.py`; Alembic data step calls `seed_analytes` from the app startup (idempotent), not from a migration.

**Interfaces:** Produces `normalize_name(s: str) -> str` (casefold, collapse whitespace/punctuation, drop `Аналіз крові:` prefix); `normalize_abbreviation(s: str) -> str` (uppercase, Cyrillic look-alikes `АВЕКМНОРСТХ` → Latin); `@dataclass SeedAnalyte(code: str, name: str, aliases: tuple[str, ...])`; `SEED: list[SeedAnalyte]`; `seed_analytes(db: Session) -> None`; `match_analyte(db: Session, raw_name: str, abbreviation: str | None) -> Analyte` (order: normalized abbreviation = `code`; normalized name in aliases; else create `Analyte(code=normalize_name(raw_name), name=raw_name)` plus its alias).

Seed must cover every analyte in the three samples, including Synevo names without abbreviations as aliases (`нейтрофіли (на 100 лейкоцитів)` → `NEUT%`, `… (абс.)` → `NEUT#`, same for `LYM`, `MONO`, `EO`, `BASO`, `IMG`; `швидкість осідання еритроцитів за вестергреном` → `ESR`; `Основні…` rows in Lab Service such as `Відношення кількості великих тромбоцитів…` → `P-LCR`).

- [ ] **Step 1:** Tests: for each of WBC, RBC, HGB, HCT, MCV, MCH, MCHC, PLT, RDW-SD, RDW-CV, PDW, MPV, PCT, NEUT%, NEUT#, LYM%, LYM#, MONO%, MONO#, EO%, EO#, BASO%, BASO#, IMG%, IMG#, ESR: the Lab Service result and the Synevo result from the fixtures resolve to the **same** `Analyte.id` (parametrised over both fixtures' parsed sets); `match_analyte(db, 'Гематокрит (НСТ)', 'НСТ').code == match_analyte(db, 'Гематокрит (HCT)', 'HCT').code` with the Cyrillic and Latin spellings written via explicit `\u` escapes in the test; an unknown name creates one analyte and a second call returns the same one; `seed_analytes` twice creates no duplicates.
- [ ] **Step 2:** Run `pytest tests/test_analytes.py -v`; expect FAIL. **Step 3:** Implement. **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat: analyte dictionary and matching`.

### Task 10: Norm math

**Files:** create `norm.py`, `tests/test_norm.py`.

**Interfaces:** Produces `percent_of_norm(value: Decimal | None, low: Decimal | None, high: Decimal | None) -> float | None`; `is_out_of_range(value: Decimal | None, low: Decimal | None, high: Decimal | None) -> bool | None`.

- [ ] **Step 1:** Tests: `percent_of_norm(Decimal('115'), Decimal('130'), Decimal('160')) == -50.0`; value at `low` → 0, at `high` → 100; `high` only (`до 15`, value 5) → `5/15*100`; `low` only → `None`; `value is None` → `None`; `low == high` → `None`; `is_out_of_range`: 115 vs 130–160 → `True`, 130 → `False` (inclusive), 5 vs `(None, 15)` → `False`, 16 → `True`, `(None, None)` → `None`, `value None` → `None`.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement. **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat: percent of norm and range check`.

### Task 11: Storage and upload service

**Files:** create `storage.py`, `uploads.py`, `tests/test_uploads.py`.

**Interfaces:**
- Consumes: `parse_pdf`, `match_analyte`, `select_reference`, `age_in_months`, models, `AppConfig`.
- Produces: `class UploadRejected(Exception)` with `.reason: str` and optional `.existing_set_id: int | None`; `save_pdf(storage_dir: Path, user_id: int, data: bytes) -> tuple[str, Path]` (returns sha256 and path `<storage_dir>/<user_id>/<sha256>.pdf`); `delete_pdf(path: Path) -> None`; `store_upload(db: Session, user: User, data: bytes, config: AppConfig) -> tuple[AnalysisSet, bool]` (the set and whether the file was merged into an existing one); `recompute_references(db: Session, user: User) -> None`.

Behaviour: reject (no file, no DB rows) if user has no `sex`/`birth_date`, `data` does not start with `%PDF-`, `len(data) > max_upload_mb * 1 MiB`, `ParseError`, or same hash for this user (`existing_set_id` set). Parse from a temp file; if the user already has a set with the same lab + order number, merge into it (add a `SetFile`; add only analytes not yet in the set; skip a repeated analyte and warn only when its value differs; keep the set's `collected_on` and warn on a differing date), otherwise create a new set; either way in one transaction; each result gets `ref_raw`, `ref_groups` (JSON of the groups), and `ref_low/high` from `select_reference(sex, age_in_months(birth_date, collected_on))`; `warnings` stored on the set; `user.last_patient_name` is updated and a differing patient name adds the warning `'Имя в файле отличается от прежнего'`. If the DB insert fails after the file was saved, the file is removed. `recompute_references` re-selects `ref_low/high` for all of the user's results from stored `ref_groups`.

- [ ] **Step 1:** Tests (use a fake `parse_pdf` via monkeypatch returning a `ParsedSet` built from the fixtures, plus a few real-bytes tests for the byte checks): store the Lab Service set → 27 results rows, `ref_low/high` for HGB `(130, 160)`; same bytes twice for one user → `UploadRejected` with `existing_set_id`; same bytes for another user → ok; two different parsed sets with the same lab + order number (the `synevo_2` fixture split into two halves) → one `AnalysisSet`, union of results, two `SetFile` rows, second call returns `merged is True`; a repeated analyte with a different value → skipped plus one warning, with an equal value → skipped silently; differing `collected_on` → set date unchanged plus a warning; two uploads without order numbers → two sets; no profile → `UploadRejected`; `b'not a pdf'` → rejected, `storage_dir` still empty; oversized → rejected; `ParseError` → rejected and nothing stored; collection date before birth date → stored with `ref_low/high` all `None` and no exception; changing the user's birth date then `recompute_references` flips a result near an age boundary (use a Synevo creatinine row: child vs adult range); second upload with a different patient name → warning present, not rejected.
- [ ] **Step 2:** Run `pytest tests/test_uploads.py -v`; expect FAIL. **Step 3:** Implement. **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat: upload service and PDF storage`.

### Task 12: Web foundation — auth, profile, health

**Files:** create `deps.py`, `app.py` (app factory `create_app() -> FastAPI`), `templates/base.html`, `templates/profile.html`, `static/style.css`, vendored `static/htmx.min.js` (htmx 2.0.11); `tests/test_web_profile.py`.

**Interfaces:** Produces `current_user(request: Request, db: Session = Depends(get_db)) -> User` (reads `X-Discord-User-Id`; missing/blank → 401; creates the row on first sight; also reads `X-Discord-Avatar-Url` for the header avatar); routes `GET /healthz` (no auth, returns `{'status': 'ok'}`), `GET /profile`, `POST /profile` (form `sex` in `M|F`, `birth_date` ISO; saves and calls `recompute_references`; 303 to `/sets`). Seeds analytes on startup (`lifespan`). Tests use `fastapi.testclient.TestClient` with the `db` fixture via dependency override.

- [ ] **Step 1:** Tests: no header → 401 on `/profile`; `/healthz` → 200 without header; first request creates the user; `POST /profile` saves sex and birth date and a bad `sex` → 422; birth date in the future → 422.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement (Russian labels). **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat: web foundation and profile`.

### Task 13: Results pages, upload, delete

**Files:** modify `app.py`; create `templates/sets.html`, `templates/set_detail.html`; `tests/test_web_sets.py`.

**Interfaces:** Consumes `store_upload`, `UploadRejected`, `delete_pdf`. Produces `GET /` (303 → `/sets`), `GET /sets` (user's sets grouped by `collected_on` desc, each card: lab, order number, result count, out-of-range count computed from `ref_low/high`), `POST /sets/upload` (multipart `file`; success 303 → `/sets/{id}`; `UploadRejected` → 200 re-render of `/sets` with the reason, or a link to the existing set), `GET /sets/{id}` (table analyte / value+unit / reference, out-of-range rows marked, warnings list, text results shown as text, raw reference shown when there is no numeric range), `POST /sets/{id}/delete` (303 → `/sets`).

- [ ] **Step 1:** Tests: upload (monkeypatched parser) then `/sets` shows the lab and `27`; detail page contains the HGB row and the number of out-of-range rows is `6`; a text result and a threshold-style cholesterol row render without error and show their raw text; user B gets 404 on user A's `/sets/{id}` and on its delete; delete removes the set, results and all its files; a merged upload shows the "added to the existing set" message; duplicate upload response contains the link to the existing set; a user without a profile gets the profile banner and cannot upload.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement. **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat: results pages, upload and delete`.

### Task 14: Dynamics

**Files:** modify `app.py`; create `templates/dynamics.html`, vendored `static/echarts.min.js` (ECharts 6.1.0), `static/dynamics.js`; `tests/test_web_dynamics.py`.

**Interfaces:** Produces `GET /dynamics` (page), `GET /api/analytes?q=` → `[{'id': int, 'name': str, 'code': str, 'count': int}]` (only analytes with at least one numeric result for the user, name filter case-insensitive), `GET /api/dynamics?analyte_id=1&analyte_id=2` → `{'series': [{'analyte_id', 'name', 'unit', 'points': [{'date': 'YYYY-MM-DD', 'value': float, 'unit', 'low': float | None, 'high': float | None, 'percent': float | None, 'out': bool | None}]}], 'table': [{'analyte_id', 'name', 'current': float, 'unit', 'low', 'high', 'ref_raw', 'previous': float | None, 'delta': float | None}]}` — points sorted by date; `percent` via `percent_of_norm` with **that result's** own range; numeric-only; the page shows a note for analytes whose points have no percentage (e.g. HDL `≥`). Client side: chips for chosen analytes with a search box, one ECharts line chart, shaded band 0–100, value axis limited to −100…300 with out-of-axis points drawn at the edge with a marker, tooltip with real value, unit, date and range, points with `out` highlighted; table below the chart.

- [ ] **Step 1:** Tests: two sets for one user with HGB (110 and 140): `/api/dynamics` returns two ascending points with `percent` ≈ −66.7 and ≈ 33.3, `table.current == 140`, `previous == 110`, `delta == 30`; a single-point analyte → `previous is None`, `delta is None`; an analyte with only text results is absent from `/api/analytes`; a point with `ref_low` only (HDL) has `percent is None` and is still in `points`; other user's analytes never appear; unknown `analyte_id` → ignored (empty series), not 500; MCHC from both labs (г/л and г/дл) yields two points on one series with each point's own `unit`.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement. **Step 4:** Run; expect PASS. **Step 5:** Start the app against a local Postgres, upload the three real PDFs in the browser as one test user, open `/dynamics`, pick hemoglobin and creatinine, and confirm the chart, tooltips and table (the spec's end-to-end criterion); note what you saw.
- [ ] **Step 6:** Commit `feat: dynamics API and page`.

### Task 15: Image and Ansible role

**Files:** modify `sources/Dockerfile`, `sources/entrypoint.sh` (alembic upgrade head → uvicorn), `ansible/roles/med-analyzer/{defaults,tasks,README}`, `ansible/inventories/home-server/group_vars/all.yml`, `ansible/playbooks/deploy.yml`.

**Interfaces:** Role defaults (prefix `med_analyzer_`): `container_name: 'med-analyzer'`, `image: 'med-analyzer:local'`, `http_port: 8000`, `data_dir: '{{ docker_volumes_directory }}/med-analyzer'` (mounted at the app's `storage_dir`), `domain`, `db_password` from Infisical (`med-analyzer-db-password`), `meow_elite_club_portal_public_domain: 'meow-elite.club'`, `service_slug: 'med-analyzer'`. Container env: `DATABASE_URL` (psycopg URL to the `postgresql` container), `STORAGE_DIR`. Container is not published on a host port. Modelled line by line on `bank-analyzer/ansible/roles/bank-analyzer`.

- [ ] **Step 1:** `docker build -t med-analyzer:test sources` (or `podman build`); expect success; `docker run --rm med-analyzer:test python3 -c 'import med_analyzer'` expect no error; confirm the process user is not root (`id -u` ≠ 0).
- [ ] **Step 2:** Run the app container against a throwaway Postgres container, hit `/healthz`; expect `{"status": "ok"}`.
- [ ] **Step 3:** `ansible-playbook -i inventories/home-server playbooks/deploy.yml --syntax-check` and `ansible-lint`; expect pass. Use built-in modules, `become` only where needed, every task named.
- [ ] **Step 4:** Commit `feat: container image and Ansible role`.

### Task 16: Nginx wiring, infra, portal, first deploy, docs

**Files:** via skills: nginx templates/tasks in the role, infra `postgresql_users` and `host_domains` entries; `README.md`, `CLAUDE.md`, role `README.md`.

All steps here are outward-facing — **ask the owner before each one**.

- [ ] **Step 1:** Run `/new-nginx-service` on this repo: host `home-server`, **subdomain** `med-analyzer` → `med-analyzer.zelgray.cherkasy.ua`, whole service gated by the Discord portal (like `bank-analyzer`'s `location.conf.j2`, no ungated locations; forward `X-Discord-User-Id` and `X-Discord-Avatar-Url`). Show the diff, including the `infra` edits, and commit nothing in `infra` without approval.
- [ ] **Step 2:** Add the `med-analyzer` user/database to infra's `postgresql_users` (the skill's Step 4, home-server inventory), run infra's postgresql playbook only after the owner approves.
- [ ] **Step 3:** Owner action: register the service in `meow-elite-club-portal` at `/admin/services` with slug `med-analyzer` (a mismatch rejects every request).
- [ ] **Step 4:** With approval: `ansible-playbook -i inventories/home-server playbooks/deploy.yml`; then open the site through the portal, complete the profile, upload `lab-service.pdf`, `synevo.pdf`, `synevo-2.pdf`; confirm sets, flags and the dynamics chart. Check the container logs contain no personal values.
- [ ] **Step 5:** Update `README.md`, `CLAUDE.md` (project structure, commands, the portal-registration note) from the actual code; commit `docs: usage and deployment`. Ask before the first `git push`, creating the GitHub repo, and applying the skill's Step 7 ruleset; keep the generated `.github/dependabot.yml` enabled (owner's decision).

---

## Self-review notes

- Spec coverage: upload/detection (T8, T11), parsing rules and duplicates (T6, T7), ranges and selection (T4, T11), results page (T13), analytes (T9), dynamics (T10, T14), profile/auth (T2, T12), per-user isolation (T13, T14 tests), deployment/nginx/portal (T15, T16), anonymised fixtures and no-PDF rule (T1, T5). Non-goals (merge UI, unit conversion, OCR) have no tasks.
- Signatures used across tasks are defined once: `ParsedSet`/`ParsedResult`/`ParsedReference` (T3), `select_reference` (T4), `match_analyte` (T9), `percent_of_norm`/`is_out_of_range` (T10), `store_upload`/`recompute_references` (T11).
- Settled by the owner after the first draft: public repo (T1), `dependabot.yml` kept (T16), order numbers (Lab Service `№`, Synevo 10-digit number) are correct and merge PDFs of one order into one set (T2, T11).
