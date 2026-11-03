"""Transparent screening heuristics. No learned or backtested profit model."""
from datetime import datetime, timedelta
import math
from .providers import IST, finite, now_ist

def indicators(bundle, mode, now=None):
    now = now or now_ist()
    rows = bundle['candles']
    interval = 86400 if mode == 'longterm' else 300
    # Daily candles are used only once the local session has ended.
    if mode == 'longterm':
        rows = [r for r in rows if datetime.fromtimestamp(r['t'], IST).date() < now.date()
                or (datetime.fromtimestamp(r['t'], IST).date() == now.date() and (now.hour, now.minute) >= (15, 35))]
    else:
        rows = [r for r in rows if r['t'] + interval <= now.timestamp()]
        if rows:
            session = datetime.fromtimestamp(rows[-1]['t'], IST).date()
            rows = [r for r in rows if datetime.fromtimestamp(r['t'], IST).date() == session]
    if len(rows) < 25:
        return {'available': False, 'reason': 'Need at least 25 completed candles in the relevant session or daily history', 'sample_count': len(rows)}
    closes = [r['c'] for r in rows]
    last = rows[-1]
    sma20 = sum(closes[-20:])/20
    sma50 = sum(closes[-50:])/50 if len(rows) >= 50 else None
    changes = [closes[i]-closes[i-1] for i in range(1, len(closes))]
    gains = sum(max(c, 0) for c in changes[:14])/14
    losses = sum(max(-c, 0) for c in changes[:14])/14
    for change in changes[14:]:
        gains = (gains*13 + max(change, 0))/14
        losses = (losses*13 + max(-change, 0))/14
    rsi = 50 if gains == losses == 0 else 100 if losses == 0 else 100 - 100/(1+gains/losses)
    tr = [max(r['h']-r['l'], abs(r['h']-rows[i-1]['c']), abs(r['l']-rows[i-1]['c'])) for i, r in enumerate(rows) if i]
    atr = sum(tr[:14])/14
    for value in tr[14:]:
        atr = (atr*13 + value)/14
    vol = sum(r['v'] for r in rows)
    vwap = sum(((r['h']+r['l']+r['c'])/3)*r['v'] for r in rows)/vol if vol > 0 and mode != 'longterm' else None
    prior_volume = sum(r['v'] for r in rows[-21:-1])/20
    relative = last['v']/prior_volume if prior_volume > 0 else None
    age = (now.timestamp()-last['t'])/60
    trend = 'Bullish' if last['c'] > sma20 and (sma50 is None or sma20 > sma50) else 'Bearish' if last['c'] < sma20 else 'Mixed'
    return {'available': True, 'price': round(last['c'], 2), 'sma20': round(sma20, 2),
            'sma50': round(sma50, 2) if sma50 else None, 'rsi14': round(rsi, 1), 'atr14': round(atr, 2),
            'vwap': round(vwap, 2) if vwap else None, 'relative_volume': round(relative, 2) if relative is not None else None,
            'trend': trend, 'change_pct': round((last['c']/rows[0]['c']-1)*100, 2),
            'age_minutes': round(age, 1), 'as_of': datetime.fromtimestamp(last['t'], IST).isoformat(),
            'sample_count': len(rows), 'sparkline': [round(c, 2) for c in closes[-60:]],
            'support': round(min(r['l'] for r in rows[-20:]), 2), 'resistance': round(max(r['h'] for r in rows[-20:]), 2)}

def score_evidence(metrics, news, fundamentals=None, mode='longterm', now=None):
    now = now or now_ist()
    news = [a for a in news if (now-datetime.fromisoformat(a['published_at'])).total_seconds() <= (14 if mode == 'longterm' else 2)*86400]
    components = {'trend': 0, 'momentum': 0, 'volume': 0, 'news': 0, 'fundamentals': 0}
    reasons, risks = [], []
    if metrics.get('available'):
        components['trend'] = 25 if metrics['trend'] == 'Bullish' else 5 if metrics['trend'] == 'Bearish' else 12
        components['momentum'] = 15 if 45 <= metrics['rsi14'] <= 65 else 7 if 35 <= metrics['rsi14'] <= 75 else 0
        rv = metrics.get('relative_volume')
        components['volume'] = 10 if rv and rv >= 1.2 else 5 if rv and rv >= .7 else 0
        reasons.append(f"{metrics['trend']} trend; RSI {metrics['rsi14']}; relative volume {rv if rv is not None else 'unavailable'}.")
        if metrics['rsi14'] > 70:
            risks.append('Momentum is overbought; chasing may produce a poor entry.')
        if metrics['trend'] == 'Bearish':
            risks.append('Price trend disagrees with a bullish investment thesis.')
        if rv is None or rv < .7:
            risks.append('Weak or unavailable volume confirmation.')
    else:
        risks.append(metrics.get('reason', 'Price indicators unavailable.'))
    publishers = len({a['publisher'] for a in news if not a.get('synthetic')})
    if news:
        weighted, weights = 0, 0
        for a in news:
            age = max(0, (now-datetime.fromisoformat(a['published_at'])).total_seconds()/3600)
            weight = math.exp(-age/(96 if mode == 'longterm' else 24))
            weighted += a['sentiment']*weight
            weights += weight
        tone = weighted/weights if weights else 0
        components['news'] = round(max(0, min(25, 12.5+12.5*tone)))
        reasons.append(f"{len(news)} recent headlines from {publishers} real publishers; headline tone {tone:+.2f}.")
        if any(a['sentiment'] < 0 for a in news):
            risks.append('Negative headlines are present; read the original reports before deciding.')
    else:
        tone = 0
        risks.append('No recent company headlines found; no confirmed news catalyst.')
    if publishers < 2:
        risks.append('Fewer than two publishers; independent corroboration is missing.')
    if fundamentals:
        checks = [finite(fundamentals.get('revenue_growth_pct')) and fundamentals['revenue_growth_pct'] > 0,
                  finite(fundamentals.get('earnings_growth_pct')) and fundamentals['earnings_growth_pct'] > 0,
                  finite(fundamentals.get('roe_pct')) and fundamentals['roe_pct'] >= 12,
                  finite(fundamentals.get('debt_to_equity')) and 0<=fundamentals['debt_to_equity'] <= 1,
                  finite(fundamentals.get('operating_cash_flow')) and fundamentals['operating_cash_flow'] > 0]
        components['fundamentals'] = sum(checks)*5
        reasons.append(f"Financial quality screen passes {sum(checks)}/5 checks; PE {fundamentals.get('pe')} needs sector comparison.")
        risks.append('Verify source financial statements and peer valuation. Debt screens are less useful for banks.')
        if not fundamentals.get('complete',True):risks.append('Financial history is incomplete or has mismatched periods; not an investment-ready financial screen.')
    elif mode == 'longterm':
        risks.append('Financial statement and valuation evidence missing; this is a research shortlist, not a buy verdict.')
    return {'score': sum(components.values()), 'components': components, 'reasons': reasons, 'risks': risks,
            'headline_tone': round(tone, 2), 'publisher_count': publishers,
            'method': 'Bullish screening score 0-100: trend 25, momentum 15, volume 10, headlines 25, financial quality 25. Not a probability or forecast. Intraday/options do not use the financial component, so their maximum is 75.'}

def intraday_plan(metrics, bundle, scored, market, capital, risk_pct, demo=False, now=None):
    now = now or now_ist()
    blocked = []
    if demo or bundle.get('synthetic'):
        blocked.append('Synthetic demo data cannot support a trade setup.')
    if not bundle.get('broker'):
        blocked.append('Connect Zerodha Kite or Upstox for fresh broker candles; public data is research only.')
    if not market.get('verified') or not market.get('open'):
        blocked.append('Normal NSE trading session is not verified open.')
    if not metrics.get('available'):
        blocked.append('Insufficient completed intraday candles.')
    else:
        age = (now - datetime.fromisoformat(metrics['as_of'])).total_seconds()/60
        if not 0 <= age <= 10 or datetime.fromisoformat(metrics['as_of']).date() != now.date():
            blocked.append('Latest completed candle is stale or outside today\'s session.')
        if metrics['trend'] != 'Bullish' or not 45 <= metrics['rsi14'] <= 65:
            blocked.append('Trend and momentum do not confirm a bullish setup.')
        if not metrics.get('relative_volume') or metrics['relative_volume'] < 1.2:
            blocked.append('At least 1.2x relative volume is required.')
        if not metrics.get('vwap') or metrics['price'] <= metrics['vwap']:
            blocked.append('Price is not above session VWAP.')
        if metrics['atr14'] <= 0:
            blocked.append('ATR is unavailable or zero.')
    if scored['publisher_count'] < 2 or scored['headline_tone'] <= 0:
        blocked.append('A positive news catalyst corroborated across two publishers is required.')
    if blocked:
        return {'status': 'WAIT', 'blocked_by': blocked}
    # Conditional breakout entry, using upward/downward paisa rounding to preserve risk bounds.
    entry = math.ceil(max(metrics['resistance'], metrics['price'])*1.001*100)/100
    stop = math.floor((entry-1.5*metrics['atr14'])*100)/100
    if stop <= 0 or stop >= entry:
        return {'status': 'WAIT', 'blocked_by': ['Invalid entry or stop distance.']}
    risk_per_share = entry-stop
    risk_budget = capital*risk_pct/100
    quantity = min(math.floor(capital/entry), math.floor(risk_budget/risk_per_share))
    if quantity <= 0:
        return {'status': 'WAIT', 'blocked_by': ['Budget is too small for this setup at the chosen risk limit.']}
    return {'status': 'CONDITIONAL SETUP', 'entry_above': entry, 'stop': stop, 'target': round(entry+2*risk_per_share, 2),
            'quantity': quantity, 'capital_required': round(quantity*entry, 2), 'planned_risk': round(quantity*risk_per_share, 2),
            'risk_reward': '1:2 before fees', 'expires_at': (now+timedelta(minutes=5)).isoformat(),
            'conditions': 'Recheck live quote, breakout, liquidity and news before entering. Stop fills can slip; risk excludes fees and gaps. This is not an order.'}

def analyse_options(chain, market, now=None):
    now = now or now_ist()
    contracts = []
    for row in chain['rows']:
        try:
            expiry = datetime.fromisoformat(row['expiry']).date()
            if expiry < now.date() or not finite(row['strike_price']) or row['strike_price'] <= 0:
                continue
            for side, field in [('CALL', 'call_options'), ('PUT', 'put_options')]:
                option = row.get(field) or {}
                m, g = option.get('market_data') or {}, option.get('option_greeks') or {}
                bid, ask = m.get('bid_price'), m.get('ask_price')
                if not all(finite(v) for v in (bid, ask, m.get('volume'), m.get('oi'))) or not 0 < bid <= ask or m['volume'] <= 0 or m['oi'] <= 0:
                    continue
                spread = (ask-bid)/((ask+bid)/2)*100
                if spread > 5:
                    continue
                contracts.append({'side': side, 'strike': row['strike_price'], 'expiry': row['expiry'],
                                  'instrument_key': option.get('instrument_key'), 'bid': bid, 'ask': ask,
                                  'as_of':option.get('as_of'), 'lot_size':option.get('lot_size'),
                                  'spread_pct': round(spread, 2), 'volume': m['volume'], 'oi': m['oi'],
                                  'iv': g.get('iv') if finite(g.get('iv')) else None,
                                  'delta': g.get('delta') if finite(g.get('delta')) else None,
                                  'theta': g.get('theta') if finite(g.get('theta')) else None,
                                  'days_to_expiry': (expiry-now.date()).days})
        except (ValueError, KeyError, TypeError):
            continue
    contracts.sort(key=lambda c: (c['spread_pct'], -c['volume']))
    return {'status': 'CHAIN RESEARCH ONLY', 'contracts': contracts[:6], 'fetched_at': chain['fetched_at'],
            'source': chain['source'], 'limitations': [chain['note'], 'Liquidity ranking is not a buy-call or buy-put signal. Theta, IV and expiry risk require strategy review.',
            'Revalidate instrument metadata, contract quote age, margin and strategy maximum loss; no option trade plan is generated.',
            'Market is verified open.' if market.get('verified') and market.get('open') else 'Market is not verified open; snapshot may reflect the last session.']}
