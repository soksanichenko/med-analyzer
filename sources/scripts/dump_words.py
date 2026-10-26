"""Dump the anonymised words of a lab PDF to JSON, for use as a test fixture.

The dump replaces only the identity fields (name, birth date, order number,
barcode, performer); measured values, dates, ages and the collection point
stay as printed. The committed fixtures went one step further: their values,
dates, ages and collection point were then replaced by random ones (flags
recomputed), so a fresh dump differs from them and the tests that quote
fixture values would need updating.

Usage:
    python sources/scripts/dump_words.py lab-service.pdf \
        tests/fixtures/lab_service.json
    python sources/scripts/dump_words.py synevo.pdf tests/fixtures/synevo.json
    python sources/scripts/dump_words.py synevo-2.pdf \
        tests/fixtures/synevo_2.json --order-number 1000000002
"""

import argparse
import logging
import sys
from pathlib import Path

# The script runs outside the pytest ``pythonpath`` setting.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from med_analyzer.log import get_logger  # noqa: E402
from med_analyzer.parsing.words import (  # noqa: E402
    anonymise,
    pages_to_json,
    pdf_to_pages,
)

logger = get_logger(__name__)


def main() -> int:
    """Parse arguments, anonymise the PDF words and write the JSON file.

    Returns:
        Process exit code: 0 on success, 1 if anonymisation was refused.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('pdf', type=Path, help='source lab PDF')
    parser.add_argument('out', type=Path, help='anonymised JSON to write')
    parser.add_argument(
        '--order-number',
        help='fake order number to write instead of the default',
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    try:
        pages = anonymise(pdf_to_pages(args.pdf), order_number=args.order_number)
    except ValueError as error:
        logger.error('%s: %s', args.pdf.name, error)
        return 1
    args.out.write_text(pages_to_json(pages), encoding='utf-8')
    logger.info(
        'Wrote %d words, %d pages', sum(len(page) for page in pages), len(pages)
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
