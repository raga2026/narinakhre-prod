from bs4 import BeautifulSoup

from stoqbell.utils.screener_client import (
    RESERVES_TO_DEBT_DEBT_FREE_SENTINEL,
    _compute_reserves_to_debt_ratio,
    _parse_balance_sheet_derived,
)


# --- _compute_reserves_to_debt_ratio ----------------------------------------

def test_reserves_to_debt_ratio_plain_division():
    assert _compute_reserves_to_debt_ratio(reserves=200, borrowings=100) == 2.0
    assert _compute_reserves_to_debt_ratio(reserves=50, borrowings=100) == 0.5


def test_reserves_to_debt_ratio_zero_debt_is_not_a_divide_by_zero_error():
    # The whole point of the edge case: must not raise, and must read as
    # "maximally healthy", not None/0.
    result = _compute_reserves_to_debt_ratio(reserves=200, borrowings=0)
    assert result == RESERVES_TO_DEBT_DEBT_FREE_SENTINEL


def test_reserves_to_debt_ratio_none_when_reserves_missing():
    assert _compute_reserves_to_debt_ratio(reserves=None, borrowings=100) is None


def test_reserves_to_debt_ratio_none_when_borrowings_missing_not_zero():
    # Missing (never scraped) is a different claim than "confirmed zero
    # debt" -- must not be treated as debt-free.
    assert _compute_reserves_to_debt_ratio(reserves=200, borrowings=None) is None


# --- _parse_balance_sheet_derived (wiring, real HTML shape) -----------------

def _balance_sheet_soup(equity, reserves, borrowings, total_assets):
    def row(label, value):
        return f'<tr><td class="text">{label}</td><td>{value}</td></tr>'
    html = (
        '<section id="balance-sheet"><table>'
        f'{row("Equity Capital", equity)}'
        f'{row("Reserves", reserves)}'
        f'{row("Borrowings", borrowings)}'
        f'{row("Total Assets", total_assets)}'
        '</table></section>'
    )
    return BeautifulSoup(html, 'html.parser')


def test_balance_sheet_exposes_reserves_and_ratio():
    soup = _balance_sheet_soup(equity=10, reserves=200, borrowings=100, total_assets=400)
    result = _parse_balance_sheet_derived(soup)
    assert result['reserves'] == 200
    assert result['reserves_to_debt_ratio'] == 2.0


def test_balance_sheet_zero_borrowings_gives_debt_free_sentinel():
    soup = _balance_sheet_soup(equity=10, reserves=200, borrowings=0, total_assets=400)
    result = _parse_balance_sheet_derived(soup)
    assert result['reserves_to_debt_ratio'] == RESERVES_TO_DEBT_DEBT_FREE_SENTINEL


def test_balance_sheet_missing_section_returns_none_for_new_fields():
    soup = BeautifulSoup('<html><body></body></html>', 'html.parser')
    result = _parse_balance_sheet_derived(soup)
    assert result['reserves'] is None
    assert result['reserves_to_debt_ratio'] is None
