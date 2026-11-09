import copy
from datetime import datetime
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid
from consentdesk.config import Settings
from consentdesk.storage import Store
from consentdesk.durable import DurableQueue
from consentdesk.serverless import create_app, ServerlessApplication, startup_failure
from consentdesk.database import namespace, postgres_sql
from test_research import temporary_directory


def fixture(symbol='INFY'):
    return {'created_at':datetime.now().astimezone().isoformat(),'mode':'longterm','symbols':[symbol],
        'question':'fixture','demo':False,'market':{'label':'fixture'},'trace':[],'duration_ms':1,'capital':1000,'risk_pct':1,
        'recommendations':[{'symbol':symbol,'name':symbol,'sector':'Tech','status':'RESEARCH ONLY','score':30,'metrics':{'available':True,'age_minutes':0},'news':[],'risks':[],'gaps':[],'reasons':[]}]}


class ServerlessTests(unittest.TestCase):
    def setup_app(self,tmp):
        settings=Settings();settings.data_dir=Path(tmp);settings.public_data_approved=True;settings.broker='none';settings.upstox_token='';settings.ai_key='';settings.ai_model='';settings.daily_scan_enabled=False
        return create_app(settings,require_database=False)

    def test_queue_survives_process_recreation_and_lease_prevents_duplicate_claim(self):
        with temporary_directory() as tmp:
            first=DurableQueue(Store(Path(tmp)))
            job_id=first.enqueue('shared','research',{'mode':'longterm'})
            second=DurableQueue(Store(Path(tmp)));job=second.claim(job_id)
            self.assertIsNone(first.claim(job_id))
            job['position']=1;job['status']='queued';self.assertTrue(second.checkpoint(job))
            recovered=first.claim(job_id);self.assertEqual(recovered['position'],1)
            recovered['lease']='wrong';self.assertFalse(first.checkpoint(recovered))
            with first.store.connect() as db:db.execute('UPDATE cd_queue SET lease_until=0 WHERE id=?',(job_id,))
            self.assertIsNotNone(second.claim(job_id))

    def test_job_owner_isolation(self):
        with temporary_directory() as tmp:
            queue=DurableQueue(Store(Path(tmp)))
            job_id=queue.enqueue('a'*32,'research',{})
            self.assertIsNone(queue.visible(job_id,'b'*32))
            self.assertEqual(queue.visible(job_id,'a'*32)['status'],'running')

    def test_scheduler_enqueues_each_sector_once_and_keeps_old_brief(self):
        with temporary_directory() as tmp:
            web=self.setup_app(tmp);root=web.consentdesk_root();root.settings.daily_scan_enabled=True
            prior=fixture();prior.update(daily_scan=True,brief_version=2,sector='healthcare',mode='intraday')
            root.store.save('common-workspace','common','previous',prior)
            with patch('consentdesk.serverless.now_ist',return_value=datetime.fromisoformat('2026-11-09T07:00:00+05:30')),patch.object(root.queue,'advance'):
                root.tick();root.tick()
            with root.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM cd_queue').fetchone()[0],33)
            self.assertEqual(root.daily_snapshot('intraday','healthcare')['report']['id'],prior['id'])

    def test_daily_checkpoints_do_not_replace_saved_brief_until_finalization(self):
        with temporary_directory() as tmp:
            web=self.setup_app(tmp);root=web.consentdesk_root()
            prior=fixture();prior.update(daily_scan=True,brief_version=2,sector='healthcare')
            root.store.save('common-workspace','common','previous',prior)
            task=root.queue.enqueue('shared','daily',{'mode':'longterm','sector':'healthcare'})
            job=root.queue.claim(task);job.update(scope=['INFY','TCS'],discovery={'symbols':['INFY','TCS']},status='queued')
            root.queue.checkpoint(job)
            def report(*a,**k):
                r=fixture(k['candidate_symbols'][0]);r.update(daily_scan=True,brief_version=2,sector='healthcare');return r
            with patch('consentdesk.durable.DailyBrief.run',side_effect=report):
                root.queue.advance(task,root)
                self.assertEqual(root.daily_snapshot('longterm','healthcare')['report']['id'],prior['id'])
                # A new queue object sees the same checkpoint without redoing INFY.
                root.queue=DurableQueue(root.store);root.queue.advance(task,root);root.queue.advance(task,root)
            self.assertEqual(root.daily_snapshot('longterm','healthcare')['report']['id'],task)
            self.assertEqual(root.queue.get(task)['status'],'complete')

    def test_paper_ledger_persists_and_two_accounts_stay_separate(self):
        with temporary_directory() as tmp:
            web=self.setup_app(tmp);root=web.consentdesk_root()
            a=root.workspace('a'*32);b=root.workspace('b'*32)
            draft={'symbol':'INFY','mode':'intraday','entry':100,'stop':95,'target':110,'quantity':10,'note':'test'}
            trade=a.desk.open_trade(draft)
            self.assertEqual(len(b.desk.snapshot()['open_trades']),0)
            a.desk.close_trade({'id':trade['id'],'price':110,'reason':'test'})
            restarted=self.setup_app(tmp).consentdesk_root().workspace('a'*32)
            self.assertEqual(restarted.desk.snapshot()['trade_count'],1)

    def test_request_driven_research_and_cold_start_history(self):
        with temporary_directory() as tmp:
            web=self.setup_app(tmp);client=web.test_client();url='https://example.com'
            with patch('threading.Thread.start',side_effect=AssertionError('No background worker')):
                response=client.post('/api/auth/register',json={'email':'test@example.com','password':'long-test-password'},base_url=url)
                self.assertEqual(response.status_code,200)
                csrf=response.json['user']['csrf'];user_id=response.json['user']['id']
                owner=web.consentdesk_root().workspace(user_id)
                owner.agent=SimpleNamespace(run=lambda question,mode,scope,*a,**k:fixture(scope[0]),fallback_answer=lambda *a:'Fixture summary')
                submitted=client.post('/api/research',json={'question':'Compare INFY.NS and TCS.NS','mode':'longterm','watchlist':[]},headers={'X-CSRF-Token':csrf},base_url=url)
                self.assertEqual(submitted.status_code,202)
                path='/api/jobs/'+submitted.json['job_id']
                # GET is read-only. Advancing requires a CSRF-protected POST.
                self.assertEqual(client.get(path,base_url=url).json['status'],'running')
                self.assertEqual(client.post(path,json={},base_url=url).status_code,403)
                for _ in range(4):result=client.post(path,json={},headers={'X-CSRF-Token':csrf},base_url=url)
                self.assertEqual(result.status_code,200)
                self.assertEqual(result.json['status'],'complete')
                self.assertEqual(len(result.json['report']['recommendations']),2)
                token=client.get_cookie('cd-auth',domain='example.com').value
                restarted=self.setup_app(tmp).test_client();restarted.set_cookie('cd-auth',token,domain='example.com')
                history=restarted.get('/api/history',base_url=url)
                self.assertEqual(history.status_code,200)
                self.assertEqual(history.json['reports'][0]['id'],submitted.json['job_id'])
                self.assertEqual(restarted.get('/api/config',base_url=url).json['stream']['state'],'SNAPSHOT POLLING')

    def test_missing_database_is_clear_503_and_never_falls_back_to_local_storage(self):
        with patch.dict(os.environ,{'DATABASE_URL':''}):
            client=create_app().test_client()
            result=client.get('/api/auth/me')
            self.assertEqual(result.status_code,503)
            self.assertIn('DATABASE_URL',result.json['error'])
            self.assertEqual(result.json['code'],'DATABASE_URL_MISSING')

    def test_startup_diagnostics_do_not_leak_connection_secrets(self):
        secret='postgresql://private-user:private-password@private-host/postgres'
        cases=[(ValueError(secret),'BACKEND_STARTUP_FAILED'),
               (RuntimeError('password authentication failed '+secret),'DATABASE_AUTH_FAILED'),
               (RuntimeError('failed to resolve host '+secret),'DATABASE_HOST_INVALID'),
               (OSError('read-only filesystem '+secret),'DATA_DIRECTORY_FAILED')]
        for error,expected in cases:
            code,message=startup_failure(error)
            self.assertEqual(code,expected)
            self.assertNotIn(secret,message)
        settings=Settings();settings.public_data_approved=False
        with patch.dict(os.environ,{'DATABASE_URL':secret}):
            with self.assertLogs('consentdesk.serverless',level='ERROR') as logs:
                result=create_app(settings).test_client().get('/api/health')
            self.assertEqual(result.json['code'],'PUBLIC_DATA_NOT_APPROVED')
            self.assertNotIn(secret,str(result.json)+str(logs.output))

    def test_rewritten_vercel_path_reaches_the_api(self):
        with temporary_directory() as tmp:
            web=self.setup_app(tmp);client=web.test_client()
            response=client.get('/api/index?__path=/api/health')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json,{'status':'ok'})

    def test_early_brief_does_not_block_morning_refresh(self):
        with temporary_directory() as tmp:
            root=self.setup_app(tmp).consentdesk_root()
            with patch('consentdesk.serverless.now_ist',return_value=datetime.fromisoformat('2026-11-09T06:00:00+05:30')):
                early=root.daily('intraday')['job_id']
            with patch('consentdesk.serverless.now_ist',return_value=datetime.fromisoformat('2026-11-09T07:00:00+05:30')):
                morning=root.daily('intraday')['job_id']
            self.assertNotEqual(early,morning)

    def test_cron_auth_and_idempotent_daily_scheduling(self):
        with temporary_directory() as tmp,patch.dict(os.environ,{'CRON_SECRET':'x'*40}):
            web=self.setup_app(tmp);client=web.test_client();root=web.consentdesk_root()
            self.assertEqual(client.get('/api/cron').status_code,401)
            with patch.object(root,'tick',return_value={'status':'ok'}) as tick:
                self.assertEqual(client.get('/api/cron',headers={'Authorization':'Bearer '+'x'*40}).status_code,200)
                tick.assert_called_once()
            first=root.daily('intraday',sector='healthcare');second=root.daily('intraday',sector='healthcare')
            self.assertEqual(first['job_id'],second['job_id'])

    def test_sql_adapter_ordering_inserts_and_namespaces(self):
        self.assertEqual(postgres_sql('INSERT OR IGNORE INTO reports VALUES(?,?,?,?,?,?)'),'INSERT INTO reports (id,session,profile,created_at,question,payload) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING')
        query=postgres_sql('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,payload TEXT)')
        self.assertIn('payload TEXT, _order BIGSERIAL UNIQUE)',query)
        self.assertNotEqual(namespace(Path('data/research.sqlite3')),namespace(Path('data/users')/('a'*32)/'research.sqlite3'))
        with self.assertRaises(ValueError):namespace(Path('data/users/invalid/research.sqlite3'))


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'Requires isolated PostgreSQL test instance')
class PostgresIntegrationTests(ServerlessTests):
    def setUp(self):
        self.env=patch.dict(os.environ,{'DATABASE_URL':os.environ['TEST_DATABASE_URL'],'DATABASE_NAMESPACE':'test_'+uuid.uuid4().hex[:12]})
        self.env.start()
    def tearDown(self):self.env.stop()


if __name__=='__main__':unittest.main()
