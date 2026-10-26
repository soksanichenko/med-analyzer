"""The web application: app factory, security headers and the profile page."""

import re
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from med_analyzer.analytes import seed_analytes
from med_analyzer.config import get_config
from med_analyzer.db import get_db, make_session_factory
from med_analyzer.deps import RequestRefused, avatar_url, current_user, same_origin
from med_analyzer.log import configure_logging, get_logger
from med_analyzer.models import User
from med_analyzer.routes_dynamics import router as dynamics_router
from med_analyzer.routes_sets import router as sets_router
from med_analyzer.uploads import recompute_references
from med_analyzer.web import templates

logger = get_logger(__name__)

# Resolved from the package so they are found in the Docker image as well.
PACKAGE_DIR = Path(__file__).parent
STATIC_DIR = PACKAGE_DIR / 'static'

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "img-src 'self' https://cdn.discordapp.com data:; "
    "style-src 'self' 'unsafe-inline'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)
SECURITY_HEADERS = {
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': CONTENT_SECURITY_POLICY,
}

SEXES = ('M', 'F')
EARLIEST_BIRTH_DATE = date(1900, 1, 1)
# Not ``date.fromisoformat`` alone: it also takes ``20900101`` and week dates.
ISO_DATE_PATTERN = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
SEX_ERROR = 'Укажите пол: мужской или женский'
FORMAT_ERROR = 'Введите дату рождения в формате ГГГГ-ММ-ДД'
FUTURE_ERROR = 'Дата рождения не может быть в будущем'
PAST_ERROR = 'Дата рождения не может быть раньше 1900 года'


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Configure logging and seed the analyte dictionary at start-up.

    Args:
        app: The application being started.

    Yields:
        Control to the running application.
    """
    config = get_config()
    configure_logging(config.log_level)
    session_factory = make_session_factory(config.database_url)
    with session_factory() as session:
        seed_analytes(session)
        session.commit()
        engine = session.get_bind()
    # Requests use the engine of ``get_db``; this one was only for seeding.
    engine.dispose()
    logger.info('Analyte dictionary seeded')
    yield


def validate_profile(sex: str, birth_date: str) -> tuple[date | None, str | None]:
    """Check the profile form fields.

    Args:
        sex: Submitted sex, ``M`` or ``F``.
        birth_date: Submitted birth date in ISO format.

    Returns:
        The parsed birth date and ``None``, or ``None`` and a Russian message.
    """
    if sex not in SEXES:
        return None, SEX_ERROR
    if not ISO_DATE_PATTERN.fullmatch(birth_date):
        return None, FORMAT_ERROR
    try:
        parsed = date.fromisoformat(birth_date)
    except ValueError:
        return None, FORMAT_ERROR
    if parsed > date.today():
        return None, FUTURE_ERROR
    if parsed < EARLIEST_BIRTH_DATE:
        return None, PAST_ERROR
    return parsed, None


def render_profile(
    request: Request,
    sex: str | None,
    birth_date: str,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Render the profile form.

    Args:
        request: The incoming request.
        sex: Sex to preselect; anything but ``M``/``F`` selects nothing.
        birth_date: Value of the date field (escaped by the template).
        error: Russian message shown above the form.
        status_code: HTTP status of the response.

    Returns:
        The rendered page.
    """
    context = {
        'active': 'profile',
        'avatar_url': avatar_url(request),
        'sex': sex if sex in SEXES else None,
        'birth_date': birth_date,
        'error': error,
    }
    return templates.TemplateResponse(
        request, 'profile.html', context, status_code=status_code
    )


def create_app() -> FastAPI:
    """Build the application.

    The configuration is read in the lifespan, not here, so creating the app
    needs neither a database nor environment variables.

    Returns:
        The configured FastAPI application.
    """
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(RequestRefused)
    async def refuse(request: Request, error: RequestRefused) -> Response:
        return PlainTextResponse(error.message, status_code=error.status_code)

    @app.middleware('http')
    async def add_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        try:
            response = await call_next(request)
        except Exception as error:
            # Frames and the exception type only: the message of a database
            # or parsing error can carry values from the request.
            logger.error(
                'Unhandled %s\n%s',
                type(error).__name__,
                ''.join(traceback.format_tb(error.__traceback__)),
            )
            response = PlainTextResponse('Internal Server Error', status_code=500)
        response.headers.update(SECURITY_HEADERS)
        # Pages carry personal health data; static files are public and cacheable.
        if not request.url.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/healthz')
    def healthz() -> dict[str, str]:
        """Report liveness without authentication or database access."""
        return {'status': 'ok'}

    @app.get('/profile', response_class=HTMLResponse)
    def profile_form(
        request: Request, user: Annotated[User, Depends(current_user)]
    ) -> HTMLResponse:
        """Show the profile form with the saved values."""
        birth_date = user.birth_date.isoformat() if user.birth_date else ''
        return render_profile(request, user.sex, birth_date)

    @app.post(
        '/profile', response_class=HTMLResponse, dependencies=[Depends(same_origin)]
    )
    def profile_save(
        request: Request,
        user: Annotated[User, Depends(current_user)],
        db: Annotated[Session, Depends(get_db)],
        sex: Annotated[str, Form()] = '',
        birth_date: Annotated[str, Form()] = '',
    ) -> Response:
        """Save the profile and refresh the user's reference ranges."""
        parsed, error = validate_profile(sex, birth_date)
        if error is not None or parsed is None:
            return render_profile(request, sex, birth_date, error, status_code=422)
        user.sex = sex
        user.birth_date = parsed
        # Ranges depend on sex and age at collection, so stored results change;
        # one commit, so a failure cannot leave a profile with stale ranges.
        recompute_references(db, user, commit=False)
        db.commit()
        return RedirectResponse('/sets', status_code=303)

    app.include_router(sets_router)
    app.include_router(dynamics_router)
    app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')
    return app
