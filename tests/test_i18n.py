"""Tests of the interface language, the page header, logout and static hashes."""

import hashlib
import json
import re
import string
from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from med_analyzer.analyte_names import ANALYTE_NAMES, analyte_name
from med_analyzer.analyte_seed import SEED
from med_analyzer.deps import session_cookie_domain
from med_analyzer.i18n import (
    DEFAULT_LANGUAGE,
    LANGUAGES,
    Msg,
    decode_msg,
    detect_language,
    encode_msg,
    js_strings,
    msg,
    parse_accept_language,
    render,
    render_stored,
    translate,
)
from med_analyzer.messages import CATALOGS
from med_analyzer.models import AnalysisSet, Analyte, Result, User
from med_analyzer.web import STATIC_DIR, static_url

USER_ID = '100000000000000001'


def as_user(**extra: str) -> dict[str, str]:
    """Return the headers nginx would add, plus any extra ones."""
    return {'X-Discord-User-Id': USER_ID, **extra}


def request_with(headers: dict[str, str]) -> Request:
    """Build a bare request carrying the given headers."""
    scope = {
        'type': 'http',
        'headers': [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    return Request(scope)


def placeholders(text: str) -> set[str]:
    """Return the replacement fields of a catalog text."""
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


# --- catalogs ----------------------------------------------------------------


def test_catalogs_have_the_same_keys_and_placeholders() -> None:
    reference = CATALOGS[DEFAULT_LANGUAGE]
    assert set(CATALOGS) == set(LANGUAGES)
    for lang, catalog in CATALOGS.items():
        assert set(catalog) == set(reference), lang
        for key, text in catalog.items():
            assert text.strip(), (lang, key)
            assert placeholders(text) == placeholders(reference[key]), (lang, key)


def test_languages_differ_from_each_other() -> None:
    # A catalog copied from another language would pass the checks above.
    for key in ('nav.sets', 'sets.empty', 'profile.hint', 'dyn.legend'):
        assert len({CATALOGS[lang][key] for lang in LANGUAGES}) == 3, key


def test_analyte_names_cover_the_seed_exactly() -> None:
    assert set(ANALYTE_NAMES) == {item.code for item in SEED}
    for code, (english, russian) in ANALYTE_NAMES.items():
        assert english.strip() and russian.strip(), code
        assert english.isascii() or '–' in english, code


def test_analyte_name_by_language() -> None:
    assert analyte_name('HGB', 'Гемоглобін', 'uk') == 'Гемоглобін'
    assert analyte_name('HGB', 'Гемоглобін', 'en') == 'Hemoglobin'
    assert analyte_name('HGB', 'Гемоглобін', 'ru') == 'Гемоглобин'
    assert analyte_name('XYZ', 'Своє', 'en') == 'Своє'


# --- translating and stored messages -----------------------------------------


def test_translate_and_render() -> None:
    assert translate('ru', 'sets.files', 3) == 'Файлов: 3'
    assert translate('uk', 'sets.files', 3) == 'Файлів: 3'
    assert translate('en', 'sets.files', 3) == 'Files: 3'
    assert translate('xx', 'sets.files', 3) == 'Файлів: 3'
    assert translate('en', 'no.such.key') == 'no.such.key'
    assert render('en', msg('upload.too_large', 10)) == (
        'The file is too large (maximum 10 MB)'
    )


def test_stored_warning_round_trip_in_every_language() -> None:
    stored = encode_msg(msg('warn.duplicate_value', 'HGB', '191', '181'))

    assert decode_msg(stored) == Msg('warn.duplicate_value', ('HGB', '191', '181'))
    assert render_stored('ru', stored) == (
        'Повтор показателя «HGB» с другим значением: 191 и 181'
    )
    assert render_stored('uk', stored) == (
        'Повтор показника «HGB» з іншим значенням: 191 і 181'
    )
    assert render_stored('en', stored) == (
        'Analyte “HGB” repeated with a different value: 191 and 181'
    )


@pytest.mark.parametrize(
    'stored',
    [
        'Повтор показателя «HGB»',  # a row written before messages were keys
        '',
        '{',
        '{"k": 1, "p": []}',
        '{"k": "no.such.key", "p": []}',
        '{"k": "sets.files", "p": [[1]]}',
        '{"k": "sets.files"}',
        '[1, 2]',
    ],
)
def test_plain_or_broken_stored_text_is_shown_as_it_is(stored: str) -> None:
    assert decode_msg(stored) is None
    assert render_stored('en', stored) == stored


def test_js_strings_hold_only_the_script_texts() -> None:
    texts = js_strings('en')

    assert texts['decimal'] == '.'
    assert texts['up_to'] == 'up to {0}'
    assert all(not key.startswith('js.') for key in texts)
    assert 'title' not in texts
    assert set(js_strings('uk')) == set(texts) == set(js_strings('ru'))


# --- choosing the language ---------------------------------------------------


@pytest.mark.parametrize(
    ('header', 'expected'),
    [
        ('', None),
        ('de-DE,de;q=0.9', None),
        ('en-US,en;q=0.9,uk;q=0.8', 'en'),
        ('uk', 'uk'),
        ('ru-RU', 'ru'),
        ('de,ru;q=0.5,en;q=0.7', 'en'),
        ('ru;q=0.4, uk;q=0.9', 'uk'),
        ('en;q=0, ru;q=0.1', 'ru'),
        ('en;q=abc, uk', 'uk'),
        ('*', None),
        ('EN', 'en'),
    ],
)
def test_parse_accept_language(header: str, expected: str | None) -> None:
    assert parse_accept_language(header) == expected


def test_detect_language_prefers_the_cookie() -> None:
    both = request_with({'cookie': 'lang=ru', 'accept-language': 'en'})
    bad_cookie = request_with({'cookie': 'lang=de', 'accept-language': 'en'})
    nothing = request_with({})

    assert detect_language(both) == 'ru'
    assert detect_language(bad_cookie) == 'en'
    assert detect_language(nothing) == 'uk'


@pytest.mark.parametrize(
    ('host', 'domain'),
    [
        ('med-analyzer.zelgray.cherkasy.ua', 'zelgray.cherkasy.ua'),
        ('med-analyzer.zelgray.cherkasy.ua:443', 'zelgray.cherkasy.ua'),
        ('localhost', None),
        ('localhost:8000', None),
        ('testserver', None),
        ('example.com', None),
        ('127.0.0.1:8000', None),
        ('10.0.0.5', None),
        ('', None),
    ],
)
def test_session_cookie_domain(host: str, domain: str | None) -> None:
    assert session_cookie_domain(request_with({'host': host})) == domain


# --- pages in a language -----------------------------------------------------


def test_pages_follow_the_language_cookie(client: TestClient) -> None:
    for lang, nav, title in (
        ('uk', 'Аналізи', 'Профіль'),
        ('en', 'Tests', 'Profile'),
        ('ru', 'Анализы', 'Профиль'),
    ):
        client.cookies.set('lang', lang)
        html = client.get('/profile', headers=as_user()).text

        assert f'<html lang="{lang}">' in html
        assert f'>{nav}</a>' in html
        assert f'<h1>{title}</h1>' in html
        assert f'<option value="{lang}" selected>' in html
        assert html.count(' selected>') == 1


def test_browser_language_is_used_without_a_cookie(client: TestClient) -> None:
    client.cookies.clear()

    english = client.get('/profile', headers=as_user(**{'Accept-Language': 'en-GB'}))
    default = client.get('/profile', headers=as_user())
    unsupported = client.get('/profile', headers=as_user(**{'Accept-Language': 'de'}))

    assert '<html lang="en">' in english.text
    assert '<html lang="uk">' in default.text
    assert '<html lang="uk">' in unsupported.text


def test_a_bad_cookie_falls_back_to_the_browser(client: TestClient) -> None:
    client.cookies.set('lang', 'fr')

    html = client.get('/profile', headers=as_user(**{'Accept-Language': 'ru'})).text

    assert '<html lang="ru">' in html


def test_profile_errors_are_in_the_page_language(client: TestClient) -> None:
    body = {'sex': 'M', 'birth_date': 'not a date'}
    expected = {
        'uk': 'Введіть дату народження у форматі РРРР-ММ-ДД',
        'en': 'Enter the date of birth as YYYY-MM-DD',
        'ru': 'Введите дату рождения в формате ГГГГ-ММ-ДД',
    }
    for lang, text in expected.items():
        client.cookies.set('lang', lang)

        response = client.post('/profile', data=body, headers=as_user())

        assert response.status_code == 422
        assert text in response.text


def test_upload_errors_are_in_the_page_language(
    client: TestClient, make_user: Callable[..., User]
) -> None:
    make_user(USER_ID, sex=None, birth_date=None)
    expected = {
        'uk': 'Спочатку заповніть профіль: стать і дата народження',
        'en': 'Fill in your profile first: sex and date of birth',
    }
    for lang, text in expected.items():
        client.cookies.set('lang', lang)

        response = client.post(
            '/sets/upload',
            files={'file': ('a.pdf', b'%PDF-1.7 x', 'application/pdf')},
            headers=as_user(),
        )

        assert response.status_code == 422
        assert text in response.text


def test_set_page_renders_stored_warnings_dates_and_numbers_per_language(
    client: TestClient, db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user(USER_ID)
    analysis_set = AnalysisSet(
        user_id=user.id,
        lab='synevo',
        collected_on=date(2021, 10, 14),
        warnings=[
            encode_msg(msg('warn.duplicate_value', 'HGB', '191', '181')),
            'Старое предупреждение',
        ],
    )
    db.add(analysis_set)
    db.flush()
    hgb = db.scalar(select(Analyte.id).where(Analyte.code == 'HGB'))
    db.add(
        Result(
            set_id=analysis_set.id,
            analyte_id=hgb,
            raw_name='HGB',
            value=Decimal('6.4'),
            unit='g',
            ref_low=Decimal('1.5'),
            ref_high=Decimal('5.5'),
        )
    )
    db.commit()
    url = f'/sets/{analysis_set.id}'
    expected = {
        'uk': ('Сінево, 14 жовтня 2021', 'Повтор показника «HGB» з іншим', '6,4 g'),
        'en': ('Synevo, October 14, 2021', 'Analyte “HGB” repeated', '6.4 g'),
        'ru': ('Синево, 14 октября 2021', 'Повтор показателя «HGB» с другим', '6,4 g'),
    }
    for lang, (heading, warning, value) in expected.items():
        client.cookies.set('lang', lang)

        html = client.get(url, headers=as_user()).text

        assert heading in html
        assert warning in html
        assert value in html
        assert '<li>Старое предупреждение</li>' in html


def test_dynamics_api_names_and_search_follow_the_language(
    client: TestClient, db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user(USER_ID)
    analysis_set = AnalysisSet(
        user_id=user.id, lab='synevo', collected_on=date(2025, 1, 1)
    )
    db.add(analysis_set)
    db.flush()
    for code in ('HGB', 'CREA'):
        analyte = db.scalar(select(Analyte.id).where(Analyte.code == code))
        db.add(
            Result(
                set_id=analysis_set.id,
                analyte_id=analyte,
                raw_name=code,
                value=Decimal(5),
                ref_low=Decimal(1),
                ref_high=Decimal(9),
            )
        )
    db.commit()

    def names(lang: str, q: str | None = None) -> list[str]:
        client.cookies.set('lang', lang)
        params = {} if q is None else {'q': q}
        found = client.get('/api/analytes', params=params, headers=as_user()).json()
        return [item['name'] for item in found]

    assert names('uk') == ['Гемоглобін', 'Креатинін']
    assert names('ru') == ['Гемоглобин', 'Креатинин']
    assert names('en') == ['Creatinine', 'Hemoglobin']
    assert names('en', 'hemo') == ['Hemoglobin']
    assert names('en', 'ГЕМОГЛ') == ['Hemoglobin']  # the stored name still matches
    assert names('ru', 'креат') == ['Креатинин']
    assert names('en', 'crea') == ['Creatinine']  # also by code
    assert names('en', '%') == []

    ids = [item['id'] for item in client.get('/api/analytes', headers=as_user()).json()]
    query = '&'.join(f'analyte_id={i}' for i in ids)
    client.cookies.set('lang', 'en')
    body = client.get(f'/api/dynamics?{query}', headers=as_user()).json()
    assert sorted(item['name'] for item in body['series']) == [
        'Creatinine',
        'Hemoglobin',
    ]
    assert sorted(item['name'] for item in body['table']) == [
        'Creatinine',
        'Hemoglobin',
    ]


def test_dynamics_page_hands_the_script_its_texts(client: TestClient) -> None:
    client.cookies.set('lang', 'en')

    html = client.get('/dynamics', headers=as_user()).text

    block = re.search(
        r'<script type="application/json" id="dyn-i18n">(.*?)</script>', html
    )
    assert block is not None
    texts = json.loads(block.group(1))
    assert texts == js_strings('en')
    assert texts['too_many'] == 'You can choose at most {0} analytes'


# --- header ------------------------------------------------------------------


def test_header_has_portal_link_picker_and_logout(client: TestClient) -> None:
    html = client.get('/profile', headers=as_user()).text

    assert '<a class="button-link" href="https://meow-elite.club/">Портал</a>' in html
    assert '<form action="/logout" method="post">' in html
    assert '>Выйти</button>' in html
    for value in LANGUAGES:
        assert f'<option value="{value}"' in html
    assert 'id="lang-select"' in html
    assert 'class="avatar-placeholder"' in html  # no avatar header was sent


def test_header_shows_the_avatar_when_the_gate_sends_one(client: TestClient) -> None:
    avatar = 'https://cdn.discordapp.com/avatars/1/abc.png'

    html = client.get(
        '/profile', headers=as_user(**{'X-Discord-Avatar-Url': avatar})
    ).text

    assert f'src="{avatar}"' in html
    assert 'avatar-placeholder' not in html


# --- logout ------------------------------------------------------------------


def test_logout_removes_the_bridged_cookie_of_the_parent_domain(
    client: TestClient,
) -> None:
    response = client.post(
        '/logout',
        headers=as_user(host='med-analyzer.zelgray.cherkasy.ua'),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers['location'] == '/'
    cookie = response.headers['set-cookie']
    assert cookie.startswith('zw_session=""') or cookie.startswith('zw_session=;')
    assert 'Domain=zelgray.cherkasy.ua' in cookie
    assert 'Path=/' in cookie
    assert 'Max-Age=0' in cookie


def test_logout_refuses_a_cross_site_request(client: TestClient) -> None:
    response = client.post(
        '/logout', headers=as_user(**{'Sec-Fetch-Site': 'cross-site'})
    )

    assert response.status_code == 403
    assert 'set-cookie' not in response.headers


def test_logout_is_post_only(client: TestClient) -> None:
    assert client.get('/logout', headers=as_user()).status_code == 405


# --- static files ------------------------------------------------------------


def test_static_url_carries_the_content_hash() -> None:
    url = static_url('style.css')
    digest = hashlib.md5((STATIC_DIR / 'style.css').read_bytes()).hexdigest()

    assert url == f'/static/style.css?v={digest[:8]}'
    assert static_url('vendor/echarts.min.js').startswith(
        '/static/vendor/echarts.min.js?v='
    )


def test_pages_link_every_asset_with_a_hash(client: TestClient) -> None:
    for path in ('/profile', '/sets', '/dynamics'):
        html = client.get(path, headers=as_user()).text
        links = re.findall(r'(?:href|src)="(/static/[^"]+)"', html)

        assert links, path
        assert all(
            re.fullmatch(r'/static/[\w./-]+\?v=[0-9a-f]{8}', link) for link in links
        ), (
            path,
            links,
        )
        assert '/static/header.js?v=' in html


def test_hashed_static_urls_are_served(client: TestClient) -> None:
    html = client.get('/dynamics', headers=as_user()).text

    for link in re.findall(r'src="(/static/[^"]+)"', html):
        response = client.get(link)

        assert response.status_code == 200, link
