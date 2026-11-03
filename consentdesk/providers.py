from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlencode, urlparse
import math
import re
import xml.etree.ElementTree as ET
from .config import UNIVERSE, read_json
from .net import CACHE, ProviderError, request, request_json

IST = timezone(timedelta(hours=5, minutes=30))

def now_ist():
    return datetime.now(IST)

def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

def safe_url(value):
    try:
        p = urlparse(value)
        return p.scheme in ('http', 'https') and bool(p.netloc)
    except (ValueError, TypeError):
        return False

POSITIVE = ('profit rises', 'profit jumps', 'wins', 'upgrade', 'record', 'expansion', 'dividend', 'surge', 'beats', 'approval', 'rises', 'gains', 'rebound')
NEGATIVE = ('profit falls', 'loss', 'fraud', 'probe', 'downgrade', 'decline', 'slump', 'penalty', 'misses', 'lawsuit', 'falls', 'crackdown', 'weak growth', 'slow growth', 'sluggish growth', 'risks')

def sentiment(title):
    text = title.lower()
    pos = sum(term in text for term in POSITIVE)
    neg = sum(term in text for term in NEGATIVE)
    # Generic growth must not make weak or negative growth sound bullish.
    if re.search(r'\bgrowth\b', text) and not re.search(r'\b(weak|slow|sluggish|negative|slowing)\s+growth\b', text):
        pos += 1
    return max(-1, min(1, pos - neg))

def parse_news(xml, symbol, now=None, days=7, limit=10):
    now = now or now_ist()
    seen, articles = set(), []
    root = ET.fromstring(xml)
    for item in root.findall('.//item'):
        title = re.sub(r'<[^>]+>', '', item.findtext('title', '')).strip()[:300]
        # Google titles usually include the publisher suffix; dedupe the headline itself.
        normal = re.sub(r'[^a-z0-9]', '', title.rsplit(' - ', 1)[0].lower())
        link = item.findtext('link', '').strip()
        try:
            published = parsedate_to_datetime(item.findtext('pubDate', ''))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        age = (now - published).total_seconds()
        if not title or not normal or normal in seen or not safe_url(link) or age < -300 or age > days * 86400:
            continue
        seen.add(normal)
        articles.append({'id': f'{symbol}-n{len(articles)+1}', 'title': title,
                         'url': link, 'publisher': item.findtext('source', 'Google News')[:100],
                         'published_at': published.astimezone(IST).isoformat(),
                         'sentiment': sentiment(title), 'scope': 'headline only', 'synthetic': False})
    return sorted(articles, key=lambda a: a['published_at'], reverse=True)[:limit]

class Providers:
    def __init__(self, settings):
        self.settings = settings
        from .kite import KiteData
        self.kite = KiteData(settings)
        from .fyers import FyersData
        self.fyers=FyersData(settings)

    def symbol_key(self,symbol):
        configured=self.settings.instrument(symbol)
        if configured:return configured
        # Kite CSV does not include ISIN; use Upstox's public NSE master for
        # broker data on symbols outside the original watchlist.
        import gzip
        import json
        def load():
            raw=request('https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz',max_bytes=6_000_000)
            try:
                data=json.loads(gzip.decompress(raw))
                return {i['trading_symbol']:i['instrument_key'] for i in data if i.get('instrument_type')=='EQ' and i.get('segment')=='NSE_EQ'}
            except (ValueError,KeyError,OSError,TypeError):raise ProviderError('Upstox NSE instrument master unavailable') from None
        mapping=CACHE.get_or_load(('upstox-equity-keys',str(now_ist().date())),3600,load)
        return mapping.get(symbol)

    def search(self, query):
        query = query.strip().upper().removesuffix('.NS')
        found = [{'symbol': s, 'name': m['name'], 'sector': m['sector']} for s, m in UNIVERSE.items()
                 if query in s or query in m['name'].upper()]
        note = None
        try:
            for item in self.kite.instruments():
                symbol = item['tradingsymbol']
                if item.get('instrument_type') == 'EQ' and re.fullmatch(r'[A-Z][A-Z0-9&-]{0,19}', symbol) and (query in symbol or query in item.get('name', '').upper()):
                    if not any(i['symbol'] == symbol for i in found):
                        found.append({'symbol': symbol, 'name': item.get('name') or symbol, 'sector': 'NSE equity'})
        except (ProviderError, OSError):
            note = 'Instrument catalogue unavailable; showing curated matches. Explicit NSE symbols can still be researched.'
        found.sort(key=lambda i: (i['symbol'] != query, not i['symbol'].startswith(query), i['symbol']))
        return {'results': found[:15], 'note': note}

    def quotes(self, symbols, demo=False):
        market = self.market_status(demo)
        notes, quotes = [], {}
        if demo:
            for s in symbols:
                b = self.candles(s, 'intraday', True)
                quotes[s] = {'symbol': s, 'price': b['candles'][-1]['c'], 'as_of': b['as_of'], 'source': 'Synthetic demonstration', 'synthetic': True, 'broker': False, 'live': False, 'change_pct': None}
        elif self.settings.broker=='fyers' and self.settings.broker_enabled:
            try:quotes=self.fyers.quotes(symbols)
            except ProviderError as e:notes.append(str(e))
        elif self.settings.kite_enabled:
            try:
                quotes = self.kite.quotes(symbols)
            except ProviderError as e:
                notes.append(str(e))
        elif self.settings.broker == 'upstox' and self.settings.upstox_token:
            try:
                keys = {}
                for s in symbols:
                    try:
                        key=self.symbol_key(s)
                        if key:keys[s]=key
                    except ProviderError:notes.append(s+': instrument key unavailable')
                if keys:
                    data = CACHE.get_or_load(('upstox-quotes', tuple(symbols)), 4, lambda: self._broker('v2/market-quote/quotes?' + urlencode({'instrument_key': ','.join(keys.values())})))
                    for s, key in keys.items():
                        q = next((v for v in data.values() if v.get('instrument_token') == key), None)
                        if not q or not finite(q.get('last_price')) or q['last_price'] <= 0:
                            continue
                        timestamp = q.get('timestamp')
                        try:
                            packet = (datetime.fromtimestamp(timestamp/1000 if timestamp>10_000_000_000 else timestamp,IST) if finite(timestamp) else datetime.fromisoformat(timestamp).astimezone(IST)).isoformat()
                        except (ValueError, TypeError):
                            packet = None
                        ohlc = q.get('ohlc') or {}
                        close = ohlc.get('close')
                        quotes[s] = {'symbol': s, 'price': q['last_price'], 'as_of': packet, 'last_trade_at': datetime.fromtimestamp(int(q['last_trade_time'])/1000, IST).isoformat() if q.get('last_trade_time') else None,
                            'change_pct': round((q['last_price']/close-1)*100, 2) if finite(close) and close > 0 else None,
                            'open': ohlc.get('open'), 'high': ohlc.get('high'), 'low': ohlc.get('low'), 'volume': q.get('volume'),
                            'bid':((q.get('depth') or {}).get('buy') or [{}])[0].get('price'),
                            'ask':((q.get('depth') or {}).get('sell') or [{}])[0].get('price'),
                            'source': 'Upstox quote snapshot', 'broker': True, 'synthetic': False, 'live': False}
            except (ProviderError, ValueError, KeyError, TypeError) as e:
                notes.append(str(e) if isinstance(e, ProviderError) else 'Malformed broker quote')
        missing = [s for s in symbols if s not in quotes]
        from concurrent.futures import ThreadPoolExecutor, as_completed
        def public(s):
            ticker = UNIVERSE.get(s, {}).get('yahoo', s + '.NS')
            def load():
                raw = request_json(f'https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker)}?interval=1m&range=1d')
                try:
                    meta = raw['chart']['result'][0]['meta']
                    price, ts = meta['regularMarketPrice'], meta['regularMarketTime']
                    if not finite(price) or price <= 0 or not finite(ts):
                        raise ValueError()
                    prior = meta.get('chartPreviousClose', meta.get('previousClose'))
                    return {'symbol': s, 'price': price, 'as_of': datetime.fromtimestamp(ts, IST).isoformat(),
                            'change_pct': round((price/prior-1)*100, 2) if finite(prior) and prior > 0 else None,
                            'previous_close': prior, 'high': meta.get('regularMarketDayHigh'), 'low': meta.get('regularMarketDayLow'),
                            'volume': meta.get('regularMarketVolume'), 'source': 'Yahoo public quote (delay unverified)',
                            'broker': False, 'synthetic': False, 'live': False, 'fetched_at': now_ist().isoformat()}
                except (KeyError, IndexError, TypeError, ValueError):
                    raise ProviderError('No quote returned for this NSE symbol') from None
            return CACHE.get_or_load(('public-quote', s), 15, load)
        if not demo:
            with ThreadPoolExecutor(max_workers=6) as pool:
                jobs = {pool.submit(public, s): s for s in missing}
                for job in as_completed(jobs):
                    s = jobs[job]
                    try:
                        quotes[s] = job.result()
                    except ProviderError as e:
                        notes.append(f'{s}: {e}')
        now = now_ist()
        regular = now.weekday() < 5 and (9,15) <= (now.hour,now.minute) < (15,30)
        for q in quotes.values():
            try:
                dated = datetime.fromisoformat(q['as_of'])
                age = (now-dated).total_seconds()
                q['age_seconds'] = round(age, 1)
                q['live'] = bool(q['broker'] and not q['synthetic'] and 0 <= age <= 30 and dated.date() == now.date() and regular)
                if self.settings.broker in ('upstox','fyers'):q['live']=bool(q['live'] and market.get('verified') and market.get('open'))
            except (ValueError, TypeError, KeyError):
                q['age_seconds'], q['live'] = None, False
            q['label'] = 'LIVE SNAPSHOT' if q['live'] else 'SYNTHETIC' if q['synthetic'] else 'BROKER LAST SNAPSHOT' if q['broker'] else 'PUBLIC · DELAY UNVERIFIED'
        if self.settings.kite_enabled and regular and any(q['live'] for q in quotes.values()):
            market = {'verified': True, 'open': True, 'label': 'Fresh Zerodha NSE packets in regular session', 'checked_at': now.isoformat(), 'verification': 'Same-day exchange packet <=30s old during normal weekday trading hours; no separate exchange-status API.'}
        return {'quotes': [quotes[s] for s in symbols if s in quotes], 'gaps': notes, 'market': market, 'checked_at': now.isoformat(), 'refresh_seconds': 10}

    def news(self, symbol, mode, demo=False):
        if demo:
            return {'source': 'Synthetic demonstration', 'articles': [
                {'id': f'{symbol}-n1', 'title': f'DEMO: {UNIVERSE.get(symbol, {}).get("name", symbol)} reports growth in demand',
                 'publisher': 'Synthetic example', 'published_at': now_ist().isoformat(),
                 'url': '', 'sentiment': 1, 'scope': 'synthetic headline', 'synthetic': True},
                {'id': f'{symbol}-n2', 'title': f'DEMO: Analysts flag valuation and margin pressure at {symbol}',
                 'publisher': 'Synthetic example', 'published_at': now_ist().isoformat(),
                 'url': '', 'sentiment': -1, 'scope': 'synthetic headline', 'synthetic': True}], 'fetched_at': now_ist().isoformat()}
        days = 14 if mode == 'longterm' else 2
        from .sectors import metadata
        company = UNIVERSE.get(symbol, metadata(symbol))['name']
        query = f'"{company}" stock India when:{days}d'
        url = 'https://news.google.com/rss/search?' + urlencode({'q': query, 'hl': 'en-IN', 'gl': 'IN', 'ceid': 'IN:en'})
        def load():
            broker_articles=[];notes=[]
            if self.settings.upstox_token:
                try:
                    key=self.symbol_key(symbol)
                    raw=self._broker('v2/news?'+urlencode({'category':'instrument_keys','instrument_keys':key,'page_size':30})) if key else {}
                    for row in raw.get(key,[]):
                        published=datetime.fromtimestamp(int(row['published_time'])/1000,IST)
                        age=(now_ist()-published).total_seconds()
                        if 0<=age<=days*86400 and safe_url(row.get('article_link')):
                            title=re.sub(r'<[^>]+>','',str(row.get('heading','')))[:300]
                            broker_articles.append({'id':symbol+'-upstox-'+str(row['published_time']),'title':title,
                                'summary':re.sub(r'<[^>]+>','',str(row.get('summary','')))[:900],'publisher':'Upstox News',
                                'url':row['article_link'],'published_at':published.isoformat(),'sentiment':sentiment(title),
                                'scope':'Provider headline and summary; full article not verified','synthetic':False})
                except (ProviderError,ValueError,KeyError,TypeError):notes.append('Upstox news unavailable; RSS fallback used')
            try:
                articles = parse_news(request(url), symbol, days=days)
            except (ET.ParseError,ProviderError):
                if not broker_articles:raise ProviderError('News providers unavailable') from None
                articles=[]
            seen=set();merged=[]
            for article in sorted(broker_articles+articles,key=lambda a:a['published_at'],reverse=True):
                normal=re.sub(r'[^a-z0-9]','',article['title'].rsplit(' - ',1)[0].lower())
                if normal not in seen:merged.append(article);seen.add(normal)
            return {'source': 'Upstox + Google News RSS' if broker_articles else 'Google News RSS', 'articles': merged[:15], 'notes':notes,'fetched_at': now_ist().isoformat()}
        return CACHE.get_or_load(('news', symbol, mode), 180, load)

    def market_news(self):
        url='https://news.google.com/rss/search?'+urlencode({'q':'India stocks earnings orders results NSE when:2d','hl':'en-IN','gl':'IN','ceid':'IN:en'})
        def load():
            try:return parse_news(request(url),'MARKET',days=2,limit=100)
            except ET.ParseError:raise ProviderError('Market news could not be parsed') from None
        return CACHE.get_or_load(('market-discovery-news',),900,load)

    def _broker(self, path):
        if not self.settings.upstox_token:
            raise ProviderError('Upstox access token is not configured')
        value = request_json('https://api.upstox.com/' + path,
                             headers={'Authorization': 'Bearer ' + self.settings.upstox_token})
        if value.get('status') != 'success':
            raise ProviderError('Upstox returned an unsuccessful response')
        return value.get('data')

    def market_status(self, demo=False):
        if not demo and self.settings.broker=='fyers':return self.fyers.market_status()
        if not demo and self.settings.kite_enabled:
            return {'verified': False, 'open': False, 'label': 'Zerodha session awaits fresh exchange quote', 'checked_at': now_ist().isoformat()}
        if demo or self.settings.broker != 'upstox' or not self.settings.upstox_token:
            return {'verified': False, 'open': False, 'label': 'Demo' if demo else 'Session unverified', 'checked_at': now_ist().isoformat()}
        try:
            data = CACHE.get_or_load(('session',), 20, lambda: self._broker('v2/market/status/NSE'))
            return {'verified': True, 'open': data['status'] == 'NORMAL_OPEN',
                    'label': data['status'], 'checked_at': now_ist().isoformat()}
        except (ProviderError, KeyError, TypeError):
            return {'verified': False, 'open': False, 'label': 'Session check failed', 'checked_at': now_ist().isoformat()}

    def candles(self, symbol, mode, demo=False):
        if demo:
            seed = sum(map(ord, symbol))
            base = 100 + seed % 1900
            step = 300 if mode != 'longterm' else 86400
            end = int(now_ist().timestamp()) - 300
            rows = []
            for i in range(80):
                c = base * (1 + i * .0008 + math.sin(i / 5) * .006)
                rows.append({'t': end - (79-i)*step, 'o': c*.999, 'h': c*1.004, 'l': c*.995, 'c': c, 'v': 10000 + (i%9)*1300})
            return self._package(rows, 'Synthetic demonstration', True, False)
        if self.settings.broker=='fyers' and self.settings.broker_enabled:
            try:return self.fyers.candles(symbol,mode)
            except ProviderError:pass
        key = self.settings.instrument(symbol)
        if not key and self.settings.broker=='upstox' and self.settings.upstox_token:
            try:key=self.symbol_key(symbol)
            except ProviderError:pass
        errors = []
        if self.settings.kite_enabled:
            try:
                return self.kite.candles(symbol, mode)
            except (ProviderError, OSError) as e:
                errors.append(str(e) if isinstance(e, ProviderError) else 'Kite instrument file unavailable')
        if self.settings.broker == 'upstox' and self.settings.upstox_token and key:
            try:
                def broker_load():
                    historical=[];history_notes=[]
                    if mode == 'longterm':
                        today = now_ist().date()
                        path = f'v3/historical-candle/{quote(key, safe="")}/days/1/{today}/{today-timedelta(days=400)}'
                    else:
                        today=now_ist().date()
                        prior_path=f'v3/historical-candle/{quote(key,safe="")}/minutes/5/{today-timedelta(days=1)}/{today-timedelta(days=10)}'
                        try:
                            prior=CACHE.get_or_load(('upstox-prior-bars',symbol,str(today)),300,lambda:self._broker(prior_path))
                            historical=prior['candles']
                        except (ProviderError,KeyError,TypeError):history_notes.append('Prior-session broker bars unavailable; volume confirmation may remain pending')
                        path = f'v3/historical-candle/intraday/{quote(key, safe="")}/minutes/5'
                    data = self._broker(path)
                    rows = [{'t': int(datetime.fromisoformat(c[0]).timestamp()), 'o': c[1], 'h': c[2], 'l': c[3], 'c': c[4], 'v': c[5]} for c in historical+data['candles']]
                    result=self._package(rows, 'Upstox broker candles', False, True)
                    result['provider_notes']=history_notes
                    return result
                return CACHE.get_or_load(('broker', symbol, mode), 20 if mode != 'longterm' else 300, broker_load)
            except (ProviderError, KeyError, ValueError, TypeError, IndexError) as e:
                errors.append(str(e) if isinstance(e, ProviderError) else 'Broker candle response malformed')
        yahoo = UNIVERSE.get(symbol, {}).get('yahoo', symbol + '.NS')
        params = {'interval': '1d' if mode == 'longterm' else '5m', 'range': '5y' if mode == 'longterm' else '5d'}
        def public_load():
            raw = request_json(f'https://query1.finance.yahoo.com/v8/finance/chart/{quote(yahoo)}?' + urlencode(params))
            try:
                data = raw['chart']['result'][0]
                q = data['indicators']['quote'][0]
                rows = [{'t': t, 'o': q['open'][i], 'h': q['high'][i], 'l': q['low'][i], 'c': q['close'][i], 'v': q['volume'][i]} for i, t in enumerate(data['timestamp'])]
                result = self._package(rows, 'Yahoo public candles (unofficial; may be delayed)', False, False)
                result['provider_notes'] = errors
                return result
            except (KeyError, IndexError, TypeError):
                raise ProviderError('No usable public candles for this symbol') from None
        return CACHE.get_or_load(('public', symbol, mode), 60 if mode != 'longterm' else 300, public_load)

    @staticmethod
    def _package(rows, source, synthetic, broker):
        valid = [r for r in rows if all(finite(r.get(k)) for k in ('t', 'o', 'h', 'l', 'c', 'v'))
                 and min(r['o'], r['h'], r['l'], r['c']) > 0 and r['v'] >= 0
                 and r['l'] <= min(r['o'], r['c']) <= max(r['o'], r['c']) <= r['h']]
        dedup = {int(r['t']): r for r in valid}
        clean = [dedup[t] for t in sorted(dedup) if t <= now_ist().timestamp() + 60]
        if not clean:
            raise ProviderError('No valid price candles returned')
        return {'candles': clean, 'source': source, 'synthetic': synthetic, 'broker': broker,
                'as_of': datetime.fromtimestamp(clean[-1]['t'], IST).isoformat(), 'fetched_at': now_ist().isoformat()}

    def options(self, symbol, expiry, demo=False):
        if self.settings.broker=='fyers':raise ProviderError('FYERS option-chain adapter is not implemented; quotes and historical research are supported')
        if demo:
            raise ProviderError('Demo mode does not fabricate an option chain')
        if self.settings.kite_enabled:
            return self.kite.options(symbol, expiry)
        if self.settings.broker == 'zerodha':
            raise ProviderError('Configure KITE_API_KEY and KITE_ACCESS_TOKEN with Zerodha market-data access to inspect OI and options')
        key = self.symbol_key(symbol)
        if not key:
            raise ProviderError('Upstox underlying instrument key missing')
        selected=expiry
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',expiry):
            contracts=CACHE.get_or_load(('upstox-option-contracts',key,str(now_ist().date())),3600,
                lambda:self._broker('v2/option/contract?'+urlencode({'instrument_key':key})))
            if not isinstance(contracts,list):raise ProviderError('Malformed listed option contracts')
            expiries=sorted({c['expiry'] for c in contracts if isinstance(c,dict) and isinstance(c.get('expiry'),str) and c['expiry']>=str(now_ist().date())})
            if expiry.endswith('_month'):
                months=sorted({e[:7] for e in expiries});offset={'current_month':0,'next_month':1,'far_month':2}.get(expiry,99)
                if offset>=len(months):raise ProviderError('Requested monthly expiry is not listed')
                selected=max(e for e in expiries if e.startswith(months[offset]))
            else:
                offset={'current_week':0,'next_week':1,'far_week':2}.get(expiry,99)
                if offset>=len(expiries):raise ProviderError('Requested expiry is not listed')
                selected=expiries[offset]
        path = 'v2/option/chain?' + urlencode({'instrument_key': key, 'expiry_date': selected})
        data = CACHE.get_or_load(('options', symbol, selected), 20, lambda: self._broker(path))
        if not isinstance(data, list) or not data:
            raise ProviderError('No option chain for the selected underlying and expiry')
        return {'rows': data, 'source': 'Upstox option chain','expiry':selected, 'fetched_at': now_ist().isoformat(),
                'note': 'Fetched snapshot; per-contract exchange timestamp and lot size are not supplied here. Confirm in broker before trading.'}

    def fundamentals(self, symbol):
        if hasattr(self,'desk'):
            with self.desk.store.connect() as db:
                row=db.execute('SELECT payload FROM desk_financials WHERE id=?',(symbol,)).fetchone()
            if row:
                import json
                data=json.loads(row[0])
            else:data=read_json(self.settings.fundamentals_file,{}).get(symbol)
        else:data = read_json(self.settings.fundamentals_file, {}).get(symbol)
        if not isinstance(data, dict):
            if self.settings.upstox_token:
                from .upstox_research import UpstoxResearch
                return UpstoxResearch(self).review(symbol)
            raise ProviderError('Dated financial statement metrics are not configured')
        fields = ('revenue_growth_pct', 'earnings_growth_pct', 'roe_pct', 'debt_to_equity', 'pe', 'operating_cash_flow')
        try:
            dated = datetime.fromisoformat(data['as_of']).date()
            age = (now_ist().date() - dated).days
            if not 0 <= age <= 180 or not safe_url(data.get('source_url')) or not all(finite(data.get(k)) for k in fields):
                raise ValueError()
            if data['pe'] <= 0 or data['debt_to_equity'] < 0:
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise ProviderError('Fundamentals need finite metrics, positive PE, nonnegative debt, source URL and date within 180 days') from None
        return {**data, 'source': 'User-supplied source-linked financial metrics; verify filings', 'age_days': age, 'complete':True,'origin':'manual'}

    def corporate_events(self,symbol):
        if not self.settings.upstox_token:raise ProviderError('Connect a read-only Upstox Analytics Token for sourced corporate actions')
        from .upstox_research import UpstoxResearch
        return UpstoxResearch(self).events(symbol)

    def market_holidays(self):
        raw=CACHE.get_or_load(('upstox-holidays',now_ist().year),3600,lambda:self._broker('v2/market/holidays'))
        if not isinstance(raw,list):raise ProviderError('Malformed exchange holiday calendar')
        return [{'id':'nse-holiday:'+r['date'],'symbol':'ALL','kind':'market_closed','date':r['date'],
                 'title':str(r.get('description','NSE trading holiday'))[:160],
                 'source_url':'https://upstox.com/developer/api-documentation/get-market-holidays/',
                 'source':'Upstox NSE holiday calendar','reviewed':False}
                for r in raw if isinstance(r,dict) and 'NSE' in r.get('closed_exchanges',[]) and isinstance(r.get('date'),str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',r['date'])]

    def validation_candles(self,symbol,mode):
        if self.settings.broker=='fyers':return self.fyers.candles(symbol,mode,validation=True)
        if self.settings.kite_enabled:
            return self.kite.candles(symbol,mode,history_days=800 if mode=='longterm' else 45)
        if self.settings.broker=='upstox' and self.settings.upstox_token:
            key=self.symbol_key(symbol)
            if key:
                today=now_ist().date();start=today-timedelta(days=730 if mode=='longterm' else 30)
                interval='days/1' if mode=='longterm' else 'minutes/5'
                raw=self._broker(f'v3/historical-candle/{quote(key,safe="")}/{interval}/{today}/{start}')
                try:return self._package([{'t':int(datetime.fromisoformat(c[0]).timestamp()),'o':c[1],'h':c[2],'l':c[3],'c':c[4],'v':c[5]} for c in raw['candles']],'Upstox historical research',False,True)
                except (KeyError,IndexError,TypeError,ValueError):raise ProviderError('Malformed historical validation candles') from None
        ticker=UNIVERSE.get(symbol,{}).get('yahoo',symbol+'.NS')
        params={'interval':'1d' if mode=='longterm' else '5m','range':'5y' if mode=='longterm' else '1mo'}
        raw=request_json('https://query1.finance.yahoo.com/v8/finance/chart/'+quote(ticker)+'?'+urlencode(params))
        try:
            data=raw['chart']['result'][0];q=data['indicators']['quote'][0]
            rows=[{'t':t,'o':q['open'][i],'h':q['high'][i],'l':q['low'][i],'c':q['close'][i],'v':q['volume'][i]} for i,t in enumerate(data['timestamp'])]
            return self._package(rows,'Yahoo public historical research (corporate actions require validation)',False,False)
        except (KeyError,TypeError,IndexError):raise ProviderError('Historical validation data unavailable') from None
