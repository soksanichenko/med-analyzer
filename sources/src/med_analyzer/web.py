"""Template environment shared by the page routes, with the Russian filters."""

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent / 'templates'

# Genitive forms, as in "14 октября 2021".
MONTHS_GENITIVE = (
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
)
LAB_NAMES = {'lab_service': 'Lab Service', 'synevo': 'Синево'}


def ru_date(value: date) -> str:
    """Format a date like ``14 октября 2021``.

    Args:
        value: The date.

    Returns:
        The date with the day, the month in the genitive case and the year.
    """
    return f'{value.day} {MONTHS_GENITIVE[value.month - 1]} {value.year}'


def num(value: Decimal | str | int | None) -> str:
    """Format a number with a decimal comma and the stored precision.

    ``Decimal('6.40')`` gives ``6,40``; no exponent is ever used.

    Args:
        value: A decimal, or a string holding one.

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
    return format(number, 'f').replace('.', ',')


def lab_name(code: str) -> str:
    """Return the display name of a lab code (unknown codes stay as they are).

    Args:
        code: Lab identifier as stored, e.g. ``synevo``.

    Returns:
        The name to show.
    """
    return LAB_NAMES.get(code, code)


templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.filters.update({'ru_date': ru_date, 'num': num, 'lab_name': lab_name})
