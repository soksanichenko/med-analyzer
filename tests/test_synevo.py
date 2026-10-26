"""Tests for the Synevo parser, run on the anonymised word fixtures."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from med_analyzer.parsing.primitives import age_in_months
from med_analyzer.parsing.reference import select_reference
from med_analyzer.parsing.synevo import _clean_reference, parse_synevo
from med_analyzer.parsing.types import Page, ParsedResult, ParsedSet, ParseError, Word
from med_analyzer.parsing.words import pages_from_json

FIXTURES = Path(__file__).parent / 'fixtures'
BIRTH_DATE = date(1989, 1, 1)
FAKE_NAME = 'Іваненко Іван Іванович'
TEXT_POINTER = 'Ваш результат готовий і знаходиться в додатковому pdf файлі.'
# Counted row by row in ``pdftotext -layout`` of the real six-page report.
SYNEVO_2_RESULTS = 46
ABSOLUTE_COUNTS = (
    'Нейтрофіли (абс.)',
    'Лімфоцити (абс.)',
    'Моноцити (абс.)',
    'Еозинофіли (абс.)',
    'Базофіли (абс.)',
    'Незрілі гранулоцити (абс.)',
)


def _load(name: str) -> list[Page]:
    return pages_from_json((FIXTURES / name).read_text(encoding='utf-8'))


@pytest.fixture
def one_page() -> list[Page]:
    return _load('synevo.json')


@pytest.fixture
def pages() -> list[Page]:
    return _load('synevo_2.json')


@pytest.fixture
def parsed(pages: list[Page]) -> ParsedSet:
    return parse_synevo(pages)


def _age(parsed: ParsedSet) -> int:
    return age_in_months(BIRTH_DATE, parsed.collected_on)


def _by_name(parsed: ParsedSet, name: str) -> ParsedResult:
    matches = [r for r in parsed.results if r.raw_name == name]
    assert len(matches) == 1, name
    return matches[0]


def _by_abbr(parsed: ParsedSet, abbreviation: str) -> ParsedResult:
    matches = [r for r in parsed.results if r.abbreviation == abbreviation]
    assert len(matches) == 1, abbreviation
    return matches[0]


def _retext(pages: list[Page], page_index: int, index: int, text: str) -> list[Page]:
    """Copy the pages, giving one word new text."""
    edited = [list(page) for page in pages]
    edited[page_index][index] = replace(edited[page_index][index], text=text)
    return edited


def _index(page: Page, text: str, nth: int = 0) -> int:
    return [i for i, w in enumerate(page) if w.text == text][nth]


def _with_word(pages: list[Page], page_index: int, word: Word) -> list[Page]:
    edited = [list(page) for page in pages]
    edited[page_index].append(word)
    return edited


def test_one_page_report(one_page: list[Page]) -> None:
    parsed = parse_synevo(one_page)
    assert parsed.lab == 'synevo'
    assert parsed.collected_on == date(2023, 7, 14)
    assert parsed.order_number == '1000000001'
    assert parsed.patient_name == FAKE_NAME
    assert parsed.warnings == []
    assert len(parsed.results) == 1
    creatinine = parsed.results[0]
    assert creatinine.raw_name == 'Креатинін'
    assert creatinine.abbreviation is None
    assert creatinine.value == Decimal(97)
    assert creatinine.value_text is None
    assert creatinine.unit == 'мкмоль/л'
    assert creatinine.flag is None
    assert creatinine.reference.raw.startswith('Діти:\nНовонароджені: 21.0 - 75.0\n')
    assert creatinine.reference.raw.endswith('Чоловіки: 62.0 - 106.0')
    assert select_reference(creatinine.reference, 'M', _age(parsed)) == (
        Decimal(62),
        Decimal(106),
    )


def test_header_fields(parsed: ParsedSet) -> None:
    assert parsed.lab == 'synevo'
    assert parsed.collected_on == date(2025, 10, 13)
    assert parsed.order_number == '1000000002'
    assert parsed.patient_name == FAKE_NAME
    assert parsed.warnings == []


def test_result_count(parsed: ParsedSet) -> None:
    assert len(parsed.results) == SYNEVO_2_RESULTS


def test_hgb(parsed: ParsedSet) -> None:
    hgb = _by_abbr(parsed, 'HGB')
    assert hgb.raw_name == 'Гемоглобін (HGB)'
    assert hgb.value == Decimal(155)
    assert hgb.unit == 'г/л'
    assert select_reference(hgb.reference, 'M', _age(parsed)) == (
        Decimal(130),
        Decimal(160),
    )


def test_wbc_wrapped_unit_is_joined(parsed: ParsedSet) -> None:
    wbc = _by_abbr(parsed, 'WBC')
    assert wbc.value == Decimal('5.71')
    assert wbc.unit == '10^9/л'
    assert select_reference(wbc.reference, 'M', _age(parsed)) == (
        Decimal('4.0'),
        Decimal('9.0'),
    )


def test_absolute_counts_have_cell_units(parsed: ParsedSet) -> None:
    for name in ABSOLUTE_COUNTS:
        assert _by_name(parsed, name).unit == '10^9/л', name
    assert _by_abbr(parsed, 'PLT').unit == '10^9/л'
    assert _by_abbr(parsed, 'RBC').unit == '10^12/л'
    assert _by_name(parsed, 'Нейтрофіли (абс.)').value == Decimal('2.2')
    assert _by_name(parsed, 'Базофіли (абс.)').value == Decimal('0.06')


def test_esr_drops_the_analyzer_line(parsed: ParsedSet) -> None:
    esr = _by_abbr(parsed, 'ESR')
    assert esr.raw_name == 'Аналіз крові: Швидкість осідання еритроцитів (ШОЕ, ESR)'
    assert esr.value == Decimal(8)
    assert esr.unit == 'мм/год'
    assert 'Виконано' not in esr.reference.raw
    assert select_reference(esr.reference, 'M', _age(parsed)) == (None, Decimal(15))


def test_glucose(parsed: ParsedSet) -> None:
    glucose = _by_name(parsed, 'Глюкоза (сироватка)')
    assert glucose.value == Decimal('4.71')
    assert glucose.unit == 'ммоль/л'
    assert select_reference(glucose.reference, 'M', _age(parsed)) == (
        Decimal('4.11'),
        Decimal('5.89'),
    )


def test_ferritin_drops_footnote_and_method(parsed: ParsedSet) -> None:
    ferritin = _by_name(parsed, 'Феритин')
    assert ferritin.value == Decimal('341.7')
    assert ferritin.unit == 'нг/мл'
    assert select_reference(ferritin.reference, 'M', _age(parsed)) == (
        Decimal(30),
        Decimal(400),
    )
    raw = ferritin.reference.raw
    assert raw.endswith('Жінки (5–20 років): >150')
    assert 'WHO' not in raw
    assert 'Метод' not in raw


def test_tsh_three_line_name(parsed: ParsedSet) -> None:
    tsh = _by_name(parsed, 'Тиреотропний гормон. ВИСОКОЧУТЛИВИЙ, БІОТИН-НЕЗАЛЕЖНИЙ')
    assert tsh.value == Decimal('2.20')
    assert tsh.unit == 'мкМО/мл'
    assert select_reference(tsh.reference, 'M', _age(parsed)) == (
        Decimal('0.27'),
        Decimal('4.2'),
    )
    assert tsh.reference.raw.endswith('Дорослі: 0.27 - 4.2')


def test_cholesterol_threshold_reference(parsed: ParsedSet) -> None:
    cholesterol = _by_name(parsed, 'Холестерин')
    assert cholesterol.value == Decimal('8.05')
    assert cholesterol.unit == 'ммоль/л'
    assert cholesterol.reference.groups == []
    assert cholesterol.reference.raw.startswith('<5.0 - відсутність ризику\n')


def test_wrapped_names(parsed: ParsedSet) -> None:
    hdl = _by_abbr(parsed, 'HDL')
    assert hdl.raw_name == 'Ліпопротеїди високої щільності (ЛПВЩ, HDL)'
    assert hdl.unit == 'ммоль/л'
    _by_name(parsed, 'Пероксидаза щитоподібної залози, антитіла (АТПО)')
    _by_name(parsed, 'Незрілі гранулоцити (на 100 лейкоцитів)')


def test_text_pointer_row_copies_the_text(parsed: ParsedSet) -> None:
    report = _by_name(parsed, 'Звіт з описом лабораторних показників')
    assert report.value is None
    assert report.value_text == TEXT_POINTER
    assert report.unit is None
    assert report.reference.raw == ''


def test_headings_and_paragraphs_are_not_results(parsed: ParsedSet) -> None:
    names = [r.raw_name for r in parsed.results]
    for name in names:
        assert not name.startswith('Пакет'), name
        assert 'Метод' not in name, name
        assert 'Загальний аналіз' not in name, name
    for heading in (
        'Біо/імунохімія',
        'Аналіз крові розгорнутий (ЗАК)',
        'Тиреотропний гормон (ТТГ)',
        'Примітка:',
    ):
        assert heading not in names


def test_every_result_name_is_unique(parsed: ParsedSet) -> None:
    names = [r.raw_name for r in parsed.results]
    assert len(names) == len(set(names))


def test_no_method_lines_in_references(parsed: ParsedSet) -> None:
    for result in parsed.results:
        for line in result.reference.raw.split('\n'):
            assert not line.startswith(
                ('Метод:', 'Реагенти:', 'Аналізатор:', 'Виконано на')
            ), result.raw_name


def test_every_result_is_well_formed(parsed: ParsedSet) -> None:
    for result in parsed.results:
        assert (result.value is None) != (result.value_text is None), result.raw_name
        assert result.flag is None
        if result.raw_name == 'Звіт з описом лабораторних показників':
            assert result.unit is None
        else:
            assert result.value is not None, result.raw_name
            assert result.unit, result.raw_name
            assert result.reference.raw, result.raw_name


def test_duplicate_with_different_value_warns_and_keeps_first(
    one_page: list[Page],
) -> None:
    second = _retext(one_page, 0, _index(one_page[0], '97'), '98')[0]
    parsed = parse_synevo([one_page[0], second])
    assert len(parsed.results) == 1
    assert parsed.results[0].value == Decimal(97)
    assert parsed.warnings == [
        'Повтор показателя «Креатинін» с другим значением: 97 и 98'
    ]


def test_identical_duplicate_does_not_warn(one_page: list[Page]) -> None:
    parsed = parse_synevo([one_page[0], one_page[0]])
    assert len(parsed.results) == 1
    assert parsed.warnings == []


def test_header_disagreement_across_pages_raises(pages: list[Page]) -> None:
    edited = _retext(pages, 1, _index(pages[1], '1000000002'), '1000000003')
    with pytest.raises(ParseError, match='page 2') as error:
        parse_synevo(edited)
    assert 'Іваненко' not in str(error.value)


def test_order_date_disagreement_raises(pages: list[Page]) -> None:
    edited = _retext(pages, 2, _index(pages[2], '13.10.2025'), '14.10.2025')
    with pytest.raises(ParseError, match='page 3'):
        parse_synevo(edited)


def test_missing_column_header_raises(one_page: list[Page]) -> None:
    edited = _retext(one_page, 0, _index(one_page[0], 'Од.'), 'Од')
    with pytest.raises(ParseError, match='column header'):
        parse_synevo(edited)


def test_missing_order_number_raises(one_page: list[Page]) -> None:
    edited = _retext(one_page, 0, _index(one_page[0], '1000000001'), '100000000')
    with pytest.raises(ParseError, match='Order number'):
        parse_synevo(edited)


def test_missing_footer_raises(one_page: list[Page]) -> None:
    edited = _retext(one_page, 0, _index(one_page[0], 'лабораторних'), 'x')
    with pytest.raises(ParseError, match='footer'):
        parse_synevo(edited)


def test_value_wrapped_over_two_lines_raises(one_page: list[Page]) -> None:
    value = one_page[0][_index(one_page[0], '97')]
    extra = replace(value, text='5', top=value.top + 11, bottom=value.bottom + 11)
    with pytest.raises(ParseError, match='Row 1 on page 1'):
        parse_synevo(_with_word(one_page, 0, extra))


def test_package_heading_with_a_value_is_not_a_result(one_page: list[Page]) -> None:
    page = one_page[0]
    value = page[_index(page, '97')]
    top = page[_index(page, 'Біо/імунохімія')].top + 8
    edited = _with_word(one_page, 0, Word('Пакет', 62.4, 84.3, top, top + 8))
    edited = _with_word(edited, 0, replace(value, text='7', top=top, bottom=top + 8))
    parsed = parse_synevo(edited)
    assert [r.raw_name for r in parsed.results] == ['Креатинін']


def test_reference_text_without_a_row_raises(one_page: list[Page]) -> None:
    page = one_page[0]
    stray = replace(page[_index(page, 'Дорослі:')], top=160.0, bottom=168.0)
    with pytest.raises(ParseError, match='(?i)page 1'):
        parse_synevo(_with_word(one_page, 0, stray))


def test_text_spanning_columns_inside_a_row_raises(one_page: list[Page]) -> None:
    page = one_page[0]
    unit = page[_index(page, 'мкмоль/л')]
    # A unit glued to the reference text cannot be split into cells.
    glued = replace(unit, text='мкмоль/лДіти:', x1=407.3)
    edited = [list(p) for p in one_page]
    edited[0][_index(page, 'мкмоль/л')] = glued
    edited[0].pop(_index(page, 'Діти:'))
    with pytest.raises(ParseError, match='(?i)page 1'):
        parse_synevo(edited)


def test_starred_line_before_adult_ranges_keeps_them() -> None:
    lines = [
        'Діти: 1-6 років: 3.5 - 4.5',
        '* Дорослі',
        'Чоловіки: 4.0 - 5.0',
        'Жінки: 3.7 - 4.7',
    ]
    assert _clean_reference(lines, 'Row 1 on page 1') == lines


def test_starred_note_before_a_bare_range_keeps_it() -> None:
    lines = ['Дорослі: 4.0 - 5.0', '*у курців', '4.5 - 5.5']
    assert _clean_reference(lines, 'Row 1 on page 1') == lines


def test_citation_with_page_numbers_is_dropped() -> None:
    lines = [
        '20-60 років: ≤0.06',
        '**Згідно наданих рекомендацій Ling-Ling Yuet',
        'al., Changes and reference intervals of immature',
        'according to pregnancy trimesterInt J Clin Exp Med',
        '2016;9(5):8169-8175',
    ]
    assert _clean_reference(lines, 'Row 1 on page 1') == ['20-60 років: ≤0.06']


def test_sample_footnotes_are_dropped(parsed: ParsedSet) -> None:
    for name in (
        'Незрілі гранулоцити (на 100 лейкоцитів)',
        'Незрілі гранулоцити (абс.)',
    ):
        raw = _by_name(parsed, name).reference.raw
        assert 'Згідно' not in raw
        assert '8169-8175' not in raw
        assert raw.split('\n')[-1].startswith('ІІІ триместр: ≤')
    vitamin_d = _by_name(parsed, 'Вітамін Д (25-гідроксивітамін D, 25- (OH)D)')
    assert vitamin_d.abbreviation is None
    # The first note's end is unclear (children's block follows), so it stays.
    assert '* Діагностика' in vitamin_d.reference.raw
    assert 'Pediatrics' not in vitamin_d.reference.raw
    assert vitamin_d.reference.raw.endswith('Оптимальний рівень: ≥20.0**')
