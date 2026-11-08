"""News-led discovery across NSE listings and a concise, conditional daily brief."""
from datetime import datetime
import math
import re
import time
import copy
from .config import UNIVERSE
from .providers import now_ist, IST, ProviderError
from .learning import completed_rows
from .analysis import indicators
from .sectors import SECTORS, sector_value
from .reasoning import rank_key, analyse
from .trade_views import holding_plan, option_views
from .analysis import analyse_options

BRIEF_VERSION=2
EXTENDED=['BEL','HAL','BHEL','TATASTEEL','JSWSTEEL','VEDL','HINDALCO','TATAPOWER','NTPC','ONGC','COALINDIA','DIXON','PERSISTENT','COFORGE','TRENT','TITAN','MARUTI','M&M','EICHERMOT','BAJFINANCE','CIPLA','DRREDDY','APOLLOHOSP','INDIGO','IRCTC']

def normalize(text):
    text=re.sub(r'\b(limited|ltd|industries|industry|corporation|corp|india|company|co|the)\b',' ',text.lower())
    return re.sub(r'[^a-z0-9]+',' ',text).strip()

def discover(instruments,articles,seeds,limit):
    catalogue={i['tradingsymbol']:i.get('name') or i['tradingsymbol'] for i in instruments if i.get('instrument_type')=='EQ' and re.fullmatch(r'[A-Z][A-Z0-9&-]{0,19}',i.get('tradingsymbol',''))}
    for symbol,meta in UNIVERSE.items():
        if symbol not in ('NIFTY','BANKNIFTY'):catalogue.setdefault(symbol,meta['name'])
    matches={}
    # Auto-generated quote pages are not company news catalysts.
    articles=[a for a in articles if not re.search(r'(share|stock) price today|price on nse/bse',a['title'],re.I)]
    headlines=[(a,' '+normalize(a['title'])+' ') for a in articles]
    for symbol,name in catalogue.items():
        aliases=[normalize(name)]
        aliases.extend(normalize(a) for a in UNIVERSE.get(symbol,{}).get('aliases',[]))
        aliases=[a for a in aliases if len(a)>=5]
        for article,title in headlines:
            named=any(' '+alias+' ' in title for alias in aliases)
            ticker=len(symbol)>=3 and bool(re.search(r'(?<!\w)'+re.escape(symbol)+r'(?!\w)',article['title']))
            if symbol=='BSE':
                ticker=bool(re.search(r'\bBSE(?:\s+(?:Ltd|Limited|shares?|stock|profit|earnings|dividend)|[’\x27]s\s+(?:profit|earnings|revenue))',article['title'],re.I))
            if named or ticker:matches.setdefault(symbol,[]).append(article)
    ranked=sorted(matches,key=lambda s:(-len(matches[s]),-sum(a.get('sentiment',0) for a in matches[s]),s))
    chosen=list(dict.fromkeys([*ranked,*seeds,*(s for s in EXTENDED if s in catalogue)]))[:limit]
    return {'symbols':chosen,'names':catalogue,'matches':matches,'listed_equities':len(catalogue),'news_matched_symbols':len(matches),'headlines':len(articles),
            'note':f'News names matched against {len(catalogue)} NSE listings; {len(chosen)} candidates deeply screened. Headlines are a bounded feed, not complete all-market coverage.'}

def morning_levels(bundle,now=None):
    rows=completed_rows(bundle.get('candles',[]),'longterm',now)
    if len(rows)<25:return None
    now=now or now_ist();last=rows[-1]
    if not 0 <= (now.timestamp()-last['t'])/86400 <= 7:return None
    atr=sum(max(rows[j]['h']-rows[j]['l'],abs(rows[j]['h']-rows[j-1]['c']),abs(rows[j]['l']-rows[j-1]['c'])) for j in range(len(rows)-14,len(rows)))/14
    if atr<=0:return None
    entry=math.ceil(last['h']*1.001*100)/100
    stop=math.floor(max(last['l'],entry-.5*atr)*100)/100
    if not 0<stop<entry:return None
    target=round(entry+2*(entry-stop),2)
    turnover=sum(r['c']*r['v'] for r in rows[-20:])/20
    return {'entry':entry,'invalidation':stop,'target':target,'risk_reward':2,'as_of':datetime.fromtimestamp(last['t'],IST).isoformat(),
            'average_daily_turnover':round(turnover),'status':'PRE-OPEN CONDITIONAL LEVELS','reference':'Previous completed daily high/low and 14-day ATR',
            'trigger':'After 09:15: fresh broker quote + volume-backed break above entry. Skip large opening gaps; do not chase.',
            'gap_skip_above':round(entry+atr,2),'expires_at':now.replace(hour=15,minute=15,second=0,microsecond=0).isoformat(),
            'not_actionable_reason':'Opening price, spread and market session still need confirmation. Stop fills can slip.'}

class DailyBrief:
    def __init__(self,agent,settings):self.agent=agent;self.providers=agent.providers;self.settings=settings

    def run(self,mode,progress=None,sector='all',discovery_override=None,candidate_symbols=None):
        if mode not in ('intraday','options','longterm'):raise ValueError('Unsupported daily mode')
        sector_value(sector)
        start=time.monotonic();now=now_ist();trace=[];gaps=[]
        def step(who,detail,status='done'):
            event={'agent':who,'detail':detail,'status':status,'elapsed_ms':round((time.monotonic()-start)*1000)}
            trace.append(event)
            if progress:progress(event)
        if discovery_override is None:
            try:instruments=self.providers.kite.instruments()
            except (ProviderError,OSError):instruments=[];gaps.append('NSE catalogue unavailable; discovery limited to known symbols.')
            try:articles=self.providers.market_news()
            except ProviderError:articles=[];gaps.append('Market news discovery unavailable; using a broader fallback universe.')
            discovery=discover(instruments,articles,self.settings.daily_symbols,self.settings.daily_candidate_limit)
            if sector!='all':
                label,members=SECTORS[sector];membership={s for s,_ in members}
                symbols=[s for s in discovery['symbols'] if s in membership]
                discovery['symbols']=list(dict.fromkeys([*symbols,*(s for s,_ in members)]))[:12]
                discovery['names'].update(dict(members))
                discovery['note']=f'{label}: {len(discovery["symbols"])} explicitly classified stocks screened; bounded curated sector/theme universe, not all listed companies.'
            if mode=='options' and sector=='all':
                discovery['symbols']=list(dict.fromkeys(['NIFTY','BANKNIFTY',*discovery['symbols']]))[:self.settings.daily_candidate_limit]
        else:
            discovery=copy.deepcopy(discovery_override)
        if candidate_symbols is not None:
            discovery['symbols']=[s for s in candidate_symbols if s in discovery['symbols']]
        step('News discovery',discovery['note'],'limited' if gaps else 'done')
        try:
            benchmark=indicators(self.providers.candles('NIFTY','longterm'),'longterm',now)
            market_context={k:benchmark.get(k) for k in ('available','trend','rsi14','price','as_of')}
        except (ProviderError,KeyError):
            market_context={'available':False,'trend':'Unverified'}
            gaps.append('Nifty market trend unavailable.')
        step('Market context','Nifty completed-daily trend: '+str(market_context.get('trend') or 'Unverified'))
        before_open=(now.hour,now.minute)<(9,15);after_close=(now.hour,now.minute)>=(15,30)
        # Before 25 five-minute bars exist, retain a completed-daily watchlist.
        # Opening confirmation is performed separately by the live alert checks.
        daily_basis=mode in ('intraday','options') and ((now.hour,now.minute)<(11,20) or after_close or now.weekday()>=5)
        records=[];market={'verified':False,'open':False,'label':'Pre-open' if before_open else 'Session ended' if after_close else 'Session unverified'}
        for offset in range(0,len(discovery['symbols']),6):
            chunk=discovery['symbols'][offset:offset+6]
            report=self.agent.run('Morning '+('longterm' if daily_basis else mode)+' research '+', '.join(s+'.NS' for s in chunk),
                                  'longterm' if daily_basis else mode,chunk,50000,1,False,[],progress=lambda e:step(e['agent'],e['detail'],e['status']))
            market=report['market'];records.extend(report['recommendations'])
        for rec in records:
            rec['name']=discovery['names'].get(rec['symbol'],rec['name'])
            rec['discovered_from_news']=bool(discovery['matches'].get(rec['symbol']))
            if daily_basis and mode=='intraday':
                try:levels=morning_levels(self.providers.candles(rec['symbol'],'longterm'),now)
                except ProviderError:levels=None
                rec['scenario']=levels;rec['plan']=None;rec['prediction']=None
                tone=sum(a.get('sentiment',0) for a in discovery['matches'].get(rec['symbol'],[]))
                valid=levels and levels['average_daily_turnover']>=50_000_000 and rec['discovered_from_news'] and tone>0 and rec['metrics'].get('trend')!='Bearish' and rec['score']>=50
                if market_context.get('trend')=='Bearish':valid=False
                rec['status']='SESSION ENDED' if after_close else 'WATCH · CONFIRM AT OPEN' if valid else 'WAIT · WEAK OR MISSING EVIDENCE'
                rec['daily_action']='watch' if valid and before_open else 'wait'
                if now.weekday()>=5:
                    rec.update(status='MARKET CLOSED · RESEARCH ONLY',daily_action='wait',scenario=None)
            else:
                rec['daily_action']='conditional' if rec['status']=='CONDITIONAL SETUP' else 'review' if rec['status']=='LONG-TERM REVIEW CANDIDATE' else 'wait'
            if sector!='all':rec['sector']=SECTORS[sector][0]
            analyse(rec,mode)
            rec['brief_reasons']=rec.get('brief_reasons') or [r[:150] for r in rec['reasons'][:2]]
            rec['brief_risk']=next(iter(rec['risks']),'Recheck opening conditions and data freshness.')[:160]
            if mode=='intraday' and market_context.get('trend')=='Bearish':
                rec['brief_risk']='Nifty daily trend is bearish; long entries need stronger confirmation.'
            if rec['discovered_from_news']:rec['brief_reasons'].insert(0,f"News: {discovery['matches'][rec['symbol']][0]['title'][:85]}")
            rec['brief_reasons']=rec['brief_reasons'][:2]
        if hasattr(self.providers,'desk'):
            from .context import enrich
            enrich({'mode':mode,'demo':False,'recommendations':records},self.providers,self.providers.desk)
            for rec in records:
                daily=None
                if mode=='intraday':
                    try:daily=self.providers.candles(rec['symbol'],'longterm')
                    except ProviderError:pass
                analyse(rec,mode,daily)
                if daily_basis:rec['deep_analysis']['basis']='Pre-open/after-close review uses completed daily bars; intraday confirmation is still required.'
        records.sort(key=rank_key)
        for rec in records:
            rec['holding_plan']=holding_plan(rec,mode,now)
            if mode=='options':
                if not rec.get('option_analysis'):
                    try:rec['option_analysis']=analyse_options(self.providers.options(rec['symbol'],'current_week'),market,now)
                    except ProviderError:rec['option_analysis']={'contracts':[]}
                rec['fno_views']=option_views(rec,market,daily_basis,now)
        selection=records[:3] if sector!='all' else records[:8]
        fno_sections={}
        if mode=='options':
            for side,trend in [('buy','Bullish'),('sell','Bearish')]:
                eligible=[r for r in records if r['metrics'].get('trend')==trend and r['fno_views'][side].get('contract')]
                eligible.sort(key=lambda r:(-r['fno_views'][side]['score'],r['symbol']))
                fno_sections[side]=eligible[:3]
            selection=list({r['symbol']:r for items in fno_sections.values() for r in items}.values())
        if not any(r.get('metrics',{}).get('available') for r in records):
            raise ProviderError('No usable price history returned; previous completed sector brief retained')
        step('Morning brief',f'{len(records)} unique stocks screened; {len(selection)} shown')
        return {'brief_version':BRIEF_VERSION,'sector':sector,'sector_label':SECTORS[sector][0] if sector!='all' else 'All sectors','created_at':now_ist().isoformat(),'question':f"Today's {mode} recommendations · {sector}",'mode':mode,'symbols':[r['symbol'] for r in records],
                'demo':False,'market':market,'recommendations':selection,'fno_sections':fno_sections,'trace':trace,'duration_ms':round((time.monotonic()-start)*1000),
                'capital':50000,'risk_pct':1,'engine':'News discovery + evidence screening','daily_scan':True,'discovery':{k:v for k,v in discovery.items() if k not in ('names','matches')},
                'gaps':gaps,'market_context':market_context,'brief_timing':'PRE-OPEN' if before_open else 'AFTER CLOSE' if after_close else 'IN SESSION',
                'answer':f"{len(records)} stocks screened. Shortlist uses news and chart evidence; opening confirmation is required for intraday ideas.",
                'disclosure':'Conditional research levels, not orders or guaranteed profit. Stop-loss fills can slip; revalidate live quotes.'}
