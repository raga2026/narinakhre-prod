"""Matches stock_universe companies to Kite's own instrument master list by
company name, so price syncing can use Kite's real instrument_token instead
of assuming our stored symbol (the raw BSE scrip code, for BSE rows) is
already the same string Kite's live-quote endpoint expects. Pure matching
logic only -- no DB or Kite API calls here (see utils/kite_instrument_map.py
for the orchestration that fetches Kite's instrument dump and stores
results).

This exists because Kite's ltp() (used just to resolve an instrument_token)
silently omits any instrument it doesn't recognize under the exact
"EXCHANGE:symbol" string we hand it -- which happens for a meaningful chunk
of BSE-listed companies whose Kite tradingsymbol isn't their raw BSE scrip
code. Matching by name against Kite's own instrument list sidesteps that
entirely: once we have the correct instrument_token, ltp() is never needed
for lookup again.
"""
import re

# Below this normalized-name similarity ratio, a candidate is treated as no
# match at all rather than a low-confidence guess -- wrong instrument_token
# would mean tracking (or worse, suggesting) the wrong company entirely, so
# this errs firmly toward "leave unmatched" over "maybe right".
FUZZY_MATCH_THRESHOLD = 0.92

# Common corporate-suffix/filler words that vary between our source lists
# and Kite's own naming without changing which company it is -- stripped
# before comparing, from both sides.
_SUFFIX_WORDS = {
    'LIMITED', 'LTD', 'LTD.', 'PRIVATE', 'PVT', 'PVT.', 'COMPANY', 'CO',
    'CO.', 'INDIA', 'THE', 'AND', 'CORPORATION', 'CORP', 'INDUSTRIES',
    'INDUSTRY',
}


def normalize_company_name(name):
    """Uppercases, strips punctuation, collapses whitespace, and drops
    common corporate-suffix words -- e.g. 'Bliss GVS Pharma Ltd.' and
    'BLISS GVS PHARMA LIMITED' both normalize to 'BLISS GVS PHARMA'."""
    if not name:
        return ''
    upper = re.sub(r'[^A-Z0-9\s]', ' ', name.upper())
    words = [w for w in upper.split() if w not in _SUFFIX_WORDS]
    return ' '.join(words)


def _similarity(a, b):
    # difflib avoided in favor of a dependency-free ratio: proportion of
    # the shorter normalized name's words that appear in the longer one.
    # Simpler than SequenceMatcher and good enough at this threshold --
    # exact matches (the overwhelming majority) never need it at all.
    words_a, words_b = set(a.split()), set(b.split())
    if not words_a or not words_b:
        return 0.0
    shorter, longer = (words_a, words_b) if len(words_a) <= len(words_b) else (words_b, words_a)
    overlap = len(shorter & longer)
    return overlap / len(shorter)


def match_instruments_to_universe(universe_rows, kite_instruments):
    """universe_rows: iterable of {symbol, exchange, company_name} (from
    stock_universe). kite_instruments: iterable of {tradingsymbol, name,
    instrument_token, exchange} (from KiteClient.fetch_instruments()).

    Matches strictly within the same exchange -- a BSE company is never
    matched against an NSE instrument entry, since they're different
    listings even when the name is identical.

    Returns a list of {symbol, exchange, kite_tradingsymbol,
    kite_instrument_token, confidence, matched_name} -- one entry per
    universe row that found a match (confidence is 'exact' or 'fuzzy').
    Universe rows with no sufficiently-similar match are simply omitted,
    not included with a null token -- callers should treat "not in the
    result" as "still unmatched", same as before this matching existed."""
    by_exchange = {}
    for inst in kite_instruments:
        by_exchange.setdefault(inst['exchange'], []).append(inst)
    for exchange, insts in by_exchange.items():
        for inst in insts:
            inst['_normalized_name'] = normalize_company_name(inst.get('name'))

    matches = []
    for row in universe_rows:
        candidates = by_exchange.get(row['exchange'], [])
        target = normalize_company_name(row.get('company_name'))
        if not target or not candidates:
            continue

        exact = next((c for c in candidates if c['_normalized_name'] == target), None)
        if exact:
            matches.append({
                'symbol': row['symbol'], 'exchange': row['exchange'],
                'kite_tradingsymbol': exact['tradingsymbol'],
                'kite_instrument_token': exact['instrument_token'],
                'confidence': 'exact', 'matched_name': exact.get('name'),
            })
            continue

        best, best_score = None, 0.0
        for c in candidates:
            score = _similarity(target, c['_normalized_name'])
            if score > best_score:
                best, best_score = c, score
        if best is not None and best_score >= FUZZY_MATCH_THRESHOLD:
            matches.append({
                'symbol': row['symbol'], 'exchange': row['exchange'],
                'kite_tradingsymbol': best['tradingsymbol'],
                'kite_instrument_token': best['instrument_token'],
                'confidence': 'fuzzy', 'matched_name': best.get('name'),
            })

    return matches
