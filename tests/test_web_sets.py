"""Tests of the results pages: upload, list by date, detail and delete.

The parser is replaced by a fake returning sets parsed from the word fixtures,
so the uploaded bytes only need to look like a PDF.
"""

import asyncio
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.requests import Request

from med_analyzer import uploads
from med_analyzer.config import AppConfig, get_config
from med_analyzer.models import AnalysisSet, Analyte, Result, SetFile, User
from med_analyzer.parsing.lab_service import parse_lab_service
from med_analyzer.parsing.synevo import parse_synevo
from med_analyzer.parsing.types import ParsedSet
from med_analyzer.parsing.words import pages_from_json
from med_analyzer.upload_body import (
    BodyTooLarge,
    InvalidForm,
    limited_stream,
    read_upload,
)
from med_analyzer.uploads import PROFILE_REQUIRED
from med_analyzer.i18n import format_date, render
from med_analyzer.web import lab_name, num

FIXTURES = Path(__file__).parent / 'fixtures'
USER_ID = '100000000000000001'
OTHER_USER_ID = '100000000000000002'
PDF_A = b'%PDF-1.7 first fake report'
PDF_B = b'%PDF-1.7 second fake report'
XSS = '<script>alert(1)</script>'
NOTE = 'отметка лаборатории не совпала с расчётом'


def as_user(discord_id: str = USER_ID, **extra: str) -> dict[str, str]:
    """Return the headers nginx would add for the given Discord account."""
    return {'X-Discord-User-Id': discord_id, **extra}


def parse_fixture(name: str) -> ParsedSet:
    pages = pages_from_json((FIXTURES / name).read_text(encoding='utf-8'))
    return (
        parse_synevo(pages) if name.startswith('synevo') else parse_lab_service(pages)
    )


def halves(parsed: ParsedSet) -> tuple[ParsedSet, ParsedSet]:
    middle = len(parsed.results) // 2
    return (
        replace(parsed, results=parsed.results[:middle], warnings=[]),
        replace(parsed, results=parsed.results[middle:], warnings=[]),
    )


@pytest.fixture
def fake_parser(monkeypatch: pytest.MonkeyPatch) -> list[ParsedSet]:
    """Make ``parse_pdf`` return the queued sets one per call."""
    queue: list[ParsedSet] = []

    def _fake(path: Path) -> ParsedSet:
        assert path.is_file()
        return queue.pop(0)

    monkeypatch.setattr(uploads, 'parse_pdf', _fake)
    return queue


@pytest.fixture
def owner(client: TestClient, make_user: Callable[..., User]) -> User:
    """The caller of most tests: a user with a complete profile."""
    return make_user(USER_ID)


def upload(
    client: TestClient,
    data: bytes = PDF_A,
    discord_id: str = USER_ID,
    **extra: str,
):
    return client.post(
        '/sets/upload',
        files={'file': ('report.pdf', data, 'application/pdf')},
        headers=as_user(discord_id, **extra),
        follow_redirects=False,
    )


def stored_files(client: TestClient) -> list[Path]:
    storage_dir = get_config().storage_dir
    return (
        [p for p in storage_dir.rglob('*') if p.is_file()]
        if storage_dir.exists()
        else []
    )


def count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model))


def add_set(
    db: Session,
    user: User,
    rows: list[dict],
    collected_on: date = date(2025, 6, 1),
    lab: str = 'synevo',
    **fields: object,
) -> AnalysisSet:
    """Commit a set with hand-built results (the analyte is irrelevant)."""
    analyte_id = db.scalar(select(Analyte.id).limit(1))
    analysis_set = AnalysisSet(
        user_id=user.id, lab=lab, collected_on=collected_on, **fields
    )
    db.add(analysis_set)
    db.flush()
    for row in rows:
        db.add(Result(set_id=analysis_set.id, analyte_id=analyte_id, **row))
    db.commit()
    return analysis_set


def row_html(html: str, name: str) -> str:
    """Return the table row (with its ``<tr>`` tag) that holds ``name``."""
    match = re.search(
        r'(<tr[^>]*>(?:(?!<tr).)*?' + re.escape(name) + r'.*?</tr>)', html, re.DOTALL
    )
    assert match is not None, name
    return match.group(1)


# --- basics ------------------------------------------------------------------


def test_root_redirects_to_sets(client: TestClient) -> None:
    response = client.get('/', follow_redirects=False)

    assert response.status_code == 303
    assert response.headers['location'] == '/sets'


def test_sets_needs_the_identity_header(client: TestClient) -> None:
    assert client.get('/sets').status_code == 401


def test_empty_state(client: TestClient, owner: User) -> None:
    html = client.get('/sets', headers=as_user()).text

    assert 'Пока нет ни одного набора' in html
    assert 'action="/sets/upload"' in html
    assert 'enctype="multipart/form-data"' in html
    assert 'name="file"' in html
    assert 'accept=".pdf"' in html


# --- upload and the pages ----------------------------------------------------


def test_upload_redirects_to_the_new_set_and_list_shows_it(
    client: TestClient, owner: User, fake_parser: list[ParsedSet], db: Session
) -> None:
    parsed = parse_fixture('lab_service.json')
    fake_parser.append(parsed)

    response = upload(client)

    analysis_set = db.scalars(select(AnalysisSet)).one()
    assert response.status_code == 303
    assert response.headers['location'] == f'/sets/{analysis_set.id}'
    html = client.get('/sets', headers=as_user()).text
    assert 'Lab Service' in html
    assert 'Показателей: 27' in html
    assert 'Вне нормы: 6' in html
    assert f'href="/sets/{analysis_set.id}"' in html
    assert parsed.order_number in html


def test_detail_marks_the_out_of_range_rows(
    client: TestClient, owner: User, fake_parser: list[ParsedSet], db: Session
) -> None:
    parsed = parse_fixture('lab_service.json')
    fake_parser.append(parsed)
    location = upload(client).headers['location']

    html = client.get(location, headers=as_user()).text

    assert html.count('class="row-out"') == 6
    hgb = row_html(html, 'Концентрація гемоглобіну (HGB)')
    assert 'class="row-out"' in hgb
    assert '191' in hgb
    assert '▲' in hgb
    assert '▼' not in hgb
    assert '130–160' in hgb
    assert 'Lab Service' in html
    assert 'Файлов: 1' in html
    assert parsed.patient_name in html


def test_decimal_comma_everywhere(client: TestClient, owner: User, db: Session) -> None:
    analysis_set = add_set(
        db,
        owner,
        [
            {
                'raw_name': 'Decimal analyte',
                'value': Decimal('6.40'),
                'unit': 'ммоль/л',
                'ref_low': Decimal('3.50'),
                'ref_high': Decimal('5.00'),
            }
        ],
    )

    html = client.get(f'/sets/{analysis_set.id}', headers=as_user()).text

    row = row_html(html, 'Decimal analyte')
    assert '6,40 ммоль/л' in row
    assert '3,50–5,00' in row
    assert '6.40' not in row
    assert '▲' in row


def test_merged_upload_shows_the_message(
    client: TestClient, owner: User, fake_parser: list[ParsedSet], db: Session
) -> None:
    first, second = halves(parse_fixture('synevo_2.json'))
    fake_parser.extend([first, second])

    first_response = upload(client)
    second_response = upload(client, PDF_B)

    assert '?' not in first_response.headers['location']
    assert second_response.status_code == 303
    assert second_response.headers['location'].endswith('?merged=1')
    page = client.get(second_response.headers['location'], headers=as_user()).text
    assert 'Файл добавлен в существующий набор' in page
    assert 'Файлов: 2' in page
    plain = client.get(first_response.headers['location'], headers=as_user()).text
    assert 'Файл добавлен в существующий набор' not in plain
    assert count(db, AnalysisSet) == 1
    assert 'Файлов: 2' in client.get('/sets', headers=as_user()).text


def test_duplicate_upload_is_409_with_a_link_to_the_set(
    client: TestClient, owner: User, fake_parser: list[ParsedSet], db: Session
) -> None:
    fake_parser.append(parse_fixture('synevo.json'))
    upload(client)
    analysis_set = db.scalars(select(AnalysisSet)).one()

    response = upload(client)

    assert response.status_code == 409
    assert 'Этот файл уже загружен' in response.text
    assert f'href="/sets/{analysis_set.id}"' in response.text
    assert count(db, SetFile) == 1


def test_text_threshold_and_one_sided_rows_render(
    client: TestClient, owner: User, db: Session
) -> None:
    threshold = 'Желательный уровень: <5,2 ммоль/л\nПограничный: 5,2-6,2'
    analysis_set = add_set(
        db,
        owner,
        [
            {'raw_name': 'Text analyte', 'value_text': 'не обнаружено'},
            {
                'raw_name': 'Threshold analyte',
                'value': Decimal('5.5'),
                'unit': 'ммоль/л',
                'ref_raw': threshold,
            },
            {
                'raw_name': 'Low only analyte',
                'value': Decimal('2'),
                'unit': 'г/л',
                'ref_low': Decimal('3.5'),
            },
            {
                'raw_name': 'High only analyte',
                'value': Decimal('2'),
                'unit': 'г/л',
                'ref_high': Decimal('3.5'),
            },
            {'raw_name': 'Bare analyte', 'value': Decimal('1')},
        ],
    )

    response = client.get(f'/sets/{analysis_set.id}', headers=as_user())

    assert response.status_code == 200
    html = response.text
    text_row = row_html(html, 'Text analyte')
    assert 'не обнаружено' in text_row
    assert 'class="row-out"' not in text_row
    threshold_row = row_html(html, 'Threshold analyte')
    assert 'Желательный уровень: &lt;5,2 ммоль/л\nПограничный: 5,2-6,2' in threshold_row
    assert '<pre' in threshold_row
    assert 'от 3,5' in row_html(html, 'Low only analyte')
    assert '▼' in row_html(html, 'Low only analyte')
    assert 'до 3,5' in row_html(html, 'High only analyte')
    assert '▼' not in row_html(html, 'High only analyte')
    assert '—' in row_html(html, 'Bare analyte')
    # Only the one-sided low row is out of range.
    assert html.count('class="row-out"') == 1


def test_value_on_the_bound_is_in_range(
    client: TestClient, owner: User, db: Session
) -> None:
    analysis_set = add_set(
        db,
        owner,
        [
            {
                'raw_name': 'On the edge',
                'value': Decimal('4'),
                'ref_low': Decimal('4'),
                'ref_high': Decimal('4'),
            }
        ],
    )

    assert 'Вне нормы: 0' in client.get('/sets', headers=as_user()).text
    detail = client.get(f'/sets/{analysis_set.id}', headers=as_user()).text
    assert 'class="row-out"' not in detail


def test_lab_flag_that_disagrees_with_the_calculation_is_noted(
    client: TestClient, owner: User, db: Session
) -> None:
    analysis_set = add_set(
        db,
        owner,
        [
            {
                'raw_name': 'Opposite',
                'value': Decimal('5'),
                'ref_low': Decimal('1'),
                'ref_high': Decimal('4'),
                'flag': 'low',
            },
            {
                'raw_name': 'Calculated in range',
                'value': Decimal('3'),
                'ref_low': Decimal('1'),
                'ref_high': Decimal('4'),
                'flag': 'high',
            },
            {
                'raw_name': 'Agreeing',
                'value': Decimal('5'),
                'ref_low': Decimal('1'),
                'ref_high': Decimal('4'),
                'flag': 'high',
            },
            {
                'raw_name': 'Unflagged',
                'value': Decimal('5'),
                'ref_low': Decimal('1'),
                'ref_high': Decimal('4'),
            },
        ],
    )

    html = client.get(f'/sets/{analysis_set.id}', headers=as_user()).text

    assert NOTE in row_html(html, 'Opposite')
    assert NOTE in row_html(html, 'Calculated in range')
    assert NOTE not in row_html(html, 'Agreeing')
    assert NOTE not in row_html(html, 'Unflagged')
    assert html.count(NOTE) == 2


def test_everything_user_controlled_is_escaped(
    client: TestClient, owner: User, db: Session
) -> None:
    analysis_set = add_set(
        db,
        owner,
        [
            {
                'raw_name': XSS,
                'value_text': XSS,
                'ref_raw': XSS,
            },
            # The unit is shown only next to a number.
            {'raw_name': 'Numeric', 'value': Decimal('1'), 'unit': XSS},
            {'raw_name': 'Numeric raw', 'value': Decimal('1'), 'ref_raw': XSS},
        ],
        patient_name=XSS,
        order_number=XSS,
        warnings=[XSS],
    )

    detail = client.get(f'/sets/{analysis_set.id}', headers=as_user()).text
    listing = client.get('/sets', headers=as_user()).text

    for html in (detail, listing):
        assert '<script>' not in html
        assert 'alert(1)' not in html.replace(
            '&lt;script&gt;alert(1)&lt;/script&gt;', ''
        )
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in row_html(detail, 'Numeric')
    assert detail.count('&lt;script&gt;') >= 7


@pytest.mark.parametrize('value', ['abc', '2', '<script>', ''])
def test_odd_merged_parameter_gives_a_normal_page_without_the_notice(
    client: TestClient, owner: User, db: Session, value: str
) -> None:
    analysis_set = add_set(db, owner, [{'raw_name': 'a'}])

    response = client.get(f'/sets/{analysis_set.id}?merged={value}', headers=as_user())

    assert response.status_code == 200
    assert 'Файл добавлен в существующий набор' not in response.text
    # Digits occur in any page (dates); other values must not be echoed.
    assert not value or value.isdigit() or value not in response.text


def test_sets_are_grouped_by_date_newest_first(
    client: TestClient, owner: User, make_user: Callable[..., User], db: Session
) -> None:
    other = make_user(OTHER_USER_ID)
    older = add_set(
        db, owner, [{'raw_name': 'a'}], date(2025, 3, 5), order_number='O-1'
    )
    newer = add_set(
        db, owner, [{'raw_name': 'a'}], date(2025, 11, 4), order_number='O-2'
    )
    same_day = add_set(
        db,
        owner,
        [{'raw_name': 'a'}],
        date(2025, 11, 4),
        'lab_service',
        order_number='O-3',
    )
    add_set(db, other, [{'raw_name': 'a'}], date(2020, 9, 9), order_number='O-HIDDEN')

    html = client.get('/sets', headers=as_user()).text

    assert 'O-HIDDEN' not in html
    assert '9 сентября 2020' not in html
    order = [html.index(text) for text in ('4 ноября 2025', '5 марта 2025')]
    assert order == sorted(order)
    # Within a day the later upload comes first.
    assert html.index(f'/sets/{same_day.id}"') < html.index(f'/sets/{newer.id}"')
    assert html.index(f'/sets/{newer.id}"') < html.index(f'/sets/{older.id}"')
    assert html.count('4 ноября 2025') == 1


# --- access control ----------------------------------------------------------


def test_another_users_set_is_a_plain_404_like_a_missing_one(
    client: TestClient, owner: User, make_user: Callable[..., User], db: Session
) -> None:
    make_user(OTHER_USER_ID)
    analysis_set = add_set(db, owner, [{'raw_name': 'a'}])

    foreign = client.get(f'/sets/{analysis_set.id}', headers=as_user(OTHER_USER_ID))
    missing = client.get(f'/sets/{analysis_set.id + 100}', headers=as_user())
    delete = client.post(
        f'/sets/{analysis_set.id}/delete', headers=as_user(OTHER_USER_ID)
    )

    assert foreign.status_code == missing.status_code == delete.status_code == 404
    assert foreign.text == missing.text == delete.text
    assert foreign.headers['content-type'] == missing.headers['content-type']
    assert count(db, AnalysisSet) == 1
    assert count(db, Result) == 1


@pytest.mark.parametrize('path', ['/sets/abc', '/sets/0', '/sets/99999999999999999999'])
def test_bad_set_ids_are_404(client: TestClient, owner: User, path: str) -> None:
    assert client.get(path, headers=as_user()).status_code == 404


def test_delete_removes_rows_and_files(
    client: TestClient, owner: User, fake_parser: list[ParsedSet], db: Session
) -> None:
    first, second = halves(parse_fixture('synevo_2.json'))
    fake_parser.extend([first, second])
    upload(client)
    location = upload(client, PDF_B).headers['location'].split('?')[0]
    assert len(stored_files(client)) == 2

    page = client.get(location, headers=as_user()).text
    assert 'action="' + location + '/delete"' in page
    assert 'Удалить набор' in page
    response = client.post(
        f'{location}/delete', headers=as_user(), follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers['location'] == '/sets'
    assert (count(db, AnalysisSet), count(db, Result), count(db, SetFile)) == (0, 0, 0)
    assert stored_files(client) == []
    assert client.get(location, headers=as_user()).status_code == 404


def test_cross_site_upload_and_delete_are_refused(
    client: TestClient, owner: User, fake_parser: list[ParsedSet], db: Session
) -> None:
    analysis_set = add_set(db, owner, [{'raw_name': 'a'}])
    site = {'Sec-Fetch-Site': 'cross-site'}

    upload_response = upload(client, **site)
    delete_response = client.post(
        f'/sets/{analysis_set.id}/delete', headers=as_user(**site)
    )

    assert upload_response.status_code == delete_response.status_code == 403
    assert count(db, AnalysisSet) == 1
    assert count(db, SetFile) == 0
    assert stored_files(client) == []


# --- profile and rejected uploads --------------------------------------------


def test_without_a_profile_the_banner_shows_and_upload_is_refused(
    client: TestClient, make_user: Callable[..., User], db: Session
) -> None:
    make_user(USER_ID, sex=None, birth_date=None)

    html = client.get('/sets', headers=as_user()).text
    response = upload(client)

    required = render('ru', PROFILE_REQUIRED)
    assert required in html
    assert 'href="/profile"' in html
    assert 'disabled' in html
    assert response.status_code == 422
    assert required in response.text
    assert count(db, AnalysisSet) == 0
    assert stored_files(client) == []


def test_profile_banner_is_absent_with_a_profile(
    client: TestClient, owner: User
) -> None:
    html = client.get('/sets', headers=as_user()).text

    assert render('ru', PROFILE_REQUIRED) not in html
    assert 'disabled' not in html


def test_not_a_pdf_is_422(client: TestClient, owner: User, db: Session) -> None:
    response = upload(client, b'hello')

    assert response.status_code == 422
    assert 'Файл не похож на PDF' in response.text
    assert 'action="/sets/upload"' in response.text
    assert count(db, AnalysisSet) == 0
    assert stored_files(client) == []


def test_missing_file_field_is_422(client: TestClient, owner: User) -> None:
    response = client.post(
        '/sets/upload', data={'other': 'x'}, headers=as_user(), follow_redirects=False
    )

    assert response.status_code == 422
    assert 'Выберите PDF-файл' in response.text


def test_body_that_is_not_multipart_is_422(client: TestClient, owner: User) -> None:
    response = client.post('/sets/upload', content=b'%PDF-1.7', headers=as_user())

    assert response.status_code == 422


@pytest.mark.parametrize(
    'size',
    [
        # Just over the cap: read whole, refused by the upload service.
        1024 * 1024 + 1,
        # Far over: refused from the declared length without reading the body.
        3 * 1024 * 1024,
    ],
)
def test_oversized_upload_is_422_and_stores_nothing(
    client: TestClient,
    owner: User,
    fake_parser: list[ParsedSet],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    size: int,
) -> None:
    monkeypatch.setenv('MAX_UPLOAD_MB', '1')
    get_config.cache_clear()
    data = b'%PDF-' + b'0' * size

    response = upload(client, data)

    assert response.status_code == 422
    assert 'Файл слишком большой (максимум 1 МБ)' in response.text
    assert count(db, AnalysisSet) == 0
    assert stored_files(client) == []


def test_limited_stream_stops_reading_at_the_limit() -> None:
    pulled = []

    async def endless() -> AsyncIterator[bytes]:
        while True:
            pulled.append(1)
            yield b'x' * 1024

    async def drain() -> None:
        async for _ in limited_stream(endless(), 10 * 1024):
            pass

    with pytest.raises(BodyTooLarge):
        asyncio.run(drain())

    # One chunk past the limit was read, not the whole (endless) body.
    assert len(pulled) == 11


def test_non_ascii_digits_in_content_length_do_not_crash() -> None:
    scope = {
        'type': 'http',
        'method': 'POST',
        'headers': [
            (b'content-type', b'multipart/form-data; boundary=x'),
            ('content-length'.encode(), '²'.encode('latin-1')),
        ],
    }

    async def empty() -> dict:
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    request = Request(scope, empty)
    config = AppConfig(database_url='postgresql://x', storage_dir=Path('x'))

    # The unusable length is ignored; the (empty) body is then read and has no
    # file part, instead of ``int('²')`` raising.
    with pytest.raises(InvalidForm):
        asyncio.run(read_upload(request, config))


def test_integrity_error_is_409_with_a_retry_message(
    client: TestClient,
    owner: User,
    monkeypatch: pytest.MonkeyPatch,
    db: Session,
) -> None:
    def _race(*args: object, **kwargs: object) -> None:
        raise IntegrityError('insert', {}, Exception('duplicate order number'))

    monkeypatch.setattr('med_analyzer.routes_sets.store_upload', _race)

    response = upload(client)

    assert response.status_code == 409
    assert 'Не удалось сохранить файл, попробуйте ещё раз' in response.text
    assert 'duplicate order number' not in response.text
    # The session is usable afterwards.
    assert count(db, AnalysisSet) == 0


# --- filters -----------------------------------------------------------------


def test_filters() -> None:
    assert format_date('ru', date(2021, 10, 14)) == '14 октября 2021'
    assert format_date('ru', date(2025, 1, 31)) == '31 января 2025'
    assert format_date('ru', date(2025, 12, 1)) == '1 декабря 2025'
    assert format_date('uk', date(2021, 10, 14)) == '14 жовтня 2021'
    assert format_date('en', date(2021, 10, 14)) == 'October 14, 2021'
    assert num(Decimal('6.40'), 'en') == '6.40'
    assert num(Decimal('6.40'), 'ru') == '6,40'
    assert num(Decimal('6.40')) == '6,40'
    assert num(Decimal('125')) == '125'
    assert num(Decimal('1E+2')) == '100'
    assert num(Decimal('0.0000012')) == '0,0000012'
    assert num('3.5') == '3,5'
    assert num(None) == ''
    assert num('abc') == 'abc'
    assert lab_name('lab_service') == 'Lab Service'
    assert lab_name('synevo', 'ru') == 'Синево'
    assert lab_name('synevo', 'uk') == 'Сінево'
    assert lab_name('synevo', 'en') == 'Synevo'
    assert lab_name('other') == 'other'
