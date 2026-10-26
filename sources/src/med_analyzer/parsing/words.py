"""Word extraction from lab PDFs, JSON (de)serialisation and anonymisation.

The anonymiser finds personal data by its position relative to fixed printed
labels (``ПІБ``, ``Клієнт:``, ``Виконав``, ``Дата народження:``, the ``№`` of
the title), never by remembering a particular person's strings. That keeps the
committed test fixtures free of real data while analyte rows stay untouched.
"""

import json
import re
from pathlib import Path

import pdfplumber

from med_analyzer.log import get_logger
from med_analyzer.parsing.types import Page, ParseError, Word

logger = get_logger(__name__)

# Upper bounds for an untrusted PDF; real reports have a handful of pages.
MAX_PDF_PAGES = 30
MAX_PDF_WORDS = 50_000

FAKE_SHORT_NAME = 'Іваненко І. І.'
FAKE_FULL_NAME = 'Іваненко Іван Іванович'
FAKE_PERFORMER = 'Петренко П. П.'
FAKE_BIRTH_DATE = '01.01.1989'
FAKE_BARCODE = '(AAAAAAAAAA)'
FAKE_LAB_SERVICE_ORDER = '9000001'
FAKE_SYNEVO_ORDER = '1000000001'

# Words of one visual row differ slightly in ``top`` (different font sizes).
_ROW_TOLERANCE = 2.0
# The ПІБ value is printed on the line of its label, a hair above it.
_PIB_ROW_TOLERANCE = 3.0
# The barcode sits in the client's row but a few points lower.
_BARCODE_ROW_TOLERANCE = 4.0
# Words of one name are close; the next column starts after a wide gap.
_NAME_MAX_GAP = 40.0

_DATE_RE = re.compile(r'^\d{2}\.\d{2}\.\d{4}$')
_ORDER_RE = re.compile(r'^\d{10}$')
_BARCODE_RE = re.compile(r'^\(\S{8,12}\)$')


def pdf_to_pages(path: Path) -> list[Page]:
    """Extract the words of every page of a PDF.

    Args:
        path: Path of the PDF file.

    Returns:
        One list of words per PDF page, coordinates rounded to 2 decimals.

    Raises:
        ParseError: If the PDF has more than ``MAX_PDF_PAGES`` pages (checked
            before any page is read) or more than ``MAX_PDF_WORDS`` words.
    """
    pages: list[Page] = []
    word_count = 0
    with pdfplumber.open(path) as pdf:
        if len(pdf.pages) > MAX_PDF_PAGES:
            raise ParseError('The PDF has too many pages')
        for pdf_page in pdf.pages:
            raw_words = pdf_page.extract_words(
                keep_blank_chars=False, use_text_flow=False
            )
            word_count += len(raw_words)
            if word_count > MAX_PDF_WORDS:
                raise ParseError('The PDF has too many words')
            pages.append(
                [
                    Word(
                        text=raw['text'],
                        x0=round(float(raw['x0']), 2),
                        x1=round(float(raw['x1']), 2),
                        top=round(float(raw['top']), 2),
                        bottom=round(float(raw['bottom']), 2),
                    )
                    for raw in raw_words
                ]
            )
    logger.debug('Extracted %d pages from %s', len(pages), path.name)
    return pages


def pages_to_json(pages: list[Page]) -> str:
    """Serialise pages to JSON, one word per line for readable diffs.

    Args:
        pages: Pages of words.

    Returns:
        A JSON document that ``pages_from_json`` reads back exactly.
    """

    def dump_word(word: Word) -> str:
        return json.dumps(
            {
                'text': word.text,
                'x0': word.x0,
                'x1': word.x1,
                'top': word.top,
                'bottom': word.bottom,
            },
            ensure_ascii=False,
        )

    dumped_pages = []
    for page in pages:
        if page:
            body = ',\n'.join(f'    {dump_word(word)}' for word in page)
            dumped_pages.append(f'  [\n{body}\n  ]')
        else:
            dumped_pages.append('  []')
    return '[\n' + ',\n'.join(dumped_pages) + '\n]\n'


def pages_from_json(text: str) -> list[Page]:
    """Parse the JSON produced by ``pages_to_json``.

    Args:
        text: The JSON document.

    Returns:
        The pages of words.
    """
    return [
        [
            Word(
                text=item['text'],
                x0=float(item['x0']),
                x1=float(item['x1']),
                top=float(item['top']),
                bottom=float(item['bottom']),
            )
            for item in page
        ]
        for page in json.loads(text)
    ]


def anonymise(pages: list[Page], order_number: str | None = None) -> list[Page]:
    """Replace personal data in word pages with fixed fakes.

    Handles the Lab Service layout (patient name right of the ``ПІБ`` label
    and left of ``Вік``, performer after ``Виконав``, order number after
    ``№`` in the title) and the Synevo layout (name after ``Клієнт:``, birth
    date after ``Дата народження:``, 10-digit order number on that line,
    barcode in parentheses beside the name). Ages, sex, dates of the order
    and every analyte row are left as printed. Every page is processed on its own, so
    repeated headers are covered. The input is not modified.

    Args:
        pages: Pages of words as returned by ``pdf_to_pages``.
        order_number: Fake order number to use instead of the default; it
            replaces the Lab Service title number or the Synevo number.

    Returns:
        New pages with the personal words replaced.

    Raises:
        ValueError: If a ``ПІБ`` row has no ``Вік`` label, so the name cannot
            be delimited safely.
    """
    return [_anonymise_page(page, order_number) for page in pages]


def _anonymise_page(page: Page, order_number: str | None) -> Page:
    # Maps a word index to its new text; None means the word is dropped.
    replacements: dict[int, str | None] = {}
    _anonymise_lab_service(page, order_number, replacements)
    _anonymise_synevo(page, order_number, replacements)

    result: Page = []
    for index, word in enumerate(page):
        if index not in replacements:
            result.append(word)
        elif replacements[index] is not None:
            result.append(
                Word(replacements[index], word.x0, word.x1, word.top, word.bottom)
            )
    return result


def _row(page: Page, anchor: Word, tolerance: float) -> list[int]:
    """Indexes of the words on the anchor's row, left to right."""
    indexes = [
        i for i, word in enumerate(page) if abs(word.top - anchor.top) <= tolerance
    ]
    return sorted(indexes, key=lambda i: page[i].x0)


def _assign(
    slots: list[int], fake_text: str, replacements: dict[int, str | None]
) -> None:
    """Spread the fake text over the original word slots.

    Surplus slots are dropped; surplus fake tokens are joined into the last
    slot. Either way the words of the row read as ``fake_text``.
    """
    tokens = fake_text.split()
    for position, index in enumerate(slots):
        if position >= len(tokens):
            replacements[index] = None
        elif position == len(slots) - 1:
            replacements[index] = ' '.join(tokens[position:])
        else:
            replacements[index] = tokens[position]


def _words_after(page: Page, label: Word, tolerance: float) -> list[int]:
    """Indexes of the words right of the label on its row."""
    return [i for i in _row(page, label, tolerance) if page[i].x0 >= label.x1 - 0.5]


def _anonymise_lab_service(
    page: Page, order_number: str | None, replacements: dict[int, str | None]
) -> None:
    for label in (word for word in page if word.text == 'ПІБ'):
        # The name is printed right of the label and ends before the next
        # label, ``Вік``. Without that boundary the age and sex values on the
        # row would be overwritten and the name might survive, so give up.
        row = _row(page, label, _PIB_ROW_TOLERANCE)
        age_label_x = min(
            (page[i].x0 for i in row if page[i].text == 'Вік'),
            default=None,
        )
        if age_label_x is None:
            raise ValueError(
                'Cannot locate the age label next to the patient name; '
                'refusing to write unanonymised output'
            )
        slots = [
            i
            for i in row
            if page[i].text not in ('ПІБ', 'Вік', 'Стать') and page[i].x0 < age_label_x
        ]
        if slots:
            _assign(slots, FAKE_SHORT_NAME, replacements)

    for label in (word for word in page if word.text == 'Виконав'):
        slots = _words_after(page, label, _ROW_TOLERANCE)
        if slots:
            _assign(slots, FAKE_PERFORMER, replacements)

    for sign in (word for word in page if word.text == '№'):
        row = _row(page, sign, _ROW_TOLERANCE)
        if not any(page[i].text == 'Результати' for i in row):
            continue
        for index in _words_after(page, sign, _ROW_TOLERANCE)[:1]:
            if page[index].text.isdigit():
                replacements[index] = order_number or FAKE_LAB_SERVICE_ORDER


def _anonymise_synevo(
    page: Page, order_number: str | None, replacements: dict[int, str | None]
) -> None:
    for label in (word for word in page if word.text == 'Клієнт:'):
        after = _words_after(page, label, _ROW_TOLERANCE)
        slots: list[int] = []
        for index in after:
            # The first word always belongs to the name, the label is far
            # left of it; later words must stay close to the previous one.
            if slots and page[index].x0 - page[slots[-1]].x1 > _NAME_MAX_GAP:
                break
            slots.append(index)
        if slots:
            _assign(slots, FAKE_FULL_NAME, replacements)
        for index in _words_after(page, label, _BARCODE_ROW_TOLERANCE):
            if index not in slots and _BARCODE_RE.match(page[index].text):
                replacements[index] = FAKE_BARCODE

    for label in (word for word in page if word.text == 'народження:'):
        for index in _words_after(page, label, _ROW_TOLERANCE):
            text = page[index].text
            if _DATE_RE.match(text):
                replacements[index] = FAKE_BIRTH_DATE
            elif _ORDER_RE.match(text):
                replacements[index] = order_number or FAKE_SYNEVO_ORDER
