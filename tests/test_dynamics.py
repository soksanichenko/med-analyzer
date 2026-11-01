"""Tests of the dynamics page and its JSON API.

Rows are built with the ORM, so the tests do not depend on the parsers.
"""

import hashlib
import re
import shutil
import subprocess
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from med_analyzer.models import AnalysisSet, Analyte, Result, User

USER_ID = '100000000000000001'
OTHER_USER_ID = '100000000000000002'
STATIC_DIR = Path(__file__).parents[1] / 'sources/src/med_analyzer/static'
FORBIDDEN_JS = (
    'innerHTML',
    'outerHTML',
    'insertAdjacentHTML',
    'document.write',
    'eval(',
    'new Function',
)


def as_user(discord_id: str = USER_ID) -> dict[str, str]:
    """Return the headers nginx would add for the given Discord account."""
    return {'X-Discord-User-Id': discord_id}


def analyte_id(db: Session, code: str) -> int:
    """Return the id of a seeded analyte."""
    return db.scalar(select(Analyte.id).where(Analyte.code == code))


def add_set(
    db: Session,
    user: User,
    collected_on: date,
    rows: dict[str, dict],
    lab: str = 'synevo',
) -> AnalysisSet:
    """Commit a set holding one result per analyte code."""
    analysis_set = AnalysisSet(user_id=user.id, lab=lab, collected_on=collected_on)
    db.add(analysis_set)
    db.flush()
    for code, fields in rows.items():
        db.add(
            Result(
                set_id=analysis_set.id,
                analyte_id=analyte_id(db, code),
                raw_name=code,
                **fields,
            )
        )
    db.commit()
    return analysis_set


def hgb(value: str, low: str = '130', high: str = '160') -> dict:
    """Return the fields of a hemoglobin result."""
    return {
        'value': Decimal(value),
        'unit': 'г/л',
        'ref_low': Decimal(low),
        'ref_high': Decimal(high),
    }


@pytest.fixture
def owner(client: TestClient, make_user: Callable[..., User]) -> User:
    """The caller of most tests: a user with a complete profile."""
    return make_user(USER_ID)


def dynamics(client: TestClient, *ids: object, user: str = USER_ID):
    """Call the dynamics API with repeated ``analyte_id`` parameters."""
    return client.get(
        '/api/dynamics',
        params=[('analyte_id', i) for i in ids],
        headers=as_user(user),
    )


# --- dynamics ----------------------------------------------------------------


def test_two_points_with_percent_and_table_row(
    client: TestClient, db: Session, owner: User
) -> None:
    add_set(db, owner, date(2025, 3, 1), {'HGB': hgb('110')})
    add_set(db, owner, date(2025, 1, 1), {'HGB': hgb('140')}, lab='lab_service')
    # Inserted newest first: the answer must be sorted by date anyway.
    add_set(db, owner, date(2025, 5, 1), {'HGB': hgb('140')})
    hgb_id = analyte_id(db, 'HGB')

    response = dynamics(client, hgb_id)

    assert response.status_code == 200
    body = response.json()
    (series,) = body['series']
    assert series['analyte_id'] == hgb_id
    assert series['code'] == 'HGB'
    assert series['unit'] == 'г/л'
    assert [p['date'] for p in series['points']] == [
        '2025-01-01',
        '2025-03-01',
        '2025-05-01',
    ]
    percents = [p['percent'] for p in series['points']]
    assert percents == pytest.approx([33.333, -66.667, 33.333], abs=1e-2)
    first = series['points'][0]
    assert first['value'] == 140
    assert first['value_str'] == '140'
    assert (first['low'], first['high']) == (130, 160)
    assert first['out'] is False
    assert series['points'][1]['out'] is True
    (row,) = body['table']
    assert row['current'] == 140
    assert row['current_str'] == '140'
    assert row['date'] == '2025-05-01'
    assert row['previous'] == 110
    assert row['previous_str'] == '110'
    assert row['previous_date'] == '2025-03-01'
    assert row['delta'] == 30
    assert row['unit'] == 'г/л'
    assert row['out'] is False


def test_single_point_has_no_previous_and_no_delta(
    client: TestClient, db: Session, owner: User
) -> None:
    add_set(db, owner, date(2025, 1, 1), {'HGB': hgb('140')})

    body = dynamics(client, analyte_id(db, 'HGB')).json()

    (row,) = body['table']
    assert row['current'] == 140
    assert row['previous'] is None
    assert row['previous_str'] is None
    assert row['previous_date'] is None
    assert row['delta'] is None


def test_lower_bound_only_point_is_kept_without_percent(
    client: TestClient, db: Session, owner: User
) -> None:
    add_set(
        db,
        owner,
        date(2025, 1, 1),
        {'HDL': {'value': Decimal('0.9'), 'unit': 'ммоль/л', 'ref_low': Decimal('1')}},
    )

    body = dynamics(client, analyte_id(db, 'HDL')).json()

    (point,) = body['series'][0]['points']
    assert point['percent'] is None
    assert point['low'] == 1
    assert point['high'] is None
    assert point['out'] is True
    assert body['table'][0]['current'] == 0.9


def test_each_point_uses_its_own_range(
    client: TestClient, db: Session, owner: User
) -> None:
    add_set(db, owner, date(2025, 1, 1), {'HGB': hgb('140', '130', '150')})
    add_set(db, owner, date(2025, 2, 1), {'HGB': hgb('140', '125', '155')})

    points = dynamics(client, analyte_id(db, 'HGB')).json()['series'][0]['points']

    assert [p['percent'] for p in points] == pytest.approx([50.0, 50.0])
    add_set(db, owner, date(2025, 3, 1), {'HGB': hgb('140', '100', '180')})
    points = dynamics(client, analyte_id(db, 'HGB')).json()['series'][0]['points']
    assert points[2]['percent'] == pytest.approx(50.0)
    add_set(db, owner, date(2025, 4, 1), {'HGB': hgb('140', '140', '180')})
    points = dynamics(client, analyte_id(db, 'HGB')).json()['series'][0]['points']
    assert points[3]['percent'] == pytest.approx(0.0)


def test_mchc_from_two_labs_keeps_each_unit_and_has_no_delta(
    client: TestClient, db: Session, owner: User
) -> None:
    add_set(
        db,
        owner,
        date(2025, 1, 1),
        {'MCHC': {'value': Decimal(340), 'unit': 'г/л', 'ref_low': Decimal(320)}},
        lab='lab_service',
    )
    add_set(
        db,
        owner,
        date(2025, 2, 1),
        {
            'MCHC': {
                'value': Decimal('34.5'),
                'unit': 'г/дл',
                'ref_low': Decimal(32),
                'ref_high': Decimal(36),
            }
        },
    )

    body = dynamics(client, analyte_id(db, 'MCHC')).json()

    (series,) = body['series']
    assert [p['unit'] for p in series['points']] == ['г/л', 'г/дл']
    assert series['unit'] == 'г/дл'
    assert body['table'][0]['delta'] is None
    assert body['table'][0]['previous'] == 340


@pytest.mark.parametrize(
    ('previous', 'current', 'delta'),
    [('0.1', '0.3', 0.2), ('1.10', '1.30', 0.2), ('0.3', '0.1', -0.2)],
)
def test_delta_is_computed_in_decimal(
    client: TestClient,
    db: Session,
    owner: User,
    previous: str,
    current: str,
    delta: float,
) -> None:
    add_set(db, owner, date(2025, 1, 1), {'HGB': hgb(previous)})
    add_set(db, owner, date(2025, 2, 1), {'HGB': hgb(current)})

    row = dynamics(client, analyte_id(db, 'HGB')).json()['table'][0]

    # A float subtraction would give 0.19999999999999998 here.
    assert row['delta'] == delta


def test_text_only_analyte_is_omitted(
    client: TestClient, db: Session, owner: User
) -> None:
    add_set(
        db,
        owner,
        date(2025, 1, 1),
        {'HGB': {'value_text': 'не обнаружено'}, 'MCH': hgb('28')},
    )

    body = dynamics(client, analyte_id(db, 'HGB'), analyte_id(db, 'MCH')).json()

    assert [s['code'] for s in body['series']] == ['MCH']
    assert [r['code'] for r in body['table']] == ['MCH']


def test_foreign_and_unknown_ids_are_omitted_without_error(
    client: TestClient, db: Session, owner: User, make_user: Callable[..., User]
) -> None:
    stranger = make_user(OTHER_USER_ID)
    add_set(db, stranger, date(2025, 1, 1), {'MCH': hgb('29')})
    add_set(db, owner, date(2025, 1, 1), {'HGB': hgb('140')})

    response = dynamics(client, analyte_id(db, 'MCH'), 2**31 - 1, analyte_id(db, 'HGB'))

    assert response.status_code == 200
    assert [s['code'] for s in response.json()['series']] == ['HGB']
    assert dynamics(client, 999999).json() == {'series': [], 'table': []}


def test_other_users_values_never_appear(
    client: TestClient, db: Session, owner: User, make_user: Callable[..., User]
) -> None:
    stranger = make_user(OTHER_USER_ID)
    add_set(db, stranger, date(2025, 1, 1), {'HGB': hgb('111')})
    add_set(db, stranger, date(2025, 2, 1), {'HGB': hgb('222')})
    add_set(db, owner, date(2025, 3, 1), {'HGB': hgb('140')})
    hgb_id = analyte_id(db, 'HGB')

    mine = dynamics(client, hgb_id).json()
    theirs = dynamics(client, hgb_id, user=OTHER_USER_ID).json()

    assert [p['value'] for p in mine['series'][0]['points']] == [140]
    assert mine['table'][0]['previous'] is None
    assert [p['value'] for p in theirs['series'][0]['points']] == [111, 222]
    assert '111' not in str(mine) and '222' not in str(mine)


def test_series_follow_the_order_of_the_request_and_duplicates_collapse(
    client: TestClient, db: Session, owner: User
) -> None:
    add_set(db, owner, date(2025, 1, 1), {'HGB': hgb('140'), 'MCH': hgb('28')})
    hgb_id, mch_id = analyte_id(db, 'HGB'), analyte_id(db, 'MCH')

    first = dynamics(client, mch_id, hgb_id, mch_id).json()
    second = dynamics(client, hgb_id, mch_id).json()

    assert [s['code'] for s in first['series']] == ['MCH', 'HGB']
    assert [r['code'] for r in first['table']] == ['MCH', 'HGB']
    assert [s['code'] for s in second['series']] == ['HGB', 'MCH']


def test_no_ids_gives_empty_answer(client: TestClient, owner: User) -> None:
    response = client.get('/api/dynamics', headers=as_user())

    assert response.status_code == 200
    assert response.json() == {'series': [], 'table': []}


@pytest.mark.parametrize('bad', ['abc', '0', '-1', str(2**40), '1.5', ''])
def test_bad_ids_are_refused(client: TestClient, owner: User, bad: str) -> None:
    assert dynamics(client, bad).status_code == 422


def test_too_many_ids_are_refused(client: TestClient, owner: User) -> None:
    assert dynamics(client, *range(1, 13)).status_code == 200
    assert dynamics(client, *range(1, 14)).status_code == 422


def test_query_count_does_not_grow_with_the_analytes(
    client: TestClient, db: Session, owner: User
) -> None:
    codes = ['HGB', 'MCH', 'MCHC', 'HDL', 'NONHDL']
    for month in (1, 2):
        add_set(db, owner, date(2025, month, 1), {code: hgb('5') for code in codes})
    ids = [analyte_id(db, code) for code in codes]
    statements: list[str] = []

    def count(conn, cursor, statement, *args) -> None:
        statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, 'before_cursor_execute', count)
    try:
        dynamics(client, ids[0])
        single = len(statements)
        statements.clear()
        response = dynamics(client, *ids)
        many = len(statements)
    finally:
        event.remove(engine, 'before_cursor_execute', count)

    assert len(response.json()['series']) == 5
    assert many == single


# --- analytes ----------------------------------------------------------------


def test_analytes_list_only_numeric_own_results(
    client: TestClient, db: Session, owner: User, make_user: Callable[..., User]
) -> None:
    stranger = make_user(OTHER_USER_ID)
    add_set(db, stranger, date(2025, 1, 1), {'MCHC': hgb('330')})
    add_set(
        db,
        owner,
        date(2025, 1, 1),
        {'HGB': hgb('140'), 'MCH': {'value_text': 'норма'}},
    )
    add_set(db, owner, date(2025, 2, 1), {'HGB': hgb('141')})

    client.cookies.set('lang', 'uk')
    response = client.get('/api/analytes', headers=as_user())

    assert response.status_code == 200
    assert response.json() == [
        {
            'id': analyte_id(db, 'HGB'),
            'code': 'HGB',
            'name': db.scalar(select(Analyte.name).where(Analyte.code == 'HGB')),
            'count': 2,
        }
    ]


def test_analytes_are_ordered_by_name_and_filtered_case_insensitively(
    client: TestClient, db: Session, owner: User
) -> None:
    add_set(
        db,
        owner,
        date(2025, 1, 1),
        {'MCHC': hgb('330'), 'HGB': hgb('140'), 'HDL': hgb('1')},
    )

    everything = client.get('/api/analytes', headers=as_user()).json()
    by_name = client.get('/api/analytes', params={'q': 'ГЕМОГ'}, headers=as_user())
    by_code = client.get('/api/analytes', params={'q': 'hgb'}, headers=as_user())

    names = [item['name'] for item in everything]
    assert names == sorted(names)
    assert {item['code'] for item in by_name.json()} >= {'HGB'}
    assert all('гемог' in item['name'].lower() for item in by_name.json())
    assert [item['code'] for item in by_code.json()] == ['HGB']
    assert client.get('/api/analytes', params={'q': ''}, headers=as_user()).json() == (
        everything
    )


@pytest.mark.parametrize('q', ['%', '_', '\\', 'H%B', 'H_B'])
def test_wildcards_in_the_search_match_literally(
    client: TestClient, db: Session, owner: User, q: str
) -> None:
    add_set(db, owner, date(2025, 1, 1), {'HGB': hgb('140')})

    response = client.get('/api/analytes', params={'q': q}, headers=as_user())

    assert response.status_code == 200
    assert response.json() == []


def test_search_text_longer_than_100_is_refused(
    client: TestClient, owner: User
) -> None:
    ok = client.get('/api/analytes', params={'q': 'a' * 100}, headers=as_user())
    long = client.get('/api/analytes', params={'q': 'a' * 101}, headers=as_user())

    assert ok.status_code == 200
    assert long.status_code == 422


# --- page and assets ---------------------------------------------------------


@pytest.mark.parametrize('path', ['/dynamics', '/api/analytes', '/api/dynamics'])
def test_dynamics_routes_need_an_identity(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


class ScriptCollector(HTMLParser):
    """Collect the attributes of every ``<script>`` tag that has no ``src``."""

    def __init__(self) -> None:
        """Start with no scripts seen."""
        super().__init__()
        self.inline: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record a script tag without a source."""
        found = dict(attrs)
        if tag == 'script' and 'src' not in found:
            self.inline.append(found)


def test_page_links_the_scripts_and_has_no_inline_script(
    client: TestClient, owner: User
) -> None:
    response = client.get('/dynamics', headers=as_user())

    assert response.status_code == 200
    html = response.text
    assert re.search(r'src="/static/vendor/echarts\.min\.js\?v=[0-9a-f]{8}"', html)
    assert re.search(r'src="/static/dynamics\.js\?v=[0-9a-f]{8}"', html)
    assert html.index('echarts.min.js') < html.index('dynamics.js')
    # The only script without a source is the JSON of texts, which never runs.
    collector = ScriptCollector()
    collector.feed(html)
    assert collector.inline == [{'type': 'application/json', 'id': 'dyn-i18n'}]
    assert ' onclick=' not in html and 'javascript:' not in html
    assert 'class="active"' in html
    assert response.headers['cache-control'] == 'no-store'


def test_scripts_are_served(client: TestClient) -> None:
    for path in ('/static/vendor/echarts.min.js', '/static/dynamics.js'):
        response = client.get(path)
        assert response.status_code == 200
        assert 'javascript' in response.headers['content-type']


def test_client_script_avoids_html_injection_and_eval() -> None:
    source = (STATIC_DIR / 'dynamics.js').read_text(encoding='utf-8')

    found = [token for token in FORBIDDEN_JS if token in source]

    assert found == []


@pytest.mark.skipif(shutil.which('node') is None, reason='node is not installed')
def test_client_script_is_valid_javascript() -> None:
    result = subprocess.run(
        ['node', '--check', str(STATIC_DIR / 'dynamics.js')],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_vendored_echarts_matches_the_recorded_hash() -> None:
    readme = (STATIC_DIR / 'vendor/README.md').read_text(encoding='utf-8')
    digest = hashlib.sha256((STATIC_DIR / 'vendor/echarts.min.js').read_bytes())

    recorded = re.search(r'echarts\.min\.js.*?`([0-9a-f]{64})`', readme, re.DOTALL)

    assert recorded is not None
    assert recorded.group(1) == digest.hexdigest()
    assert '6.1.0' in readme
