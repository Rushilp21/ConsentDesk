"""Read-only Kite Connect snapshots, instrument discovery and history."""
import csv
import gzip
import io
import json
from datetime import datetime, timedelta
import threading
import time
from urllib.parse import urlencode
from .net import CACHE, ProviderError, request, request_json
from .providers import now_ist, IST, finite

QUOTE_LOCK = threading.Lock()
HISTORY_LOCK = threading.Lock()
CATALOG_LOCK = threading.Lock()
LAST_CALL = {'quote': 0., 'history': 0.}

def broker_time(value):
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.replace(tzinfo=IST) if parsed.tzinfo is None else parsed.astimezone(IST)
    except (ValueError, TypeError):
        return None

class KiteData:
    def __init__(self, settings):
        self.settings = settings

    @property
    def headers(self):
        return {'X-Kite-Version': '3', 'Authorization': f'token {self.settings.kite_api_key}:{self.settings.kite_access_token}'}

    def get(self, path, category='quote'):
        if not self.settings.kite_enabled:
            raise ProviderError('Configure Zerodha KITE_API_KEY and KITE_ACCESS_TOKEN with market-data access')
        lock = QUOTE_LOCK if category == 'quote' else HISTORY_LOCK
        spacing = 1.05 if category == 'quote' else .36
        with lock:
            pause = spacing - (time.monotonic()-LAST_CALL[category])
            if pause > 0:
                time.sleep(pause)
            LAST_CALL[category] = time.monotonic()
            data = request_json('https://api.kite.trade/' + path, headers=self.headers)
        if data.get('status') != 'success':
            raise ProviderError('Kite returned an unsuccessful response; check token expiry and API data entitlement')
        return data.get('data')

    def instruments(self, exchange='NSE'):
        if exchange not in ('NSE', 'NFO'):
            raise ProviderError('Unsupported exchange')
        path = self.settings.data_dir / f'kite-{exchange.lower()}-instruments.json'
        today = str(now_ist().date())
        def load():
            try:
                stored = json.loads(path.read_text(encoding='utf-8'))
                if stored.get('date') == today and isinstance(stored.get('items'), list):
                    return stored['items']
            except (ValueError, OSError):
                pass
            # Instrument master is public reference data, not a quote source.
            blob = request('https://api.kite.trade/instruments/' + exchange, headers={'X-Kite-Version': '3'}, max_bytes=16_000_000)
            if blob.startswith(b'\x1f\x8b'):
                with gzip.GzipFile(fileobj=io.BytesIO(blob)) as gz:
                    blob = gz.read(64_000_001)
                if len(blob) > 64_000_000:
                    raise ProviderError('Instrument file exceeds the decompression limit')
            try:
                items = list(csv.DictReader(io.StringIO(blob.decode('utf-8-sig'))))
                if not items or not all(k in items[0] for k in ('instrument_token', 'tradingsymbol', 'exchange')):
                    raise ValueError()
            except (ValueError, UnicodeError):
                raise ProviderError('Kite instrument file is not valid CSV') from None
            items = [i for i in items if i.get('exchange') == exchange and
                     (i.get('instrument_type') in ('EQ', 'CE', 'PE') or i.get('segment') == 'INDICES')]
            self.settings.data_dir.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'date': today, 'items': items}), encoding='utf-8')
            temporary.replace(path)
            return items
        def guarded_load():
            with CATALOG_LOCK:
                return load()
        return CACHE.get_or_load(('kite-instruments', exchange, today), 3600, guarded_load)

    @staticmethod
    def exchange_symbol(symbol):
        return 'NSE:' + {'NIFTY': 'NIFTY 50', 'BANKNIFTY': 'NIFTY BANK'}.get(symbol, symbol)

    def quotes(self, symbols):
        identifiers = sorted(set(self.exchange_symbol(s) for s in symbols))
        data = self.raw_quotes(identifiers)
        results = {}
        for s in symbols:
            q = data.get(self.exchange_symbol(s))
            if not isinstance(q, dict) or not finite(q.get('last_price')) or q['last_price'] <= 0:
                continue
            packet, trade = broker_time(q.get('timestamp')), broker_time(q.get('last_trade_time'))
            ohlc = q.get('ohlc') or {}
            close = ohlc.get('close')
            results[s] = {'symbol': s, 'price': q['last_price'], 'previous_close': close if finite(close) else None,
                'change_pct': round((q['last_price']/close-1)*100, 2) if finite(close) and close > 0 else None,
                'open': ohlc.get('open'), 'high': ohlc.get('high'), 'low': ohlc.get('low'), 'volume': q.get('volume'),
                'bid':((q.get('depth') or {}).get('buy') or [{}])[0].get('price'),
                'ask':((q.get('depth') or {}).get('sell') or [{}])[0].get('price'),
                'as_of': packet.isoformat() if packet else None, 'last_trade_at': trade.isoformat() if trade else None,
                'fetched_at': data.get('_fetched_at'), 'source': 'Zerodha Kite quote snapshot', 'broker': True,
                'synthetic': False, 'instrument_token': q.get('instrument_token'), 'live': False}
        return results

    def raw_quotes(self, identifiers):
        if not 1 <= len(identifiers) <= 500:
            raise ProviderError('Quote request must contain 1 to 500 instruments')
        ids = tuple(sorted(set(identifiers)))
        def load():
            data = self.get('quote?' + urlencode([('i', s) for s in ids]))
            if not isinstance(data, dict):
                raise ProviderError('Malformed Kite quote response')
            return {**data, '_fetched_at': now_ist().isoformat()}
        return CACHE.get_or_load(('kite-quotes', ids), 4, load)

    def candles(self, symbol, mode, history_days=None):
        target = self.exchange_symbol(symbol).split(':', 1)[1]
        instrument = next((i for i in self.instruments() if i['tradingsymbol'] == target), None)
        if not instrument:
            raise ProviderError('Stock not found in today\'s Zerodha NSE instrument master')
        end = now_ist()
        days = history_days or (400 if mode == 'longterm' else 10)
        start = (end-timedelta(days=days)).replace(hour=9, minute=15, second=0, microsecond=0)
        interval = 'day' if mode == 'longterm' else '5minute'
        def load():
            params = {'from': start.strftime('%Y-%m-%d %H:%M:%S'), 'to': end.strftime('%Y-%m-%d %H:%M:%S'), 'oi': 1}
            data = self.get(f'instruments/historical/{instrument["instrument_token"]}/{interval}?' + urlencode(params), 'history')
            try:
                rows = [{'t': int(broker_time(c[0]).timestamp()), 'o': c[1], 'h': c[2], 'l': c[3], 'c': c[4], 'v': c[5], 'oi': c[6] if len(c)>6 else None} for c in data['candles']]
            except (KeyError, IndexError, TypeError, AttributeError):
                raise ProviderError('Malformed Kite historical candle response') from None
            from .providers import Providers
            return Providers._package(rows, 'Zerodha Kite historical candles', False, True)
        return CACHE.get_or_load(('kite-candles', symbol, mode, days), 30 if mode != 'longterm' else 300, load)

    def options(self, symbol, expiry):
        def load():
            candidates = [i for i in self.instruments('NFO') if i.get('instrument_type') in ('CE', 'PE') and
                          i.get('name') == symbol and i.get('expiry', '') >= str(now_ist().date())]
            expiries = sorted({i['expiry'] for i in candidates})
            if not expiries:
                raise ProviderError('No listed NSE options found for this stock in Zerodha instruments')
            if re_date(expiry):
                selected = expiry
            elif expiry.endswith('_month'):
                months = sorted({e[:7] for e in expiries})
                offset = {'current_month': 0, 'next_month': 1, 'far_month': 2}[expiry]
                if offset >= len(months):
                    raise ProviderError('Requested monthly expiry is not listed')
                selected = max(e for e in expiries if e.startswith(months[offset]))
            else:
                offset = {'current_week': 0, 'next_week': 1, 'far_week': 2}.get(expiry, 0)
                if offset >= len(expiries):
                    raise ProviderError('Requested expiry is not listed')
                selected = expiries[offset]
            instruments = [i for i in candidates if i['expiry'] == selected]
            if not instruments:
                raise ProviderError('No contracts for this expiry')
            underlying = self.raw_quotes([self.exchange_symbol(symbol)])
            spot = underlying.get(self.exchange_symbol(symbol), {}).get('last_price')
            if not finite(spot) or spot <= 0:
                raise ProviderError('Underlying quote is unavailable')
            strikes = sorted({float(i['strike']) for i in instruments}, key=lambda k: abs(k-spot))[:30]
            instruments = [i for i in instruments if float(i['strike']) in strikes]
            quotes = self.raw_quotes(['NFO:' + i['tradingsymbol'] for i in instruments])
            rows = {}
            for i in instruments:
                q = quotes.get('NFO:' + i['tradingsymbol'])
                if not q:
                    continue
                strike = float(i['strike'])
                row = rows.setdefault(strike, {'expiry': selected, 'strike_price': strike, 'underlying_spot_price': spot})
                depth = q.get('depth') or {}
                bids, asks = depth.get('buy') or [], depth.get('sell') or []
                packet = broker_time(q.get('timestamp'))
                option = {'instrument_key': 'NFO:'+i['tradingsymbol'], 'lot_size': int(i['lot_size']),
                          'as_of': packet.isoformat() if packet else None,
                          'market_data': {'ltp': q.get('last_price'), 'volume': q.get('volume'), 'oi': q.get('oi'),
                          'bid_price': bids[0].get('price') if bids else None, 'ask_price': asks[0].get('price') if asks else None,
                          'close_price': (q.get('ohlc') or {}).get('close')}, 'option_greeks': {}}
                row['call_options' if i['instrument_type']=='CE' else 'put_options'] = option
            return {'rows': [rows[k] for k in sorted(rows)], 'source': 'Zerodha Kite option snapshots', 'fetched_at': quotes['_fetched_at'],
                    'expiry': selected, 'coverage': 'Nearest 30 listed strikes; PCR and OI walls refer to this window, not the full chain.',
                    'note': 'Kite does not supply option Greeks in these quotes. No Greeks are invented; OI changes need recorded snapshots.'}
        return CACHE.get_or_load(('kite-chain', symbol, expiry), 30, load)

def re_date(value):
    try:
        datetime.strptime(value, '%Y-%m-%d')
        return True
    except ValueError:
        return False
