"""Directional option watchlists and explicit planning horizons, not orders."""
from datetime import timedelta, datetime
from .providers import now_ist, finite
from .reasoning import news_review


def holding_plan(rec, mode, now=None):
    now = now or now_ist()
    if mode == 'longterm':
        return {'period': '12–36 months', 'review': 'Every 30 days and after quarterly results',
                'next_review': (now + timedelta(days=30)).date().isoformat(),
                'exit_rule': 'Review an exit if the business thesis weakens, valuation becomes excessive, or the risk level breaks.',
                'basis': 'Planning horizon, not a predicted target date. Starts only after a verified entry.',
                'entry_ready': rec.get('status') == 'LONG-TERM REVIEW CANDIDATE'}
    return {'period': 'Same session; close by 15:15 IST',
            'review': 'Exit at stop, target or time limit, whichever comes first',
            'basis': 'Intraday only; no overnight or expiry-day holding plan.'}


def option_views(rec, market, daily_basis=False, now=None):
    """Keep long-call and long-put evidence separate. Never use PCR as direction."""
    now = now or now_ist()
    m = rec.get('metrics') or {}
    patterns = (rec.get('chart_structure') or {}).get('patterns', [])
    levels = (rec.get('chart_structure') or {}).get('levels', {})
    news = news_review(rec.get('news', []), now)
    results = {}
    for key, trend, side, direction in [('buy', 'Bullish', 'CALL', 'bullish'), ('sell', 'Bearish', 'PUT', 'bearish')]:
        contracts = [c for c in (rec.get('option_analysis') or {}).get('contracts', []) if c['side'] == side]
        # Balanced analysis supplies at most three liquid candidates per side.
        contracts.sort(key=lambda c: (abs(c['strike']-m.get('price', c['strike'])), c['spread_pct']))
        contract = contracts[0] if contracts else None
        rsi = m.get('rsi14')
        news_direction = news['positive_count'] if key == 'buy' else sum(e['catalyst'] and e['tone'] < 0 for e in news['events'])
        opposite_news = news['negative_count'] if key == 'buy' else news['positive_count']
        checks = [
            ('Trend', m.get('available') and m.get('trend') == trend),
            ('Momentum', finite(rsi) and (45 <= rsi <= 65 if key == 'buy' else 35 <= rsi <= 55)),
            ('Volume', finite(m.get('relative_volume')) and m['relative_volume'] >= 1.2),
            ('Pattern', any(p['direction'] == direction for p in patterns) and not any(p['direction'] == ('bearish' if key == 'buy' else 'bullish') for p in patterns)),
            ('News', news_direction > 0 and news['publisher_count'] >= 2 and not opposite_news),
        ]
        blocked = [name+' does not confirm' for name, passed in checks if not passed]
        if not m.get('available') or not 0 <= m.get('age_minutes', float('inf')) <= (7*1440 if daily_basis else 10):
            blocked.append('Chart data missing or stale')
        if not market.get('verified') or not market.get('open'): blocked.append('Market not verified open')
        if daily_basis: blocked.append('Daily chart watchlist; confirm today’s intraday chart after open')
        if not (rec.get('quote') or {}).get('live'): blocked.append('Fresh underlying quote required')
        if rec.get('demo'): blocked.append('Synthetic data')
        if not contract: blocked.append('No liquid listed contract on this side')
        lot = contract.get('lot_size') if contract else None
        if not finite(lot) or lot <= 0 or int(lot) != lot:
            lot = None; blocked.append('Lot size unverified')
        if contract:
            try:
                age = (now-datetime.fromisoformat(contract['as_of'])).total_seconds()
                if not 0 <= age <= 60: raise ValueError()
            except (KeyError, TypeError, ValueError): blocked.append('Fresh contract exchange timestamp required')
        entry = stop = target = None
        atr = m.get('atr14'); price = m.get('price')
        if all(finite(v) and v > 0 for v in (atr, price, levels.get('support'), levels.get('resistance'))):
            entry = round(max(price, levels['resistance'])*1.001, 2) if key == 'buy' else round(min(price, levels['support'])*.999, 2)
            stop = round(entry + (-1 if key == 'buy' else 1)*1.5*atr, 2)
            target = round(entry + (1 if key == 'buy' else -1)*3*atr, 2)
            if min(entry, stop, target) <= 0: entry = stop = target = None
        if entry is None: blocked.append('Underlying trigger levels unavailable')
        # Greeks, costs and expiry exposure have not been strategy-tested.
        blocked.append('Premium stop/target and expiry risk need broker review')
        sources = [e for e in news['events'] if e['catalyst'] and (e['tone'] > 0 if key == 'buy' else e['tone'] < 0)]
        results[key] = {'action': 'BUY CALL (CE)' if key == 'buy' else 'BUY PUT (PE)',
            'direction': direction, 'status': 'WATCH ONLY' if m.get('trend') == trend and contract else 'WAIT',
            'score': sum(bool(passed) for _, passed in checks), 'checks_total': len(checks),
            'reasons': [trend+' underlying trend', next((p['name'] for p in patterns if p['direction'] == direction), 'No confirming pattern')],
            'news': [{'title': e['title'], 'url': e['url']} for e in sources[:1]],
            'contract': contract, 'lot_size': lot,
            'one_lot_cost': round(contract['ask']*lot, 2) if contract and lot else None,
            'premium_risk': 'Entire premium paid plus costs can be lost; underlying stop does not cap premium loss.',
            'underlying_entry': entry, 'underlying_stop': stop, 'underlying_target': target,
            'trigger': 'Above' if key == 'buy' else 'Below', 'blocked_by': blocked,
            'holding': holding_plan(rec, 'intraday', now), 'chart_basis': 'Daily' if daily_basis else '5-minute',
            'chart_as_of': m.get('as_of'),
            'expires_at': now.replace(hour=15,minute=15,second=0,microsecond=0).isoformat()}
    return results
