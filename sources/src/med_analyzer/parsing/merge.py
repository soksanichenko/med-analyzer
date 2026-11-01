"""Shared handling of repeated analytes: comparison, display and merging.

Both lab parsers and the upload service need the same rules: two values of one
analyte conflict only when they differ after rounding to the lower of their
precisions, and the first occurrence always wins.
"""

import re
from decimal import ROUND_HALF_UP, Decimal

from med_analyzer.i18n import encode_msg, msg
from med_analyzer.parsing.types import ParsedResult


def _decimal_places(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def values_differ(
    old: Decimal | None, old_text: str | None, new: Decimal | None, new_text: str | None
) -> bool:
    """Compare two results, numbers rounded to the lower of their precisions.

    Args:
        old: Numeric value of the first result, if numeric.
        old_text: Text value of the first result, if not numeric.
        new: Numeric value of the second result, if numeric.
        new_text: Text value of the second result, if not numeric.

    Returns:
        ``True`` if the two conflict (``5.7`` and ``5.68`` do not). If either
        is not numeric, the numbers and the texts must both be equal.
    """
    if old is None or new is None:
        return old != new or old_text != new_text
    places = min(_decimal_places(old), _decimal_places(new))
    step = Decimal(1).scaleb(-places)
    return old.quantize(step, ROUND_HALF_UP) != new.quantize(step, ROUND_HALF_UP)


def display_value(value: Decimal | None, value_text: str | None) -> str:
    """Return a result as text for a warning: the number, else the text.

    Args:
        value: Numeric value, if numeric.
        value_text: Text value, if not numeric.

    Returns:
        The string shown to the user.
    """
    return str(value) if value is not None else str(value_text)


def duplicate_warning(label: str, first: str, second: str) -> str:
    """Build the warning about a repeated analyte with a conflicting value.

    Args:
        label: Name or abbreviation of the analyte.
        first: Displayed first value (the one kept).
        second: Displayed repeated value (the one dropped).

    Returns:
        The warning as stored with the set (a serialised message, rendered
        in the reader's language).
    """
    return encode_msg(msg('warn.duplicate_value', label, first, second))


def merge_duplicates(rows: list[ParsedResult]) -> tuple[list[ParsedResult], list[str]]:
    """Keep the first occurrence of each analyte and warn on conflicting repeats.

    Rows are the same analyte when their abbreviations are equal, or, without
    an abbreviation, when their names are equal ignoring case and punctuation.

    Args:
        rows: Parsed rows of one report in print order.

    Returns:
        The rows without repeats and one warning per conflicting repeat.
    """
    seen: dict[tuple[str, str], ParsedResult] = {}
    warnings: list[str] = []
    for row in rows:
        if row.abbreviation is not None:
            key = ('abbreviation', row.abbreviation)
        else:
            key = (
                'name',
                ' '.join(re.sub(r'[\W_]+', ' ', row.raw_name.lower()).split()),
            )
        first = seen.setdefault(key, row)
        if first is not row and values_differ(
            first.value, first.value_text, row.value, row.value_text
        ):
            warnings.append(
                duplicate_warning(
                    row.abbreviation or row.raw_name,
                    display_value(first.value, first.value_text),
                    display_value(row.value, row.value_text),
                )
            )
    return list(seen.values()), warnings
