"""Tests for the repeated-analyte rules shared by the parsers and uploads."""

from decimal import Decimal

import pytest

from med_analyzer.parsing.merge import (
    display_value,
    duplicate_warning,
    merge_duplicates,
    values_differ,
)
from med_analyzer.parsing.types import ParsedReference, ParsedResult


def _row(
    name: str,
    value: str | None,
    abbreviation: str | None = None,
    text: str | None = None,
) -> ParsedResult:
    return ParsedResult(
        raw_name=name,
        abbreviation=abbreviation,
        value=None if value is None else Decimal(value),
        value_text=text,
        unit=None,
        flag=None,
        reference=ParsedReference(raw='', groups=[]),
    )


@pytest.mark.parametrize(
    ('old', 'new', 'differ'),
    [
        ('5.7', '5.68', False),
        ('5.68', '5.7', False),
        ('5.7', '5.8', True),
        ('5.75', '5.8', False),
        ('125', '135', True),
        ('125', '125.0', False),
    ],
)
def test_numbers_are_compared_at_the_lower_precision(
    old: str, new: str, differ: bool
) -> None:
    assert values_differ(Decimal(old), None, Decimal(new), None) is differ


def test_text_values_must_match_exactly() -> None:
    assert not values_differ(None, 'negative', None, 'negative')
    assert values_differ(None, 'negative', None, 'positive')
    assert values_differ(Decimal('1'), None, None, '1')


def test_display_prefers_the_number() -> None:
    assert display_value(Decimal('4.0'), None) == '4.0'
    assert display_value(None, 'negative') == 'negative'


def test_duplicate_warning_names_both_values() -> None:
    text = duplicate_warning('HGB', '125', '135')
    assert 'HGB' in text
    assert '125' in text
    assert '135' in text


def test_first_occurrence_wins_and_conflicts_warn() -> None:
    rows = [
        _row('Hemoglobin (HGB)', '125', 'HGB'),
        _row('Other', '1.0'),
        _row('Hemoglobin analyser (HGB)', '135', 'HGB'),
    ]

    kept, warnings = merge_duplicates(rows)

    assert kept == rows[:2]
    assert warnings == [duplicate_warning('HGB', '125', '135')]


def test_agreeing_repeat_is_dropped_silently() -> None:
    rows = [_row('A (HGB)', '5.7', 'HGB'), _row('B (HGB)', '5.68', 'HGB')]

    kept, warnings = merge_duplicates(rows)

    assert kept == rows[:1]
    assert warnings == []


def test_rows_without_abbreviation_match_by_normalised_name() -> None:
    rows = [_row('Креатинін', '86'), _row('  креатинін. ', '87')]

    kept, warnings = merge_duplicates(rows)

    assert kept == rows[:1]
    assert warnings == [duplicate_warning('  креатинін. ', '86', '87')]


def test_different_names_are_not_repeats() -> None:
    rows = [_row('Креатинін', '86'), _row('Глюкоза', '5.1')]

    assert merge_duplicates(rows) == (rows, [])
