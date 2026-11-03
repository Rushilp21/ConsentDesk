"""Completed-daily market regime, aligned relative strength and event flags."""
from datetime import datetime, timedelta
from .providers import IST, finite, now_ist
from .learning import completed_rows
from .config import UNIVERSE
from .net import ProviderError
from .sectors import SECTORS, metadata


def regime(bundle,now=None):
    rows=completed_rows(bundle.get('candles',[]),'longterm',now)
    if len(rows)<60:return {'available':False,'label':'INSUFFICIENT HISTORY'}
    now=now or now_ist()
    if not 0<=(now.timestamp()-rows[-1]['t'])/86400<=7:return {'available':False,'label':'STALE HISTORY'}
    tr=[];plus=[];minus=[]
    for previous,current in zip(rows,rows[1:]):
        up=current['h']-previous['h'];down=previous['l']-current['l']
        plus.append(max(up,0) if up>down else 0);minus.append(max(down,0) if down>up else 0)
        tr.append(max(current['h']-current['l'],abs(current['h']-previous['c']),abs(current['l']-previous['c'])))
    smoothed=[sum(values[:14]) for values in (tr,plus,minus)];dx=[]
    for i in range(14,len(tr)):
        smoothed=[old-old/14+values[i] for old,values in zip(smoothed,(tr,plus,minus))]
        total=smoothed[1]+smoothed[2];dx.append(abs(smoothed[1]-smoothed[2])/total*100 if total else 0)
    adx=sum(dx[:14])/14
    for value in dx[14:]:adx=(adx*13+value)/14
    sma20=sum(r['c'] for r in rows[-20:])/20;sma50=sum(r['c'] for r in rows[-50:])/50
    direction='UP' if rows[-1]['c']>sma20>sma50 else 'DOWN' if rows[-1]['c']<sma20<sma50 else 'MIXED'
    label='TRENDING '+direction if adx>=25 and direction!='MIXED' else 'RANGE / MIXED'
    return {'available':True,'label':label,'adx14':round(adx,1),'direction':direction,'as_of':datetime.fromtimestamp(rows[-1]['t'],IST).isoformat(),
            'source':bundle.get('source'),'note':'Completed daily regime; not an intraday prediction.'}


def relative_strength(bundle,benchmark,now=None):
    def closes(value):return {str(datetime.fromtimestamp(r['t'],IST).date()):r['c'] for r in completed_rows(value.get('candles',[]),'longterm',now)}
    stock,index=closes(bundle),closes(benchmark);common=sorted(set(stock)&set(index))
    if len(common)<21:return None
    first,last=common[-21],common[-1]
    if (now or now_ist()).date()-datetime.fromisoformat(last).date()>timedelta(days=7):return None
    sr=(stock[last]/stock[first]-1)*100;br=(index[last]/index[first]-1)*100
    return {'stock_return_pct':round(sr,2),'benchmark_return_pct':round(br,2),'excess_return_pp':round(sr-br,2),
            'from':first,'to':last,'intervals':20,'aligned_dates':True}


def event_flags(events,news,now=None):
    now=now or now_ist();end=now.date()+timedelta(days=3)
    upcoming=[e for e in events if e.get('status')!='CANCELLED' and str(now.date())<=e['date']<=str(end)]
    headlines=[{'title':a['title'],'url':a.get('url'),'published_at':a.get('published_at')}
               for a in news if any(word in a['title'].lower() for word in ('results','earnings','dividend','stock split','rights issue','rbi','rate decision'))][:3]
    return {'upcoming':upcoming[:5],'news_flags':headlines,
            'high_event_risk':any(e['kind'] in ('earnings','policy','market_closed') for e in upcoming),
            'note':'News flags do not establish future event dates. Calendar coverage depends on sourced/manual records.'}


def enrich(report,providers,desk):
    if report.get('demo') or not report.get('recommendations'):return report
    if getattr(getattr(providers,'settings',None),'upstox_token',None):
        try:desk.save_provider_events(providers.market_holidays())
        except ProviderError:pass
    try:benchmark=providers.candles('NIFTY','longterm');market=regime(benchmark)
    except ProviderError:benchmark={'candles':[]};market={'available':False,'label':'MARKET HISTORY UNAVAILABLE'}
    report['market_context']={**report.get('market_context',{}),'regime':market}
    now=now_ist()
    for r in report.get('recommendations',[]):
        try:stock=providers.candles(r['symbol'],'longterm');relative=relative_strength(stock,benchmark)
        except ProviderError:relative=None
        sector=r.get('sector');peers=[s for s,m in UNIVERSE.items() if m['sector']==sector and s!=r['symbol']][:2]
        if not peers:
            peers=next(([s for s,_ in members if s!=r['symbol']][:2] for label,members in SECTORS.values() if any(s==r['symbol'] for s,_ in members)),[])
        sector_values=[]
        for peer in peers:
            try:
                value=relative_strength(providers.candles(peer,'longterm'),benchmark)
                if value:sector_values.append({'symbol':peer,**value})
            except ProviderError:pass
        sector_return=sum(p['stock_return_pct'] for p in sector_values)/len(sector_values) if sector_values else None
        events=desk.events(r['symbol'])
        # Listed option expiries are events, never a guessed weekly calendar.
        for expiry in (r.get('oi_analysis') or {}).get('expiries',[]):
            events.append({'symbol':r['symbol'],'kind':'expiry','date':expiry['expiry'],'title':'Listed options expiry','source':'Broker option chain','reviewed':False})
        flags=event_flags(events,r.get('news',[]));blocked=[]
        if report['mode']=='intraday':
            if market.get('label')=='TRENDING DOWN':blocked.append('Nifty daily regime is trending down; long setup held for review.')
            if any(e['date']==str(now.date()) and e['kind'] in ('earnings','policy','market_closed') for e in flags['upcoming']):blocked.append('A sourced high-impact event is scheduled today; setup held for review.')
        r['decision_context']={'market_regime':market,'relative_strength':relative,
            'sector_peers':sector_values,'sector_return_pct':round(sector_return,2) if sector_return is not None else None,
            'sector_note':'Equal-weight return of up to two known same-sector peers; not a sector index.' if sector_values else 'Sector peer history unavailable.',
            'events':flags,'filter_status':'WAIT' if blocked else 'CAUTION' if flags['high_event_risk'] else 'REVIEW', 'blocked_by':blocked}
        if blocked:
            r['risks']=blocked+r.get('risks',[]);r['status']='WAIT · MARKET / EVENT FILTER';r['daily_action']='wait';r['brief_risk']=blocked[0]
            if r.get('plan'):r['plan']={'status':'WAIT','blocked_by':blocked+(r['plan'].get('blocked_by') or [])}
    return report
