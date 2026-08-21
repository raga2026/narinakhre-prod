from datetime import date

from stoqbell.utils.kite_client import KiteClient, KiteClientError


class FakeKite:
    """Stands in for pykiteconnect's KiteConnect -- just the methods
    fetch_daily_candles()/fetch_instruments()/place_market_order()/
    get_order_fill() call. Class-level constants mirror the real
    KiteConnect's own (VARIETY_REGULAR='regular', etc.) since
    place_market_order references them off self._kite directly."""

    VARIETY_REGULAR = 'regular'
    PRODUCT_CNC = 'CNC'
    ORDER_TYPE_MARKET = 'MARKET'

    def __init__(self, ltp_response=None, historical_data_response=None, instruments_response=None,
                 place_order_response=None, place_order_error=None, order_history_response=None):
        self._ltp_response = ltp_response if ltp_response is not None else {}
        self._historical_data_response = historical_data_response or []
        self._instruments_response = instruments_response or []
        self._place_order_response = place_order_response
        self._place_order_error = place_order_error
        self._order_history_response = order_history_response or []
        self.ltp_call_count = 0
        self.place_order_calls = []

    def ltp(self, instrument_keys):
        self.ltp_call_count += 1
        return self._ltp_response

    def historical_data(self, instrument_token, from_date, to_date, interval):
        return self._historical_data_response

    def instruments(self, exchange):
        return self._instruments_response

    def place_order(self, **kwargs):
        self.place_order_calls.append(kwargs)
        if self._place_order_error:
            raise self._place_order_error
        return self._place_order_response

    def order_history(self, order_id):
        return self._order_history_response


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


# --- fetch_ltp_batch --------------------------------------------------------

def test_fetch_ltp_batch_passes_the_whole_list_in_one_call():
    fake_kite = FakeKite(ltp_response={
        'NSE:RELIANCE': {'instrument_token': 1, 'last_price': 2500.5},
        'NSE:TCS': {'instrument_token': 2, 'last_price': 3600.0},
    })
    client = KiteClient(kite=fake_kite)

    prices = client.fetch_ltp_batch(['NSE:RELIANCE', 'NSE:TCS'])

    assert fake_kite.ltp_call_count == 1  # one batched call, not one per symbol
    assert prices == {'NSE:RELIANCE': 2500.5, 'NSE:TCS': 3600.0}


def test_fetch_ltp_batch_omits_symbols_kite_has_no_quote_for():
    fake_kite = FakeKite(ltp_response={'NSE:RELIANCE': {'instrument_token': 1, 'last_price': 2500.5}})
    client = KiteClient(kite=fake_kite)

    prices = client.fetch_ltp_batch(['NSE:RELIANCE', 'BSE:532835'])

    assert prices == {'NSE:RELIANCE': 2500.5}


def test_fetch_ltp_batch_empty_input_makes_no_kite_call():
    fake_kite = FakeKite()
    client = KiteClient(kite=fake_kite)

    assert client.fetch_ltp_batch([]) == {}
    assert fake_kite.ltp_call_count == 0


def test_fetch_instruments_returns_kite_instrument_list_as_is():
    fake_kite = FakeKite(instruments_response=[
        {'tradingsymbol': 'BLISSGVS', 'name': 'BLISS GVS PHARMA LIMITED', 'instrument_token': 999, 'exchange': 'BSE'},
    ])
    client = KiteClient(kite=fake_kite)

    result = client.fetch_instruments('BSE')

    assert result == [
        {'tradingsymbol': 'BLISSGVS', 'name': 'BLISS GVS PHARMA LIMITED', 'instrument_token': 999, 'exchange': 'BSE'},
    ]


# --- place_market_order / get_order_fill ------------------------------------

def test_place_market_order_sends_correct_params_and_returns_order_id():
    fake_kite = FakeKite(place_order_response='250817000012345')
    client = KiteClient(kite=fake_kite)

    order_id = client.place_market_order('GOODCO', 'NSE', 'BUY', 60)

    assert order_id == '250817000012345'
    assert len(fake_kite.place_order_calls) == 1
    call = fake_kite.place_order_calls[0]
    assert call['tradingsymbol'] == 'GOODCO'
    assert call['exchange'] == 'NSE'
    assert call['transaction_type'] == 'BUY'
    assert call['quantity'] == 60
    assert call['product'] == FakeKite.PRODUCT_CNC
    assert call['order_type'] == FakeKite.ORDER_TYPE_MARKET
    assert call['variety'] == FakeKite.VARIETY_REGULAR


def test_place_market_order_wraps_a_rejection_in_kite_client_error():
    fake_kite = FakeKite(place_order_error=Exception('Insufficient funds'))
    client = KiteClient(kite=fake_kite)

    try:
        client.place_market_order('GOODCO', 'NSE', 'BUY', 60)
        assert False, 'expected KiteClientError'
    except KiteClientError as e:
        assert 'GOODCO' in str(e)
        assert 'BUY' in str(e)
        assert 'Insufficient funds' in str(e)


def test_get_order_fill_returns_the_latest_status():
    fake_kite = FakeKite(order_history_response=[
        {'status': 'OPEN', 'average_price': 0, 'filled_quantity': 0},
        {'status': 'COMPLETE', 'average_price': 413.5, 'filled_quantity': 60},
    ])
    client = KiteClient(kite=fake_kite)

    fill = client.get_order_fill('250817000012345')

    assert fill == {'status': 'COMPLETE', 'average_price': 413.5, 'filled_quantity': 60}


def test_get_order_fill_returns_none_for_unknown_order():
    fake_kite = FakeKite(order_history_response=[])
    client = KiteClient(kite=fake_kite)

    assert client.get_order_fill('nonexistent') is None
