"""Interpretable pattern and level detection, without predictive claims."""
from datetime import datetime
import math
from .providers import IST
from .learning import completed_rows

def chart_structure(bundle,mode,now=None):
    rows=completed_rows(bundle.get('candles',[]),mode,now)
    if mode!='longterm' and rows:
        day=datetime.fromtimestamp(rows[-1]['t'],IST).date()
        rows=[r for r in rows if datetime.fromtimestamp(r['t'],IST).date()==day]
    if len(rows)<20:
        return {'available':False,'reason':'Need 20 completed bars for levels and patterns.'}
    recent=rows[-60:];last=recent[-1];prior=recent[-2]
    peaks=[recent[i]['h'] for i in range(2,len(recent)-2) if recent[i]['h']>max(r['h'] for r in recent[i-2:i]+recent[i+1:i+3])]
    troughs=[recent[i]['l'] for i in range(2,len(recent)-2) if recent[i]['l']<min(r['l'] for r in recent[i-2:i]+recent[i+1:i+3])]
    support=max([v for v in troughs if v<last['c']],default=min(r['l'] for r in recent[-20:]))
    resistance=min([v for v in peaks if v>last['c']],default=max(r['h'] for r in recent[-20:]))
    levels={'support':round(support,2),'resistance':round(resistance,2),'range_low':round(min(r['l'] for r in recent[-20:]),2),'range_high':round(max(r['h'] for r in recent[-20:]),2)}
    detected=[]
    high=max(r['h'] for r in recent[-21:-1]);low=min(r['l'] for r in recent[-21:-1])
    if last['c']>high:detected.append({'name':'20-bar breakout','direction':'bullish','evidence':'Completed close above prior 20-bar high; check volume and retest.'})
    if last['c']<low:detected.append({'name':'20-bar breakdown','direction':'bearish','evidence':'Completed close below prior 20-bar low.'})
    if last['c']>last['o'] and prior['c']<prior['o'] and last['o']<=prior['c'] and last['c']>=prior['o']:
        detected.append({'name':'Bullish engulfing body','direction':'bullish','evidence':'Last bullish candle body contains the previous bearish body.'})
    if last['c']<last['o'] and prior['c']>prior['o'] and last['o']>=prior['c'] and last['c']<=prior['o']:
        detected.append({'name':'Bearish engulfing body','direction':'bearish','evidence':'Last bearish body contains the previous bullish body.'})
    body=abs(last['c']-last['o']);span=last['h']-last['l'];lower=min(last['o'],last['c'])-last['l'];upper=last['h']-max(last['o'],last['c'])
    if span>0 and body/span<=.1:detected.append({'name':'Doji','direction':'neutral','evidence':'Small candle body; indecision, not a directional signal.'})
    elif body>0 and lower>=2*body and upper<=body:detected.append({'name':'Long lower wick','direction':'bullish','evidence':'Price rejection below the body; requires trend and volume context.'})
    if len(peaks)>=2 and len(troughs)>=2:
        direction='bullish' if peaks[-1]>peaks[-2] and troughs[-1]>troughs[-2] else 'bearish' if peaks[-1]<peaks[-2] and troughs[-1]<troughs[-2] else 'mixed'
        detected.append({'name':'Swing structure','direction':direction,'evidence':'Comparison of the two most recent confirmed swing highs and lows.'})
    return {'available':True,'levels':levels,'patterns':detected,'as_of':datetime.fromtimestamp(last['t'],IST).isoformat(),
            'note':'Detected geometric patterns and pivot levels are observations, not proof of future returns. Pivots require two later completed bars to confirm.'}

def oi_summary(chain):
    by_expiry={}
    timestamps=[]
    for row in chain.get('rows',[]):
        expiry=row.get('expiry')
        if not expiry:continue
        totals=by_expiry.setdefault(expiry,{'call_oi':0,'put_oi':0,'call_volume':0,'put_volume':0,'strikes':[],'call_wall':None,'put_wall':None,'call_oi_change':0,'put_oi_change':0,'changes_available':False})
        wall_c,wall_p=0,0
        for side,field in [('call','call_options'),('put','put_options')]:
            option=row.get(field) or {}
            data=option.get('market_data') or {}
            if option.get('as_of'):timestamps.append(option['as_of'])
            oi=data.get('oi');volume=data.get('volume');previous=data.get('prev_oi')
            if isinstance(oi,(int,float)) and math.isfinite(oi) and oi>=0:
                totals[side+'_oi']+=oi
                if isinstance(previous,(int,float)) and math.isfinite(previous) and previous>=0:
                    totals[side+'_oi_change']+=oi-previous;totals['changes_available']=True
                current=totals[side+'_wall']
                if current is None or oi>current['oi']:
                    totals[side+'_wall']={'strike':row.get('strike_price'),'oi':oi}
            if isinstance(volume,(int,float)) and math.isfinite(volume) and volume>=0:totals[side+'_volume']+=volume
        totals['strikes'].append(row.get('strike_price'))
    result=[]
    for expiry,totals in sorted(by_expiry.items()):
        result.append({'expiry':expiry,**totals,'pcr_oi':round(totals['put_oi']/totals['call_oi'],3) if totals['call_oi'] else None,
                       'pcr_volume':round(totals['put_volume']/totals['call_volume'],3) if totals['call_volume'] else None})
    return {'expiries':result,'source':chain.get('source'),'as_of':chain.get('fetched_at'),'coverage':chain.get('coverage','Returned option-chain strikes only'),
            'oldest_contract_packet':min(timestamps) if timestamps else None,'latest_contract_packet':max(timestamps) if timestamps else None,
            'interpretation':'High put OI may mark positioning near a support area and high call OI near a resistance area, but OI alone cannot distinguish buyer from writer or prove direction. Do not treat PCR as a standalone buy/sell rule.'}
