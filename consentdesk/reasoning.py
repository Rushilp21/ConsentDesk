"""Evidence synthesis with provenance, contradictions and explicit missing checks.

No learned-news claim: this layer is a reproducible analyst checklist, not a
probability model. Summaries are provider text, never executable instructions.
"""
from datetime import datetime
import re
from urllib.parse import urlparse
from .providers import now_ist, finite

CATALYSTS={
    'Results':r'earnings|quarterly|net profit|revenue|margin|financial results',
    'Orders / expansion':r'order win|wins?\b|contract|capacity|expansion|commission',
    'Regulatory':r'approval|fda\b|regulator|licen[cs]e|policy|rbi\b',
    'Risk event':r'fraud|probe|penalty|lawsuit|recall|default|downgrade|loss|misses',
    'Corporate action':r'dividend|buyback|stock split|bonus issue|rights issue',
}


def news_review(articles, now=None):
    now=now or now_ist();events=[];seen=set();weighted=0;weight=0
    for article in articles:
        if article.get('synthetic'):continue
        try:age=(now-datetime.fromisoformat(article['published_at'])).total_seconds()/3600
        except (KeyError,ValueError,TypeError):continue
        if not 0<=age<=14*24:continue
        title=article.get('title','');normal=re.sub(r'[^a-z0-9]','',title.rsplit(' - ',1)[0].lower())
        if not normal or normal in seen:continue
        seen.add(normal);text=title+' '+article.get('summary','')
        categories=[k for k,p in CATALYSTS.items() if re.search(p,text,re.I)]
        quote_page=bool(re.search(r'(share|stock) price today|price on nse/bse',title,re.I))
        tone=article.get('sentiment',0);w=1/(1+age/24)
        if not quote_page:weighted+=tone*w;weight+=w
        events.append({'id':article.get('id'),'title':title,'summary':article.get('summary','')[:500],
            'url':article.get('url'),'publisher':article.get('publisher'),'published_at':article['published_at'],
            'age_hours':round(age,1),'categories':categories,'tone':tone,'catalyst':bool(categories and not quote_page),
            'scope':article.get('scope','headline only')})
    publishers={e['publisher'] for e in events if e['publisher'] and e['catalyst']}
    positive=[e for e in events if e['tone']>0 and e['catalyst']];negative=[e for e in events if e['tone']<0]
    return {'events':events[:8],'weighted_tone':round(weighted/weight,2) if weight else None,
        'catalyst_count':sum(e['catalyst'] for e in events),'publisher_count':len(publishers),
        'positive_count':len(positive),'negative_count':len(negative),'conflicting':bool(positive and negative),
        'corroboration':'Multiple publisher labels; independent reporting not verified' if len(publishers)>=2 else 'Independent corroboration missing',
        'note':'Headline/provider-summary analysis; full articles and economic impact are not independently verified.'}


def analyse(rec,mode,daily_bundle=None):
    from .analysis import indicators
    m=rec.get('metrics',{});structure=rec.get('chart_structure',{});news=news_review(rec.get('news',[]))
    daily=indicators(daily_bundle,'longterm') if daily_bundle and daily_bundle.get('candles') else None
    checks=[];support=[];opposition=[]
    def check(name,passed,detail,missing=False):
        checks.append({'name':name,'state':'MISSING' if missing else 'SUPPORTS' if passed else 'CONFLICTS','detail':detail})
        (support if passed and not missing else opposition).append(detail)
    check('Trend',m.get('trend')=='Bullish',f"Chart trend: {m.get('trend','unavailable')}",not m.get('available'))
    rsi=m.get('rsi14');check('Momentum',finite(rsi) and 45<=rsi<=65,f'RSI {rsi if rsi is not None else "unavailable"}',not finite(rsi))
    vol=m.get('relative_volume');check('Participation',finite(vol) and vol>=1.2,f'Volume {vol if vol is not None else "unavailable"}× prior-bar average',not finite(vol))
    if mode=='intraday':
        check('Daily alignment',bool(daily and daily.get('trend')=='Bullish'),f'Daily trend: {(daily or {}).get("trend","unavailable")}',not daily or not daily.get('available'))
        vwap=m.get('vwap');check('VWAP',finite(vwap) and m.get('price',0)>vwap,'Above session VWAP' if finite(vwap) and m.get('price',0)>vwap else 'VWAP not confirmed',not finite(vwap))
    patterns=structure.get('patterns',[]);bull=[p for p in patterns if p['direction']=='bullish'];bear=[p for p in patterns if p['direction']=='bearish']
    check('Pattern',bool(bull and not bear),' · '.join(p['name']+' ('+p['direction']+')' for p in patterns[:3]) or 'No confirmed chart pattern',not patterns)
    check('News catalyst',news['positive_count']>0 and news['publisher_count']>=2 and not news['negative_count'],
          f"{news['catalyst_count']} catalyst headlines · {news['publisher_count']} publisher labels"+(' · conflicting news' if news['conflicting'] else ''),not news['events'])
    if mode=='longterm':
        f=rec.get('fundamentals') or {};check('Financial quality',bool(f.get('complete') and f.get('earnings_growth_pct',-1)>0 and f.get('operating_cash_flow',-1)>0),
            'Source-linked financials available' if f.get('complete') else 'Financials/valuation incomplete',not f.get('complete'))
    oi=(rec.get('oi_analysis') or {}).get('expiries',[])
    context=rec.get('decision_context') or {};opposition=list(dict.fromkeys([*context.get('blocked_by',[]),*opposition,*rec.get('risks',[])[:2]]))
    score=sum(c['state']=='SUPPORTS' for c in checks);coverage=sum(c['state']!='MISSING' for c in checks)
    sources=[{'id':e['id'],'title':e['title'],'url':e['url'],'published_at':e['published_at']} for e in news['events'][:3]]
    rec['deep_analysis']={'checks':checks,'support':support[:3],'counterarguments':opposition[:4],
        'news':news,'timeframe_alignment':{'selected':m.get('trend'),'daily':(daily or {}).get('trend')},
        'pattern_summary':' · '.join(p['name']+' ('+p['direction']+')' for p in patterns[:2]) or 'No confirmed pattern',
        'evidence_score':score,'checks_total':len(checks),'checks_observed':coverage,'sources':sources,
        'oi_note':f"PCR {oi[0].get('pcr_oi')} for {oi[0].get('expiry')}; positioning only, not direction" if oi else 'OI unavailable for this stock/expiry',
        'invalidation':(rec.get('scenario') or {}).get('invalidation'),
        'method':'Evidence checklist, not profit probability. News/OI are not trained features. Missing or contradictory evidence can produce WAIT.'}
    rec['brief_reasons']=(support[:1]+([news['events'][0]['title'][:130]] if news['events'] else ['No current news evidence']))[:2]
    return rec


def rank_key(rec):
    deep=rec.get('deep_analysis') or {};m=rec.get('metrics') or {}
    usable=bool(m.get('available') and m.get('age_minutes',float('inf'))<=7*1440 and not rec.get('demo'))
    return (not usable,rec.get('daily_action')=='wait' or rec.get('status','').startswith(('WAIT','SESSION')),
            -deep.get('evidence_score',0),-rec.get('score',0),rec['symbol'])
