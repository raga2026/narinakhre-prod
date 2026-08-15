from datetime import date

from utils.kite_client import KiteClient, KiteClientError


class FakeKite:
    """Stands in for pykiteconnect's KiteConnect -- just the two methods
    fetch_daily_candles() calls."""

    def __init__(self, ltp_response, historical_data_response=None):
        self._ltp_response = ltp_response
        self._historical_data_response = historical_data_response or []

    def ltp(self, instrument_keys):
        return self._ltp_response

    def historical_data(self, instrument_token, from_date, to_date, interval):
        return self._historical_data_response


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
