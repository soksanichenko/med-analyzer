"""Interface language: choosing it, translating texts and formatting dates.

The language is the ``lang`` cookie (set by the language picker) or, without
it, the best match of the browser's ``Accept-Language`` among the supported
languages, else Ukrainian. Texts live in ``messages``.

User-facing messages that are created away from a request (upload errors, the
warnings stored with a set) are ``Msg`` values: a key and parameters, rendered
in the language of whoever reads them. A stored warning is the JSON of a
``Msg``; a plain string, as older rows hold, is shown as it is.
"""

import json
from datetime import date
from typing import NamedTuple

from fastapi import Request

from med_analyzer.messages import CATALOGS

LANGUAGES = ('uk', 'en', 'ru')
DEFAULT_LANGUAGE = 'uk'
LANG_COOKIE = 'lang'
JS_PREFIX = 'js.'

# Genitive forms, as in "14 жовтня 2021" / "October 14, 2021".
MONTHS = {
    'uk': (
        'січня',
        'лютого',
        'березня',
        'квітня',
        'травня',
        'червня',
        'липня',
        'серпня',
        'вересня',
        'жовтня',
        'листопада',
        'грудня',
    ),
    'en': (
        'January',
        'February',
        'March',
        'April',
        'May',
        'June',
        'July',
        'August',
        'September',
        'October',
        'November',
        'December',
    ),
    'ru': (
        'января',
        'февраля',
        'марта',
        'апреля',
        'мая',
        'июня',
        'июля',
        'августа',
        'сентября',
        'октября',
        'ноября',
        'декабря',
    ),
}


class Msg(NamedTuple):
    """A user-facing message not yet rendered into a language.

    Attributes:
        key: Message id in ``messages``.
        params: Values for the ``{0}``, ``{1}`` placeholders.
    """

    key: str
    params: tuple[str | int, ...] = ()


def msg(key: str, *params: str | int) -> Msg:
    """Build a message.

    Args:
        key: Message id in ``messages``.
        *params: Values for the placeholders.

    Returns:
        The message.
    """
    return Msg(key, params)


def translate(lang: str, key: str, *params: str | int) -> str:
    """Return a text in a language.

    Args:
        lang: ``uk``, ``en`` or ``ru``; anything else gives the default.
        key: Message id.
        *params: Values for the placeholders.

    Returns:
        The text; the key itself if no language knows it.
    """
    catalog = CATALOGS.get(lang, CATALOGS[DEFAULT_LANGUAGE])
    template = catalog.get(key) or CATALOGS[DEFAULT_LANGUAGE].get(key)
    if template is None:
        return key
    return template.format(*params)


def render(lang: str, message: Msg) -> str:
    """Render a message in a language.

    Args:
        lang: ``uk``, ``en`` or ``ru``.
        message: The message.

    Returns:
        The text.
    """
    return translate(lang, message.key, *message.params)


def encode_msg(message: Msg) -> str:
    """Serialise a message for storage with a set.

    Args:
        message: The message.

    Returns:
        A JSON string that ``render_stored`` turns back into text.
    """
    return json.dumps({'k': message.key, 'p': list(message.params)}, ensure_ascii=False)


def decode_msg(stored: str) -> Msg | None:
    """Parse a stored message.

    Args:
        stored: A stored warning.

    Returns:
        The message, or ``None`` for a plain string (older rows) or anything
        that is not a known message.
    """
    if not stored.startswith('{'):
        return None
    try:
        data = json.loads(stored)
        key = data['k']
        params = tuple(data['p'])
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(key, str) or key not in CATALOGS[DEFAULT_LANGUAGE]:
        return None
    if not all(isinstance(item, str | int) for item in params):
        return None
    return Msg(key, params)


def render_stored(lang: str, stored: str) -> str:
    """Render a stored warning in a language.

    Args:
        lang: ``uk``, ``en`` or ``ru``.
        stored: The warning as stored with the set.

    Returns:
        The text; a plain stored string comes back unchanged.
    """
    message = decode_msg(stored)
    return stored if message is None else render(lang, message)


def js_strings(lang: str) -> dict[str, str]:
    """Return the texts the dynamics script needs.

    Args:
        lang: ``uk``, ``en`` or ``ru``.

    Returns:
        The ``js.*`` entries of the language with the prefix removed.
    """
    catalog = CATALOGS.get(lang, CATALOGS[DEFAULT_LANGUAGE])
    return {
        key[len(JS_PREFIX) :]: value
        for key, value in catalog.items()
        if key.startswith(JS_PREFIX)
    }


def format_date(lang: str, value: date) -> str:
    """Format a date with the month spelled out.

    Args:
        lang: ``uk``, ``en`` or ``ru``.
        value: The date.

    Returns:
        ``14 жовтня 2021``, ``October 14, 2021`` or ``14 октября 2021``.
    """
    months = MONTHS.get(lang, MONTHS[DEFAULT_LANGUAGE])
    month = months[value.month - 1]
    if lang == 'en':
        return f'{month} {value.day}, {value.year}'
    return f'{value.day} {month} {value.year}'


def parse_accept_language(header: str) -> str | None:
    """Pick the supported language the browser prefers.

    Args:
        header: The ``Accept-Language`` header value.

    Returns:
        The supported language with the highest quality, or ``None``.
    """
    best: tuple[float, str] | None = None
    for position, part in enumerate(header.split(',')):
        tag, _, params = part.strip().partition(';')
        primary = tag.strip().split('-')[0].lower()
        if primary not in LANGUAGES:
            continue
        quality = 1.0
        params = params.strip()
        if params.startswith('q='):
            try:
                quality = float(params[2:])
            except ValueError:
                continue
        # Earlier entries win ties, so the position breaks them.
        rank = quality - position * 1e-6
        if quality > 0 and (best is None or rank > best[0]):
            best = (rank, primary)
    return None if best is None else best[1]


def detect_language(request: Request) -> str:
    """Return the interface language of a request.

    Args:
        request: The incoming request.

    Returns:
        The language of the ``lang`` cookie if valid, else the browser's
        preference, else Ukrainian.
    """
    chosen = request.cookies.get(LANG_COOKIE, '')
    if chosen in LANGUAGES:
        return chosen
    preferred = parse_accept_language(request.headers.get('accept-language', ''))
    return preferred or DEFAULT_LANGUAGE
