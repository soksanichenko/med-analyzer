"""Unit tests for the parsing primitives."""

from datetime import date
from decimal import Decimal

import pytest

from med_analyzer.parsing.primitives import (
    age_in_months,
    extract_abbreviation,
    normalize_unit,
    parse_decimal,
    parse_ua_date,
)

# Cyrillic look-alikes are spelled with escapes to keep the source unambiguous.
CYR_P = 'Р'
CYR_S = 'С'
CYR_T = 'Т'


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('4,63', Decimal('4.63')),
        ('0.390', Decimal('0.390')),
        ('  12 ', Decimal(12)),
        ('1 234,5', Decimal('1234.5')),
        ('1 234.5', Decimal('1234.5')),
        ('-0,5', Decimal('-0.5')),
    ],
)
def test_parse_decimal_valid(text: str, expected: Decimal) -> None:
    assert parse_decimal(text) == expected


@pytest.mark.parametrize(
    'text', ['abc', '', '   ', '1e5', '1E-3', 'NaN', 'inf', '1,2,3', '5.', '--1']
)
def test_parse_decimal_invalid(text: str) -> None:
    with pytest.raises(ValueError):
        parse_decimal(text)


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('14 жовтень 2021', date(2021, 10, 14)),
        ('14 жовтня 2021', date(2021, 10, 14)),
        ('15 СІЧНЯ 2023', date(2023, 1, 15)),
        ('01 січень 2023', date(2023, 1, 1)),
        ('03 лютий 2023', date(2023, 2, 3)),
        ('03 лютого 2023', date(2023, 2, 3)),
        ('04 березень 2023', date(2023, 3, 4)),
        ('04 березня 2023', date(2023, 3, 4)),
        ('05 квітень 2023', date(2023, 4, 5)),
        ('05 квітня 2023', date(2023, 4, 5)),
        ('06 травень 2023', date(2023, 5, 6)),
        ('06 травня 2023', date(2023, 5, 6)),
        ('07 червень 2023', date(2023, 6, 7)),
        ('07 червня 2023', date(2023, 6, 7)),
        ('08 липень 2023', date(2023, 7, 8)),
        ('08 липня 2023', date(2023, 7, 8)),
        ('09 серпень 2023', date(2023, 8, 9)),
        ('09 серпня 2023', date(2023, 8, 9)),
        ('10 вересень 2023', date(2023, 9, 10)),
        ('10 вересня 2023', date(2023, 9, 10)),
        ('11 листопад 2023', date(2023, 11, 11)),
        ('11 листопада 2023', date(2023, 11, 11)),
        ('12 грудень 2023', date(2023, 12, 12)),
        ('12 грудня 2023', date(2023, 12, 12)),
        ('7 жовтня 2021', date(2021, 10, 7)),
        ('14.07.2023', date(2023, 7, 14)),
        ('14.07.2023 07:32', date(2023, 7, 14)),
    ],
)
def test_parse_ua_date_valid(text: str, expected: date) -> None:
    assert parse_ua_date(text) == expected


@pytest.mark.parametrize(
    'text',
    [
        '',
        'abc',
        '14 october 2021',
        '14 жовтень',
        '31.02.2024',
        '2021-10-14',
        '32.01.2024',
    ],
)
def test_parse_ua_date_invalid(text: str) -> None:
    with pytest.raises(ValueError):
        parse_ua_date(text)


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('10^9 клітин/л', '10^9/л'),
        ('10^9 / клітин/л', '10^9/л'),
        ('10^9 /\nклітин/л', '10^9/л'),
        ('10^9/л', '10^9/л'),
        ('10^12 клітин/л', '10^12/л'),
        ('10^3 клітин/л', '10^3/л'),
        ('г/л', 'г/л'),
        ('  г/л  ', 'г/л'),
        ('мг/\n дл', 'мг/ дл'),
        ('мм/год.', 'мм/год'),
        ('мм/год. ', 'мм/год'),
        ('мм/год .', 'мм/год'),
        ('мм/год', 'мм/год'),
        ('10^9/л.', '10^9/л'),
    ],
)
def test_normalize_unit(text: str, expected: str) -> None:
    assert normalize_unit(text) == expected


@pytest.mark.parametrize(
    ('name', 'expected'),
    [
        ('Нейтрофіли (NEUT%)', 'NEUT%'),
        ('Аспартатамінотрансфераза (АСТ, AST)', 'AST'),
        ('Середній об’єм еритроцитів (MCV)', 'MCV'),
        ('Нейтрофіли (#NEUT)', '#NEUT'),
        ('Ширина розподілу (RDW-CV)', 'RDW-CV'),
        ('Гемоглобін (до 100) (HGB)', 'HGB'),
        (f'Тромбокрит ({CYR_P}{CYR_S}{CYR_T})', None),
        ('Лімфоцити (на 100 лейкоцитів)', None),
        ('Глюкоза', None),
        ('Глюкоза (GLU) у крові', 'GLU'),
        ('Примітка (1)', None),
        ('Глюкоза ()', None),
        ('Вітамін Д (25-гідроксивітамін D, 25- (OH)D)', None),
        ('Вітамін Д (25-гідроксивітамін D, 25-(OH)D)', None),
        ('Ліпопротеїди (LDL, ЛПНЩ (розрах.))', 'LDL'),
        ('Гемоглобін (до 100 (уточн.)) (HGB)', 'HGB'),
        ('Показник (HGB) (примітка (1))', None),
    ],
)
def test_extract_abbreviation(name: str, expected: str | None) -> None:
    assert extract_abbreviation(name) == expected


def test_age_in_months_whole_years() -> None:
    assert age_in_months(date(1990, 5, 15), date(2024, 5, 15)) == 408


def test_age_in_months_day_before_birthday_month() -> None:
    assert age_in_months(date(1990, 5, 15), date(2024, 5, 14)) == 407


def test_age_in_months_same_day_is_zero() -> None:
    assert age_in_months(date(2020, 5, 5), date(2020, 5, 5)) == 0


def test_age_in_months_negative_when_before_birth() -> None:
    assert age_in_months(date(2020, 5, 5), date(2020, 3, 5)) == -2
    assert age_in_months(date(2020, 5, 5), date(2020, 5, 4)) < 0


def test_age_in_months_feb_29_birth() -> None:
    birth = date(2020, 2, 29)
    # 28 Feb 2021 is before the 29th, so the twelfth month is not complete.
    assert age_in_months(birth, date(2021, 2, 28)) == 11
    assert age_in_months(birth, date(2021, 3, 1)) == 12
    assert age_in_months(birth, date(2024, 2, 29)) == 48
