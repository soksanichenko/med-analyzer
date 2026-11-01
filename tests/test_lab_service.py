"""Tests for the Lab Service parser, run on the anonymised word fixture."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from med_analyzer.parsing.lab_service import parse_lab_service
from med_analyzer.parsing.merge import duplicate_warning
from med_analyzer.parsing.primitives import age_in_months
from med_analyzer.parsing.reference import parse_reference, select_reference
from med_analyzer.parsing.types import Page, ParsedResult, ParsedSet, ParseError
from med_analyzer.parsing.words import pages_from_json

FIXTURE = Path(__file__).parent / 'fixtures' / 'lab_service.json'
BIRTH_DATE = date(1989, 1, 1)


@pytest.fixture
def pages() -> list[Page]:
    return pages_from_json(FIXTURE.read_text(encoding='utf-8'))


@pytest.fixture
def parsed(pages: list[Page]) -> ParsedSet:
    return parse_lab_service(pages)


def _by_abbr(parsed: ParsedSet, abbreviation: str) -> ParsedResult:
    matches = [r for r in parsed.results if r.abbreviation == abbreviation]
    assert len(matches) == 1
    return matches[0]


def _retext(pages: list[Page], page_index: int, index: int, text: str) -> list[Page]:
    """Copy the pages, giving one word new text."""
    edited = [list(page) for page in pages]
    edited[page_index][index] = replace(edited[page_index][index], text=text)
    return edited


def _index(page: Page, text: str, nth: int = 0, near_top: float | None = None) -> int:
    found = [
        i
        for i, w in enumerate(page)
        if w.text == text and (near_top is None or abs(w.top - near_top) < 3)
    ]
    return found[nth]


def _word_on_row_of(page: Page, anchor_index: int, text: str) -> int:
    anchor = page[anchor_index]
    return next(
        i for i, w in enumerate(page) if w.text == text and abs(w.top - anchor.top) < 1
    )


def test_header_fields(parsed: ParsedSet) -> None:
    assert parsed.lab == 'lab_service'
    assert parsed.order_number == '9000001'
    assert parsed.collected_on == date(2020, 9, 9)
    assert parsed.patient_name == 'Іваненко І. І.'


def test_result_count_and_no_warnings(parsed: ParsedSet) -> None:
    assert len(parsed.results) == 27
    assert parsed.warnings == []


def test_hgb(parsed: ParsedSet) -> None:
    hgb = _by_abbr(parsed, 'HGB')
    assert hgb.raw_name == 'Концентрація гемоглобіну (HGB)'
    assert hgb.value == Decimal(191)
    assert hgb.value_text is None
    assert hgb.unit == 'г/л'
    assert hgb.flag == 'high'
    age = age_in_months(BIRTH_DATE, date(2020, 9, 9))
    assert select_reference(hgb.reference, 'M', age) == (
        Decimal(130),
        Decimal(160),
    )


def test_rbc_low_flag_and_printed_precision(parsed: ParsedSet) -> None:
    rbc = _by_abbr(parsed, 'RBC')
    assert rbc.value == Decimal('3.37')
    assert rbc.flag == 'low'
    assert rbc.unit == '10^12/л'
    # A trailing zero is printed precision and must survive parsing.
    assert str(_by_abbr(parsed, 'PCT').value) == '0.350'


def test_esr_wrapped_name_without_abbreviation(parsed: ParsedSet) -> None:
    esr = next(r for r in parsed.results if 'Вестергреном' in r.raw_name)
    assert esr.raw_name == 'Швидкість осідання еритроцитів за Вестергреном'
    assert esr.abbreviation is None
    assert esr.value == 8
    assert esr.unit == 'мм/год'
    assert esr.flag is None
    age = age_in_months(BIRTH_DATE, date(2020, 9, 9))
    assert select_reference(esr.reference, 'M', age) == (None, Decimal(15))


def test_mch_name_is_rejoined_from_two_lines(parsed: ParsedSet) -> None:
    mch = _by_abbr(parsed, 'MCH')
    assert mch.raw_name == 'Середній вміст гемоглобіну в еритроциті (MCH)'
    assert mch.flag is None
    assert mch.unit == 'пг'


def test_three_line_name_is_rejoined(parsed: ParsedSet) -> None:
    img = _by_abbr(parsed, 'IMG#')
    assert img.raw_name == 'Абсолютна кількість незрілих гранулоцитів (IMG#)'
    assert img.value == Decimal('0.07')


def test_exactly_six_results_have_a_flag(parsed: ParsedSet) -> None:
    flags = [r.flag for r in parsed.results if r.flag is not None]
    assert len(flags) == 6
    assert set(flags) == {'high', 'low'}


def test_footer_and_legend_are_not_results(parsed: ParsedSet) -> None:
    for result in parsed.results:
        for marker in ('Стор', 'друку', 'результат вище', 'Виконав', '▲', '▼'):
            assert marker not in result.raw_name


def test_every_result_has_name_and_unit(parsed: ParsedSet) -> None:
    for result in parsed.results:
        assert result.raw_name
        assert result.raw_name == ' '.join(result.raw_name.split())
        assert result.unit


def test_wrapped_unit_is_normalised(pages: list[Page]) -> None:
    """The Synevo-style unit form must come out as the canonical one."""
    unit = _index(pages[0], '10^9/л', near_top=249.79)
    original = pages[0][unit]
    edited = [list(page) for page in pages]
    edited[0][unit] = replace(original, text='10^9', x0=255.0, x1=270.0)
    edited[0].append(replace(original, text='клітин/л', x0=272.0, x1=290.0))
    result = parse_lab_service(edited)
    assert result.results[1].abbreviation == 'WBC'
    assert result.results[1].unit == '10^9/л'


def test_duplicate_with_different_value_warns_and_keeps_first(
    pages: list[Page],
) -> None:
    second_hgb = _index(pages[0], '(HGB)', nth=1)
    value = _word_on_row_of(pages[0], second_hgb, '191')
    result = parse_lab_service(_retext(pages, 0, value, '181'))
    assert result.warnings == [duplicate_warning('HGB', '191', '181')]
    assert len(result.results) == 27
    assert _by_abbr(result, 'HGB').value == Decimal(191)


def test_duplicate_differing_only_in_extra_precision_does_not_warn(
    pages: list[Page],
) -> None:
    second_hgb = _index(pages[0], '(HGB)', nth=1)
    value = _word_on_row_of(pages[0], second_hgb, '191')
    result = parse_lab_service(_retext(pages, 0, value, '191,4'))
    assert result.warnings == []
    assert _by_abbr(result, 'HGB').value == Decimal(191)


def test_non_numeric_result_is_kept_as_text(pages: list[Page]) -> None:
    value = _index(pages[0], '8', near_top=233.6)
    result = parse_lab_service(_retext(pages, 0, value, 'negative'))
    esr = next(r for r in result.results if 'Вестергреном' in r.raw_name)
    assert esr.value is None
    assert esr.value_text == 'negative'
    assert esr.unit == 'мм/год'


def test_missing_column_header_raises(pages: list[Page]) -> None:
    header_top = pages[1][_index(pages[1], 'Результат')].top
    edited = [word for word in pages[1] if abs(word.top - header_top) > 1]
    with pytest.raises(ParseError, match='page 2'):
        parse_lab_service([pages[0], edited])


def test_row_without_name_raises_with_row_and_page(pages: list[Page]) -> None:
    wbc = _index(pages[0], '(WBC)')
    top = pages[0][wbc].top
    edited = [w for w in pages[0] if not (abs(w.top - top) < 1 and w.x1 < 190)]
    with pytest.raises(ParseError, match=r'Row 2 on page 1') as info:
        parse_lab_service([edited, pages[1]])
    assert 'Іваненко' not in str(info.value)


def test_row_with_unit_but_no_result_raises(pages: list[Page]) -> None:
    value = _index(pages[0], '8', near_top=233.6)
    edited = [w for i, w in enumerate(pages[0]) if i != value]
    with pytest.raises(ParseError, match=r'Row 1 on page 1'):
        parse_lab_service([edited, pages[1]])


def test_word_straddling_a_column_boundary_raises(pages: list[Page]) -> None:
    value = _index(pages[0], '8', near_top=233.6)
    wide = replace(pages[0][value], x0=240.0, x1=262.0)
    edited = list(pages[0])
    edited[value] = wide
    with pytest.raises(ParseError, match='column'):
        parse_lab_service([edited, pages[1]])


def test_order_number_must_match_across_pages(pages: list[Page]) -> None:
    number = _index(pages[1], '9000001')
    with pytest.raises(ParseError, match='differs'):
        parse_lab_service(_retext(pages, 1, number, '9000002'))


def test_missing_title_raises(pages: list[Page]) -> None:
    sign = _index(pages[0], '№', near_top=100.25)
    edited = [w for i, w in enumerate(pages[0]) if i != sign]
    with pytest.raises(ParseError, match='Title row'):
        parse_lab_service([edited, pages[1]])


def _move_down(pages: list[Page], index: int, delta: float) -> list[Page]:
    """Copy the pages, moving one word of page 1 down by ``delta``."""
    edited = [list(page) for page in pages]
    word = edited[0][index]
    edited[0][index] = replace(word, top=word.top + delta, bottom=word.bottom + delta)
    return edited


def test_wrapped_reference_is_joined_with_newline(pages: list[Page]) -> None:
    # ESR: its second name line shares the visual line with the moved word.
    esr_end = _index(pages[0], '15', near_top=233.6)
    result = parse_lab_service(_move_down(pages, esr_end, 4.77))
    esr = next(r for r in result.results if 'Вестергреном' in r.raw_name)
    assert esr.raw_name == 'Швидкість осідання еритроцитів за Вестергреном'
    assert esr.reference.raw == 'до\n15'
    assert esr.reference == parse_reference('до\n15')
    assert esr.value == 8


def test_wrapped_reference_on_its_own_line(pages: list[Page]) -> None:
    wbc = _index(pages[0], '(WBC)')
    nine = _word_on_row_of(pages[0], wbc, '9')
    result = parse_lab_service(_move_down(pages, nine, 4.77))
    assert _by_abbr(result, 'WBC').reference.raw == '4 -\n9'
    assert len(result.results) == 27


def test_reference_continuation_without_a_row_raises(pages: list[Page]) -> None:
    stray = replace(pages[0][_index(pages[0], 'до')], top=220.0, bottom=228.0)
    with pytest.raises(ParseError, match='reference text without a row'):
        parse_lab_service([[*pages[0], stray], pages[1]])


def test_reference_continuation_too_far_from_its_row_raises(
    pages: list[Page],
) -> None:
    esr_end = _index(pages[0], '15', near_top=233.6)
    with pytest.raises(ParseError, match='reference text without a row'):
        parse_lab_service(_move_down(pages, esr_end, 8.0))


@pytest.mark.parametrize('column_x', [(255.0, 262.0), (310.0, 318.0)])
def test_flag_glyph_in_unit_or_reference_cell_raises(
    pages: list[Page], column_x: tuple[float, float]
) -> None:
    glyph = _index(pages[0], '▼', near_top=261.13)
    moved = replace(pages[0][glyph], x0=column_x[0], x1=column_x[1])
    edited = list(pages[0])
    edited[glyph] = moved
    with pytest.raises(ParseError, match=r'Row 3 on page 1.*glyph'):
        parse_lab_service([edited, pages[1]])
