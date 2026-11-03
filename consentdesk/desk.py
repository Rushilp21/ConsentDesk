"""Persistent paper ledger, risk budgets, sourced events and in-app alerts.

This service never places broker orders. Manual paper fills and automatic exits
from sampled live quotes are explicitly distinguished in the ledger.
"""
from datetime import datetime, timedelta
import json
import math
import re
import uuid
from .config import UNIVERSE
from .sectors import metadata
from .providers import now_ist, IST, finite, safe_url

DEFAULT_LIMITS = {'capital': 50000, 'risk_pct': 1, 'max_open_risk_pct': 3,
                  'daily_loss_pct': 2, 'max_sector_pct': 40,
                  'fee_bps': 5, 'slippage_bps': 5}


def symbol_value(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Z][A-Z0-9&-]{0,19}', value):
        raise ValueError('Enter a valid NSE stock symbol')
    return value


def number(value, name, low, high):
    if not finite(value) or not low <= value <= high:
        raise ValueError(f'{name} must be between {low} and {high}')
    return value


def usable_quote(q, now=None):
    now = now or now_ist()
    try:
        dated = datetime.fromisoformat(q['as_of'])
        return bool(q.get('live') and q.get('broker') and not q.get('synthetic')
                    and finite(q.get('price')) and q['price'] > 0
                    and 0 <= (now-dated).total_seconds() <= 30
                    and dated.astimezone(IST).date() == now.date()
                    and now.weekday() < 5 and (9,15) <= (now.hour,now.minute) < (15,30))
    except (KeyError, ValueError, TypeError):
        return False


def costs(entry, exit_price, quantity, limits):
    buy = entry*(1+limits['slippage_bps']/10000)
    sell = exit_price*(1-limits['slippage_bps']/10000)
    entry_fee = buy*quantity*limits['fee_bps']/10000
    exit_fee = sell*quantity*limits['fee_bps']/10000
    net = (sell-buy)*quantity-entry_fee-exit_fee
    return {'entry_fill': round(buy,4), 'exit_fill': round(sell,4),
            'entry_fee': round(entry_fee,4), 'exit_fee': round(exit_fee,4),
            'fees': round(entry_fee+exit_fee,4),
            'slippage': round(((buy-entry)+(exit_price-sell))*quantity,4),
            'gross_pnl': round((exit_price-entry)*quantity,4), 'net_pnl': round(net,4)}


class TradingDesk:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS desk_settings (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT)')
            db.execute('INSERT OR IGNORE INTO desk_settings VALUES (1,?)', (json.dumps(DEFAULT_LIMITS),))
            for table in ('paper_trades','desk_alerts','desk_events','desk_ideas','desk_quotes','desk_financials','desk_validations'):
                db.execute(f'CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS desk_checks (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT)')

    @staticmethod
    def rows(db, table):
        return [json.loads(r[0]) for r in db.execute(f'SELECT payload FROM {table} ORDER BY rowid DESC')]

    @staticmethod
    def put(db, table, value):
        db.execute(f'INSERT INTO {table} VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                   (value['id'],json.dumps(value,allow_nan=False)))

    def settings(self, db=None):
        if db is not None:
            return {**DEFAULT_LIMITS, **json.loads(db.execute('SELECT payload FROM desk_settings WHERE id=1').fetchone()[0])}
        with self.store.connect() as connection:
            return self.settings(connection)

    def set_limits(self, data):
        ranges = {'capital':(100,100_000_000),'risk_pct':(.1,2),'max_open_risk_pct':(.1,10),
                  'daily_loss_pct':(.1,10),'max_sector_pct':(5,100),'fee_bps':(0,100),'slippage_bps':(0,200)}
        if not isinstance(data,dict) or set(data)-set(ranges):
            raise ValueError('Use the displayed risk settings')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=self.settings(db)
            updated={**current,**{k:number(v,k,*ranges[k]) for k,v in data.items()}}
            if updated['capital'] != current['capital'] and self.rows(db,'paper_trades'):
                raise ValueError('Starting capital is fixed once a paper trade is recorded; risk and costs remain editable')
            db.execute('UPDATE desk_settings SET payload=? WHERE id=1',(json.dumps(updated),))
        return updated

    def summary(self, db, now=None):
        now=now or now_ist();limits=self.settings(db);trades=self.rows(db,'paper_trades')
        open_trades=[t for t in trades if t['status']=='OPEN'];closed=[t for t in trades if t['status']=='CLOSED']
        quotes={q['id']:q for q in self.rows(db,'desk_quotes')}
        realised=sum(t['result']['net_pnl'] for t in closed)
        today=sum(t['result']['net_pnl'] for t in closed if t['closed_at'][:10]==str(now.date()))
        open_cost=sum(t['entry_cost'] for t in open_trades)
        cash=limits['capital']+realised-open_cost;value=0;unrealised=0;open_loss=0
        sectors={};marked=[];missing_marks=0;stale_marks=0
        for t in open_trades:
            q=quotes.get(t['symbol']);price=q.get('price') if q else None
            if not finite(price):price=t['entry'];missing_marks+=1
            if not q or not usable_quote(q,now):stale_marks+=1
            outcome=costs(t['entry'],price,t['quantity'],t['cost_assumptions'])
            value+=t['entry_cost']+outcome['net_pnl'];unrealised+=outcome['net_pnl'];open_loss+=min(0,outcome['net_pnl'])
            sectors[t['sector']]=sectors.get(t['sector'],0)+t['entry_cost']
            marked.append({**t,'mark':price,'mark_as_of':q.get('as_of') if q else None,
                           'mark_label':q.get('label') if q else 'Entry reference only',
                           'mark_live':bool(q and usable_quote(q,now)), 'unrealised_net':round(outcome['net_pnl'],2),
                           'exit_due':t['mode']=='intraday' and now.isoformat()>=t['exit_due_at']})
        open_risk=sum(t['planned_risk'] for t in open_trades)
        loss_used=max(0,-today)+abs(open_loss)
        daily_room=max(0,limits['capital']*limits['daily_loss_pct']/100-loss_used-open_risk)
        peak=limits['capital'];equity=peak;drawdown=0;curve=[]
        for t in sorted(closed,key=lambda t:t['closed_at']):
            equity+=t['result']['net_pnl'];peak=max(peak,equity);drawdown=max(drawdown,peak-equity)
            curve.append({'at':t['closed_at'],'equity':round(equity,2)})
        wins=sum(t['result']['net_pnl']>0 for t in closed)
        return {'limits':limits,'cash':round(cash,2),'realised_net':round(realised,2),'today_realised_net':round(today,2),
                'equity_estimate':round(cash+value,2),'unrealised_net':round(unrealised,2),
                'open_risk':round(open_risk,2),'loss_used':round(loss_used,2),'daily_risk_room':round(daily_room,2),
                'daily_limit_reached':daily_room<=0,'sector_exposure':{k:round(v,2) for k,v in sectors.items()},
                'open_trades':marked,'closed_trades':closed[:100], 'trade_count':len(closed),
                'win_rate':round(wins/len(closed)*100,1) if closed else None,
                'realised_drawdown':round(drawdown,2),'equity_curve':curve,
                'fees_paid':round(sum(t['result']['fees'] for t in closed)+sum(t['entry_fee'] for t in open_trades),2),
                'missing_marks':missing_marks,'stale_marks':stale_marks,
                'note':'Cash-only long stock simulations. P&L includes configured fees and slippage; marks may be delayed. Daily risk room reserves open stop risk and current losses. Stops can gap. Closed-trade drawdown excludes intratrade moves.'}

    def draft(self, data):
        if not isinstance(data,dict):raise ValueError('Enter paper trade details')
        symbol=symbol_value(data.get('symbol'));mode=data.get('mode','intraday')
        if mode not in ('intraday','longterm'):raise ValueError('Paper trades support intraday or long-term cash stocks')
        if UNIVERSE.get(symbol,{}).get('sector')=='Index':raise ValueError('Paper ledger supports cash stocks, not index units or derivatives')
        entry=number(data.get('entry'),'Entry',.01,1_000_000)
        stop=number(data.get('stop'),'Stop',.01,1_000_000)
        target=number(data.get('target'),'Target',.01,1_000_000)
        if not stop<entry<target:raise ValueError('For a long stock plan use stop < entry < target')
        qty=data.get('quantity')
        if qty is not None and (isinstance(qty,bool) or not isinstance(qty,int) or not 1<=qty<=10_000_000):raise ValueError('Quantity must be a positive whole number')
        note=data.get('note','')
        if not isinstance(note,str) or len(note)>500:raise ValueError('Keep the journal note within 500 characters')
        report_id=data.get('report_id')
        if report_id is not None and (not isinstance(report_id,str) or not re.fullmatch(r'[a-f0-9]{32}',report_id)):raise ValueError('Invalid linked report')
        return {'symbol':symbol,'sector':UNIVERSE.get(symbol,metadata(symbol)).get('sector','Unclassified'),
                'mode':mode,'entry':entry,'stop':stop,'target':target,'quantity':qty,'note':note.strip(),'report_id':report_id}

    def preview(self, data, db=None):
        if db is None:
            with self.store.connect() as connection:return self.preview(data,connection)
        d=self.draft(data);s=self.summary(db);limits=s['limits']
        # Group unknown sectors together so unknown metadata cannot bypass exposure limits.
        per_risk=max(.000001,-costs(d['entry'],d['stop'],1,limits)['net_pnl'])
        per_cost=d['entry']*(1+limits['slippage_bps']/10000)*(1+limits['fee_bps']/10000)
        risk_room=min(limits['capital']*limits['risk_pct']/100,
                      max(0,limits['capital']*limits['max_open_risk_pct']/100-s['open_risk']),s['daily_risk_room'])
        sector_room=max(0,limits['capital']*limits['max_sector_pct']/100-s['sector_exposure'].get(d['sector'],0))
        maximum=max(0,min(math.floor(risk_room/per_risk),math.floor(max(0,s['cash'])/per_cost),math.floor(sector_room/per_cost)))
        qty=d['quantity'] if d['quantity'] is not None else maximum
        qty=max(0,qty);outcome=costs(d['entry'],d['target'],qty,limits)
        blocked=[]
        if qty==0:blocked.append('No shares fit the available cash, risk or sector budget.')
        if qty>maximum:blocked.append(f'Quantity exceeds the risk/cash/sector cap of {maximum} shares.')
        if s['daily_limit_reached']:blocked.append('Daily loss/risk budget is exhausted.')
        if len(s['open_trades'])>=20:blocked.append('Close a position before adding more than 20 open paper trades.')
        return {**d,'quantity':qty,'max_quantity':maximum,'planned_risk':round(per_risk*qty,2),
                'capital_required':round(per_cost*qty,2),'net_target_gain':round(outcome['net_pnl'],2),
                'reward_risk_net':round(outcome['net_pnl']/(per_risk*qty),2) if qty else None,
                'blocked_by':blocked,'allowed':not blocked,'cost_assumptions':limits,
                'cash_available':s['cash'],'risk_available':round(risk_room,2),'sector_available':round(sector_room,2),
                'fill_source':'Manual paper simulation; not a broker fill'}

    def open_trade(self, data):
        now=now_ist()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');p=self.preview(data,db)
            if not p['allowed']:raise ValueError(' '.join(p['blocked_by']))
            if p['report_id'] and not db.execute('SELECT id FROM reports WHERE id=?',(p['report_id'],)).fetchone():raise ValueError('Linked research report no longer exists')
            result=costs(p['entry'],p['entry'],p['quantity'],p['cost_assumptions'])
            trade={**p,'id':uuid.uuid4().hex,'status':'OPEN','opened_at':now.isoformat(),
                   'entry_fill':result['entry_fill'],'entry_fee':result['entry_fee'],'entry_cost':p['capital_required'],
                   'exit_due_at':now.replace(hour=15,minute=20,second=0,microsecond=0).isoformat() if p['mode']=='intraday' else None}
            self.put(db,'paper_trades',trade)
        return trade

    def close_in_db(self, db, trade, price, reason, source, packet=None):
        result=costs(trade['entry'],price,trade['quantity'],trade['cost_assumptions'])
        trade.update(status='CLOSED',exit_reference=price,closed_at=now_ist().isoformat(),
                     exit_reason=reason,exit_source=source,exit_packet_at=packet,result=result)
        self.put(db,'paper_trades',trade)
        return trade

    def close_trade(self, data):
        price=number(data.get('price'),'Exit price',.01,1_000_000)
        reason=data.get('reason','Manual exit')
        if not isinstance(reason,str) or len(reason)>120:raise ValueError('Keep the exit reason within 120 characters')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT payload FROM paper_trades WHERE id=?',(data.get('id'),)).fetchone()
            if not row:raise ValueError('Paper trade not found')
            trade=json.loads(row[0])
            if trade['status']!='OPEN':raise ValueError('This paper trade is already closed')
            return self.close_in_db(db,trade,price,reason,'Manual paper simulation')

    def save_ideas(self, report, db=None):
        if report.get('demo'):return
        if db is None:
            with self.store.connect() as connection:return self.save_ideas(report,connection)
        for r in report.get('recommendations',[]):
            self.put(db,'desk_ideas',{'id':report['id']+':'+r['symbol'],'report_id':report['id'],
                'symbol':r['symbol'],'mode':report['mode'],'created_at':report['created_at'],'status':r['status'],
                'daily':report.get('daily_scan',False),'levels':r.get('scenario'),'plan':r.get('plan'),
                'score':r['score'],'reasons':r.get('brief_reasons',r.get('reasons',[])[:2])})

    def add_event(self, data):
        symbol=data.get('symbol','ALL')
        if symbol!='ALL':symbol_value(symbol)
        kind=data.get('kind')
        if kind not in ('earnings','policy','corporate','expiry','market_closed'):raise ValueError('Select a supported event type')
        try:day=datetime.strptime(data['date'],'%Y-%m-%d').date()
        except (KeyError,ValueError,TypeError):raise ValueError('Use a valid event date') from None
        if abs((day-now_ist().date()).days)>730:raise ValueError('Event must be within two years')
        title=data.get('title','')
        if not isinstance(title,str) or not 2<=len(title.strip())<=160 or not safe_url(data.get('source_url')):raise ValueError('Enter a short event title and source link')
        event={'id':uuid.uuid4().hex,'symbol':symbol,'kind':kind,'date':str(day),'title':title.strip(),
               'source_url':data['source_url'],'source':'User-entered source-linked event','reviewed':True,'created_at':now_ist().isoformat()}
        with self.store.connect() as db:self.put(db,'desk_events',event)
        return event

    def save_provider_events(self, events):
        with self.store.connect() as db:
            for event in events:self.put(db,'desk_events',event)

    def events(self, symbol=None):
        with self.store.connect() as db:events=self.rows(db,'desk_events')
        return sorted([e for e in events if not symbol or e['symbol'] in (symbol,'ALL')],key=lambda e:(e['date'],e['title']))

    def cancel(self, table, record_id):
        if table not in ('desk_alerts','desk_events'):raise ValueError('Unsupported record')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute(f'SELECT payload FROM {table} WHERE id=?',(record_id,)).fetchone()
            if not row:raise ValueError('Record not found')
            value=json.loads(row[0]);value['status']='CANCELLED';self.put(db,table,value)
        return value

    def add_alert(self, data):
        symbol=symbol_value(data.get('symbol'));kind=data.get('kind','above')
        if kind not in ('above','below','setup'):raise ValueError('Choose above, below or confirmed setup')
        level=number(data.get('level'),'Alert price',.01,1_000_000)
        now=now_ist();expires=data.get('expires_at') or (now+timedelta(days=7)).isoformat()
        try:
            dated=datetime.fromisoformat(expires)
            if dated.tzinfo is None:dated=dated.replace(tzinfo=IST)
            dated=dated.astimezone(IST)
            if not now<dated<=now+timedelta(days=90):raise ValueError()
        except (ValueError,TypeError):raise ValueError('Alert expiry must be within the next 90 days') from None
        alert={'id':uuid.uuid4().hex,'symbol':symbol,'kind':kind,'level':level,'created_at':now.isoformat(),
               'expires_at':dated.isoformat(),'status':'ARMED','last_check':'Awaiting a fresh broker quote'}
        if kind=='setup':
            report=self.store.get(data.get('report_id'),'common-workspace','common')
            rec=next((r for r in (report or {}).get('recommendations',[]) if r['symbol']==symbol),None)
            if not rec or report['mode']!='intraday' or report.get('demo'):raise ValueError('Link a real intraday research report for a setup alert')
            if report['created_at'][:10]!=str(now.date()) or rec['status'] not in ('WATCH · CONFIRM AT OPEN','CONDITIONAL SETUP'):raise ValueError('Only a current watch or conditional setup can arm a confirmation alert')
            p=rec.get('plan') or {};s=rec.get('scenario') or {}
            entry=p.get('entry_above') if p.get('status')=='CONDITIONAL SETUP' else s.get('entry')
            if not finite(entry):raise ValueError('This report has no valid entry level')
            expiry=now.replace(hour=15,minute=15,second=0,microsecond=0)
            if p.get('status')=='CONDITIONAL SETUP' and p.get('expires_at'):
                expiry=min(expiry,datetime.fromisoformat(p['expires_at']).astimezone(IST))
            alert.update(level=entry,report_id=report['id'],gap_skip_above=s.get('gap_skip_above'),
                         stop=p.get('stop') or s.get('invalidation'),
                         expires_at=min(dated,expiry).isoformat())
            if alert['expires_at']<=now.isoformat():raise ValueError('Intraday setup has expired')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if sum(a['status']=='ARMED' for a in self.rows(db,'desk_alerts'))>=30:raise ValueError('Maximum 30 active alerts')
            self.put(db,'desk_alerts',alert)
        return alert

    def notification(self, db, symbol, text, now):
        self.put(db,'desk_alerts',{'id':uuid.uuid4().hex,'symbol':symbol,'kind':'position','status':'TRIGGERED',
            'created_at':now.isoformat(),'triggered_at':now.isoformat(),'message':text,'level':None})

    def refresh(self, providers):
        now=now_ist()
        with self.store.connect() as db:
            alerts=self.rows(db,'desk_alerts');trades=self.rows(db,'paper_trades')
        active=[a for a in alerts if a['status']=='ARMED'];open_trades=[t for t in trades if t['status']=='OPEN']
        symbols=list(dict.fromkeys([*(a['symbol'] for a in active),*(t['symbol'] for t in open_trades)]))
        quotes={};gaps=[];market={'verified':False,'open':False}
        for start in range(0,len(symbols),12):
            result=providers.quotes(symbols[start:start+12]);quotes.update({q['symbol']:q for q in result['quotes']})
            gaps.extend(result.get('gaps',[]));market=result['market']
        checks={}
        for a in active:
            q=quotes.get(a['symbol']);valid=q and usable_quote(q,now) and market.get('verified') and market.get('open')
            if valid and a['kind']=='setup':
                try:
                    confirmed,message=self.confirm_setup(a,q,providers.candles(a['symbol'],'intraday'),now)
                    if confirmed:
                        from .reasoning import news_review
                        from .analysis import indicators
                        news=news_review(providers.news(a['symbol'],'intraday')['articles'],now)
                        daily=indicators(providers.candles(a['symbol'],'longterm'),'longterm',now)
                        if not daily.get('available') or daily.get('age_minutes',float('inf'))>7*1440:confirmed=False;message='Daily history unavailable/stale; confirmation paused'
                        elif daily.get('trend')=='Bearish':confirmed=False;message='Daily trend conflicts with the intraday breakout'
                        elif news['publisher_count']<2 or not news['positive_count'] or news['negative_count']:confirmed=False;message='Fresh corroborated catalyst missing or opposing news present'
                    checks[a['id']]=(confirmed,message)
                except Exception:checks[a['id']]=(False,'Fresh broker candles unavailable')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for symbol,q in quotes.items():self.put(db,'desk_quotes',{'id':symbol,**q})
            closed_session=any(e.get('status')!='CANCELLED' and e['kind']=='market_closed' and e['date']==str(now.date()) for e in self.rows(db,'desk_events'))
            blocked_events=[e for e in self.rows(db,'desk_events') if e.get('status')!='CANCELLED' and e['date']==str(now.date()) and e['kind'] in ('earnings','policy','market_closed')]
            for a in self.rows(db,'desk_alerts'):
                if a['status']!='ARMED':continue
                if a['expires_at']<=now.isoformat():a.update(status='EXPIRED',last_check='Expiry passed')
                elif a['kind']=='setup' and any(e['symbol'] in ('ALL',a['symbol']) for e in blocked_events):
                    a.update(status='INVALIDATED',last_check='Sourced high-impact event today · generate a new reviewed setup')
                else:
                    q=quotes.get(a['symbol']);valid=q and usable_quote(q,now) and market.get('verified') and market.get('open') and not closed_session
                    if not valid:a['last_check']='Paused · fresh broker quote and open session required'
                    else:
                        if a['kind']=='setup' and (a.get('gap_skip_above') and q['price']>a['gap_skip_above'] or a.get('stop') and q['price']<=a['stop']):
                            a.update(status='INVALIDATED',last_check='Setup price/gap limit breached · generate a new setup')
                            self.put(db,'desk_alerts',a);continue
                        passed=q['price']>=a['level'] if a['kind']=='above' else q['price']<=a['level']
                        message='Price level reached'
                        if a['kind']=='setup':passed,message=checks.get(a['id'],(False,'Confirmation pending'))
                        a.update(last_check=message if a['kind']=='setup' else 'Checked '+q['as_of'],last_price=q['price'],quote_as_of=q['as_of'])
                        if passed:a.update(status='TRIGGERED',triggered_at=now.isoformat(),message=message+' · '+a['symbol']+' '+str(q['price']))
                self.put(db,'desk_alerts',a)
            for t in self.rows(db,'paper_trades'):
                if t['status']!='OPEN':continue
                q=quotes.get(t['symbol'])
                if not q or not usable_quote(q,now) or not market.get('verified') or not market.get('open') or closed_session:continue
                # Never invent a next-day fill for an intraday exit missed while offline.
                if t['mode']=='intraday' and t['opened_at'][:10]!=str(now.date()):continue
                reason='Stop reached' if q['price']<=t['stop'] else 'Target reached' if q['price']>=t['target'] else 'Session exit' if t['mode']=='intraday' and now.isoformat()>=t['exit_due_at'] else None
                if reason:
                    self.close_in_db(db,t,q['price'],reason,'Automatic paper exit from sampled broker quote',q['as_of'])
                    self.notification(db,t['symbol'],reason+' · paper position closed at sampled price '+str(q['price']),now)
            summary=self.summary(db,now)
            if summary['loss_used']>=summary['limits']['capital']*summary['limits']['daily_loss_pct']/100:
                key='loss-limit:'+str(now.date())
                if not db.execute('SELECT id FROM desk_alerts WHERE id=?',(key,)).fetchone():
                    self.put(db,'desk_alerts',{'id':key,'symbol':'ACCOUNT','kind':'risk','status':'TRIGGERED','created_at':now.isoformat(),'triggered_at':now.isoformat(),'level':None,'message':'Daily paper loss limit reached · new positions blocked'})
            state={'checked_at':now.isoformat(),'symbols':len(symbols),'live_quotes':sum(usable_quote(q,now) for q in quotes.values()),'gaps':gaps,
                   'note':'Alerts and automatic paper exits require fresh broker data. Sampled snapshots can miss crossings; no orders are sent.'}
            db.execute('INSERT INTO desk_checks VALUES (1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(json.dumps(state),))
        return state

    @staticmethod
    def confirm_setup(alert,q,bundle,now):
        if not bundle.get('broker') or bundle.get('synthetic'):return False,'Broker candle confirmation unavailable'
        rows=[r for r in bundle.get('candles',[]) if r['t']+300<=now.timestamp()]
        session=[r for r in rows if datetime.fromtimestamp(r['t'],IST).date()==now.date()]
        if len(session)<4 or not rows or now.timestamp()-(session[-1]['t']+300)>300:return False,'Wait for opening range and a fresh completed breakout bar'
        if alert.get('gap_skip_above') and q['price']>alert['gap_skip_above']:return False,'Gap too large · do not chase'
        bid,ask=q.get('bid'),q.get('ask')
        if not finite(bid) or not finite(ask) or not 0<bid<=ask:return False,'Bid/ask spread unavailable'
        if (ask-bid)/((ask+bid)/2)*100>.3:return False,'Spread too wide'
        prior=[r for r in rows if r['t']<session[-1]['t']][-20:]
        average=sum(r['v'] for r in prior)/len(prior) if len(prior)==20 else 0
        volume=sum(r['v'] for r in session)
        vwap=sum((r['h']+r['l']+r['c'])/3*r['v'] for r in session)/volume if volume>0 else None
        opening_high=max(r['h'] for r in session[:3])
        if not average or session[-1]['v']/average<1.2:return False,'Volume below 1.2× prior-bar average'
        if session[-1]['c']<=max(alert['level'],opening_high) or not vwap or q['price']<=vwap or q['price']<alert['level']:return False,'Wait for completed breakout above opening range and VWAP'
        return True,'Opening range, volume, VWAP and spread confirmed · recheck before trading'

    def snapshot(self):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for alert in self.rows(db,'desk_alerts'):
                if alert['status']=='ARMED' and alert['expires_at']<=now_ist().isoformat():
                    alert.update(status='EXPIRED',last_check='Expiry passed');self.put(db,'desk_alerts',alert)
            result=self.summary(db);result['alerts']=self.rows(db,'desk_alerts')[:100]
            today=str(now_ist().date());end=str(now_ist().date()+timedelta(days=30))
            result['events']=sorted([e for e in self.rows(db,'desk_events') if e.get('status')!='CANCELLED' and today<=e['date']<=end],key=lambda e:e['date'])[:100]
            result['ideas']=self.rows(db,'desk_ideas')[:80]
            result['validations']=self.rows(db,'desk_validations')[:10]
            row=db.execute('SELECT payload FROM desk_checks WHERE id=1').fetchone();result['monitor']=json.loads(row[0]) if row else None
        return result

    def save_financials(self,data):
        symbol=symbol_value(data.get('symbol'))
        fields=('revenue_growth_pct','earnings_growth_pct','roe_pct','debt_to_equity','pe','operating_cash_flow')
        if not all(finite(data.get(k)) for k in fields) or data['pe']<=0 or data['debt_to_equity']<0:raise ValueError('Enter finite financial metrics, positive P/E and nonnegative debt/equity')
        if not safe_url(data.get('source_url')):raise ValueError('Add the financial statement source link')
        try:
            dated=datetime.strptime(data['as_of'],'%Y-%m-%d').date()
            if not 0<=(now_ist().date()-dated).days<=180:raise ValueError()
        except (ValueError,TypeError,KeyError):raise ValueError('Financial date must be within the past 180 days') from None
        peer=data.get('sector_pe')
        if peer is not None:number(peer,'Sector P/E',.01,10000)
        record={'id':symbol,'symbol':symbol,**{k:data[k] for k in fields},'as_of':str(dated),'source_url':data['source_url'],
                'sector_pe':peer,'origin':'manual','complete':True,'review_status':'USER-SUPPLIED · VERIFY FILINGS',
                'valuation':'Above sector P/E' if peer and data['pe']>peer else 'At/below sector P/E' if peer else 'Sector comparison unavailable'}
        with self.store.connect() as db:self.put(db,'desk_financials',record)
        return record
