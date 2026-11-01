"""Tests for PDF storage and the upload service.

The parser is replaced by a fake returning sets parsed from the word fixtures,
so the byte strings below only need to look like a PDF.
"""

import json
import logging
import stat
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from med_analyzer import uploads
from med_analyzer.analytes import seed_analytes
from med_analyzer.config import AppConfig
from med_analyzer.dynamics import build_dynamics
from med_analyzer.i18n import encode_msg, render, render_stored
from med_analyzer.models import AnalysisSet, Analyte, Result, SetFile, User
from med_analyzer.parsing.lab_service import parse_lab_service
from med_analyzer.parsing.synevo import parse_synevo
from med_analyzer.parsing.types import (
    ParsedReference,
    ParsedResult,
    ParsedSet,
    ParseError,
)
from med_analyzer.parsing.words import (
    FAKE_FULL_NAME,
    FAKE_SHORT_NAME,
    pages_from_json,
)
from med_analyzer.storage import delete_pdf, save_pdf
from med_analyzer.uploads import (
    UploadRejected,
    delete_set,
    recompute_references,
    store_upload,
)

FIXTURES = Path(__file__).parent / 'fixtures'
PDF_A = b'%PDF-1.7 first fake report'
PDF_B = b'%PDF-1.7 second fake report'
TEXT_POINTER_ROW = 'Звіт з описом лабораторних показників'
FAKE_NAME = 'Петренко Петро Петрович'
OTHER_NAME = 'Сидоренко Сидір Сидорович'


def _ru(warnings: list[str]) -> list[str]:
    """Render the stored warnings of a set in Russian."""
    return [render_stored('ru', warning) for warning in warnings]


def _parse(name: str) -> ParsedSet:
    pages = pages_from_json((FIXTURES / name).read_text(encoding='utf-8'))
    if name.startswith('synevo'):
        return parse_synevo(pages)
    return parse_lab_service(pages)


@pytest.fixture
def config(pg_url: str, tmp_path: Path) -> AppConfig:
    return AppConfig(database_url=pg_url, storage_dir=tmp_path / 'storage')


@pytest.fixture
def seeded(db: Session) -> Session:
    seed_analytes(db)
    db.commit()
    return db


@pytest.fixture
def fake_parser(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[ParsedSet]]:
    """Make ``parse_pdf`` return the queued sets one per call."""
    queue: list[ParsedSet] = []

    def _fake(path: Path) -> ParsedSet:
        assert path.is_file()
        return queue.pop(0)

    monkeypatch.setattr(uploads, 'parse_pdf', _fake)
    yield queue


def _files(storage_dir: Path) -> list[Path]:
    if not storage_dir.exists():
        return []
    return sorted(p for p in storage_dir.rglob('*') if p.is_file())


def _count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model))


def _results(db: Session, analysis_set: AnalysisSet) -> list[Result]:
    return list(db.scalars(select(Result).where(Result.set_id == analysis_set.id)))


def _by_code(db: Session, analysis_set: AnalysisSet, code: str) -> Result:
    return db.scalars(
        select(Result)
        .join(Analyte)
        .where(Result.set_id == analysis_set.id, Analyte.code == code)
    ).one()


def _assert_nothing_stored(db: Session, config: AppConfig) -> None:
    assert _count(db, AnalysisSet) == 0
    assert _count(db, SetFile) == 0
    assert _count(db, Result) == 0
    assert _files(config.storage_dir) == []


# --- storage -----------------------------------------------------------------


def test_save_pdf_layout_and_modes(tmp_path: Path) -> None:
    storage_dir = tmp_path / 'storage'
    sha, path = save_pdf(storage_dir, 7, PDF_A)

    assert len(sha) == 64
    assert path == storage_dir / '7' / f'{sha}.pdf'
    assert path.read_bytes() == PDF_A
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    # No temporary file is left next to the PDF.
    assert list(path.parent.iterdir()) == [path]


def test_delete_pdf_ignores_missing_and_refuses_outside(tmp_path: Path) -> None:
    storage_dir = tmp_path / 'storage'
    _, path = save_pdf(storage_dir, 1, PDF_A)
    outside = tmp_path / 'outside.pdf'
    outside.write_bytes(PDF_A)

    delete_pdf(storage_dir, path)
    delete_pdf(storage_dir, path)
    assert not path.exists()

    with pytest.raises(ValueError):
        delete_pdf(storage_dir, storage_dir / '..' / 'outside.pdf')
    assert outside.exists()


# --- store_upload: happy path ------------------------------------------------


def test_lab_service_set_is_stored(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    user = make_user()
    parsed = _parse('lab_service.json')
    fake_parser.append(parsed)

    analysis_set, merged = store_upload(db, user, PDF_A, config)

    assert merged is False
    assert analysis_set.user_id == user.id
    assert analysis_set.lab == 'lab_service'
    assert analysis_set.collected_on == parsed.collected_on
    assert analysis_set.order_number == parsed.order_number
    assert len(_results(db, analysis_set)) == 27
    hgb = _by_code(db, analysis_set, 'HGB')
    assert (hgb.ref_low, hgb.ref_high) == (Decimal(130), Decimal(160))
    assert hgb.ref_raw is not None
    (set_file,) = analysis_set.files
    assert len(_files(config.storage_dir)) == 1
    assert (config.storage_dir / set_file.file_path).read_bytes() == PDF_A


def test_ref_groups_are_json_and_round_trip(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    user = make_user()
    parsed = _parse('synevo.json')
    fake_parser.append(parsed)

    analysis_set, _ = store_upload(db, user, PDF_A, config)
    db.expire_all()

    (row,) = _results(db, analysis_set)
    (source,) = parsed.results
    assert json.loads(json.dumps(row.ref_groups)) == row.ref_groups
    assert row.ref_raw == source.reference.raw
    assert uploads.groups_from_json(row.ref_groups) == source.reference.groups
    assert row.ref_groups[0] == {
        'sex': None,
        'age_from_months': 0,
        'age_to_months': 1,
        'low': '21.0',
        'high': '75.0',
        'label': source.reference.groups[0].label,
    }


def test_text_pointer_row_is_a_text_result(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    user = make_user()
    fake_parser.append(_parse('synevo_2.json'))

    analysis_set, _ = store_upload(db, user, PDF_A, config)

    (row,) = [r for r in _results(db, analysis_set) if r.raw_name == TEXT_POINTER_ROW]
    assert row.value is None
    assert row.value_text
    assert (row.ref_low, row.ref_high) == (None, None)


def test_long_reference_text_is_stored_whole(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    user = make_user()
    parsed = _parse('synevo.json')
    long_raw = 'Дорослі: 1.0 - 2.0\n' + 'x' * 5000
    (source,) = parsed.results
    long_row = replace(
        source,
        value=None,
        value_text='y' * 5000,
        reference=replace(source.reference, raw=long_raw),
    )
    fake_parser.append(replace(parsed, results=[long_row]))

    analysis_set, _ = store_upload(db, user, PDF_A, config)
    db.expire_all()

    (row,) = _results(db, analysis_set)
    assert row.ref_raw == long_raw
    assert row.value_text == 'y' * 5000


# --- duplicates and merging --------------------------------------------------


def test_same_file_twice_is_rejected_with_the_set(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    user = make_user()
    fake_parser.extend([_parse('synevo.json'), _parse('synevo.json')])
    analysis_set, _ = store_upload(db, user, PDF_A, config)

    with pytest.raises(UploadRejected) as caught:
        store_upload(db, user, PDF_A, config)

    assert render('ru', caught.value.reason) == 'Этот файл уже загружен'
    assert caught.value.existing_set_id == analysis_set.id
    assert _count(db, SetFile) == 1
    assert len(_files(config.storage_dir)) == 1


def test_same_file_for_another_user_is_accepted(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    first = make_user('u1')
    second = make_user('u2')
    fake_parser.extend([_parse('synevo.json'), _parse('synevo.json')])

    first_set, _ = store_upload(db, first, PDF_A, config)
    second_set, merged = store_upload(db, second, PDF_A, config)

    assert merged is False
    assert first_set.id != second_set.id
    assert second_set.user_id == second.id
    files = _files(config.storage_dir)
    assert [p.parent.name for p in files] == sorted([str(first.id), str(second.id)])


def _halves(parsed: ParsedSet) -> tuple[ParsedSet, ParsedSet]:
    middle = len(parsed.results) // 2
    return (
        replace(parsed, results=parsed.results[:middle], warnings=[]),
        replace(parsed, results=parsed.results[middle:], warnings=[]),
    )


def test_same_lab_and_order_number_are_merged(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    user = make_user()
    full = _parse('synevo_2.json')
    first, second = _halves(full)
    fake_parser.extend([first, second])

    first_set, first_merged = store_upload(db, user, PDF_A, config)
    second_set, second_merged = store_upload(db, user, PDF_B, config)

    assert (first_merged, second_merged) == (False, True)
    assert second_set.id == first_set.id
    assert _count(db, AnalysisSet) == 1
    assert _count(db, SetFile) == 2
    names = sorted(r.raw_name for r in _results(db, first_set))
    assert names == sorted(r.raw_name for r in full.results)
    assert second_set.warnings == []
    assert len(_files(config.storage_dir)) == 2


def _with_value(result: ParsedResult, value: str) -> ParsedResult:
    return replace(result, value=Decimal(value), value_text=None)


@pytest.mark.parametrize(
    ('repeat_value', 'warned'),
    # Spec 6.6: compare after rounding to the lower precision (5.7 == 5.68).
    [('5.5', True), ('4.1', True), ('4.0', False), ('4.00', False), ('4.04', False)],
)
def test_repeated_analyte_on_merge(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
    repeat_value: str,
    warned: bool,
) -> None:
    db = seeded
    user = make_user()
    parsed = _parse('synevo_2.json')
    row = _with_value(parsed.results[0], '4.0')
    fake_parser.extend(
        [
            replace(parsed, results=[row], warnings=[]),
            replace(
                parsed,
                results=[_with_value(row, repeat_value), parsed.results[1]],
                warnings=[],
            ),
        ]
    )

    store_upload(db, user, PDF_A, config)
    analysis_set, merged = store_upload(db, user, PDF_B, config)

    assert merged is True
    results = _results(db, analysis_set)
    assert len(results) == 2
    kept = [r for r in results if r.raw_name == row.raw_name]
    assert [r.value for r in kept] == [Decimal('4.0')]
    expected = [
        f'Повтор показателя «{row.raw_name}» с другим значением: 4.0 и {repeat_value}'
    ]
    assert _ru(analysis_set.warnings) == (expected if warned else [])


@pytest.mark.parametrize(
    ('repeat_text', 'warned'), [('positive', True), ('negative', False)]
)
def test_repeated_text_analyte_on_merge(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
    repeat_text: str,
    warned: bool,
) -> None:
    db = seeded
    user = make_user()
    parsed = _parse('synevo_2.json')
    row = replace(parsed.results[0], value=None, value_text='negative')
    fake_parser.extend(
        [
            replace(parsed, results=[row], warnings=[]),
            replace(
                parsed, results=[replace(row, value_text=repeat_text)], warnings=[]
            ),
        ]
    )

    store_upload(db, user, PDF_A, config)
    analysis_set, merged = store_upload(db, user, PDF_B, config)

    assert merged is True
    assert [r.value_text for r in _results(db, analysis_set)] == ['negative']
    expected = [
        f'Повтор показателя «{row.raw_name}» с другим значением: '
        f'negative и {repeat_text}'
    ]
    assert _ru(analysis_set.warnings) == (expected if warned else [])


def test_differing_date_on_merge_keeps_the_set_date(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    user = make_user()
    first, second = _halves(_parse('synevo_2.json'))
    later = replace(second, collected_on=first.collected_on + timedelta(days=3))
    fake_parser.extend([first, later])

    store_upload(db, user, PDF_A, config)
    analysis_set, merged = store_upload(db, user, PDF_B, config)

    assert merged is True
    assert analysis_set.collected_on == first.collected_on
    assert _ru(analysis_set.warnings) == ['Дата в файле отличается от даты набора']


def test_without_order_number_every_file_is_its_own_set(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    user = make_user()
    first, second = _halves(_parse('synevo_2.json'))
    fake_parser.extend(
        [replace(first, order_number=None), replace(second, order_number=None)]
    )

    first_set, first_merged = store_upload(db, user, PDF_A, config)
    second_set, second_merged = store_upload(db, user, PDF_B, config)

    assert (first_merged, second_merged) == (False, False)
    assert first_set.id != second_set.id
    assert _count(db, AnalysisSet) == 2


def test_same_value_under_two_spellings_is_skipped_with_a_russian_note(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    user = make_user()
    parsed = _parse('lab_service.json')
    hgb = next(r for r in parsed.results if r.abbreviation == 'HGB')
    twin = replace(hgb, raw_name='Гемоглобін (HGB)')
    fake_parser.append(replace(parsed, results=[hgb, twin], warnings=[]))

    analysis_set, _ = store_upload(db, user, PDF_A, config)

    assert _ru(analysis_set.warnings) == [
        'Повтор показателя «Гемоглобін (HGB)» пропущен: '
        f'тот же показатель, что и «{hgb.raw_name}»'
    ]


def test_same_analyte_twice_in_one_file_keeps_the_first(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    user = make_user()
    parsed = _parse('lab_service.json')
    hgb = next(r for r in parsed.results if r.abbreviation == 'HGB')
    # A different spelling that still resolves to HGB.
    twin = replace(hgb, raw_name='Гемоглобін (HGB)', value=hgb.value + 1)
    fake_parser.append(replace(parsed, results=[hgb, twin], warnings=[]))

    analysis_set, _ = store_upload(db, user, PDF_A, config)

    assert [r.raw_name for r in _results(db, analysis_set)] == [hgb.raw_name]
    assert len(analysis_set.warnings) == 1
    assert _ru(analysis_set.warnings)[0].startswith(
        'Повтор показателя «Гемоглобін (HGB)»'
    )


# --- rejections --------------------------------------------------------------


@pytest.mark.parametrize(
    ('sex', 'birth_date'), [(None, date(1989, 1, 1)), ('M', None), (None, None)]
)
def test_incomplete_profile_is_rejected(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
    sex: str | None,
    birth_date: date | None,
) -> None:
    user = make_user(sex=sex, birth_date=birth_date)
    fake_parser.append(_parse('synevo.json'))

    with pytest.raises(UploadRejected) as caught:
        store_upload(seeded, user, PDF_A, config)

    assert (
        render('ru', caught.value.reason)
        == 'Сначала заполните профиль: пол и дата рождения'
    )
    _assert_nothing_stored(seeded, config)


def test_not_a_pdf_is_rejected(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    user = make_user()
    fake_parser.append(_parse('synevo.json'))

    with pytest.raises(UploadRejected) as caught:
        store_upload(seeded, user, b'not a pdf', config)

    assert render('ru', caught.value.reason) == 'Файл не похож на PDF'
    assert caught.value.existing_set_id is None
    _assert_nothing_stored(seeded, config)


def test_oversized_file_is_rejected(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    user = make_user()
    small = config.model_copy(update={'max_upload_mb': 1})
    limit = 1024 * 1024
    fake_parser.append(_parse('synevo.json'))

    with pytest.raises(UploadRejected) as caught:
        store_upload(seeded, user, b'%PDF-' + b'0' * (limit - 4), small)

    assert render('ru', caught.value.reason) == 'Файл слишком большой (максимум 1 МБ)'
    _assert_nothing_stored(seeded, config)

    # Exactly at the limit is accepted.
    store_upload(seeded, user, b'%PDF-' + b'0' * (limit - 5), small)
    assert _count(seeded, AnalysisSet) == 1


def test_parse_error_is_rejected_and_the_file_removed(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = make_user()

    def _broken(path: Path) -> ParsedSet:
        assert path.is_file()
        raise ParseError('Unknown report format')

    monkeypatch.setattr(uploads, 'parse_pdf', _broken)

    with pytest.raises(UploadRejected) as caught:
        store_upload(seeded, user, PDF_A, config)

    assert caught.value.reason == uploads.parse_error_reason(
        ParseError('Unknown report format')
    )
    _assert_nothing_stored(seeded, config)


@pytest.mark.parametrize(
    ('message', 'reason'),
    [
        (
            'Unknown report format',
            'Не удалось определить лабораторию: поддерживаются отчёты Lab Service '
            'и Синево',
        ),
        ('Ambiguous report format', 'Не удалось однозначно определить лабораторию'),
        (
            'The PDF has no text layer (scanned documents are not supported)',
            'В PDF нет текстового слоя (сканы не поддерживаются)',
        ),
        ('Cannot read the PDF', 'Не удалось прочитать PDF'),
        ('The PDF has too many pages', 'В PDF слишком много страниц или текста'),
        ('The PDF has too many words', 'В PDF слишком много страниц или текста'),
        (
            'Title row not found exactly once on page 2',
            'Не удалось разобрать PDF (Title row not found exactly once on page 2)',
        ),
    ],
)
def test_parse_errors_are_shown_in_russian_and_logged_in_english(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    message: str,
    reason: str,
) -> None:
    user = make_user()

    def _broken(path: Path) -> ParsedSet:
        raise ParseError(message)

    monkeypatch.setattr(uploads, 'parse_pdf', _broken)

    with caplog.at_level(logging.INFO), pytest.raises(UploadRejected) as caught:
        store_upload(seeded, user, PDF_A, config)

    assert render('ru', caught.value.reason) == reason
    assert message in caplog.text
    _assert_nothing_stored(seeded, config)


def test_cleanup_failure_does_not_hide_the_rejection(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = make_user()

    def _broken(path: Path) -> ParsedSet:
        raise ParseError('Unknown report format')

    def _undeletable(storage_dir: Path, path: Path) -> None:
        raise PermissionError('injected')

    monkeypatch.setattr(uploads, 'parse_pdf', _broken)
    monkeypatch.setattr(uploads, 'delete_pdf', _undeletable)

    with pytest.raises(UploadRejected) as caught:
        store_upload(seeded, user, PDF_A, config)

    assert caught.value.reason == uploads.parse_error_reason(
        ParseError('Unknown report format')
    )
    assert _count(seeded, SetFile) == 0


def test_failure_inside_the_db_part_rolls_everything_back(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = seeded
    user = make_user()
    fake_parser.append(_parse('lab_service.json'))
    real_match = uploads.match_analyte
    calls = []

    def _flaky(session: Session, raw_name: str, abbreviation: str | None) -> Analyte:
        calls.append(raw_name)
        if len(calls) == 3:
            raise RuntimeError('injected')
        return real_match(session, raw_name, abbreviation)

    monkeypatch.setattr(uploads, 'match_analyte', _flaky)

    with pytest.raises(RuntimeError, match='injected'):
        store_upload(db, user, PDF_A, config)

    _assert_nothing_stored(db, config)
    db.refresh(user)
    assert user.last_patient_name is None


def test_failed_upload_keeps_a_file_another_row_refers_to(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent upload of the same bytes must not delete the winner's file."""
    db = seeded
    user = make_user()
    fake_parser.append(_parse('synevo.json'))
    analysis_set, _ = store_upload(db, user, PDF_A, config)
    (stored,) = _files(config.storage_dir)

    # Simulate losing the race: the duplicate check sees nothing yet.
    real_scalar = db.scalar
    skipped = []

    def _blind_scalar(statement: Any, *args: Any, **kwargs: Any) -> Any:
        if not skipped and 'set_files' in str(statement):
            skipped.append(True)
            return None
        return real_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(db, 'scalar', _blind_scalar)
    fake_parser.append(_parse('synevo.json'))

    with pytest.raises(UploadRejected) as caught:
        store_upload(db, user, PDF_A, config)

    assert render('ru', caught.value.reason) == 'Этот файл уже загружен'
    assert caught.value.existing_set_id == analysis_set.id
    assert stored.read_bytes() == PDF_A
    assert _count(db, AnalysisSet) == 1


# --- references and the profile ----------------------------------------------


def test_collection_before_birth_stores_no_ranges(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    parsed = _parse('lab_service.json')
    user = make_user(birth_date=parsed.collected_on + timedelta(days=1))
    fake_parser.append(parsed)

    analysis_set, _ = store_upload(db, user, PDF_A, config)

    results = _results(db, analysis_set)
    assert len(results) == 27
    assert all((r.ref_low, r.ref_high) == (None, None) for r in results)


def _creatinine_set() -> ParsedSet:
    parsed = _parse('synevo.json')
    (source,) = parsed.results
    row = ParsedResult(
        raw_name=source.raw_name,
        abbreviation=source.abbreviation,
        value=Decimal('70.0'),
        value_text=None,
        unit=source.unit,
        flag=None,
        reference=ParsedReference(source.reference.raw, source.reference.groups),
    )
    return replace(parsed, results=[row], warnings=[])


def _years_before(day: date, years: int) -> date:
    return day.replace(year=day.year - years)


def test_recompute_references_follows_the_profile(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    parsed = _creatinine_set()
    collected = parsed.collected_on
    # Exactly 18 years old at collection: the adult male line.
    user = make_user(sex='M', birth_date=_years_before(collected, 18))
    fake_parser.append(parsed)
    analysis_set, _ = store_upload(db, user, PDF_A, config)
    (row,) = _results(db, analysis_set)
    assert (row.ref_low, row.ref_high) == (Decimal('62.0'), Decimal('106.0'))

    def _recompute(sex: str, birth_date: date) -> tuple[Decimal | None, ...]:
        user.sex, user.birth_date = sex, birth_date
        db.commit()
        recompute_references(db, user)
        db.expire_all()
        refreshed = db.get(Result, row.id)
        return refreshed.ref_low, refreshed.ref_high

    # One day younger: 17 years 11 months, which no group covers.
    one_day_later = _years_before(collected, 18) + timedelta(days=1)
    assert _recompute('M', one_day_later) == (None, None)
    # 14 years old: the 13-15 years child band.
    assert _recompute('M', _years_before(collected, 14)) == (
        Decimal('50.0'),
        Decimal('77.0'),
    )
    # Adult again but female.
    assert _recompute('F', _years_before(collected, 30)) == (
        Decimal('44.0'),
        Decimal('80.0'),
    )


def test_recompute_references_without_commit_leaves_the_transaction_open(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    parsed = _creatinine_set()
    user = make_user(sex='M', birth_date=_years_before(parsed.collected_on, 18))
    fake_parser.append(parsed)
    analysis_set, _ = store_upload(db, user, PDF_A, config)
    (row,) = _results(db, analysis_set)
    before = (row.ref_low, row.ref_high)

    user.sex = 'F'
    recompute_references(db, user, commit=False)
    assert (row.ref_low, row.ref_high) != before
    db.rollback()
    db.expire_all()

    (row,) = _results(db, analysis_set)
    assert (row.ref_low, row.ref_high) == before


def test_recompute_references_skips_an_incomplete_profile(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    parsed = _creatinine_set()
    user = make_user(sex='M', birth_date=_years_before(parsed.collected_on, 30))
    fake_parser.append(parsed)
    analysis_set, _ = store_upload(db, user, PDF_A, config)

    user.sex = None
    db.commit()
    recompute_references(db, user)
    db.expire_all()

    (row,) = _results(db, analysis_set)
    assert (row.ref_low, row.ref_high) == (Decimal('62.0'), Decimal('106.0'))


def test_recompute_references_leaves_other_users_untouched(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    parsed = _creatinine_set()
    adult = _years_before(parsed.collected_on, 18)
    first = make_user('u1', sex='M', birth_date=adult)
    second = make_user('u2', sex='M', birth_date=adult)
    # The same order for both users: separate sets, never merged.
    fake_parser.extend([parsed, parsed])
    first_set, _ = store_upload(db, first, PDF_A, config)
    second_set, _ = store_upload(db, second, PDF_B, config)
    expected = (Decimal('62.0'), Decimal('106.0'))

    first.sex = 'F'
    db.commit()
    recompute_references(db, first)
    db.expire_all()

    (changed,) = _results(db, first_set)
    (untouched,) = _results(db, second_set)
    assert (changed.ref_low, changed.ref_high) == (Decimal('44.0'), Decimal('80.0'))
    assert (untouched.ref_low, untouched.ref_high) == expected


def test_patient_name_is_remembered_and_a_change_warned(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    user = make_user()
    first, second = _halves(_parse('synevo_2.json'))
    fake_parser.extend(
        [
            replace(first, patient_name=FAKE_NAME, order_number='1'),
            replace(second, patient_name=OTHER_NAME, order_number='2'),
        ]
    )

    first_set, _ = store_upload(db, user, PDF_A, config)
    second_set, _ = store_upload(db, user, PDF_B, config)

    assert first_set.patient_name == FAKE_NAME
    assert first_set.warnings == []
    assert second_set.patient_name == OTHER_NAME
    assert _ru(second_set.warnings) == ['Имя в файле отличается от прежнего']
    db.refresh(user)
    assert user.last_patient_name == FAKE_NAME


def _name_pair_sets(a_name: str, b_name: str) -> tuple[ParsedSet, ParsedSet]:
    first, second = _halves(_parse('synevo_2.json'))
    return (
        replace(first, patient_name=a_name, order_number='1'),
        replace(second, patient_name=b_name, order_number='2'),
    )


def test_short_and_full_forms_of_one_name_do_not_warn(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    user = make_user()
    short_set = _parse('lab_service.json')
    full_set = _parse('synevo_2.json')
    assert short_set.patient_name == FAKE_SHORT_NAME
    assert full_set.patient_name == FAKE_FULL_NAME
    fake_parser.extend([short_set, full_set])

    first, _ = store_upload(db, user, PDF_A, config)
    second, _ = store_upload(db, user, PDF_B, config)

    assert uploads.NAME_DIFFERS not in first.warnings
    assert uploads.NAME_DIFFERS not in second.warnings
    db.refresh(user)
    # The more informative form replaces the abbreviated one.
    assert user.last_patient_name == FAKE_FULL_NAME


def test_longer_remembered_name_is_kept_when_a_short_form_arrives(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    user = make_user()
    fake_parser.extend(_name_pair_sets(FAKE_FULL_NAME, FAKE_SHORT_NAME))

    store_upload(db, user, PDF_A, config)
    second, _ = store_upload(db, user, PDF_B, config)

    assert second.warnings == []
    db.refresh(user)
    assert user.last_patient_name == FAKE_FULL_NAME


def test_different_person_still_warns_after_a_short_form(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    user = make_user()
    fake_parser.extend(_name_pair_sets(FAKE_SHORT_NAME, OTHER_NAME))

    store_upload(db, user, PDF_A, config)
    second, _ = store_upload(db, user, PDF_B, config)

    assert second.warnings == [encode_msg(uploads.NAME_DIFFERS)]


@pytest.mark.parametrize(
    ('first', 'second', 'same'),
    [
        ('Іваненко І. І.', 'Іваненко Іван Іванович', True),
        ('Іваненко І.', 'Іваненко Іван Іванович', True),
        ('ІВАНЕНКО  іван  іванович', 'Іваненко Іван Іванович', True),
        ('Іваненко І.І.', 'Іваненко Іван Іванович', True),
        ('Іваненко Іван', 'Іваненко Петро', False),
        ('Іваненко І. І.', 'Петренко І. І.', False),
        ('Іваненко І. П.', 'Іваненко Іван Іванович', False),
        ('Іваненко', 'Іваненко', True),
        ('Іваненко', 'Петренко', False),
    ],
)
def test_same_name_rules(first: str, second: str, same: bool) -> None:
    assert uploads._same_name(first, second) is same
    assert uploads._same_name(second, first) is same


# --- deletion and isolation --------------------------------------------------


def test_delete_set_removes_rows_and_files(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    user = make_user()
    first, second = _halves(_parse('synevo_2.json'))
    fake_parser.extend([first, second])
    analysis_set, _ = store_upload(db, user, PDF_A, config)
    store_upload(db, user, PDF_B, config)
    assert len(_files(config.storage_dir)) == 2

    assert delete_set(db, user, analysis_set.id, config) is True

    _assert_nothing_stored(db, config)


def test_delete_set_of_another_user_touches_nothing(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    owner = make_user('u1')
    intruder = make_user('u2')
    fake_parser.append(_parse('synevo.json'))
    analysis_set, _ = store_upload(db, owner, PDF_A, config)

    assert delete_set(db, intruder, analysis_set.id, config) is False
    assert delete_set(db, intruder, analysis_set.id + 1000, config) is False

    assert _count(db, AnalysisSet) == 1
    assert _count(db, Result) == 1
    assert len(_files(config.storage_dir)) == 1


def test_delete_set_keeps_the_other_users_identical_file(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    first = make_user('u1')
    second = make_user('u2')
    fake_parser.extend([_parse('synevo.json'), _parse('synevo.json')])
    first_set, _ = store_upload(db, first, PDF_A, config)
    second_set, _ = store_upload(db, second, PDF_A, config)
    (second_file,) = second_set.files
    assert len(_files(config.storage_dir)) == 2

    assert delete_set(db, first, first_set.id, config) is True

    (remaining,) = _files(config.storage_dir)
    assert remaining == config.storage_dir / second_file.file_path
    assert remaining.read_bytes() == PDF_A
    assert _count(db, AnalysisSet) == 1
    assert len(_results(db, second_set)) == 1


def test_users_never_see_or_merge_into_each_other(
    seeded: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    db = seeded
    first = make_user('u1')
    second = make_user('u2')
    # Same lab and order number for both users: must not merge across them.
    fake_parser.extend([_parse('synevo.json'), _parse('synevo.json')])
    first_set, _ = store_upload(db, first, PDF_A, config)
    second_set, merged = store_upload(db, second, PDF_B, config)

    assert merged is False
    assert first_set.id != second_set.id
    first_ids = {r.id for r in _results(db, first_set)}
    second_ids = set(
        db.scalars(
            select(Result.id).join(AnalysisSet).where(AnalysisSet.user_id == second.id)
        )
    )
    assert first_ids
    assert second_ids
    assert first_ids.isdisjoint(second_ids)


# --- upload -> dynamics across both labs -------------------------------------


def test_two_labs_chain_from_upload_to_dynamics(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    config: AppConfig,
    fake_parser: list[ParsedSet],
) -> None:
    discord_id = '100000000000000001'
    owner = make_user(discord_id)
    stranger = make_user('100000000000000002')
    fake_parser.extend([_parse('lab_service.json'), _parse('synevo_2.json')])

    first_set, _ = store_upload(db, owner, PDF_A, config)
    second_set, _ = store_upload(db, owner, PDF_B, config)

    # The same person under both name forms: nothing to warn about.
    assert uploads.NAME_DIFFERS not in first_set.warnings + second_set.warnings
    codes = ('HGB', 'ESR', 'MCHC')
    ids = {
        code: db.scalar(select(Analyte.id).where(Analyte.code == code))
        for code in codes
    }
    assert all(ids.values())

    body = build_dynamics(db, owner, list(ids.values()))
    series = {item['code']: item for item in body['series']}
    table = {item['code']: item for item in body['table']}
    assert set(series) == set(codes)
    for code in codes:
        assert len(series[code]['points']) == 2, code
        assert [p['set_id'] for p in series[code]['points']] == [
            first_set.id,
            second_set.id,
        ]

    hgb, esr, mchc = (series[code]['points'] for code in codes)
    assert table['HGB']['delta'] == -36
    assert table['ESR']['delta'] == 0
    assert esr[0]['unit'] == esr[1]['unit'] == 'мм/год'
    assert table['MCHC']['delta'] is None
    assert mchc[0]['unit'] != mchc[1]['unit']
    for points in (hgb, esr, mchc):
        # Every point keeps the range printed with its own result.
        assert all(
            point['low'] is not None or point['high'] is not None for point in points
        )
        assert points[0]['ref_raw'] != points[1]['ref_raw']

    # The same data through the HTTP API.
    headers = {'X-Discord-User-Id': discord_id}
    query = '&'.join(f'analyte_id={analyte_id}' for analyte_id in ids.values())
    client.cookies.set('lang', 'uk')  # build_dynamics above names them in Ukrainian
    response = client.get(f'/api/dynamics?{query}', headers=headers)
    assert response.status_code == 200
    assert response.json() == body

    # Another user sees nothing of it.
    assert build_dynamics(db, stranger, list(ids.values())) == {
        'series': [],
        'table': [],
    }
    foreign = client.get(
        f'/api/dynamics?{query}', headers={'X-Discord-User-Id': '100000000000000002'}
    )
    assert foreign.json() == {'series': [], 'table': []}
