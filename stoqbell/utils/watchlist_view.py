"""Display-only logic for /stocks/watchlist and /stocks/company/<id> --
filtering, annotating, and ordering rows already fetched from the DB. No
DB access here, same pure-logic-separate-from-routes split used elsewhere
in this codebase (e.g. fundamental_screen.py vs stock_shortlist.py)."""
from stoqbell.utils.fundamental_screen import get_metric_note
from stoqbell.utils.suggestion_engine import passes_hard_filters

# Fields that reveal the buy-signal layer (golden/death cross, and
# anything derived from it) -- stripped by redact_recommendation_signals
# for viewers without can_view_watchlist permission on /stocks/universe and
# /stocks/universe/<id>, which (unlike /stocks/watchlist) every logged-in
# Stocks user can browse. volume_trend is deliberately NOT included here --
# it's raw data, not itself "the golden cross" or "the recommendation", so
# it stays visible.
_SIGNAL_FIELDS = ('cross_status', 'is_recommended')


def redact_recommendation_signals(rows, can_view_signals):
    """Strips _SIGNAL_FIELDS from each row when can_view_signals is False
    -- returns rows unchanged (same objects, not even copied) when True, so
    callers that already have full permission pay no extra cost. Returns a
    new list of new dicts when redacting, never mutates the input rows."""
    if can_view_signals:
        return rows
    return [{k: v for k, v in dict(row).items() if k not in _SIGNAL_FIELDS} for row in rows]


def enrich_and_sort_watchlist_rows(rows, cross_filter=None):
    """Applies the golden-cross-only filter (cross_filter == 'golden'),
    adds pe_note/opm_note (see fundamental_screen.get_metric_note) and
    is_recommended (see suggestion_engine.passes_hard_filters -- the exact
    same hard filters the suggestion engine itself uses, so "recommended
    to buy" here never drifts from what actually gets suggested) to each
    row, then sorts recommended-first, golden-cross-first, alphabetically
    by name within each tier ("list it as per the recommendation").

    Returns a new list -- never mutates the input rows."""
    if cross_filter == 'golden':
        rows = [r for r in rows if r.get('cross_status') == 'golden_cross']

    enriched = [
        {**row, 'pe_note': get_metric_note('pe_ratio', row.get('pe_ratio')),
         'opm_note': get_metric_note('opm_pct', row.get('opm_pct')),
         'is_recommended': passes_hard_filters(row)}
        for row in rows
    ]

    enriched.sort(key=lambda r: (
        not r['is_recommended'],
        r.get('cross_status') != 'golden_cross',
        (r.get('name') or r['symbol']).lower(),
    ))
    return enriched
