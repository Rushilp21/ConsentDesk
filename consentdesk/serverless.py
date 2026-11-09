"""Request-scoped orchestration; no scheduler/stream/worker daemon threads."""
from concurrent.futures import ThreadPoolExecutor
import copy
import hmac
import io
import json
import os
import threading
from urllib.parse import urlparse
from email.message import Message
from flask import Flask, request, Response
from server import Application, make_handler, validate_request
from .config import Settings
from .daily import BRIEF_VERSION
from .durable import DurableQueue
from .providers import now_ist
from .sectors import SECTORS, sector_value
from .desk import symbol_value, number


class ServerlessApplication(Application):
    def __init__(self,settings=None,parent=None):
        super().__init__(settings,parent)
        self.queue=parent.queue if parent else DurableQueue(self.store)
        self.owner=self.settings.data_dir.name if parent else 'shared'

    def daily_prefix(self):
        now=now_ist();due=tuple(map(int,self.settings.daily_scan_time.split(':')))
        slot='morning' if (now.hour,now.minute)>=due else 'early'
        return 'daily:'+str(now.date())+':'+str(BRIEF_VERSION)+':'+slot+':'

    def submit(self,data,daily_scan=False):
        args=validate_request(data)
        if daily_scan:return self.daily(args['mode'],sector=data.get('sector','all')).get('job_id')
        return self.queue.enqueue(self.owner,'research',args)

    def validation(self,data):
        args={'symbol':symbol_value(data.get('symbol')),'mode':data.get('mode','longterm'),'cost_bps':number(data.get('cost_bps',20),'Round-trip cost',0,1000)}
        if args['mode'] not in ('longterm','intraday'):raise ValueError('Invalid validation mode')
        return self.queue.enqueue(self.owner,'validation',args)

    def daily(self,mode='intraday',force=False,sector='all'):
        sector_value(sector)
        if mode not in ('intraday','longterm','options'):raise ValueError('Invalid daily mode')
        args={'mode':mode,'sector':sector,'question':"Today's "+mode+' recommendations · '+sector,'scan_day':str(now_ist().date())}
        key=self.daily_prefix()+mode+':'+sector
        job_id=self.queue.enqueue('shared','daily',args,key)
        job=self.queue.get(job_id) if job_id else None
        return {'job_id':job_id if job and job['status'] in ('queued','running') else None,
                'report':self.store.daily_report(mode,brief_version=BRIEF_VERSION,sector=sector),
                'status':job['status'] if job else 'busy'}

    def daily_snapshot(self,mode,sector='all'):
        report=self.store.daily_report(mode,brief_version=BRIEF_VERSION,sector=sector)
        key=self.daily_prefix()+mode+':'+sector
        with self.store.connect() as db:row=db.execute('SELECT id FROM cd_queue WHERE task_key=?',(key,)).fetchone()
        job=self.queue.get(row[0]) if row else None
        return {'report':report,'job_id':job['id'] if job and job['status'] in ('queued','running') else None,
                'status':job['status'] if job else 'idle','error':job.get('error') if job else None,
                'scheduled_time':self.settings.daily_scan_time,'enabled':self.settings.daily_scan_enabled,
                'is_previous_day':bool(report and report['created_at'][:10]!=str(now_ist().date()))}

    def tick(self):
        now=now_ist();due=tuple(map(int,self.settings.daily_scan_time.split(':')))
        if self.settings.daily_scan_enabled and now.weekday()<5 and (now.hour,now.minute)>=due:
            prefix=self.daily_prefix()
            with self.store.connect() as db:count=db.execute('SELECT count(*) FROM cd_queue WHERE task_key LIKE ?',(prefix+'%',)).fetchone()[0]
            if count<3*(len(SECTORS)+1):
                for sector in ('all',*SECTORS):
                    for mode in ('intraday','options','longterm'):
                        self.queue.enqueue('shared','daily',{'mode':mode,'sector':sector,'scan_day':str(now.date())},prefix+mode+':'+sector)
        self.queue.retry_daily()
        pending=self.queue.pending(3)
        # Threads are joined before returning, never left running after a response.
        with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(lambda job_id:self.queue.advance(job_id,self),pending))
        return {'status':'ok','advanced':len(pending),'checked_at':now.isoformat()}


class SetupError(ValueError):
    def __init__(self,code,message):
        super().__init__(message)
        self.code=code


def startup_failure(error):
    """Return fixed diagnostics only; exception messages may contain credentials."""
    if isinstance(error,SetupError):return error.code,str(error)
    sqlstate=getattr(error,'sqlstate',None) or ''
    message=str(error).lower()
    if sqlstate.startswith('28') or 'password authentication failed' in message:
        return 'DATABASE_AUTH_FAILED','Supabase rejected the database login. Check DATABASE_URL in Vercel Production.'
    if 'tenant or user not found' in message:
        return 'DATABASE_PROJECT_INVALID','Check the project username and pooler host in DATABASE_URL.'
    if any(term in message for term in ('resolve host','translate host','getaddrinfo')):
        return 'DATABASE_HOST_INVALID','Cannot resolve the database host. Copy the complete pooler URL and URL-encode the password.'
    if sqlstate=='42501':return 'DATABASE_PERMISSION_DENIED','The database user cannot initialise the application tables.'
    if sqlstate.startswith('08') or type(error).__name__=='OperationalError':
        return 'DATABASE_CONNECTION_FAILED','Cannot connect to Supabase. Check the pooler URL and whether the project is active.'
    if isinstance(error,OSError):return 'DATA_DIRECTORY_FAILED','Set DATA_DIR to /tmp/consentdesk in Vercel Production.'
    return 'BACKEND_STARTUP_FAILED','Backend initialisation failed. Check the startup error code in Vercel Runtime Logs.'


def create_app(settings=None,require_database=True):
    web=Flask(__name__);web.config['MAX_CONTENT_LENGTH']=16000
    guard=threading.Lock();state={}
    def root_app():
        with guard:
            if 'root' not in state:
                if require_database and not os.getenv('DATABASE_URL','').strip():
                    raise SetupError('DATABASE_URL_MISSING','Set DATABASE_URL in Vercel Production and redeploy.')
                config=copy.copy(settings) if settings else Settings()
                config.public_mode=True;config.secure_cookies=True;config.stream_enabled=False
                config.serverless=True
                if not config.public_data_approved:
                    raise SetupError('PUBLIC_DATA_NOT_APPROVED','PUBLIC_DATA_APPROVED is not true in this deployment. Complete the public-data setup and redeploy.')
                state['root']=ServerlessApplication(config)
            return state['root']

    @web.route('/',defaults={'route':''},methods=['GET','POST'])
    @web.route('/<path:route>',methods=['GET','POST'])
    def dispatch(route):
        path=request.args.get('__path') or request.path
        if not path.startswith('/api/'):return Response('Not found',status=404)
        try:root=root_app()
        except Exception as error:
            # Never return a driver DSN/password or provider credential in setup errors.
            code,message=startup_failure(error)
            web.logger.error('Backend startup failed: code=%s type=%s sqlstate=%s',code,type(error).__name__,getattr(error,'sqlstate',None))
            return {'error':message,'code':code},503
        if path=='/api/cron':
            secret=os.getenv('CRON_SECRET','')
            if len(secret)<32 or not hmac.compare_digest(request.headers.get('Authorization',''),'Bearer '+secret):return {'error':'Unauthorized'},401
            try:return root.tick()
            except Exception:
                web.logger.exception('Scheduler request failed');return {'error':'Scheduler failed; check function logs'},500
        base=make_handler(root)
        class Handler(base):
            def job_response(self,advance=False):
                if not self.gate(advance):return
                job_id=urlparse(self.path).path.rsplit('/',1)[-1]
                if len(job_id)!=32:return self.reply(404,{'error':'Research job not found'})
                owner=getattr(self,'user',{}).get('id','shared')
                job=root.queue.visible(job_id,owner)
                if not job:return self.reply(404,{'error':'Research job not found'})
                if advance:
                    root.queue.advance(job_id,root);job=root.queue.visible(job_id,owner)
                return self.reply(200,job)
            def do_GET(self):
                endpoint=urlparse(self.path).path
                if endpoint.startswith('/api/jobs/'):return self.job_response()
                if endpoint=='/api/config':
                    if not self.gate():return
                    return self.reply(200,{'serverless':True,'ai_enabled':root.settings.ai_enabled,'broker_data_enabled':root.settings.broker_enabled,'broker':root.settings.broker,
                        'daily_scan_time':root.settings.daily_scan_time,'daily_scan_enabled':root.settings.daily_scan_enabled,'daily_symbols':root.settings.daily_symbols,
                        'quote_refresh_seconds':15,'sectors':__import__('consentdesk.sectors',fromlist=['catalogue']).catalogue(),
                        'public_mode':True,'stream':{'state':'SNAPSHOT POLLING'},'execution_enabled':False,'symbols':[]})
                if endpoint=='/api/ticks':
                    if not self.gate():return
                    return self.reply(200,{'quotes':[],'stream':{'state':'SNAPSHOT POLLING'}})
                return super().do_GET()
            def do_POST(self):
                if urlparse(self.path).path.startswith('/api/jobs/'):return self.job_response(True)
                return super().do_POST()
        handler=Handler.__new__(Handler)
        handler.path=path+('?'+'&'.join(k+'='+v for k,v in request.args.items() if k!='__path') if request.args else '')
        handler.command=request.method;handler.request_version='HTTP/1.1';handler.client_address=(request.remote_addr or 'unknown',0)
        handler.headers=Message()
        for key,value in request.headers.items():handler.headers[key]=value
        body=request.get_data();handler.headers.replace_header('Content-Length',str(len(body))) if 'Content-Length' in handler.headers else handler.headers.__setitem__('Content-Length',str(len(body)))
        handler.rfile=io.BytesIO(body);handler.wfile=io.BytesIO();out={'status':200,'headers':[]}
        handler.send_response=lambda code,*args:out.update(status=code)
        handler.send_header=lambda name,value:out['headers'].append((name,value))
        handler.end_headers=lambda:None
        try:
            if request.method=='GET':handler.do_GET()
            else:handler.do_POST()
        except Exception:
            web.logger.exception('API request failed')
            return {'error':'Request failed; retry or check function logs'},500
        return Response(handler.wfile.getvalue(),status=out['status'],headers=out['headers'])
    web.consentdesk_root=root_app
    return web
