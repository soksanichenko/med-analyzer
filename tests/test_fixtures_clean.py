"""Checks that the committed PDF word fixtures carry no real personal data.

The repository is public, so none of the strings below are real: they are the
fakes that ``anonymise`` writes, plus invented names for the synthetic pages.
"""

from pathlib import Path

import pytest

from med_analyzer.parsing.types import Page, Word
from med_analyzer.parsing.words import (
    anonymise,
    pages_from_json,
    pages_to_json,
    pdf_to_pages,
)

FIXTURES = Path(__file__).parent / 'fixtures'
FIXTURE_NAMES = ['lab_service', 'synevo', 'synevo_2']

FAKE_SHORT_NAME = 'Іваненко І. І.'
FAKE_FULL_NAME = 'Іваненко Іван Іванович'
FAKE_PERFORMER = 'Петренко П. П.'
FAKE_BIRTH_DATE = '01.01.1989'
FAKE_BARCODE = '(AAAAAAAAAA)'
FAKE_ORDER_NUMBERS = {
    'lab_service': '9000001',
    'synevo': '1000000001',
    'synevo_2': '1000000002',
}


def _load(name: str) -> list[Page]:
    return pages_from_json((FIXTURES / f'{name}.json').read_text(encoding='utf-8'))


def _w(text: str, x0: float, top: float) -> Word:
    """Build a synthetic word whose width follows its length."""
    return Word(text, x0, x0 + 5.0 * len(text), top, top + 8.0)


def _row_texts(page: Page, top: float, tolerance: float = 1.5) -> list[str]:
    """Texts of the words on one visual row, left to right."""
    row = [word for word in page if abs(word.top - top) <= tolerance]
    return [word.text for word in sorted(row, key=lambda word: word.x0)]


def _row_of(page: Page, label: str) -> list[str]:
    """Texts of the row that holds the given label word."""
    anchor = next(word for word in page if word.text == label)
    return _row_texts(page, anchor.top)


def _sub_sequence_present(row: list[str], expected: list[str]) -> bool:
    size = len(expected)
    return any(row[i : i + size] == expected for i in range(len(row) - size + 1))


@pytest.mark.parametrize('name', FIXTURE_NAMES)
def test_fixtures_roundtrip(name: str) -> None:
    pages = _load(name)
    assert pages
    assert pages_from_json(pages_to_json(pages)) == pages


def test_pages_json_roundtrip_keeps_unicode_and_floats() -> None:
    pages = [[_w('Тест', 10.25, 20.5)], [], [_w('ok', 1.0, 2.0)]]
    text = pages_to_json(pages)
    assert 'Тест' in text
    assert pages_from_json(text) == pages


@pytest.mark.parametrize('name', FIXTURE_NAMES)
def test_fixture_has_fake_patient_identity(name: str) -> None:
    pages = _load(name)
    order = FAKE_ORDER_NUMBERS[name]
    for page in pages:
        texts = {word.text for word in page}
        if name == 'lab_service':
            assert _sub_sequence_present(_row_of(page, 'ПІБ'), FAKE_SHORT_NAME.split())
            if 'Виконав' in texts:
                assert _sub_sequence_present(
                    _row_of(page, 'Виконав'), FAKE_PERFORMER.split()
                )
            title = _row_of(page, 'Результати')
            assert title[title.index('№') + 1] == order
        else:
            assert _sub_sequence_present(
                _row_of(page, 'Клієнт:'), FAKE_FULL_NAME.split()
            )
            assert FAKE_BIRTH_DATE in _row_of(page, 'народження:')
            assert order in _row_of(page, 'народження:')
            assert FAKE_BARCODE in texts


def test_fixtures_carry_no_other_synevo_order_number() -> None:
    for name in ('synevo', 'synevo_2'):
        words = [word.text for page in _load(name) for word in page]
        ten_digit = {text for text in words if text.isdigit() and len(text) == 10}
        assert ten_digit == {FAKE_ORDER_NUMBERS[name]}


def _lab_service_page(
    name: list[str], performer: list[str], order: str, with_performer: bool = True
) -> Page:
    page = [
        _w('Результати', 144, 100),
        _w('досліджень', 214, 100),
        _w('№', 286, 100),
        _w(order, 302, 100),
    ]
    x = 34.0
    for part in name:
        page.append(_w(part, x, 139))
        x += 5.0 * len(part) + 6.0
    page += [
        _w('ПІБ', 15, 140),
        _w('Вік', 252, 140),
        _w('32', 269, 139),
        _w('Чоловіча', 334, 139),
        _w('Лікар', 17, 154),
        _w('Діагноз', 174, 154),
        _w('Кількість', 49, 250),
        _w('лейкоцитів', 84, 250),
        _w('(WBC)', 126, 250),
        _w('4,63', 208, 250),
    ]
    if with_performer:
        page += [_w('09.09.2020', 57, 409), _w('Виконав', 171, 409)]
        x = 308.0
        for part in performer:
            page.append(_w(part, x, 409))
            x += 5.0 * len(part) + 6.0
    return page


def _synevo_page(name: list[str], barcode: str, birth: str, order: str) -> Page:
    page = [_w('Клієнт:', 62, 102)]
    x = 150.0
    for part in name:
        page.append(_w(part, x, 102))
        x += 5.0 * len(part) + 6.0
    page += [
        _w(barcode, 462, 104),
        _w('Дата', 62, 118),
        _w('народження:', 85, 118),
        _w(birth, 150, 119),
        _w(order, 479, 117),
        _w('Вік:', 240, 121),
        _w('34', 271, 121),
        _w('Y', 283, 121),
        _w('Показник', 124, 136),
        _w('Креатинін', 69, 200),
        _w('(CREA)', 121, 200),
        _w('1234567890', 300, 300),
    ]
    return page


def _texts(page: Page) -> list[str]:
    return [word.text for word in page]


@pytest.mark.parametrize(
    'name', [['Тест', 'Т.', 'Т.'], ['Тест', 'Т.'], ['Тест', 'Т.', 'Т.', 'Ф.']]
)
def test_anonymise_replaces_lab_service_by_label(name: list[str]) -> None:
    pages = [
        _lab_service_page(name, ['Тестов', 'Я.', 'Я.'], '12345678'),
        _lab_service_page(name, ['Тестов', 'Я.', 'Я.'], '12345678'),
    ]
    result = anonymise(pages)

    for page in result:
        row = _row_texts(page, 139)
        labels_and_values = ('ПІБ', 'Вік', '32', 'Чоловіча')
        assert ' '.join(w for w in row if w not in labels_and_values) == (
            FAKE_SHORT_NAME
        )
        assert '32' in row and 'Чоловіча' in row
        assert 'ПІБ' in _texts(page) and 'Вік' in _texts(page)
        title = _row_texts(page, 100)
        assert title == ['Результати', 'досліджень', '№', '9000001']
        assert ' '.join(_row_texts(page, 409)[2:]) == FAKE_PERFORMER
        assert _row_texts(page, 409)[:2] == ['09.09.2020', 'Виконав']
        # analyte rows and unrelated rows stay as they were
        assert _row_texts(page, 250) == ['Кількість', 'лейкоцитів', '(WBC)', '4,63']
        assert _row_texts(page, 154) == ['Лікар', 'Діагноз']


def test_anonymise_lab_service_page_without_performer() -> None:
    page = _lab_service_page(['Тест', 'Т.', 'Т.'], [], '12345678', False)
    result = anonymise([page])[0]
    assert not any(word.text == 'Петренко' for word in result)


@pytest.mark.parametrize(
    'name', [['Тест', 'Тестович', 'Тестенко'], ['Тест', 'Тестович'], ['Тест']]
)
def test_anonymise_replaces_synevo_by_label(name: list[str]) -> None:
    pages = [
        _synevo_page(name, '(Abcdefghi|)', '15.05.1990', '1234567890'),
        _synevo_page(name, '(Abcdefghi|)', '15.05.1990', '1234567890'),
    ]
    result = anonymise(pages, order_number='1000000002')

    for page in result:
        client = _row_texts(page, 102)
        assert client[0] == 'Клієнт:'
        assert ' '.join(client[1:]) == FAKE_FULL_NAME
        assert FAKE_BARCODE in _texts(page)
        assert 'Abcdefghi|' not in ''.join(_texts(page))
        assert _row_texts(page, 118) == [
            'Дата',
            'народження:',
            FAKE_BIRTH_DATE,
            '1000000002',
        ]
        # age line, header and analyte rows stay, and so does a 10-digit
        # number that is not on the birth-date line
        assert _row_texts(page, 121, tolerance=0.5) == ['Вік:', '34', 'Y']
        assert _row_texts(page, 200) == ['Креатинін', '(CREA)']
        assert '1234567890' in _texts(page)


def test_anonymise_is_idempotent_and_replaces_by_label() -> None:
    pages = [
        _lab_service_page(['Тест', 'Т.', 'Т.'], ['Тестов', 'Я.', 'Я.'], '12345678'),
        _synevo_page(
            ['Тест', 'Тестович', 'Тестенко'], '(Abcdefghi|)', '15.05.1990', '1234567890'
        ),
    ]
    once = anonymise(pages)
    assert anonymise(once) == once
    assert once != pages
    assert anonymise([]) == []
    # the input is not mutated
    assert pages[0][0].text == 'Результати'
    assert any(word.text == 'Тестов' for word in pages[0])


def test_anonymise_refuses_pib_row_without_age_label() -> None:
    page = [
        word
        for word in _lab_service_page(['Тест', 'Т.', 'Т.'], [], '12345678', False)
        if word.text != 'Вік'
    ]
    snapshot = list(page)
    with pytest.raises(ValueError, match='age label'):
        anonymise([page])
    assert page == snapshot


def test_anonymise_without_labels_changes_nothing() -> None:
    page = [_w('Кількість', 49, 250), _w('(WBC)', 126, 250), _w('4,63', 208, 250)]
    assert anonymise([page]) == [page]


def test_pdf_to_pages_reads_words_with_coordinates(tmp_path: Path) -> None:
    content = b'BT /F1 12 Tf 50 700 Td (Hello world) Tj ET'
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 800] '
        b'/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
        b'<< /Length %d >>\nstream\n' % len(content) + content + b'\nendstream',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    pdf = bytearray(b'%PDF-1.4\n')
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += b'%d 0 obj\n' % number + body + b'\nendobj\n'
    xref = len(pdf)
    pdf += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1)
    for offset in offsets:
        pdf += b'%010d 00000 n \n' % offset
    pdf += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (
        len(objects) + 1,
        xref,
    )
    path = tmp_path / 'tiny.pdf'
    path.write_bytes(bytes(pdf))

    pages = pdf_to_pages(path)

    assert len(pages) == 1
    assert [word.text for word in pages[0]] == ['Hello', 'world']
    first = pages[0][0]
    assert isinstance(first.x0, float) and first.x0 == pytest.approx(50.0, abs=0.5)
    assert first.top < first.bottom
    assert first.x0 < first.x1
    assert round(first.top, 2) == first.top
