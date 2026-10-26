"""Tests of the web foundation: identity header, profile page, health, headers."""

import logging
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import BaseRoute
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from med_analyzer.app import CONTENT_SECURITY_POLICY, create_app
from med_analyzer import uploads
from med_analyzer.db import get_db, make_session_factory
from med_analyzer.deps import same_origin
from med_analyzer.routes_sets import router as sets_router
from med_analyzer.models import AnalysisSet, Analyte, Result, User
from med_analyzer.parsing.types import ReferenceGroup
from med_analyzer.uploads import groups_to_json

USER_ID = '100000000000000001'
OTHER_USER_ID = '100000000000000002'
AVATAR = 'https://cdn.discordapp.com/avatars/1/abc.png'
POST_FORM = {'sex': 'M', 'birth_date': '1990-01-01'}


def as_user(discord_id: str = USER_ID, **extra: str) -> dict[str, str]:
    """Return the headers nginx would add for the given Discord account."""
    return {'X-Discord-User-Id': discord_id, **extra}


def test_profile_without_header_is_401(client: TestClient) -> None:
    response = client.get('/profile')

    assert response.status_code == 401
    assert response.text == 'Unauthorized'


@pytest.mark.parametrize(
    'value', ['', '   ', 'abc', '12a', '12 34', '-1', '1.5', '1' * 33]
)
def test_profile_with_malformed_id_is_401(client: TestClient, value: str) -> None:
    response = client.get('/profile', headers={'X-Discord-User-Id': value})

    assert response.status_code == 401


def test_malformed_id_creates_no_user(client: TestClient, db: Session) -> None:
    client.get('/profile', headers={'X-Discord-User-Id': "1'; DROP TABLE users;--"})

    assert db.scalar(select(func.count()).select_from(User)) == 0


def test_longest_valid_id_is_accepted(client: TestClient) -> None:
    response = client.get('/profile', headers=as_user('1' * 32))

    assert response.status_code == 200


def test_healthz_needs_no_header_and_no_database(
    web_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app()

    def _no_database() -> Iterator[Session]:
        raise AssertionError('/healthz must not touch the database')
        yield  # pragma: no cover

    app.dependency_overrides[get_db] = _no_database
    # No ``with``: the lifespan, which needs the database, is not started.
    response = TestClient(app).get('/healthz')

    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


def test_first_request_creates_the_user(client: TestClient, db: Session) -> None:
    response = client.get('/profile', headers=as_user())

    assert response.status_code == 200
    user = db.scalar(select(User).where(User.discord_user_id == USER_ID))
    assert user is not None
    assert (user.sex, user.birth_date) == (None, None)


def test_second_request_reuses_the_user(client: TestClient, db: Session) -> None:
    client.get('/profile', headers=as_user())
    client.get('/profile', headers=as_user())

    assert db.scalar(select(func.count()).select_from(User)) == 1


def test_profile_is_in_russian_with_navigation(client: TestClient) -> None:
    html = client.get('/profile', headers=as_user()).text

    for text in ('Мужской', 'Женский', 'Анализы', 'Динамика', 'Профиль'):
        assert text in html
    for link in ('href="/sets"', 'href="/dynamics"', 'href="/profile"'):
        assert link in html
    assert 'type="date"' in html


def test_post_saves_profile_and_redirects(client: TestClient, db: Session) -> None:
    response = client.post(
        '/profile', data=POST_FORM, headers=as_user(), follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers['location'] == '/sets'
    db.expire_all()
    user = db.scalar(select(User).where(User.discord_user_id == USER_ID))
    assert (user.sex, user.birth_date) == ('M', date(1990, 1, 1))


def test_saved_profile_is_prefilled(client: TestClient) -> None:
    client.post(
        '/profile', data={'sex': 'F', 'birth_date': '1985-05-17'}, headers=as_user()
    )

    html = client.get('/profile', headers=as_user()).text

    assert 'value="1985-05-17"' in html
    assert 'value="F" checked' in html
    assert 'value="M" checked' not in html


def test_post_recomputes_references(client: TestClient, db: Session) -> None:
    client.get('/profile', headers=as_user())
    user = db.scalar(select(User).where(User.discord_user_id == USER_ID))
    analyte = db.scalar(select(Analyte).limit(1))
    analysis_set = AnalysisSet(
        user_id=user.id, lab='synevo', collected_on=date(2025, 6, 1)
    )
    db.add(analysis_set)
    db.flush()
    groups = [
        ReferenceGroup('M', 216, None, Decimal('10'), Decimal('20'), 'men'),
        ReferenceGroup('F', 216, None, Decimal('30'), Decimal('40'), 'women'),
    ]
    result = Result(
        set_id=analysis_set.id,
        analyte_id=analyte.id,
        raw_name='test',
        value=Decimal('15'),
        ref_groups=groups_to_json(groups),
    )
    db.add(result)
    db.commit()

    client.post(
        '/profile', data={'sex': 'M', 'birth_date': '1990-01-01'}, headers=as_user()
    )
    db.expire_all()
    assert (result.ref_low, result.ref_high) == (Decimal('10'), Decimal('20'))

    client.post(
        '/profile', data={'sex': 'F', 'birth_date': '1990-01-01'}, headers=as_user()
    )
    db.expire_all()
    assert (result.ref_low, result.ref_high) == (Decimal('30'), Decimal('40'))


def test_failed_recompute_does_not_save_the_new_profile(
    client: TestClient,
    db: Session,
    pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = {'sex': 'F', 'birth_date': '1985-05-17'}
    client.post('/profile', data=old, headers=as_user())
    user = db.scalar(select(User).where(User.discord_user_id == USER_ID))
    analyte = db.scalar(select(Analyte).limit(1))
    analysis_set = AnalysisSet(
        user_id=user.id, lab='synevo', collected_on=date(2025, 6, 1)
    )
    db.add(analysis_set)
    db.flush()
    db.add(
        Result(
            set_id=analysis_set.id,
            analyte_id=analyte.id,
            raw_name='test',
            value=Decimal('15'),
            ref_groups=[],
        )
    )
    db.commit()

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError('injected')

    monkeypatch.setattr(uploads, '_select', _boom)
    response = client.post('/profile', data=POST_FORM, headers=as_user())

    assert response.status_code == 500
    # Read with a session of its own: only committed data is visible there.
    with make_session_factory(pg_url)() as other:
        saved = other.scalar(select(User).where(User.discord_user_id == USER_ID))
        assert (saved.sex, saved.birth_date) == ('F', date(1985, 5, 17))


@pytest.mark.parametrize(
    ('form', 'message'),
    [
        ({'sex': 'X', 'birth_date': '1990-01-01'}, 'Укажите пол'),
        ({'sex': '', 'birth_date': '1990-01-01'}, 'Укажите пол'),
        ({'birth_date': '1990-01-01'}, 'Укажите пол'),
        ({'sex': 'M', 'birth_date': 'not a date'}, 'формате'),
        ({'sex': 'M', 'birth_date': '19900101'}, 'формате'),
        ({'sex': 'M', 'birth_date': '1990-02-30'}, 'формате'),
        ({'sex': 'M', 'birth_date': ''}, 'формате'),
        ({'sex': 'M'}, 'формате'),
        ({'sex': 'M', 'birth_date': '1899-12-31'}, '1900'),
        (
            {
                'sex': 'M',
                'birth_date': (date.today() + timedelta(days=1)).isoformat(),
            },
            'в будущем',
        ),
    ],
)
def test_invalid_profile_is_422_and_not_saved(
    client: TestClient, db: Session, form: dict[str, str], message: str
) -> None:
    response = client.post('/profile', data=form, headers=as_user())

    assert response.status_code == 422
    assert message in response.text
    db.expire_all()
    user = db.scalar(select(User).where(User.discord_user_id == USER_ID))
    assert (user.sex, user.birth_date) == (None, None)


def test_birth_date_boundaries_are_accepted(client: TestClient) -> None:
    for day in ('1900-01-01', date.today().isoformat()):
        response = client.post(
            '/profile',
            data={'sex': 'M', 'birth_date': day},
            headers=as_user(),
            follow_redirects=False,
        )
        assert response.status_code == 303


def test_error_page_does_not_echo_raw_input(client: TestClient) -> None:
    payload = '<script>alert(1)</script>'

    response = client.post(
        '/profile', data={'sex': payload, 'birth_date': payload}, headers=as_user()
    )

    assert response.status_code == 422
    assert '<script>' not in response.text
    assert payload not in response.text


@pytest.mark.parametrize('site', ['cross-site', 'same-site'])
def test_cross_site_post_is_403_and_saves_nothing(
    client: TestClient, db: Session, site: str
) -> None:
    response = client.post(
        '/profile', data=POST_FORM, headers=as_user(**{'Sec-Fetch-Site': site})
    )

    assert response.status_code == 403
    assert (
        db.scalar(select(func.count()).select_from(User).where(User.sex.is_not(None)))
        == 0
    )
    # The refusal comes before the identity is even looked up.
    assert db.scalar(select(User).where(User.discord_user_id == USER_ID)) is None


@pytest.mark.parametrize(
    'origin', ['http://evil.example', 'null', 'http://testserver.evil']
)
def test_foreign_origin_post_is_403(
    client: TestClient, db: Session, origin: str
) -> None:
    response = client.post('/profile', data=POST_FORM, headers=as_user(Origin=origin))

    assert response.status_code == 403
    assert (
        db.scalar(select(func.count()).select_from(User).where(User.sex.is_not(None)))
        == 0
    )


@pytest.mark.parametrize(
    'extra',
    [
        {'Sec-Fetch-Site': 'same-origin'},
        {'Sec-Fetch-Site': 'none'},
        {'Origin': 'http://testserver'},
        {},
    ],
)
def test_same_origin_and_non_browser_posts_work(
    client: TestClient, extra: dict[str, str]
) -> None:
    response = client.post(
        '/profile',
        data=POST_FORM,
        headers=as_user(**extra),
        follow_redirects=False,
    )

    assert response.status_code == 303


@pytest.mark.parametrize('path', ['/profile', '/healthz'])
def test_security_headers(client: TestClient, path: str) -> None:
    response = client.get(path, headers=as_user())

    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.headers['X-Frame-Options'] == 'DENY'
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    csp = response.headers['Content-Security-Policy']
    assert csp == CONTENT_SECURITY_POLICY
    assert 'script-src' not in csp


def test_security_headers_on_refusals(client: TestClient) -> None:
    response = client.get('/profile')

    assert response.status_code == 401
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.headers['Cache-Control'] == 'no-store'


def test_profile_is_not_cacheable(client: TestClient) -> None:
    response = client.get('/profile', headers=as_user())

    assert response.headers['Cache-Control'] == 'no-store'


def test_static_files_are_served_and_cacheable(client: TestClient) -> None:
    response = client.get('/static/style.css')

    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/css')
    assert 'no-store' not in response.headers.get('Cache-Control', '')
    assert response.headers['X-Content-Type-Options'] == 'nosniff'


def test_discord_avatar_is_rendered(client: TestClient) -> None:
    html = client.get(
        '/profile', headers=as_user(**{'X-Discord-Avatar-Url': AVATAR})
    ).text

    assert AVATAR in html


@pytest.mark.parametrize(
    'url',
    [
        'https://evil.example/a.png',
        'http://cdn.discordapp.com/a.png',
        'https://cdn.discordapp.com.evil.example/a.png',
        'https://cdn.discordapp.com@evil.example/a.png',
        'javascript:alert(1)',
        'data:image/svg+xml,<svg/>',
    ],
)
def test_foreign_avatar_is_not_rendered(client: TestClient, url: str) -> None:
    html = client.get('/profile', headers=as_user(**{'X-Discord-Avatar-Url': url})).text

    assert 'evil.example' not in html
    assert 'javascript:' not in html
    assert 'data:image/svg' not in html
    assert '<img' not in html


def test_users_have_independent_profiles(client: TestClient) -> None:
    client.post(
        '/profile', data={'sex': 'M', 'birth_date': '1990-01-01'}, headers=as_user()
    )

    other = client.get('/profile', headers=as_user(OTHER_USER_ID)).text
    assert '1990-01-01' not in other
    assert ' checked' not in other

    client.post(
        '/profile',
        data={'sex': 'F', 'birth_date': '1975-03-04'},
        headers=as_user(OTHER_USER_ID),
    )

    first = client.get('/profile', headers=as_user()).text
    second = client.get('/profile', headers=as_user(OTHER_USER_ID)).text
    assert 'value="1990-01-01"' in first and '1975-03-04' not in first
    assert 'value="1975-03-04"' in second and '1990-01-01' not in second


def test_startup_seeds_analytes(client: TestClient, db: Session) -> None:
    assert db.scalar(select(func.count()).select_from(Analyte)) > 0


def test_unhandled_error_is_500_with_security_headers_and_a_clean_log(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    secret = 'secret-lab-value-123'

    def boom() -> None:
        raise RuntimeError(secret)

    client.app.add_api_route('/boom', boom)

    with caplog.at_level(logging.ERROR):
        response = client.get('/boom', headers=as_user())

    assert response.status_code == 500
    assert response.text == 'Internal Server Error'
    assert response.headers['Content-Security-Policy'] == CONTENT_SECURITY_POLICY
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.headers['Cache-Control'] == 'no-store'
    assert any('Unhandled' in record.getMessage() for record in caplog.records)
    # The exception message may carry values from the request; only type and
    # frames are logged.
    assert secret not in caplog.text
    assert 'RuntimeError' in caplog.text


SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}


def _dependency_calls(dependant: Dependant) -> Iterator[object]:
    for sub in dependant.dependencies:
        yield sub.call
        yield from _dependency_calls(sub)


def _state_changing_routes(
    routes: list[BaseRoute], inherited: tuple[object, ...] = ()
) -> Iterator[tuple[str, tuple[object, ...]]]:
    """Yield ``(path, dependency callables)`` of every non-safe route.

    FastAPI 0.159 keeps an ``include_router`` as one object that holds the
    router and the include-level dependencies, so descend into it.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            if set(route.methods) <= SAFE_METHODS:
                continue
            calls = (
                *inherited,
                *(depends.dependency for depends in route.dependencies),
                *_dependency_calls(route.dependant),
            )
            yield route.path, calls
        elif hasattr(route, 'original_router'):
            included = tuple(
                depends.dependency for depends in route.include_context.dependencies
            )
            yield from _state_changing_routes(
                route.original_router.routes, (*inherited, *included)
            )


def _unprotected(app: FastAPI) -> tuple[list[str], list[str]]:
    """Return the checked paths and those without ``same_origin``."""
    checked, missing = [], []
    for path, calls in _state_changing_routes(app.routes):
        checked.append(path)
        if same_origin not in calls:
            missing.append(path)
    return checked, missing


def test_every_state_changing_route_checks_the_origin() -> None:
    """A new POST route that forgets ``same_origin`` fails here."""
    checked, missing = _unprotected(create_app())

    # Pin the real routes so a walker that finds nothing cannot pass.
    assert {'/profile', '/sets/upload', '/sets/{set_id:int}/delete'} <= set(checked)
    assert missing == []


def test_the_origin_guard_detects_an_unprotected_post_route() -> None:
    router = APIRouter()

    @router.post('/unprotected')
    def unprotected() -> None: ...

    @router.post('/protected', dependencies=[Depends(same_origin)])
    def protected() -> None: ...

    app = FastAPI()
    app.include_router(router)
    app.include_router(sets_router)
    nested = FastAPI()
    inner = APIRouter()
    inner.include_router(router, prefix='/inner')
    nested.include_router(inner)

    checked, missing = _unprotected(app)
    nested_checked, nested_missing = _unprotected(nested)

    assert '/protected' in checked
    assert missing == ['/unprotected']
    # Nested includes are walked too (the path shown lacks the include prefix).
    assert nested_missing == ['/unprotected']
    assert nested_checked == ['/unprotected', '/protected']


def test_the_origin_guard_accepts_include_level_dependencies() -> None:
    router = APIRouter()

    @router.post('/covered')
    def covered() -> None: ...

    app = FastAPI()
    app.include_router(router, dependencies=[Depends(same_origin)])

    assert _unprotected(app) == (['/covered'], [])
