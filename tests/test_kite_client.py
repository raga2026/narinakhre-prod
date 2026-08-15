from datetime import date

from utils.kite_client import KiteClient, KiteClientError


class FakeKite:
    """Stands in for pykiteconnect's KiteConnect -- just the methods
    fetch_daily_candles()/fetch_instruments() call."""

    def __init__(self, ltp_response=None, historical_data_response=None, instruments_response=None):
        self._ltp_response = ltp_response if ltp_response is not None else {}
        self._historical_data_response = historical_data_response or []
        self._instruments_response = instruments_response or []
        self.ltp_call_count = 0

    def ltp(self, instrument_keys):
        self.ltp_call_count += 1
        return self._ltp_response

    def historical_data(self, instrument_token, from_date, to_date, interval):
        return self._historical_data_response

    def instruments(self, exchange):
        return self._instruments_response


def test_missing_instrument_in_ltp_response_raises_clear_error_not_bare_keyerror():
    # Kite's ltp() simply omits instruments it doesn't recognize/quote from
    # its response dict, rather than raising -- the old code did
    # quote[instrument_key][...] directly, which surfaced as a bare
    # KeyError("'BSE:532835'") with zero explanation of what went wrong.
    fake_kite = FakeKite(ltp_response={})  # BSE:532835 not present
    client = KiteClient(kite=fake_kite)

    try:
        client.fetch_daily_candles('532835', 'BSE', date(2026, 7, 15), date(2026, 8, 15))
        assert False, 'expected KiteClientError'
    except KiteClientError as e:
        assert 'BSE:532835' in str(e)
        assert 'no quote' in str(e).lower()


def test_found_instrument_returns_parsed_candles():
    fake_kite = FakeKite(
        ltp_response={'NSE:RELIANCE': {'instrument_token': 12345}},
        historical_data_response=[
            {'date': date(2026, 8, 10), 'open': 100.0, 'high': 105.0, 'low': 99.0, 'close': 102.0, 'volume': 1000},
        ],
    )
    client = KiteClient(kite=fake_kite)

    candles = client.fetch_daily_candles('RELIANCE', 'NSE', date(2026, 7, 15), date(2026, 8, 15))

    assert len(candles) == 1
    assert candles[0]['trade_date'] == date(2026, 8, 10)
    assert candles[0]['close'] == 102.0


def test_pre_resolved_instrument_token_skips_ltp_lookup_entirely():
    # This is the whole point of caching a Kite instrument_token (see
    # utils/kite_instrument_map.py): a BSE company whose Kite tradingsymbol
    # doesn't match our stored scrip code would otherwise always fail at
    # the ltp() step, before ever reaching historical_data.
    fake_kite = FakeKite(
        ltp_response={},  # would raise if ltp() were called at all
        historical_data_response=[
            {'date': date(2026, 8, 10), 'open': 10.0, 'high': 11.0, 'low': 9.5, 'close': 10.5, 'volume': 500},
        ],
    )
    client = KiteClient(kite=fake_kite)

    candles = client.fetch_daily_candles(
        '532835', 'BSE', date(2026, 7, 15), date(2026, 8, 15), instrument_token=99999
    )

    assert fake_kite.ltp_call_count == 0
    assert len(candles) == 1
    assert candles[0]['close'] == 10.5


def test_fetch_instruments_returns_kite_instrument_list_as_is():
    fake_kite = FakeKite(instruments_response=[
        {'tradingsymbol': 'BLISSGVS', 'name': 'BLISS GVS PHARMA LIMITED', 'instrument_token': 999, 'exchange': 'BSE'},
    ])
    client = KiteClient(kite=fake_kite)

    result = client.fetch_instruments('BSE')

    assert result == [
        {'tradingsymbol': 'BLISSGVS', 'name': 'BLISS GVS PHARMA LIMITED', 'instrument_token': 999, 'exchange': 'BSE'},
    ]
