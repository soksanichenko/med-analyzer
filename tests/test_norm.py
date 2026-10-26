"""Unit tests for the norm math: percent of norm and the range check."""

from decimal import Decimal

import pytest

from med_analyzer.norm import is_out_of_range, percent_of_norm

D = Decimal


@pytest.mark.parametrize(
    ('value', 'low', 'high', 'expected'),
    [
        # Both bounds: (value - low) / (high - low) * 100.
        (D('115'), D('130'), D('160'), -50.0),
        (D('130'), D('130'), D('160'), 0.0),
        (D('160'), D('130'), D('160'), 100.0),
        (D('200'), D('130'), D('160'), pytest.approx(233.3333, abs=1e-4)),
        (D('0.390'), D('0.15'), D('0.35'), pytest.approx(120.0)),
        # Only an upper bound ("до X", <X, <=X): the lower bound is 0.
        (D('5'), None, D('15'), pytest.approx(5 / 15 * 100)),
        (D('15'), None, D('15'), 100.0),
        (D('0'), None, D('15'), 0.0),
        (D('20'), None, D('15'), pytest.approx(133.3333, abs=1e-4)),
        # No upper bound, so no percentage.
        (D('5'), D('1'), None, None),
        (D('5'), None, None, None),
        (None, D('130'), D('160'), None),
        (None, None, D('15'), None),
        # Degenerate or invalid ranges.
        (D('130'), D('130'), D('130'), None),
        (D('140'), D('160'), D('130'), None),
        (D('5'), None, D('0'), None),
        (D('5'), None, D('-1'), None),
    ],
)
def test_percent_of_norm(value, low, high, expected):
    assert percent_of_norm(value, low, high) == expected


def test_percent_of_norm_returns_float():
    result = percent_of_norm(D('0.390'), D('0.15'), D('0.35'))
    assert type(result) is float


@pytest.mark.parametrize(
    ('value', 'low', 'high', 'expected'),
    [
        (D('115'), D('130'), D('160'), True),
        (D('130'), D('130'), D('160'), False),
        (D('145'), D('130'), D('160'), False),
        (D('160'), D('130'), D('160'), False),
        (D('160.01'), D('130'), D('160'), True),
        (D('5'), None, D('15'), False),
        (D('15'), None, D('15'), False),
        (D('16'), None, D('15'), True),
        (D('1.0'), D('1.0'), None, False),
        (D('0.9'), D('1.0'), None, True),
        (D('5'), None, None, None),
        (None, D('130'), D('160'), None),
        (None, None, None, None),
    ],
)
def test_is_out_of_range(value, low, high, expected):
    assert is_out_of_range(value, low, high) is expected
