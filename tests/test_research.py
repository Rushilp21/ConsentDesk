import copy
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer

from consentdesk.config import Settings
from consentdesk.providers import Providers, ProviderError, IST, parse_news, sentiment
from consentdesk.analysis import indicators, score_evidence, intraday_plan, analyse_options
from consentdesk.agent import ResearchAgent, resolve_symbols, requested_mode
from consentdesk.storage import Store
from consentdesk.net import TTLCache
from consentdesk import llm
from consentdesk.execution import DisabledExecution, OrderIntent
from server import Application, make_handler, validate_request

NOW = datetime(2026, 10, 9, 13, 0, tzinfo=IST)
TEST_TEMP = Path(__file__).resolve().parents[1] / 'tmp' / 'tests'
TEST_TEMP.mkdir(parents=True, exist_ok=True)

def temporary_directory():
    return tempfile.TemporaryDirectory(dir=TEST_TEMP)

def base_request():
    return {'session': 'test-browser-session-1234', 'profile': 'common', 'question': 'Compare Infosys and TCS',
            'mode': 'longterm', 'capital': 50000, 'risk_pct': 1, 'watchlist': ['INFY', 'TCS'], 'demo': True}

def fixture_bundle():
    rows=[]
    for i in range(35):
        c=100+i*.15+math.sin(i)*.5
        rows.append({'t': int(NOW.timestamp())-(35-i)*300, 'o': c-.1, 'h': c+.6, 'l': c-.7, 'c': c, 'v': 1000 if i<34 else 2000})
    return {'candles': rows, 'source': 'Fixture', 'broker': True, 'synthetic': False}

def fixture_news():
    return [{'id': 'n1', 'title': 'Company wins major contract', 'publisher': 'Publisher A', 'published_at': NOW.isoformat(), 'sentiment': 1, 'synthetic': False},
            {'id': 'n2', 'title': 'Company reports growth', 'publisher': 'Publisher B', 'published_at': NOW.isoformat(), 'sentiment': 1, 'synthetic': False}]

def ready_metrics():
    return {'available': True, 'as_of': (NOW-timedelta(minutes=5)).isoformat(), 'trend': 'Bullish',
            'price': 100, 'resistance': 101, 'rsi14': 55, 'relative_volume': 1.5, 'vwap': 99, 'atr14': 1.33}

class DataTests(unittest.TestCase):
    def test_weak_growth_is_not_a_positive_catalyst(self):
        self.assertLess(sentiment('India IT set for fourth year of weak growth on geopolitics, AI risks'),0)

    def test_upstox_adapters_parse_real_response_shapes(self):
        settings=Settings();settings.upstox_token='test-token';settings.broker='upstox'
        p=Providers(settings)
        from consentdesk.net import CACHE
        with CACHE.lock:
            CACHE.items.clear()
        broker_candles={'status':'success','data':{'candles':[
            ['2026-10-09T12:55:00+05:30',100,102,99,101,1200,0],
            ['2026-10-09T12:50:00+05:30',99,101,98,100,1100,0]]}}
        with patch('consentdesk.providers.request_json',return_value=broker_candles) as mocked,patch('consentdesk.providers.now_ist',return_value=NOW):
            b=p.candles('INFY','intraday')
            self.assertTrue(b['broker'])
            self.assertEqual(b['candles'][-1]['c'],101)
            self.assertIn('/intraday/NSE_EQ%7CINE009A01021/minutes/5',mocked.call_args.args[0])
        with patch('consentdesk.providers.request_json',return_value={'status':'success','data':{'status':'NORMAL_OPEN'}}):
            self.assertTrue(p.market_status()['open'])
        def option_response(url,**kwargs):
            return {'status':'success','data':[{'expiry':'2026-10-15'}] if '/option/contract?' in url else [{'expiry':'2026-10-15','strike_price':25000}]}
        with patch('consentdesk.providers.request_json',side_effect=option_response) as mocked,patch('consentdesk.providers.now_ist',return_value=NOW):
            result=p.options('NIFTY','current_week')
            self.assertEqual(result['rows'][0]['strike_price'],25000)
            self.assertIn('expiry_date=2026-10-15',mocked.call_args.args[0])
        with CACHE.lock:
            CACHE.items.clear()

    def test_news_dedup_age_future_and_invalid_link(self):
        xml='''<rss><channel>
        <item><title>Profit rises - A</title><link>https://example.com/a</link><pubDate>Fri, 09 Oct 2026 06:00:00 GMT</pubDate><source>A</source></item>
        <item><title>Profit rises - B</title><link>https://example.com/b</link><pubDate>Fri, 09 Oct 2026 06:00:00 GMT</pubDate><source>B</source></item>
        <item><title>Old story</title><link>https://example.com/c</link><pubDate>Fri, 02 Oct 2026 06:00:00 GMT</pubDate></item>
        <item><title>Future story</title><link>https://example.com/d</link><pubDate>Sat, 10 Oct 2026 06:00:00 GMT</pubDate></item>
        <item><title>Bad link</title><link>javascript:alert(1)</link><pubDate>Fri, 09 Oct 2026 06:00:00 GMT</pubDate></item>
        </channel></rss>'''
        result=parse_news(xml,'INFY',NOW,days=2)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['sentiment'],1)

    def test_package_rejects_nonfinite_and_broken_ohlc(self):
        with self.assertRaises(ProviderError):
            Providers._package([{'t':1,'o':100,'h':99,'l':100,'c':100,'v':0}], 'bad', False, False)
        with self.assertRaises(ProviderError):
            Providers._package([{'t':1,'o':100,'h':101,'l':99,'c':float('nan'),'v':0}], 'bad', False, False)

    def test_vwap_single_session_and_incomplete_bar_removed(self):
        b=fixture_bundle()
        prior={**b['candles'][0], 't': int((NOW-timedelta(days=1)).timestamp()), 'c': 999,'h':1000,'o':999,'l':998,'v':1000000}
        b['candles'].insert(0,prior)
        b['candles'].append({**b['candles'][-1], 't': int(NOW.timestamp()), 'c':200})
        m=indicators(b,'intraday',NOW)
        self.assertEqual(m['sample_count'],35)
        self.assertLess(m['vwap'],110)
        self.assertLess(m['price'],110)
        self.assertEqual(m['age_minutes'],5)

    def test_daily_current_bar_excluded_before_close(self):
        b=fixture_bundle()
        for i,r in enumerate(b['candles']):r['t']=int((NOW-timedelta(days=34-i)).timestamp())
        m=indicators(b,'longterm',NOW)
        self.assertEqual(m['sample_count'],34)
        self.assertIsNone(m['vwap'])

    def test_flat_rsi(self):
        b=fixture_bundle()
        for r in b['candles']:r.update(o=100,h=100,l=100,c=100)
        m=indicators(b,'intraday',NOW)
        self.assertEqual(m['rsi14'],50)
        self.assertEqual(m['atr14'],0)

    def test_missing_news_does_not_get_neutral_credit(self):
        score=score_evidence(ready_metrics(),[],None,'intraday',NOW)
        self.assertEqual(score['components']['news'],0)

    def test_cache_is_copy_and_keeps_original_timestamp(self):
        cache=TTLCache()
        first=cache.get_or_load('a',10,lambda:{'as_of':'original','items':[1]})
        first['items'].append(2)
        second=cache.get_or_load('a',10,lambda:self.fail('cache miss'))
        self.assertEqual(second,{'as_of':'original','items':[1]})

    def test_fundamentals_missing_stale_future_and_valid(self):
        with temporary_directory() as tmp:
            settings=Settings();settings.upstox_token='';settings.fundamentals_file=str(Path(tmp)/'f.json')
            p=Providers(settings)
            with self.assertRaises(ProviderError):p.fundamentals('INFY')
            f={'as_of':'2026-10-09','source_url':'https://example.com/filing','revenue_growth_pct':5,'earnings_growth_pct':4,'roe_pct':18,'debt_to_equity':.2,'pe':20,'operating_cash_flow':100}
            with patch('consentdesk.providers.now_ist',return_value=NOW):
                for dated in ['2020-01-01','2030-01-01']:
                    f['as_of']=dated;Path(settings.fundamentals_file).write_text(json.dumps({'INFY':f}))
                    with self.assertRaises(ProviderError):p.fundamentals('INFY')
                f['as_of']='2026-10-09';Path(settings.fundamentals_file).write_text(json.dumps({'INFY':f}))
                self.assertEqual(p.fundamentals('INFY')['roe_pct'],18)

class RiskTests(unittest.TestCase):
    def setUp(self):
        self.m=ready_metrics();self.b=fixture_bundle();self.s={'publisher_count':2,'headline_tone':1};self.market={'verified':True,'open':True}

    def plan(self,**kwargs):
        return intraday_plan(self.m,self.b,self.s,self.market,kwargs.get('capital',50000),kwargs.get('risk',1),kwargs.get('demo',False),NOW)

    def test_valid_setup_bounds_risk_and_cash(self):
        p=self.plan()
        self.assertEqual(p['status'],'CONDITIONAL SETUP')
        self.assertLessEqual(p['quantity']*p['entry_above'],50000)
        self.assertLessEqual(p['quantity']*(p['entry_above']-p['stop']),500)
        self.assertLess(p['stop'],p['entry_above'])
        self.assertGreater(p['target'],p['entry_above'])

    def test_no_plan_with_synthetic_or_public_data(self):
        self.assertEqual(self.plan(demo=True)['status'],'WAIT')
        self.b['synthetic']=True;self.assertEqual(self.plan()['status'],'WAIT')
        self.b['synthetic']=False;self.b['broker']=False;self.assertEqual(self.plan()['status'],'WAIT')

    def test_closed_or_unverified_session_blocks(self):
        self.market['open']=False;self.assertEqual(self.plan()['status'],'WAIT')
        self.market.update(open=True,verified=False);self.assertEqual(self.plan()['status'],'WAIT')

    def test_stale_or_future_candles_block(self):
        for delta in [timedelta(hours=-1),timedelta(days=-1),timedelta(minutes=5)]:
            self.m['as_of']=(NOW+delta).isoformat();self.assertEqual(self.plan()['status'],'WAIT')

    def test_small_budget_zero_quantity_blocks(self):
        self.assertEqual(self.plan(capital=100,risk=.1)['status'],'WAIT')

    def test_insufficient_news_or_momentum_blocks(self):
        self.s['publisher_count']=1;self.assertEqual(self.plan()['status'],'WAIT')
        self.s['publisher_count']=2;self.m['rsi14']=90;self.assertEqual(self.plan()['status'],'WAIT')

    def test_options_filter_bad_spreads_and_expired_contracts(self):
        valid={'expiry':'2026-10-15','strike_price':25000,'call_options':{'instrument_key':'NSE_FO|123','market_data':{'bid_price':100,'ask_price':102,'volume':1000,'oi':5000},'option_greeks':{'delta':.5,'theta':-1,'iv':15}}}
        bad=copy.deepcopy(valid);bad['call_options']['market_data']['ask_price']=150
        old=copy.deepcopy(valid);old['expiry']='2025-01-01'
        result=analyse_options({'rows':[bad,old,valid],'fetched_at':NOW.isoformat(),'source':'Fixture','note':'Snapshot'},self.market,NOW)
        self.assertEqual(len(result['contracts']),1)
        self.assertEqual(result['status'],'CHAIN RESEARCH ONLY')
        self.assertNotIn('quantity',result['contracts'][0])

    def test_execution_disabled(self):
        with self.assertRaises(RuntimeError):DisabledExecution().submit_confirmed(OrderIntent('INFY','BUY',1,100,'abc'),'fake')

class AgentTests(unittest.TestCase):
    def test_resolves_alias_custom_and_compound_index(self):
        self.assertEqual(resolve_symbols('Compare Infosys and TCS'),['TCS','INFY'])
        self.assertEqual(resolve_symbols('Bank Nifty options'),['BANKNIFTY'])
        self.assertEqual(resolve_symbols('Research TATAMOTORS.NS'),['TATAMOTORS'])
        self.assertEqual(requested_mode('intraday research today','longterm'),'intraday')

    def test_demo_runs_tools_and_blocks_plans(self):
        settings=Settings();settings.ai_key='';settings.ai_model=''
        progress=[]
        report=ResearchAgent(settings).run('Intraday Infosys','longterm',[],50000,1,True,[],progress=progress.append)
        self.assertEqual(report['mode'],'intraday')
        self.assertEqual(report['symbols'],['INFY'])
        self.assertTrue(report['demo'])
        self.assertEqual(report['recommendations'][0]['plan']['status'],'WAIT')
        self.assertIn('Synthetic demo',report['answer'])
        self.assertGreater(len(progress),5)

    def test_followup_uses_prior_symbols(self):
        settings=Settings();settings.ai_key=''
        r=ResearchAgent(settings).run('What are the risks for these?','longterm',['RELIANCE'],50000,1,True,[{'symbols':['TCS','INFY']}])
        self.assertEqual(r['symbols'],['TCS','INFY'])

    def test_all_provider_failures_are_honest(self):
        class Offline:
            def market_status(self,demo):return {'verified':False,'open':False,'label':'Unverified'}
            def news(self,*args):raise ProviderError('offline')
            def candles(self,*args):raise ProviderError('offline')
            def fundamentals(self,*args):raise ProviderError('missing')
        settings=Settings();settings.ai_key=''
        r=ResearchAgent(settings,Offline()).run('Infosys','longterm',[],50000,1,False,[])
        self.assertIn('could not obtain enough evidence',r['answer'])
        self.assertEqual(r['recommendations'][0]['score'],0)
        self.assertEqual(r['recommendations'][0]['status'],'RESEARCH ONLY')

    def test_ai_failure_falls_back(self):
        settings=Settings();settings.ai_key='test';settings.ai_model='test'
        providers=Providers(settings)
        class Fixtures:
            def market_status(self,demo):return {'verified':False,'open':False,'label':'Unverified'}
            def news(self,symbol,mode,demo):return providers.news(symbol,mode,True)
            def candles(self,symbol,mode,demo):return providers.candles(symbol,mode,True)
            def fundamentals(self,*args):raise ProviderError('missing')
        with patch('consentdesk.llm.plan',side_effect=ProviderError('AI unavailable')),patch('consentdesk.llm.explain',side_effect=ProviderError('AI unavailable')):
            r=ResearchAgent(settings,Fixtures()).run('Infosys','longterm',[],50000,1,False,[])
        self.assertEqual(r['engine'],'Deterministic research')
        self.assertTrue(any(t['status']=='fallback' for t in r['trace']))

    def test_llm_tool_arguments_and_continuation(self):
        settings=Settings();settings.ai_key='test';settings.ai_model='test'
        planned_response={'output':[{'type':'function_call','name':'research_market','call_id':'call1','arguments':json.dumps({'symbols':['INFY'],'focus':'news'})}]}
        with patch('consentdesk.llm.response',return_value=planned_response):
            planned=llm.plan(settings,'Infosys',[],['INFY'],'longterm')
            self.assertEqual(planned['symbols'],['INFY'])
        with patch('consentdesk.llm.response',return_value={'output':[{'type':'message','content':[{'type':'output_text','text':'Evidence commentary'}]}]}) as mocked:
            answer=llm.explain(settings,'Infosys',[],{'mode':'longterm','recommendations':[],'market':{},'demo':False},planned)
            self.assertEqual(answer,'Evidence commentary')
            self.assertEqual(mocked.call_args.args[1]['input'][-1]['type'],'function_call_output')
        planned_response['output'][0]['arguments']='{"symbols":["FAKE"],"focus":""}'
        with patch('consentdesk.llm.response',return_value=planned_response):
            with self.assertRaises(ProviderError):llm.plan(settings,'x',[],['INFY'],'longterm')

class ServerTests(unittest.TestCase):
    def test_validation(self):
        for key,value in [('capital',float('nan')),('risk_pct',3),('demo','false'),('watchlist',['BAD/URL']),('mode','fake'),('expiry','2000-01-01'),('profile','other')]:
            data=base_request();data[key]=value
            with self.subTest(key=key):
                with self.assertRaises(ValueError):validate_request(data)

    def test_storage_survives_restart_and_scopes_profile(self):
        with temporary_directory() as tmp:
            store=Store(Path(tmp));report={'created_at':NOW.isoformat(),'question':'x'}
            store.save('s','rushil','x',report)
            self.assertEqual(len(Store(Path(tmp)).history('s','rushil')),1)
            self.assertEqual(store.history('s','parents'),[])
            self.assertIsNotNone(store.get(report['id'],'s','rushil'))
            self.assertIsNone(store.get(report['id'],'s','parents'))
            store.clear('s','rushil');self.assertEqual(store.history('s','rushil'),[])

    def test_http_job_poll_history_and_validation(self):
        with temporary_directory() as tmp:
            settings=Settings();settings.data_dir=Path(tmp);settings.ai_key='';settings.upstox_token=''
            app=Application(settings)
            server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(app))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(base+'/') as r:self.assertIn(b'ConsentDesk',r.read())
                data=base_request()
                req=Request(base+'/api/research',data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})
                with urlopen(req) as r:
                    self.assertEqual(r.status,202);job_id=json.load(r)['job_id']
                path=f'/api/jobs/{job_id}?session={data["session"]}&profile=common'
                for _ in range(100):
                    with urlopen(base+path) as r:job=json.load(r)
                    if job['status']!='running':break
                    time.sleep(.01)
                self.assertEqual(job['status'],'complete')
                self.assertEqual(job['report']['symbols'],['TCS','INFY'])
                with urlopen(base+'/api/history?session='+data['session']+'&profile=common') as r:self.assertEqual(len(json.load(r)['reports']),1)
                with self.assertRaises(HTTPError) as caught:urlopen(base+path.replace('common','other'))
                self.assertEqual(caught.exception.code,400)
                with self.assertRaises(HTTPError) as caught:urlopen(Request(base+'/api/research',data=b'{}'))
                self.assertEqual(caught.exception.code,400)
            finally:
                server.shutdown();server.server_close();thread.join()

if __name__ == '__main__':
    unittest.main()
