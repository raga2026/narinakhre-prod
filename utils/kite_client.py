import os

from kiteconnect import KiteConnect

from utils.kite_session import get_kite_access_token


class KiteClientError(RuntimeError):
    pass


class KiteClient:
    """Thin wrapper around pykiteconnect for Nari Nakhre Stocks ingestion.

    Reads STOCKS_KITE_API_KEY from the environment. The access token comes
    from the encrypted kite_session (see utils/kite_session.py) -- pass
    db=get_db() to have it looked up and decrypted automatically, or pass
    access_token= directly (tests do this to skip the DB entirely). The old
    Phase 1 KITE_ACCESS_TOKEN env var is tried last, only if kite_session
    has no token yet, kept for backward compatibility during this
    transition -- it should stop being needed once every environment has
    logged in through /admin/stocks/kite/login at least once.
    """

    def __init__(self, db=None, access_token=None, kite=None):
        self._kite = kite or self._build_client(db, access_token)

    def _build_client(self, db, access_token):
        api_key = os.environ.get('STOCKS_KITE_API_KEY', '').strip()
        if not api_key:
            raise KiteClientError('STOCKS_KITE_API_KEY must be set in the environment.')

        token = (access_token or '').strip()
        if not token and db is not None:
            token = get_kite_access_token(db) or ''
        if not token:
            token = os.environ.get('KITE_ACCESS_TOKEN', '').strip()
        if not token:
            raise KiteClientError(
                'No Kite access token available. A super_admin must log in via '
                '/admin/stocks/kite/login first.'
            )
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(token)
        return kite

    def fetch_instruments(self, exchange):
        """Returns Kite's full instrument list for one exchange ('NSE' or
        'BSE') as-is: a list of dicts including tradingsymbol, name,
        instrument_token, exchange, among others. This is Kite's own
        authoritative symbol/token mapping -- see
        utils/kite_instrument_map.py, which matches it against
        stock_universe by company name so fetch_daily_candles() can use a
        real instrument_token directly instead of relying on ltp() (which
        silently omits any instrument it doesn't recognize under our stored
        symbol string, notably for a chunk of BSE listings)."""
        return self._kite.instruments(exchange)

    def fetch_daily_candles(self, symbol, exchange, from_date, to_date, instrument_token=None):
        """Return a list of {trade_date, open, high, low, close, volume} dicts
        for one symbol between from_date and to_date (inclusive), using Kite's
        day-interval historical data API.

        instrument_token, if given (see utils/kite_instrument_map.get_cached_instrument_token),
        skips the ltp() lookup entirely and goes straight to historical_data
        -- this is what lets a BSE company whose Kite tradingsymbol doesn't
        match our stored scrip code still sync, once it's been matched by
        name at least once. Falls back to the ltp()-based lookup (unchanged
        from before instrument mapping existed) when no cached token is
        available yet."""
        if instrument_token is None:
            instrument_key = f'{exchange}:{symbol}'
            quote = self._kite.ltp([instrument_key])
            if instrument_key not in quote:
                raise KiteClientError(
                    f'Kite has no quote for {instrument_key} -- it may not be tradable '
                    f'through Kite (illiquid/unlisted-on-Kite BSE scrip), or the symbol '
                    f'may be delisted/renamed on the exchange.'
                )
            instrument_token = quote[instrument_key]['instrument_token']

        candles = self._kite.historical_data(
            instrument_token,
            from_date,
            to_date,
            interval='day',
        )

        records = []
        for candle in candles:
            trade_date = candle['date']
            if hasattr(trade_date, 'date'):
                trade_date = trade_date.date()
            records.append({
                'trade_date': trade_date,
                'open': candle['open'],
                'high': candle['high'],
                'low': candle['low'],
                'close': candle['close'],
                'volume': candle['volume'],
            })
        return records

    def place_market_order(self, tradingsymbol, exchange, transaction_type, quantity):
        """Places a real, immediate-execution market order -- BUY or SELL
        (transaction_type), product=CNC (equity delivery/investment, held
        toward a target/stop-loss rather than squared off same-day like an
        intraday MIS order would be). Used only by utils/auto_trader.py's
        live mode -- see its module docstring.

        tradingsymbol MUST be Kite's own tradingsymbol for this listing
        (see utils.kite_instrument_map.get_cached_kite_tradingsymbol), NOT
        this app's internal stock_watchlist.symbol -- for a lot of BSE
        listings those are different strings (often a numeric scrip code
        on our side), and Kite's order API will reject or mis-resolve a
        tradingsymbol it doesn't recognize.

        Regulatory note this class does NOT resolve on its own: exchange
        rules require API-placed orders originating from an automated
        strategy to carry an Algo ID registered with the broker/exchange
        under NSE/BSE's retail algo-trading framework. No algo_id/tag is
        passed here -- if that requirement is enforced on this account,
        Kite may reject orders placed this way until one is registered.
        That's a compliance decision for the account holder, not something
        to guess a value for here.

        Returns Kite's order_id (str). Raises KiteClientError (wrapping
        whatever pykiteconnect raised -- e.g. insufficient margin, invalid
        tradingsymbol, market closed) on rejection; never swallowed, since
        a failed real order is never something a caller should silently
        treat as success."""
        try:
            return self._kite.place_order(
                variety=self._kite.VARIETY_REGULAR,
                exchange=exchange,
                tradingsymbol=tradingsymbol,
                transaction_type=transaction_type,
                quantity=quantity,
                product=self._kite.PRODUCT_CNC,
                order_type=self._kite.ORDER_TYPE_MARKET,
            )
        except Exception as e:
            raise KiteClientError(f'Kite order placement failed ({transaction_type} {quantity} {tradingsymbol}): {e}')

    def get_order_fill(self, order_id):
        """Returns {'status', 'average_price', 'filled_quantity'} for
        order_id, reflecting its LATEST lifecycle update (Kite's
        order_history returns every status change oldest-first; the last
        entry is the current state). None if Kite has no history for this
        order_id at all. status is one of Kite's own values -- callers
        care specifically about 'COMPLETE' (average_price is the real fill
        price to record) vs. anything else (not yet actionable -- still
        'OPEN'/'TRIGGER PENDING', or failed -- 'REJECTED'/'CANCELLED')."""
        history = self._kite.order_history(order_id)
        if not history:
            return None
        latest = history[-1]
        return {
            'status': latest.get('status'),
            'average_price': latest.get('average_price'),
            'filled_quantity': latest.get('filled_quantity'),
        }
