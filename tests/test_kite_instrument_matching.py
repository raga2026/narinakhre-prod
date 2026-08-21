from stoqbell.utils.kite_instrument_matching import match_instruments_to_universe, normalize_company_name


def test_normalize_strips_suffixes_punctuation_and_case():
    assert normalize_company_name('Bliss GVS Pharma Ltd.') == 'BLISS GVS PHARMA'
    assert normalize_company_name('BLISS GVS PHARMA LIMITED') == 'BLISS GVS PHARMA'
    assert normalize_company_name('Tata Consultancy Services Co.') == 'TATA CONSULTANCY SERVICES'


def test_normalize_empty_or_none_returns_empty_string():
    assert normalize_company_name(None) == ''
    assert normalize_company_name('') == ''


def test_exact_match_after_normalization():
    universe = [{'symbol': '532835', 'exchange': 'BSE', 'company_name': 'Bliss GVS Pharma Ltd.'}]
    kite_instruments = [
        {'tradingsymbol': 'BLISSGVS', 'name': 'BLISS GVS PHARMA LIMITED', 'instrument_token': 999, 'exchange': 'BSE'},
    ]

    matches = match_instruments_to_universe(universe, kite_instruments)

    assert len(matches) == 1
    assert matches[0]['symbol'] == '532835'
    assert matches[0]['kite_tradingsymbol'] == 'BLISSGVS'
    assert matches[0]['kite_instrument_token'] == 999
    assert matches[0]['confidence'] == 'exact'


def test_exchange_scoping_never_matches_across_exchanges():
    # Same normalized name, but the Kite entry is NSE -- a BSE universe row
    # must never match it, since they're different listings.
    universe = [{'symbol': '500325', 'exchange': 'BSE', 'company_name': 'Reliance Industries Ltd'}]
    kite_instruments = [
        {'tradingsymbol': 'RELIANCE', 'name': 'RELIANCE INDUSTRIES', 'instrument_token': 111, 'exchange': 'NSE'},
    ]

    matches = match_instruments_to_universe(universe, kite_instruments)

    assert matches == []


def test_fuzzy_match_above_threshold_is_accepted():
    # Not an exact normalized-string match (Kite's name drops "Enterprises"
    # entirely), but every word Kite does use appears in our longer name --
    # similar enough to accept as a fuzzy match rather than leave unmatched.
    universe = [{'symbol': '531358', 'exchange': 'BSE', 'company_name': 'ABC Industries Enterprises Ltd'}]
    kite_instruments = [
        {'tradingsymbol': 'ABCIND', 'name': 'ABC INDUSTRIES', 'instrument_token': 222, 'exchange': 'BSE'},
    ]

    matches = match_instruments_to_universe(universe, kite_instruments)

    assert len(matches) == 1
    assert matches[0]['confidence'] == 'fuzzy'
    assert matches[0]['kite_instrument_token'] == 222


def test_dissimilar_names_are_left_unmatched_not_guessed():
    universe = [{'symbol': '999999', 'exchange': 'BSE', 'company_name': 'Totally Unrelated Enterprises Ltd'}]
    kite_instruments = [
        {'tradingsymbol': 'SOMETHINGELSE', 'name': 'A Completely Different Business', 'instrument_token': 333, 'exchange': 'BSE'},
    ]

    matches = match_instruments_to_universe(universe, kite_instruments)

    assert matches == []


def test_no_kite_instruments_for_that_exchange_leaves_row_unmatched():
    universe = [{'symbol': '532835', 'exchange': 'BSE', 'company_name': 'Bliss GVS Pharma Ltd.'}]

    matches = match_instruments_to_universe(universe, kite_instruments=[])

    assert matches == []


def test_missing_company_name_is_skipped_without_crashing():
    universe = [{'symbol': '532835', 'exchange': 'BSE', 'company_name': None}]
    kite_instruments = [
        {'tradingsymbol': 'BLISSGVS', 'name': 'BLISS GVS PHARMA LIMITED', 'instrument_token': 999, 'exchange': 'BSE'},
    ]

    matches = match_instruments_to_universe(universe, kite_instruments)

    assert matches == []


def test_multiple_universe_rows_each_get_their_own_best_match():
    universe = [
        {'symbol': '532835', 'exchange': 'BSE', 'company_name': 'Bliss GVS Pharma Ltd.'},
        {'symbol': '500325', 'exchange': 'NSE', 'company_name': 'Reliance Industries Ltd'},
    ]
    kite_instruments = [
        {'tradingsymbol': 'BLISSGVS', 'name': 'BLISS GVS PHARMA LIMITED', 'instrument_token': 999, 'exchange': 'BSE'},
        {'tradingsymbol': 'RELIANCE', 'name': 'RELIANCE INDUSTRIES', 'instrument_token': 111, 'exchange': 'NSE'},
    ]

    matches = match_instruments_to_universe(universe, kite_instruments)

    assert {m['symbol']: m['kite_instrument_token'] for m in matches} == {'532835': 999, '500325': 111}
