"""Dependency-free personal web server. Deploy behind a private access proxy."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from datetime import date, datetime
import json
import logging
import math
import mimetypes
import re
import threading
import time
import uuid
import copy
from http.cookies import SimpleCookie
import hmac
from consentdesk.accounts import Accounts
from consentdesk.streaming import MarketStream
from consentdesk.sectors import SECTORS, sector_value, catalogue
from consentdesk.http_server import BoundedHTTPServer
from consentdesk.config import Settings, ROOT, UNIVERSE
from consentdesk.storage import Store
from consentdesk.agent import ResearchAgent
from consentdesk.providers import now_ist
from consentdesk.providers import Providers
from consentdesk.analysis import indicators, analyse_options
from consentdesk.patterns import chart_structure, oi_summary
from consentdesk.learning import PatternLearner
from consentdesk.learning import walk_forward
from consentdesk.daily import DailyBrief, BRIEF_VERSION
from consentdesk.desk import TradingDesk, symbol_value, number
from consentdesk.context import enrich, regime, relative_strength, event_flags

LOG = logging.getLogger('consentdesk')

def validate_identity(session, profile):
    if not isinstance(session, str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,64}', session):
        raise ValueError('Invalid browser session')
    if profile != 'common':
        raise ValueError('This app uses one common workspace')

def validate_request(data):
    if not isinstance(data, dict):
        raise ValueError('Request must be a JSON object')
    session, profile = data.get('session', 'common-workspace'), data.get('profile', 'common')
    validate_identity(session, profile)
    question = data.get('question', '')
    if not isinstance(question, str) or not 2 <= len(question.strip()) <= 2000:
        raise ValueError('Enter a question between 2 and 2000 characters')
    mode = data.get('mode', 'longterm')
    if mode not in ('intraday', 'options', 'longterm'):
        raise ValueError('Invalid research mode')
    capital, risk = data.get('capital', 50000), data.get('risk_pct', 1)
    for value in (capital, risk):
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError('Budget and risk must be finite numbers')
    if not 100 <= capital <= 100_000_000 or not .1 <= risk <= 2:
        raise ValueError('Budget must be Rs 100 to 10 crore; risk must be 0.1 to 2 percent')
    watchlist = data.get('watchlist', [])
    if not isinstance(watchlist, list) or len(watchlist) > 6 or any(not isinstance(s, str) or not re.fullmatch(r'[A-Z][A-Z0-9&-]{0,19}', s) for s in watchlist):
        raise ValueError('Use up to six NSE symbols in your watchlist')
    demo = data.get('demo', False)
    if not isinstance(demo, bool):
        raise ValueError('Demo must be true or false')
    context_id = data.get('context_report_id')
    if context_id is not None and (not isinstance(context_id, str) or not re.fullmatch(r'[a-f0-9]{32}', context_id)):
        raise ValueError('Invalid research context ID')
    expiry = data.get('expiry', 'current_week')
    if expiry not in ('current_week', 'next_week', 'far_week', 'current_month', 'next_month', 'far_month'):
        try:
            if not isinstance(expiry, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', expiry):
                raise ValueError()
            if not 0 <= (date.fromisoformat(expiry)-now_ist().date()).days <= 365:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('Expiry must be a supported relative expiry or a date within the next year') from None
    return {'session': 'common-workspace', 'profile': 'common', 'question': question.strip(), 'mode': mode,
            'capital': capital, 'risk_pct': risk, 'watchlist': list(dict.fromkeys(watchlist)), 'demo': demo, 'expiry': expiry, 'context_report_id': context_id}

class Application:
    def __init__(self, settings=None, parent=None):
        self.settings = settings or Settings()
        self.store = Store(self.settings.data_dir)
        self.store.unify_workspace()
        self.agent = ResearchAgent(self.settings)
        self.providers = self.agent.providers
        self.desk = TradingDesk(self.store)
        self.providers.desk = self.desk
        for report in reversed(self.store.history('common-workspace','common',20)):
            self.desk.save_ideas(report)
        self.jobs = self.store.load_jobs()
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(3)
        self.daily_jobs = {}
        for job_id,job in reversed(list(self.jobs.items())):
            if job.get('daily'):
                key=job.get('mode','intraday')+(':'+job['sector'] if job.get('sector','all')!='all' else '')
                self.daily_jobs[key]=job_id
        self.daily_lock = threading.Lock()
        self.stopping = threading.Event()
        self.monitor_lock = threading.Lock()
        self.parent=parent
        self.workspaces={};self.workspace_lock=threading.Lock()
        self.accounts=parent.accounts if parent else Accounts(self.settings.data_dir/'accounts.sqlite3',self.settings.max_users)
        self.stream=parent.stream if parent else MarketStream(self.providers,self.stopping)
        self.providers.stream=self.stream
        self.slots=parent.slots if parent else self.slots
        self.ai_budget_lock=parent.ai_budget_lock if parent else threading.Lock()
        self.settings.ai_budget_check=lambda:self.accounts.spend('server','ai',self.settings.ai_daily_calls,str(now_ist().date()))
        self.scan_retry={}

    def workspace(self,user_id):
        with self.workspace_lock:
            if user_id not in self.workspaces:
                settings=copy.copy(self.settings);settings.data_dir=self.settings.data_dir/'users'/user_id
                self.workspaces[user_id]=Application(settings,parent=self)
            return self.workspaces[user_id]

    def import_shared_report(self,workspace,report_id):
        if not report_id or workspace is self:return
        report=self.store.get(report_id,'common-workspace','common')
        if report and report.get('daily_scan'):
            with workspace.store.connect() as db:
                db.execute('INSERT OR IGNORE INTO reports VALUES(?,?,?,?,?,?)',(report['id'],'common-workspace','common',report['created_at'],report.get('question','Daily brief'),json.dumps(report)))

    def start_monitor(self):
        def loop():
            while not self.stopping.is_set():
                if self.settings.broker_enabled:
                    try:
                        if self.monitor_lock.acquire(blocking=False):
                            try:
                                self.desk.refresh(self.providers)
                                # Existing private ledgers continue monitoring after a restart.
                                if self.settings.public_mode:
                                    with self.accounts.db() as db:user_ids=[r[0] for r in db.execute('SELECT id FROM users')]
                                    for user_id in user_ids:self.workspace(user_id).desk.refresh(self.workspace(user_id).providers)
                            finally:self.monitor_lock.release()
                    except Exception:LOG.exception('Paper/alert monitor failed')
                self.stopping.wait(10)
        threading.Thread(target=loop,daemon=True).start()

    def validation(self,data):
        symbol=symbol_value(data.get('symbol'));mode=data.get('mode','longterm')
        if mode not in ('intraday','longterm'):raise ValueError('Select intraday or long-term validation')
        cost=number(data.get('cost_bps',20),'Round-trip cost',0,1000)
        if not self.slots.acquire(blocking=False):return None
        job_id=uuid.uuid4().hex
        with self.lock:
            while len(self.jobs)>=100:
                finished=next((k for k,j in self.jobs.items() if j['status']!='running'),None)
                if finished is None:break
                del self.jobs[finished]
            self.jobs[job_id]={'status':'running','trace':[],'started':time.monotonic(),'session':'common-workspace','profile':'common'}
            self.store.save_job(job_id,self.jobs[job_id])
        def worker():
            try:
                with self.lock:self.jobs[job_id]['trace'].append({'agent':'Historical validation','detail':'Fetching historical candles and testing three chronological windows','status':'running'})
                bundle=self.providers.validation_candles(symbol,mode);result=walk_forward(bundle,mode,cost)
                result.update(symbol=symbol,id=job_id,source=bundle['source'],created_at=now_ist().isoformat())
                with self.store.connect() as db:self.desk.put(db,'desk_validations',result)
                with self.lock:self.jobs[job_id].update(status='complete',validation=result)
            except Exception:
                LOG.exception('Historical validation failed')
                with self.lock:self.jobs[job_id].update(status='error',error='Historical validation failed. Check provider access and available history.')
            finally:
                try:
                    with self.lock:self.store.save_job(job_id,self.jobs[job_id])
                except Exception:LOG.exception('Could not persist completed job status')
                finally:self.slots.release()
        threading.Thread(target=worker,daemon=True).start()
        return job_id

    def daily(self, mode='intraday', force=False,sector='all'):
        sector_value(sector);key=mode if sector=='all' else mode+':'+sector
        day = str(now_ist().date())
        with self.daily_lock:
            previous = self.store.daily_report(mode, day, BRIEF_VERSION,sector=sector)
            visible = previous or self.store.daily_report(mode, brief_version=BRIEF_VERSION,sector=sector)
            running = self.daily_jobs.get(key)
            if running:
                with self.lock:
                    job = self.jobs.get(running)
                    if job and job['status'] == 'running':
                        return {'job_id': running, 'report': visible, 'status': 'running'}
                    if job and job['status']=='error' and time.monotonic()-job['started']<300 and not force:
                        return {'report':visible,'status':'error','error':job.get('error')}
            scheduled=tuple(map(int,self.settings.daily_scan_time.split(':')))
            prior_time=datetime.fromisoformat(previous['created_at']) if previous else None
            if previous and not force and (prior_time.hour,prior_time.minute)>=scheduled:
                return {'report': previous, 'status': 'complete'}
            args = {'session': 'common-workspace', 'profile': 'common', 'question': f'Daily {mode} scan for '+', '.join(s+'.NS' for s in self.settings.daily_symbols),
                    'mode':mode,'watchlist':self.settings.daily_symbols,'capital':50000,'risk_pct':1,'demo':False}
            args['sector']=sector
            job = self.submit(args, daily_scan=True)
            if job:
                self.daily_jobs[key] = job
            return {'job_id':job,'report':visible,'status':'running' if job else 'busy'}

    def daily_snapshot(self, mode,sector='all'):
        sector_value(sector);key=mode if sector=='all' else mode+':'+sector
        # Only completed workers write reports. Keep the previous brief throughout
        # refreshes, failures, midnight, weekends and server restarts.
        report = self.store.daily_report(mode, brief_version=BRIEF_VERSION,sector=sector)
        today = str(now_ist().date())
        with self.lock:
            job_id = self.daily_jobs.get(key)
            job = self.jobs.get(job_id)
            status = job['status'] if job else 'idle'
            error = job.get('error') if job and status == 'error' else None
        return {'report': report, 'job_id': job_id if status == 'running' else None,
                'status': status, 'error': error,
                'brief_date': report['created_at'][:10] if report else None,
                'is_previous_day': bool(report and report['created_at'][:10] != today),
                'scheduled_time': self.settings.daily_scan_time, 'enabled': self.settings.daily_scan_enabled,
                'note': 'Weekdays at the scheduled IST time while the server runs. The last completed brief stays visible until its replacement is saved.'}

    def start_scheduler(self):
        if not self.settings.daily_scan_enabled:
            return
        def loop():
            while not self.stopping.is_set():
                try:
                    current = now_ist()
                    hour, minute = map(int,self.settings.daily_scan_time.split(':'))
                    if current.weekday()<5 and (hour,minute)<=(current.hour,current.minute):
                        for mode in ('intraday','longterm','options'):
                            self.daily(mode)
                        for sector in SECTORS:
                            for mode in ('intraday','longterm','options'):
                                key=mode+':'+sector
                                if time.monotonic()>=self.scan_retry.get(key,0):
                                    result=self.daily(mode,sector=sector)
                                    if result.get('job_id'):self.scan_retry[key]=time.monotonic()+300
                except Exception:
                    LOG.exception('Scheduled daily scan failed')
                self.stopping.wait(30)
        threading.Thread(target=loop,daemon=True).start()

    def submit(self, data, daily_scan=False):
        sector=sector_value(data.get('sector','all')) if daily_scan else 'all'
        args = validate_request(data)
        if not self.slots.acquire(blocking=False):
            return None
        job_id = uuid.uuid4().hex
        with self.lock:
            # Keep completed job state for 30 minutes. Reports remain in SQLite.
            cutoff = time.monotonic()-1800
            expired = [k for k, j in self.jobs.items() if j['started'] < cutoff and j['status'] != 'running']
            for k in expired:
                del self.jobs[k]
            while len(self.jobs) >= 100:
                completed = next((k for k, j in self.jobs.items() if j['status'] != 'running'), None)
                if completed is None:
                    break
                del self.jobs[completed]
            self.jobs[job_id] = {'status': 'running', 'trace': [], 'started': time.monotonic(), 'session': args['session'], 'profile': args['profile'],'daily':daily_scan,'mode':args['mode'],'sector':sector}
            self.store.save_job(job_id,self.jobs[job_id])
        def progress(event):
            with self.lock:
                self.jobs[job_id]['trace'].append(event)
        def worker():
            try:
                history = self.store.history(args['session'], args['profile'], 5)
                if args.get('context_report_id'):
                    selected = self.store.get(args['context_report_id'], args['session'], args['profile'])
                    if selected:
                        history = [selected] + [r for r in history if r['id'] != selected['id']]
                context = [{'question': r['question'], 'symbols': r['symbols'], 'mode': r['mode']} for r in history if r['demo'] == args['demo']][:3]
                if daily_scan:
                    report=DailyBrief(self.agent,self.settings).run(args['mode'],progress,**({'sector':sector} if sector!='all' else {}))
                else:
                    report = self.agent.run(**{k: v for k, v in args.items() if k not in ('session', 'profile', 'context_report_id')}, context=context, progress=progress)
                report['daily_scan'] = daily_scan
                self.store.save(args['session'], args['profile'], args['question'], report,on_saved=self.desk.save_ideas)
                with self.lock:
                    self.jobs[job_id].update(status='complete', report=report)
            except Exception:
                LOG.exception('Research job failed')
                with self.lock:
                    self.jobs[job_id].update(status='error', error='Research could not complete. Check the server log and try again.')
            finally:
                try:
                    with self.lock:self.store.save_job(job_id,self.jobs[job_id])
                except Exception:LOG.exception('Could not persist completed job status')
                finally:self.slots.release()
        threading.Thread(target=worker, daemon=True).start()
        return job_id

def make_handler(app):
    root=app
    class Handler(BaseHTTPRequestHandler):
        @property
        def app(self):return getattr(self,'workspace_app',root)

        def cookie_token(self):
            try:
                cookie=SimpleCookie(self.headers.get('Cookie',''));return cookie['cd-auth'].value if 'cd-auth' in cookie else ''
            except Exception:return ''

        def gate(self,write=False):
            self.workspace_app=root
            if not root.settings.public_mode:return True
            if urlparse(self.path).path=='/api/health':return True
            self.user=root.accounts.session(self.cookie_token())
            if not self.user:self.reply(401,{'error':'Sign in to use the app'});return False
            if not root.accounts.allow(('request',self.user['id']),240):self.reply(429,{'error':'Too many requests; retry in a minute'});return False
            if write:
                if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),self.user['csrf']):self.reply(403,{'error':'Session changed. Reload and try again.'});return False
                if not root.accounts.allow(('write',self.user['id']),30):self.reply(429,{'error':'Too many changes; retry shortly'});return False
            self.workspace_app=root.workspace(self.user['id'])
            return True

        def auth(self,route,data):
            if not root.settings.public_mode:return self.reply(400,{'error':'Accounts are enabled only in public mode'})
            origin=self.headers.get('Origin')
            allowed_frontend=root.settings.public_origin
            if origin and urlparse(origin).netloc!=self.headers.get('Host') and not (origin==allowed_frontend and urlparse(origin).scheme=='https'):
                return self.reply(403,{'error':'Cross-site request refused'})
            ip=self.client_address[0]
            if root.settings.trust_proxy:
                import ipaddress
                candidate=self.headers.get('X-Forwarded-For','').split(',')[0].strip()
                try:ip=str(ipaddress.ip_address(candidate))
                except ValueError:pass
            if not root.accounts.allow(('login',ip),10,300):return self.reply(429,{'error':'Too many attempts. Wait five minutes.'})
            if route=='/api/auth/logout':
                if not self.gate(True):return
                root.accounts.logout(self.cookie_token());return self.reply(200,{'status':'signed out'},cookie='cd-auth=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0'+('; Secure' if root.settings.secure_cookies else ''))
            if route=='/api/auth/register':
                if not root.settings.registration_enabled:return self.reply(403,{'error':'Registration is closed'})
                root.accounts.register(data.get('email'),data.get('password'))
            token,user=root.accounts.login(data.get('email'),data.get('password'))
            return self.reply(200,{'user':user},cookie='cd-auth='+token+'; HttpOnly; SameSite=Lax; Path=/; Max-Age=604800'+('; Secure' if root.settings.secure_cookies else ''))

        def log_message(self, fmt, *args):
            # Avoid logging session IDs and query strings.
            LOG.info('%s %s', self.command, urlparse(self.path).path)

        def reply(self, code, value,cookie=None):
            body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            if cookie:self.send_header('Set-Cookie',cookie)
            self.end_headers()
            self.wfile.write(body)

        def identity(self, query):
            session = query.get('session', ['common-workspace'])[0]
            profile = query.get('profile', ['common'])[0]
            validate_identity(session, profile)
            return 'common-workspace', 'common'

        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            try:
                if url.path=='/api/auth/me':
                    user=root.accounts.session(self.cookie_token()) if root.settings.public_mode else None
                    return self.reply(200,{'public_mode':root.settings.public_mode,'user':user,'registration_enabled':root.settings.registration_enabled})
                if url.path.startswith('/api/') and not self.gate():return
                if url.path == '/api/health':
                    return self.reply(200, {'status': 'ok'})
                if url.path == '/api/desk':
                    return self.reply(200,self.app.desk.snapshot())
                if url.path == '/api/financials':
                    return self.reply(200,self.app.providers.fundamentals(self.symbols(query)[0]))
                if url.path == '/api/context':
                    symbol=self.symbols(query)[0]
                    stock=self.app.providers.candles(symbol,'longterm');benchmark=self.app.providers.candles('NIFTY','longterm')
                    return self.reply(200,{'symbol':symbol,'regime':regime(benchmark),'relative_strength':relative_strength(stock,benchmark),
                        'events':event_flags(self.app.desk.events(symbol),[]),'checked_at':now_ist().isoformat()})
                if url.path == '/api/config':
                    return self.reply(200, {'ai_enabled': self.app.settings.ai_enabled, 'broker_data_enabled': self.app.settings.broker_enabled,
                        'broker':self.app.settings.broker,'daily_scan_time':self.app.settings.daily_scan_time,'daily_scan_enabled':self.app.settings.daily_scan_enabled,
                        'daily_symbols':self.app.settings.daily_symbols,'quote_refresh_seconds':10,'sectors':catalogue(),'public_mode':root.settings.public_mode,'stream':root.stream.snapshot([])['stream'],
                        'execution_enabled': False, 'symbols': [{'symbol': s, 'name': m['name'], 'sector': m['sector']} for s, m in UNIVERSE.items()]})
                if url.path=='/api/ticks':
                    symbols=self.symbols(query)
                    if root.settings.broker_enabled and root.settings.broker=='upstox':root.stream.subscribe(symbols)
                    result=root.stream.snapshot(symbols);market=root.providers.market_status()
                    for q in result['quotes']:
                        q['live']=bool(q['live'] and market.get('verified') and market.get('open'))
                        q['label']='LIVE STREAM' if q['live'] else 'BROKER STREAM SNAPSHOT'
                    return self.reply(200,result)
                if url.path=='/api/status':
                    return self.reply(200,{'broker_configured':root.settings.broker_enabled,'provider':root.settings.broker,'stream':root.stream.snapshot([])['stream'],
                        'public_mode':root.settings.public_mode,'scheduler_time':root.settings.daily_scan_time,'ai_configured':root.settings.ai_enabled,
                        'note':'Configured credentials still require a successful market-hours check.'})
                if url.path == '/api/search':
                    q = query.get('q',[''])[0]
                    if not 1 <= len(q) <= 80:
                        raise ValueError('Search must contain 1 to 80 characters')
                    return self.reply(200, self.app.providers.search(q))
                if url.path == '/api/quotes':
                    symbols = self.symbols(query)
                    return self.reply(200, self.app.providers.quotes(symbols, query.get('demo',['false'])[0]=='true'))
                if url.path == '/api/stock':
                    symbol=self.symbols(query)[0]
                    mode=query.get('mode',['intraday'])[0]
                    if mode not in ('intraday','longterm'):
                        raise ValueError('Select intraday or longterm chart')
                    demo=query.get('demo',['false'])[0]=='true'
                    bundle=self.app.providers.candles(symbol,mode,demo)
                    return self.reply(200, {'symbol':symbol,'mode':mode,'source':bundle['source'],'as_of':bundle['as_of'],
                        'synthetic':bundle['synthetic'],'metrics':indicators(bundle,mode),'structure':chart_structure(bundle,mode),
                        'candles':bundle['candles'][-250:],'provider_notes':bundle.get('provider_notes',[])})
                if url.path == '/api/options':
                    symbol=self.symbols(query)[0]
                    expiry=query.get('expiry',['current_month'])[0]
                    validate_request({'question':'inspect options','expiry':expiry})
                    chain=self.app.providers.options(symbol,expiry,query.get('demo',['false'])[0]=='true')
                    market=self.app.providers.market_status()
                    return self.reply(200,{'analysis':analyse_options(chain,market),'oi':oi_summary(chain)})
                if url.path == '/api/daily':
                    mode=query.get('mode',['intraday'])[0]
                    if mode not in ('intraday','longterm','options'):
                        raise ValueError('Invalid daily scan mode')
                    return self.reply(200, root.daily_snapshot(mode,sector_value(query.get('sector',['all'])[0])))
                if url.path == '/api/history':
                    session, profile = self.identity(query)
                    return self.reply(200, {'reports': self.app.store.history(session, profile)})
                if url.path.startswith('/api/jobs/'):
                    session, profile = self.identity(query)
                    job_id = url.path.rsplit('/', 1)[-1]
                    with self.app.lock:
                        job = self.app.jobs.get(job_id)
                        if not job:
                            shared=root.jobs.get(job_id)
                            if shared and shared.get('daily'):job=shared
                        if not job or (job['session'], job['profile']) != (session, profile):
                            return self.reply(404, {'error': 'Research job not found'})
                        snapshot = {k: v for k, v in job.items() if k in ('status', 'trace', 'report', 'error','validation')}
                    return self.reply(200, snapshot)
                allowed = {'/': 'index.html', '/app.js': 'app.js', '/auth.js':'auth.js','/dashboard.js':'dashboard.js','/desk.js':'desk.js', '/style.css': 'style.css', '/brief.css':'brief.css','/desk.css':'desk.css', '/favicon.svg': 'favicon.svg'}
                if url.path not in allowed:
                    return self.reply(404, {'error': 'Not found'})
                path = ROOT / 'web' / allowed[url.path]
                body = path.read_bytes()
                if url.path=='/':
                    body=body.replace(b'<section id="desk-view" hidden></section>',(ROOT/'web'/'desk-section.html').read_bytes())
                self.send_response(200)
                mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
                self.send_header('Content-Type', mime + ('; charset=utf-8' if mime.startswith('text/') or path.suffix == '.js' else ''))
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                self.wfile.write(body)
            except ValueError as e:
                self.reply(400, {'error': str(e)})
            except Exception as e:
                from consentdesk.net import ProviderError
                if isinstance(e,ProviderError):
                    self.reply(502, {'error':str(e)})
                elif isinstance(e,(BrokenPipeError,ConnectionResetError)):
                    pass
                else:
                    LOG.exception('Data request failed')
                    self.reply(500, {'error':'Data service failed; check server log'})

        def symbols(self,query):
            symbols=list(dict.fromkeys(query.get('symbols',['INFY'])[0].upper().split(',')))
            if not 1 <= len(symbols)<=12 or any(not re.fullmatch(r'[A-Z][A-Z0-9&-]{0,19}',s) for s in symbols):
                raise ValueError('Use 1 to 12 valid NSE symbols')
            return symbols

        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16000:
                    return self.reply(413, {'error': 'Request is empty or too large'})
                data = json.loads(self.rfile.read(length))
                if not isinstance(data,dict):raise ValueError('Request must be a JSON object')
                route=urlparse(self.path).path
                if route in ('/api/auth/login','/api/auth/register','/api/auth/logout'):return self.auth(route,data)
                if not self.gate(True):return
                if route in ('/api/research','/api/validation','/api/daily') and root.settings.public_mode:
                    if not root.accounts.spend(self.user['id'],'research',root.settings.daily_research_limit,str(now_ist().date())):return self.reply(429,{'error':'Daily research limit reached. Saved briefs remain available.'})
                if route in ('/api/paper/open','/api/alerts'):root.import_shared_report(self.app,data.get('report_id'))
                if route=='/api/desk/limits':return self.reply(200,self.app.desk.set_limits(data))
                if route=='/api/paper/preview':return self.reply(200,self.app.desk.preview(data))
                if route=='/api/paper/open':return self.reply(201,self.app.desk.open_trade(data))
                if route=='/api/paper/close':return self.reply(200,self.app.desk.close_trade(data))
                if route=='/api/alerts':return self.reply(201,self.app.desk.add_alert(data))
                if route=='/api/alerts/cancel':return self.reply(200,self.app.desk.cancel('desk_alerts',data.get('id')))
                if route=='/api/events':return self.reply(201,self.app.desk.add_event(data))
                if route=='/api/events/cancel':return self.reply(200,self.app.desk.cancel('desk_events',data.get('id')))
                if route=='/api/financials':return self.reply(200,self.app.desk.save_financials(data))
                if route=='/api/events/sync':
                    symbol=symbol_value(data.get('symbol'));events=self.app.providers.corporate_events(symbol);self.app.desk.save_provider_events(events)
                    return self.reply(200,{'events':events,'symbol':symbol})
                if route=='/api/desk/check':
                    if not self.app.monitor_lock.acquire(blocking=False):return self.reply(200,{'status':'checking'})
                    try:return self.reply(200,self.app.desk.refresh(self.app.providers))
                    finally:self.app.monitor_lock.release()
                if route=='/api/validation':
                    job=self.app.validation(data)
                    return self.reply(202 if job else 429,{'job_id':job} if job else {'error':'Research is busy. Retry shortly.'})
                if urlparse(self.path).path == '/api/daily':
                    if not isinstance(data,dict) or data.get('mode','intraday') not in ('intraday','longterm','options'):
                        raise ValueError('Select intraday, F&O or longterm scan')
                    return self.reply(202, root.daily(data.get('mode','intraday'), force=False if root.settings.public_mode else True,sector=sector_value(data.get('sector','all'))))
                if urlparse(self.path).path != '/api/research':
                    return self.reply(404, {'error': 'Not found'})
                job = self.app.submit(data)
                if job is None:
                    return self.reply(429, {'error': 'Three research runs are already active. Retry shortly.'})
                self.reply(202, {'job_id': job})
            except (ValueError, UnicodeError) as e:
                self.reply(400, {'error': str(e)})
            except Exception as e:
                from consentdesk.net import ProviderError
                if isinstance(e,ProviderError):self.reply(502,{'error':str(e)})
                else:
                    LOG.exception('Workspace action failed');self.reply(500,{'error':'Workspace action could not complete; check server log'})

        def do_DELETE(self):
            try:
                if not self.gate(True):return
                url = urlparse(self.path)
                if url.path != '/api/history':
                    return self.reply(404, {'error': 'Not found'})
                session, profile = self.identity(parse_qs(url.query))
                with self.app.lock:
                    if any(j['status'] == 'running' and (j['session'], j['profile']) == (session, profile) for j in self.app.jobs.values()):
                        return self.reply(409, {'error': 'Wait for active research to finish before clearing history'})
                self.app.store.clear(session, profile)
                self.reply(200, {'status': 'cleared'})
            except ValueError as e:
                self.reply(400, {'error': str(e)})
    return Handler

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    app = Application()
    if app.settings.public_mode and not app.settings.public_data_approved:
        raise SystemExit('Before public mode, confirm data-display and recommendation permissions, then set PUBLIC_DATA_APPROVED=true. Use private mode for local testing.')
    app.stream.start()
    app.start_scheduler()
    app.start_monitor()
    server = BoundedHTTPServer((app.settings.host, app.settings.port), make_handler(app))
    print(f'ConsentDesk: http://{app.settings.host}:{app.settings.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        app.stopping.set()
        server.server_close()
