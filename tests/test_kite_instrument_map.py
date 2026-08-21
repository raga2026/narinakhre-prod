from stoqbell.utils.kite_instrument_map import (
    get_cached_instrument_token,
    sync_kite_instrument_map,
    upsert_instrument_map,
)


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeInstrumentMapDB:
    def __init__(self, universe_rows=None):
        self.universe_rows = universe_rows or []
        self.map_rows = {}  # (symbol, exchange) -> row dict

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith('SELECT symbol, exchange, company_name FROM stock_universe'):
            return FakeCursor(self.universe_rows)

        if normalized.startswith('SELECT kite_instrument_token FROM stock_kite_instrument_map'):
            symbol, exchange = params
            row = self.map_rows.get((symbol, exchange))
            return FakeCursor([row] if row else [])

        if normalized.startswith('INSERT INTO stock_kite_instrument_map'):
            # 6 params per row: symbol, exchange, kite_tradingsymbol,
            # kite_instrument_token, confidence, matched_name
            for i in range(0, len(params), 6):
                symbol, exchange, tradingsymbol, token, confidence, matched_name = params[i:i + 6]
                self.map_rows[(symbol, exchange)] = {
                    'kite_instrument_token': token,
                    'kite_tradingsymbol': tradingsymbol,
                    'confidence': confidence,
                    'matched_name': matched_name,
                }
            return FakeCursor([])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


class FakeKiteClient:
    def __init__(self, nse_instruments=None, bse_instruments=None):
        self._instruments = {'NSE': nse_instruments or [], 'BSE': bse_instruments or []}

    def fetch_instruments(self, exchange):
        return self._instruments[exchange]


def test_get_cached_instrument_token_returns_none_when_unmapped():
    db = FakeInstrumentMapDB()
    assert get_cached_instrument_token(db, '532835', 'BSE') is None


def test_upsert_then_get_cached_instrument_token_round_trips():
    db = FakeInstrumentMapDB()
    upsert_instrument_map(db, [{
        'symbol': '532835', 'exchange': 'BSE', 'kite_tradingsymbol': 'BLISSGVS',
        'kite_instrument_token': 999, 'confidence': 'exact', 'matched_name': 'BLISS GVS PHARMA LIMITED',
    }])

    assert get_cached_instrument_token(db, '532835', 'BSE') == 999


def test_sync_kite_instrument_map_matches_and_persists_and_reports_breakdown():
    db = FakeInstrumentMapDB(universe_rows=[
        {'symbol': '532835', 'exchange': 'BSE', 'company_name': 'Bliss GVS Pharma Ltd.'},
        {'symbol': 'RELIANCE', 'exchange': 'NSE', 'company_name': 'Reliance Industries Ltd'},
        {'symbol': '999999', 'exchange': 'BSE', 'company_name': 'Nobody Matches This Ltd'},
    ])
    kite_client = FakeKiteClient(
        nse_instruments=[
            {'tradingsymbol': 'RELIANCE', 'name': 'RELIANCE INDUSTRIES', 'instrument_token': 111, 'exchange': 'NSE'},
        ],
        bse_instruments=[
            {'tradingsymbol': 'BLISSGVS', 'name': 'BLISS GVS PHARMA LIMITED', 'instrument_token': 999, 'exchange': 'BSE'},
        ],
    )

    summary = sync_kite_instrument_map(db, kite_client)

    assert summary['universe_count'] == 3
    assert summary['matched'] == 2
    assert summary['exact'] == 2
    assert summary['fuzzy'] == 0
    assert summary['unmatched'] == 1

    assert get_cached_instrument_token(db, '532835', 'BSE') == 999
    assert get_cached_instrument_token(db, 'RELIANCE', 'NSE') == 111
    assert get_cached_instrument_token(db, '999999', 'BSE') is None


def test_upsert_overwrites_a_stale_mapping_on_rerun():
    db = FakeInstrumentMapDB()
    upsert_instrument_map(db, [{
        'symbol': '532835', 'exchange': 'BSE', 'kite_tradingsymbol': 'OLDSYMBOL',
        'kite_instrument_token': 111, 'confidence': 'exact', 'matched_name': 'OLD NAME',
    }])
    upsert_instrument_map(db, [{
        'symbol': '532835', 'exchange': 'BSE', 'kite_tradingsymbol': 'NEWSYMBOL',
        'kite_instrument_token': 222, 'confidence': 'exact', 'matched_name': 'NEW NAME',
    }])

    assert get_cached_instrument_token(db, '532835', 'BSE') == 222
