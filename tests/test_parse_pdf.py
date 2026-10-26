"""Tests for lab detection and ``parse_pdf``."""

import logging
from pathlib import Path

import pdfplumber
import pytest

from med_analyzer.parsing import detect_lab, parse_pdf
from med_analyzer.parsing.types import Page, ParseError, Word
from med_analyzer.parsing import words
from med_analyzer.parsing.words import MAX_PDF_PAGES, pages_from_json

FIXTURES = Path(__file__).parent / 'fixtures'
REPO_ROOT = Path(__file__).resolve().parents[1]


def _page(*texts: str) -> Page:
    """Build a page of words laid out left to right on one row."""
    return [
        Word(text, i * 50.0, i * 50.0 + 40.0, 10.0, 20.0)
        for i, text in enumerate(texts)
    ]


def _blank_pdf() -> bytes:
    """Build a minimal one-page PDF without any text, with a valid xref."""
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>',
    ]
    out = b'%PDF-1.4\n'
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b'%d 0 obj\n' % number + body + b'\nendobj\n'
    xref_at = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1)
    for offset in offsets:
        out += b'%010d 00000 n \n' % offset
    out += b'trailer\n<< /Size %d /Root 1 0 R >>\n' % (len(objects) + 1)
    out += b'startxref\n%d\n%%%%EOF\n' % xref_at
    return out


def _text_pdf(page_count: int, words_per_page: int = 1) -> bytes:
    """Build a PDF of ``page_count`` pages, each with a row of short words."""
    text = ' '.join(['ab'] * words_per_page)
    stream = f'BT /F1 4 Tf 5 190 Td ({text}) Tj ET'.encode()
    first_page = 4
    page_ids = [first_page + i for i in range(page_count)]
    kids = ' '.join(f'{i} 0 R' for i in page_ids)
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        f'<< /Type /Pages /Kids [{kids}] /Count {page_count} >>'.encode(),
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    for _ in page_ids:
        objects.append(
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 200] '
            b'/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>'
            % (first_page + page_count)
        )
    objects.append(
        b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream'
    )
    out = b'%PDF-1.4\n'
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b'%d 0 obj\n' % number + body + b'\nendobj\n'
    xref_at = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1)
    for offset in offsets:
        out += b'%010d 00000 n \n' % offset
    out += b'trailer\n<< /Size %d /Root 1 0 R >>\n' % (len(objects) + 1)
    out += b'startxref\n%d\n%%%%EOF\n' % xref_at
    return out


def _load(name: str) -> list[Page]:
    return pages_from_json((FIXTURES / name).read_text(encoding='utf-8'))


@pytest.mark.parametrize(
    ('fixture', 'lab'),
    [
        ('lab_service.json', 'lab_service'),
        ('synevo.json', 'synevo'),
        ('synevo_2.json', 'synevo'),
    ],
)
def test_detect_lab_on_fixtures(fixture: str, lab: str) -> None:
    assert detect_lab(_load(fixture)) == lab


def test_detect_lab_synevo_latin_name_only() -> None:
    assert detect_lab([_page('Report', '(Synevo)')]) == 'synevo'


def test_detect_lab_lab_service_with_glued_number_sign() -> None:
    page = _page('Результати', 'досліджень', '№123', 'Найменування', 'Референтні')
    assert detect_lab([page]) == 'lab_service'


@pytest.mark.parametrize('pages', [[], [[]]])
def test_detect_lab_without_words_raises(pages: list[Page]) -> None:
    with pytest.raises(ParseError, match='Unknown report format'):
        detect_lab(pages)


def test_detect_lab_unrelated_words_raises() -> None:
    with pytest.raises(ParseError, match='Unknown report format'):
        detect_lab([_page('Hello', 'world', 'Секретне', 'значення')])


def test_detect_lab_title_without_column_header_raises() -> None:
    with pytest.raises(ParseError, match='Unknown report format'):
        detect_lab([_page('Результати', 'досліджень', '№', '1')])


def test_detect_lab_looks_at_first_page_only() -> None:
    with pytest.raises(ParseError, match='Unknown report format'):
        detect_lab([_page('x'), _page('Сінево')])


def test_detect_lab_both_markers_is_ambiguous() -> None:
    page = _page(
        'Сінево', 'Результати', 'досліджень', '№', '1', 'Найменування', 'Референтні'
    )
    with pytest.raises(ParseError, match='Ambiguous report format'):
        detect_lab([page])


def test_parse_pdf_blank_pdf_has_no_text_layer(tmp_path: Path) -> None:
    path = tmp_path / 'blank.pdf'
    path.write_bytes(_blank_pdf())
    with pytest.raises(ParseError, match='no text layer'):
        parse_pdf(path)


def test_parse_pdf_garbage_bytes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / 'garbage.pdf'
    path.write_bytes(b'not a pdf, SECRET-CONTENT\x00\x01\x02' * 20)
    with caplog.at_level(logging.WARNING), pytest.raises(ParseError) as info:
        parse_pdf(path)
    assert str(info.value) == 'Cannot read the PDF'
    assert 'SECRET-CONTENT' not in caplog.text
    assert 'PdfminerException' in caplog.text


def test_parse_pdf_truncated_pdf(tmp_path: Path) -> None:
    data = _blank_pdf()
    path = tmp_path / 'truncated.pdf'
    path.write_bytes(data[: len(data) // 2])
    with pytest.raises(ParseError) as info:
        parse_pdf(path)
    assert str(info.value) == 'Cannot read the PDF'


def test_parse_pdf_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ParseError) as info:
        parse_pdf(tmp_path / 'missing.pdf')
    assert str(info.value) == 'Cannot read the PDF'


def test_pdf_with_max_pages_is_extracted(tmp_path: Path) -> None:
    path = tmp_path / 'ok.pdf'
    path.write_bytes(_text_pdf(MAX_PDF_PAGES, words_per_page=3))
    pages = words.pdf_to_pages(path)
    assert len(pages) == MAX_PDF_PAGES
    assert [word.text for word in pages[0]] == ['ab'] * 3


def test_pdf_with_too_many_pages_is_rejected_before_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / 'long.pdf'
    path.write_bytes(_text_pdf(MAX_PDF_PAGES + 1))

    def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError('extract_words must not run')

    monkeypatch.setattr(pdfplumber.page.Page, 'extract_words', _fail)
    with pytest.raises(ParseError) as info:
        parse_pdf(path)
    assert str(info.value) == 'The PDF has too many pages'


def test_pdf_with_too_many_words_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / 'wordy.pdf'
    path.write_bytes(_text_pdf(2, words_per_page=3))
    # Three words fit, six words over two pages do not.
    monkeypatch.setattr(words, 'MAX_PDF_WORDS', 5)
    with pytest.raises(ParseError) as info:
        parse_pdf(path)
    assert str(info.value) == 'The PDF has too many words'


def _real_report(name: str, lab: str, count: int) -> object:
    return pytest.param(
        name,
        lab,
        count,
        marks=pytest.mark.skipif(
            not (REPO_ROOT / name).exists(),
            reason='real report is untracked personal data',
        ),
    )


@pytest.mark.parametrize(
    ('name', 'lab', 'count'),
    [
        _real_report('lab-service.pdf', 'lab_service', 27),
        _real_report('synevo.pdf', 'synevo', 1),
        _real_report('synevo-2.pdf', 'synevo', 46),
    ],
)
def test_parse_pdf_real_reports(name: str, lab: str, count: int) -> None:
    parsed = parse_pdf(REPO_ROOT / name)
    assert parsed.lab == lab
    assert len(parsed.results) == count
