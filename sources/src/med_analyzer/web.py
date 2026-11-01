"""Template environment shared by the page routes: language, filters, statics."""

import hashlib
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from med_analyzer.deps import avatar_url
from med_analyzer.i18n import (
    DEFAULT_LANGUAGE,
    LANGUAGES,
    Msg,
    detect_language,
    format_date,
    js_strings,
    render,
    render_stored,
    translate,
)
from med_analyzer.messages import CATALOGS

# Resolved from the package so they are found in the Docker image as well.
PACKAGE_DIR = Path(__file__).parent
TEMPLATES_DIR = PACKAGE_DIR / 'templates'
STATIC_DIR = PACKAGE_DIR / 'static'
# The Discord SSO portal, linked from the page header.
PORTAL_URL = 'https://meow-elite.club/'
HASH_LENGTH = 8


def num(value: Decimal | str | int | None, lang: str = DEFAULT_LANGUAGE) -> str:
    """Format a number with the decimal separator of the language.

    ``Decimal('6.40')`` gives ``6,40`` (``6.40`` in English); no exponent is
    ever used.

    Args:
        value: A decimal, or a string holding one.
        lang: ``uk``, ``en`` or ``ru``.

    Returns:
        The text, empty for ``None``; a string that is not a number is
        returned unchanged.
    """
    if value is None:
        return ''
    try:
        number = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        return str(value)
    text = format(number, 'f')
    return text if lang == 'en' else text.replace('.', ',')


def lab_name(code: str, lang: str = DEFAULT_LANGUAGE) -> str:
    """Return the display name of a lab code (unknown codes stay as they are).

    Args:
        code: Lab identifier as stored, e.g. ``synevo``.
        lang: ``uk``, ``en`` or ``ru``.

    Returns:
        The name to show.
    """
    key = f'lab.{code}'
    return translate(lang, key) if key in CATALOGS[DEFAULT_LANGUAGE] else code


@lru_cache
def static_url(name: str) -> str:
    """Return the URL of a static file with a content hash for cache busting.

    The hash changes with the file, so a browser or a CDN never serves an old
    copy of a changed file, and the file itself can be cached for long.

    Args:
        name: Path below the static directory, e.g. ``style.css``.

    Returns:
        ``/static/<name>?v=<first 8 hex digits of the MD5 of the content>``.
    """
    digest = hashlib.md5((STATIC_DIR / name).read_bytes(), usedforsecurity=False)
    return f'/static/{name}?v={digest.hexdigest()[:HASH_LENGTH]}'


def page_context(request: Request) -> dict[str, Any]:
    """Return what every page needs: the language, texts and the user's avatar.

    Args:
        request: The incoming request.

    Returns:
        Template variables ``lang``, ``languages``, ``t``, ``js_strings``,
        ``avatar_url`` and ``portal_url``.
    """
    lang = detect_language(request)
    return {
        'lang': lang,
        'languages': LANGUAGES,
        't': lambda key, *params: translate(lang, key, *params),
        'js_strings': js_strings(lang),
        'avatar_url': avatar_url(request),
        'portal_url': PORTAL_URL,
    }


@pass_context
def date_filter(context: Any, value: date) -> str:
    """Format a date in the language of the page."""
    return format_date(context['lang'], value)


@pass_context
def num_filter(context: Any, value: Decimal | str | int | None) -> str:
    """Format a number in the language of the page."""
    return num(value, context['lang'])


@pass_context
def lab_name_filter(context: Any, code: str) -> str:
    """Name a lab in the language of the page."""
    return lab_name(code, context['lang'])


@pass_context
def msg_filter(context: Any, message: Msg) -> str:
    """Render a message in the language of the page."""
    return render(context['lang'], message)


@pass_context
def stored_filter(context: Any, stored: str) -> str:
    """Render a stored warning in the language of the page."""
    return render_stored(context['lang'], stored)


templates = Jinja2Templates(directory=TEMPLATES_DIR, context_processors=[page_context])
templates.env.globals['static_url'] = static_url
templates.env.filters.update(
    {
        'date_text': date_filter,
        'num': num_filter,
        'lab_name': lab_name_filter,
        'msg': msg_filter,
        'stored': stored_filter,
    }
)
