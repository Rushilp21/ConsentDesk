"""Leased, checkpointed jobs. Each advance runs within an HTTP invocation."""
import copy
from datetime import datetime
import json
import logging
import time
import uuid
from .config import DEFAULT_SYMBOLS
from .agent import resolve_symbols, requested_mode
from .daily import DailyBrief, discover, BRIEF_VERSION
from .sectors import SECTORS, sector_value
from .providers import ProviderError, now_ist
from .reasoning import rank_key
from .learning import walk_forward

LOG=logging.getLogger('consentdesk')


class DurableQueue:
    def __init__(self,store):
        self.store=store
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS cd_queue(id TEXT PRIMARY KEY,task_key TEXT UNIQUE,owner TEXT,kind TEXT,status TEXT,payload TEXT,lease TEXT,lease_until REAL,created REAL,updated REAL)')

    @staticmethod
    def unpack(row):
        if not row:return None
        job=json.loads(row[5]);job.update(id=row[0],owner=row[2],kind=row[3],status=row[4],lease=row[6],lease_until=row[7],updated=row[9])
        return job

    def get(self,job_id):
        with self.store.connect() as db:return self.unpack(db.execute('SELECT * FROM cd_queue WHERE id=?',(job_id,)).fetchone())

    def enqueue(self,owner,kind,args,key=None):
        now=time.time();job_id=uuid.uuid4().hex;key=key or job_id
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing=db.execute('SELECT id,status,updated FROM cd_queue WHERE task_key=?',(key,)).fetchone()
            if existing:
                if existing[1]=='error' and now-existing[2]>300:
                    db.execute("UPDATE cd_queue SET status='queued',lease='',lease_until=0,updated=? WHERE id=?",(now,existing[0]))
                return existing[0]
            pending=db.execute("SELECT count(*) FROM cd_queue WHERE owner=? AND status IN ('queued','running')",(owner,)).fetchone()[0]
            if pending>=(40 if owner=='shared' else 3):return None
            payload={'args':args,'trace':[],'parts':[],'position':0}
            db.execute('INSERT INTO cd_queue VALUES(?,?,?,?,?,?,?,?,?,?)',(job_id,key,owner,kind,'queued',json.dumps(payload),'',0,now,now))
            db.execute("DELETE FROM cd_queue WHERE status IN ('complete','error') AND updated<?",(now-7*86400,))
        return job_id

    def pending(self,limit=3):
        with self.store.connect() as db:
            return [r[0] for r in db.execute("SELECT id FROM cd_queue WHERE status IN ('queued','running') ORDER BY updated LIMIT ?",(limit,))]

    def retry_daily(self):
        with self.store.connect() as db:
            rows=db.execute("SELECT id,payload FROM cd_queue WHERE kind='daily' AND status='error' AND updated<?",(time.time()-300,)).fetchall()
            for job_id,raw in rows:
                job=json.loads(raw)
                if job.get('failures',0)<3 and job.get('args',{}).get('scan_day',str(now_ist().date()))==str(now_ist().date()):
                    db.execute("UPDATE cd_queue SET status='queued',updated=? WHERE id=?",(time.time(),job_id))

    def claim(self,job_id):
        now=time.time()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            job=self.unpack(db.execute('SELECT * FROM cd_queue WHERE id=?',(job_id,)).fetchone())
            if not job or job['status'] not in ('queued','running') or job['lease_until']>now:return None
            active=db.execute("SELECT count(*) FROM cd_queue WHERE status='running' AND lease_until>?",(now,)).fetchone()[0]
            if active>=3:return None
            token=uuid.uuid4().hex
            db.execute("UPDATE cd_queue SET status='running',lease=?,lease_until=?,updated=? WHERE id=?",(token,now+330,now,job_id))
            job.update(lease=token,lease_until=now+330,status='running')
            return job

    def checkpoint(self,job):
        payload={k:v for k,v in job.items() if k not in ('id','owner','kind','status','lease','lease_until','updated')}
        with self.store.connect() as db:
            result=db.execute("UPDATE cd_queue SET status=?,payload=?,lease='',lease_until=0,updated=? WHERE id=? AND lease=?",(job['status'],json.dumps(payload,allow_nan=False),time.time(),job['id'],job['lease']))
        return result.rowcount==1

    def visible(self,job_id,owner):
        job=self.get(job_id)
        if not job or job['owner'] not in (owner,'shared'):return None
        return {**{k:v for k,v in job.items() if k in ('trace','report','validation','error')},
                'status':'running' if job['status'] in ('queued','running') else job['status']}

    def advance(self,job_id,root):
        job=self.claim(job_id)
        if not job:return
        app=root if job['owner']=='shared' else root.workspace(job['owner'])
        try:
            self.step(job,app)
        except Exception:
            LOG.exception('Durable research step failed')
            job.update(status='error',error='Research step failed. Check provider access and retry; previous saved results are retained.')
            job['failures']=job.get('failures',0)+1
        self.checkpoint(job)

    def step(self,job,app):
        args=job['args'];kind=job['kind']
        if kind=='daily' and args.get('scan_day',str(now_ist().date()))!=str(now_ist().date()):
            job.update(status='error',error='This scan expired at midnight. The previous completed brief is retained.')
            return
        def progress(e):job['trace']=(job['trace']+[e])[-60:]
        if kind=='validation':
            bundle=app.providers.validation_candles(args['symbol'],args['mode'])
            result=walk_forward(bundle,args['mode'],args['cost_bps'])
            result.update(id=job['id'],symbol=args['symbol'],source=bundle['source'],created_at=now_ist().isoformat())
            with app.store.connect() as db:app.desk.put(db,'desk_validations',result)
            job.update(status='complete',validation=result);return
        if 'scope' not in job:
            if kind=='daily':
                try:instruments=app.providers.kite.instruments()
                except (ProviderError,OSError):instruments=[]
                try:news=app.providers.market_news()
                except ProviderError:news=[]
                discovery=discover(instruments,news,app.settings.daily_symbols,app.settings.daily_candidate_limit)
                sector=args['sector']
                if sector!='all':
                    label,members=SECTORS[sector];membership=dict(members)
                    discovery['symbols']=list(dict.fromkeys([s for s in discovery['symbols'] if s in membership]+list(membership)))[:12]
                    discovery['names'].update(membership)
                    discovery['note']=label+' · Curated sector scan'
                elif args['mode']=='options':discovery['symbols']=list(dict.fromkeys(['NIFTY','BANKNIFTY',*discovery['symbols']]))[:app.settings.daily_candidate_limit]
                discovery['names']={s:discovery['names'].get(s,s) for s in discovery['symbols']}
                discovery['matches']={s:discovery['matches'].get(s,[]) for s in discovery['symbols']}
                job.update(scope=discovery['symbols'],discovery=discovery)
            else:
                history=app.store.history('common-workspace','common',5)
                if args.get('context_report_id'):
                    prior=app.store.get(args['context_report_id'],'common-workspace','common')
                    if prior:history=[prior]+history
                context=[{'question':r['question'],'symbols':r['symbols'],'mode':r['mode']} for r in history if r.get('demo')==args['demo']][:3]
                explicit=resolve_symbols(args['question']);selected=requested_mode(args['question'],args['mode']);args['mode']=selected
                import re
                followup=bool(re.search(r'\b(it|these|those|them|why|risk|compare|more|which|same|this)\b',args['question'],re.I))
                scope=explicit or (context[0]['symbols'] if followup and context else []) or (['NIFTY'] if selected=='options' else args['watchlist'] or DEFAULT_SYMBOLS)
                job.update(scope=list(dict.fromkeys(scope))[:6],context=context)
            progress({'agent':'Planner','detail':str(len(job['scope']))+' symbols queued; one stock per request','status':'done'})
            return
        if job['position']<len(job['scope']):
            symbol=job['scope'][job['position']]
            if kind=='daily':
                try:report=DailyBrief(app.agent,app.settings).run(args['mode'],progress,sector=args['sector'],discovery_override=job['discovery'],candidate_symbols=[symbol])
                except ProviderError:
                    progress({'agent':'Data gap','detail':symbol+': unavailable; continuing the bounded scan','status':'limited'})
                    job['position']+=1
                    return
            else:
                report=app.agent.run('Research '+symbol+'.NS for '+args['mode'],args['mode'],[symbol],args['capital'],args['risk_pct'],args['demo'],job.get('context',[]),args['expiry'],progress)
            job['parts'].append(report);job['position']+=1
            progress({'agent':'Progress','detail':str(job['position'])+'/'+str(len(job['scope']))+' stocks analysed','status':'done'})
            return
        parts=job['parts']
        if not parts:raise ValueError('No research returned')
        report=copy.deepcopy(parts[-1]);records=[r for part in parts for r in part['recommendations']]
        records.sort(key=rank_key)
        if kind=='daily':
            sector=args['sector'];report['discovery']={k:v for k,v in job['discovery'].items() if k not in ('names','matches')}
            if args['mode']=='options':
                groups={side:sorted([r for r in records if r.get('fno_views',{}).get(side,{}).get('contract') and r['metrics'].get('trend')==trend],key=lambda r:(-r['fno_views'][side]['score'],r['symbol']))[:3] for side,trend in [('buy','Bullish'),('sell','Bearish')]}
                report['fno_sections']=groups;records=list({r['symbol']:r for rows in groups.values() for r in rows}.values())
            else:records=records[:3 if sector!='all' else 8]
        report.update(id=job['id'],created_at=now_ist().isoformat(),question=args.get('question',"Today's "+args['mode']+' recommendations'),symbols=job['scope'],recommendations=records,trace=job['trace'],daily_scan=kind=='daily')
        report['duration_ms']=sum(p.get('duration_ms',0) for p in parts)
        report['gaps']=list(dict.fromkeys(g for part in parts for g in part.get('gaps',[])))
        report['answer']=app.agent.fallback_answer(report,report['question']) if records else 'No qualifying directional idea with a liquid contract. Wait for confirmation.'
        report.pop('ai_commentary',None)  # A last-symbol narrative is not a combined report.
        with app.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO reports VALUES(?,?,?,?,?,?)',(report['id'],'common-workspace','common',report['created_at'],report['question'],json.dumps(report,allow_nan=False)))
            app.desk.save_ideas(report,db)
        job.update(status='complete',report=report)
        job.pop('parts',None)
