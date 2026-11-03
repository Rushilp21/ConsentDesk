import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import struct
import threading
import unittest
from html.parser import HTMLParser
import re
import time
from unittest.mock import Mock, patch
from urllib.request import Request, build_opener, HTTPCookieProcessor
from urllib.error import HTTPError

from consentdesk.accounts import Accounts
from consentdesk.config import Settings
from consentdesk.daily import DailyBrief, BRIEF_VERSION
from consentdesk.sectors import SECTORS, catalogue, metadata, sector_value
from consentdesk.reasoning import analyse, news_review, rank_key
from consentdesk.streaming import decode, fields, MarketStream
from consentdesk.storage import Store
from consentdesk.net import TTLCache, ProviderError
from server import Application, make_handler
from test_research import NOW, temporary_directory
from test_morning_brief import candles
from test_trading_desk import draft
from scripts.backup import backup


def varint(value):
    result=bytearray()
    while value>127:result.append((value&127)|128);value>>=7
    result.append(value);return bytes(result)


def pb(field,value):
    if isinstance(value,bytes):return varint(field*8+2)+varint(len(value))+value
    if isinstance(value,float):return varint(field*8+1)+struct.pack('<d',value)
    return varint(field*8)+varint(value)


def packet(kind=1,price=100,when=NOW):
    ltpc=pb(1,float(price))+pb(2,int(when.timestamp()*1000))+pb(4,99.0)
    depth=pb(1,pb(1,20)+pb(2,99.9)+pb(3,30)+pb(4,100.1))
    market=pb(1,ltpc)+pb(2,depth)+pb(6,12345)
    feed=pb(2,pb(1,market))
    pair=pb(1,b'NSE_EQ|INE009A01021')+pb(2,feed)
    return pb(1,kind)+pb(2,pair)+pb(3,int(when.timestamp()*1000))


class StreamingTests(unittest.TestCase):
    def test_stream_sends_binary_subscription_and_reconnects_without_leaking_token(self):
        settings=Settings();settings.upstox_token='secret';settings.broker='upstox';settings.stream_enabled=True
        provider=Mock(settings=settings);provider.symbol_key.return_value='NSE_EQ|INE009A01021'
        provider._broker.return_value={'authorized_redirect_uri':'wss://feed.example.invalid/session'}
        stop=threading.Event();socket=Mock();socket.recv.side_effect=lambda:(stop.set() or packet())
        stream=MarketStream(provider,stop);stream.subscribe(['INFY'])
        with patch('websocket.create_connection',return_value=socket):
            stream.start()
            for _ in range(100):
                if stream.last_packet and not stream.connected:break
                time.sleep(.01)
        self.assertIsNotNone(stream.last_packet)
        payload=socket.send_binary.call_args.args[0]
        self.assertIsInstance(payload,bytes);self.assertEqual(json.loads(payload)['data']['mode'],'full')
        self.assertNotIn('secret',payload.decode())
        self.assertEqual(stream.snapshot(['INFY'])['stream']['connected'],False)

    def test_official_full_schema_price_time_depth_and_volume(self):
        q=decode(packet())['NSE_EQ|INE009A01021']
        self.assertEqual(q['price'],100);self.assertEqual(q['volume'],12345)
        self.assertEqual(q['bid'],99.9);self.assertEqual(q['ask'],100.1)
        self.assertEqual(q['as_of'],NOW.isoformat());self.assertTrue(q['stream_tick'])
        self.assertFalse(decode(packet(kind=0))['NSE_EQ|INE009A01021']['stream_tick'])

    def test_bad_packets_and_invalid_prices_do_not_create_live_quotes(self):
        for raw in [b'\x00',b'\x12\xff',b'\x09\x00',b'\x80'*20]:
            with self.assertRaises(ValueError):fields(raw)
        self.assertEqual(decode(packet(price=float('nan'))),{})
        self.assertEqual(decode(packet(price=-1)),{})

    def test_initial_stale_disconnected_and_future_packets_not_live(self):
        provider=Mock();provider.settings.upstox_token='fixture';stream=MarketStream(provider,threading.Event())
        q=decode(packet())['NSE_EQ|INE009A01021'];stream.quotes={'INFY':{'symbol':'INFY',**q}}
        with patch('consentdesk.streaming.now_ist',return_value=NOW):
            self.assertFalse(stream.snapshot(['INFY'])['quotes'][0]['live'])
            stream.connected=True;self.assertTrue(stream.snapshot(['INFY'])['quotes'][0]['live'])
            stream.quotes['INFY']['stream_tick']=False;self.assertFalse(stream.snapshot(['INFY'])['quotes'][0]['live'])
        with patch('consentdesk.streaming.now_ist',return_value=NOW+timedelta(seconds=31)):self.assertEqual(stream.snapshot(['INFY'])['quotes'],[])
        with patch('consentdesk.streaming.now_ist',return_value=NOW-timedelta(seconds=1)):self.assertEqual(stream.snapshot(['INFY'])['quotes'],[])

    def test_subscriptions_are_reused_bounded_and_tokens_not_exposed(self):
        provider=Mock();provider.settings.upstox_token='secret';provider.symbol_key.side_effect=lambda s:'NSE_EQ|'+s
        stream=MarketStream(provider,threading.Event());stream.subscribe(['INFY']*5)
        self.assertEqual(provider.symbol_key.call_count,1)
        stream.subscribe(['S'+str(i) for i in range(210)])
        self.assertLessEqual(len(stream.wanted),200)
        self.assertNotIn('secret',json.dumps(stream.snapshot([])))

    def test_market_requests_are_single_flight(self):
        cache=TTLCache();entered=threading.Event();release=threading.Event();calls=[]
        def load():calls.append(1);entered.set();release.wait(2);return {'value':1}
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs=[pool.submit(cache.get_or_load,'key',60,load) for _ in range(4)]
            entered.wait(2);release.set();results=[j.result() for j in jobs]
        self.assertEqual(len(calls),1);self.assertEqual(len(results),4)
        results[0]['value']=2;self.assertEqual(results[1]['value'],1)


class SectorTests(unittest.TestCase):
    def test_html_has_every_fixed_script_target_and_selected_sector_controls(self):
        root=Path(__file__).resolve().parents[1]
        html=(root/'web/index.html').read_text(encoding='utf-8').replace('<section id="desk-view" hidden></section>',(root/'web/desk-section.html').read_text(encoding='utf-8'))
        class IDs(HTMLParser):
            def __init__(self):super().__init__();self.ids=[]
            def handle_starttag(self,tag,attrs):
                self.ids.extend(v for k,v in attrs if k=='id')
        parser=IDs();parser.feed(html);self.assertEqual(len(parser.ids),len(set(parser.ids)))
        for name in ('app.js','dashboard.js','desk.js','auth.js'):
            source=(root/'web'/name).read_text(encoding='utf-8')
            targets=re.findall(r"\$\('#([a-zA-Z0-9-]+)'\)",source)
            for target in targets:
                if target not in ('pending','live-trace'):self.assertIn(target,parser.ids,(name,target))
        for target in ('daily-sector','modal-sector','auth-dialog'):self.assertIn(target,parser.ids)

    def test_category_metadata_and_validation(self):
        self.assertEqual(metadata('IRCTC')['sector'],'Railways')
        self.assertGreaterEqual(len(catalogue()),10)
        with self.assertRaises(ValueError):sector_value('../banking')

    def test_sector_specific_scan_returns_only_three_members_and_provenance(self):
        settings=Settings();settings.daily_candidate_limit=6
        provider=Mock();provider.desk.events.return_value=[];provider.kite.instruments.return_value=[];provider.market_news.return_value=[];provider.candles.return_value=candles()
        def run(question,mode,symbols,*args,**kwargs):
            return {'market':{'verified':False,'open':False,'label':'Fixture'},'recommendations':[
                {'symbol':s,'name':s,'sector':'Unclassified','metrics':{'available':True,'trend':'Bullish','rsi14':55,'relative_volume':1.5,'age_minutes':1440},
                 'score':50+i,'news':[],'reasons':['Fixture'],'risks':[],'status':'RESEARCH ONLY','chart_structure':{},'plan':None}
                for i,s in enumerate(symbols)]}
        agent=Mock(providers=provider,run=run)
        with patch('consentdesk.daily.now_ist',return_value=NOW.replace(hour=7)):
            result=DailyBrief(agent,settings).run('intraday',sector='healthcare')
        self.assertEqual(len(result['recommendations']),3)
        self.assertEqual(result['sector'],'healthcare')
        members={s for s,_ in SECTORS['healthcare'][1]}
        self.assertTrue(all(r['symbol'] in members for r in result['recommendations']))
        self.assertTrue(all('deep_analysis' in r for r in result['recommendations']))
        self.assertTrue(all(r['status'].startswith('WAIT') for r in result['recommendations']))

    def test_empty_price_scan_does_not_replace_saved_sector_brief(self):
        settings=Settings();provider=Mock();provider.desk.events.return_value=[];provider.kite.instruments.return_value=[];provider.market_news.return_value=[];provider.candles.side_effect=ProviderError('offline')
        agent=Mock(providers=provider);agent.run.return_value={'market':{},'recommendations':[{'symbol':'SUNPHARMA','name':'Sun','metrics':{'available':False},'score':0,'news':[],'reasons':[],'risks':[],'status':'WAIT'}]}
        with self.assertRaises(ProviderError):DailyBrief(agent,settings).run('longterm',sector='healthcare')

    def test_saved_briefs_and_jobs_survive_restart_without_cross_sector_leak(self):
        with temporary_directory() as tmp:
            store=Store(Path(tmp))
            for sector in ('healthcare','energy'):
                r={'created_at':NOW.isoformat(),'daily_scan':True,'mode':'intraday','sector':sector,'brief_version':BRIEF_VERSION}
                store.save('common-workspace','common',sector,r)
            self.assertEqual(store.daily_report('intraday',sector='healthcare')['sector'],'healthcare')
            self.assertIsNone(store.daily_report('intraday'))
            store.save_job('running',{'status':'running','session':'common-workspace','profile':'common'})
            self.assertEqual(Store(Path(tmp)).load_jobs()['running']['status'],'error')

    def test_rank_uses_usable_evidence_before_missing_data(self):
        a={'symbol':'A','metrics':{'available':False},'score':100,'deep_analysis':{'evidence_score':8}}
        b={'symbol':'B','metrics':{'available':True,'age_minutes':30},'score':20,'deep_analysis':{'evidence_score':1}}
        self.assertEqual(sorted([a,b],key=rank_key)[0]['symbol'],'B')


class EvidenceTests(unittest.TestCase):
    def test_news_recency_duplicates_conflicts_and_summary_scope(self):
        def article(title,tone,age=1):return {'id':title,'title':title,'publisher':'Publisher','published_at':(NOW-timedelta(hours=age)).isoformat(),'url':'https://example.com/article','sentiment':tone,'summary':'Provider summary only'}
        values=[article('Company wins contract',1),article('Company wins contract - Other',1),article('Company faces penalty',-1),article('Future earnings',1,-1),article('Old earnings',1,400)]
        r=news_review(values,NOW)
        self.assertEqual(len(r['events']),2);self.assertTrue(r['conflicting']);self.assertEqual(r['publisher_count'],1)
        self.assertIn('not independently verified',r['note'])

    def test_missing_financials_and_negative_patterns_are_explicit(self):
        rec={'metrics':{'available':True,'trend':'Bearish','rsi14':35,'relative_volume':.5},'chart_structure':{'patterns':[{'name':'Breakdown','direction':'bearish'}]},'news':[],'risks':[]}
        d=analyse(rec,'longterm')['deep_analysis']
        self.assertEqual(d['evidence_score'],0)
        self.assertTrue(any(c['name']=='Financial quality' and c['state']=='MISSING' for c in d['checks']))
        self.assertIn('bearish',d['pattern_summary']);self.assertFalse(d['sources'])


class AccountTests(unittest.TestCase):
    def test_database_backup_contains_separate_account_and_workspace_databases(self):
        with temporary_directory() as tmp:
            root=Path(tmp);data=root/'data';data.mkdir();a=Accounts(data/'accounts.sqlite3')
            a.register('backup@example.com','long-test-password');Store(data/'users'/'fixture')
            target,count=backup(data,root/'backups');self.assertEqual(count,2)
            self.assertTrue((target/'users'/'fixture'/'research.sqlite3').exists())
            restored=Accounts(target/'accounts.sqlite3');self.assertTrue(restored.login('backup@example.com','long-test-password'))
            with self.assertRaises(ValueError):backup(data,data/'backups')

    def test_passwords_sessions_expiry_revocation_and_atomic_daily_budget(self):
        with temporary_directory() as tmp:
            a=Accounts(Path(tmp)/'accounts.sqlite3');a.register('user@example.com','long-test-password')
            with a.db() as db:password=db.execute('SELECT password FROM users').fetchone()[0]
            self.assertNotEqual(password,'long-test-password')
            token,user=a.login('user@example.com','long-test-password');self.assertEqual(a.session(token),user)
            with self.assertRaises(ValueError):a.login('user@example.com','wrong')
            with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(lambda _:a.spend(user['id'],'research',2,'day'),range(4)))
            self.assertEqual(sum(results),2)
            a.logout(token);self.assertIsNone(a.session(token))
            self.assertTrue(a.allow('ip',1));self.assertFalse(a.allow('ip',1))

    def test_public_api_isolates_accounts_and_enforces_csrf(self):
        with temporary_directory() as tmp:
            settings=Settings();settings.data_dir=Path(tmp);settings.public_mode=True;settings.secure_cookies=False;settings.upstox_token='';settings.ai_key=''
            app=Application(settings);server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(app));thread=threading.Thread(target=server.serve_forever);thread.start()
            base='http://127.0.0.1:'+str(server.server_port)
            alice=build_opener(HTTPCookieProcessor(CookieJar()));bob=build_opener(HTTPCookieProcessor(CookieJar()));anon=build_opener()
            def call(client,path,data=None,csrf=None):
                headers={'Content-Type':'application/json'}
                if csrf:headers['X-CSRF-Token']=csrf
                with client.open(Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)) as r:return json.load(r)
            try:
                with self.assertRaises(HTTPError) as e:call(anon,'/api/desk')
                self.assertEqual(e.exception.code,401)
                users=[]
                for client,email in [(alice,'alice@example.com'),(bob,'bob@example.com')]:
                    d=call(client,'/api/auth/register',{'email':email,'password':'long-test-password'});users.append(d['user'])
                with self.assertRaises(HTTPError) as e:call(alice,'/api/paper/open',draft())
                self.assertEqual(e.exception.code,403)
                trade=call(alice,'/api/paper/open',draft(),users[0]['csrf'])
                self.assertEqual(len(call(alice,'/api/desk')['open_trades']),1)
                self.assertEqual(len(call(bob,'/api/desk')['open_trades']),0)
                with self.assertRaises(HTTPError):call(bob,'/api/paper/close',{'id':trade['id'],'price':104},users[1]['csrf'])
                self.assertIs(app.workspace(users[0]['id']).stream,app.workspace(users[1]['id']).stream)
                with app.workspace(users[0]['id']).store.connect() as db:db.execute('INSERT INTO reports VALUES(?,?,?,?,?,?)',('private','common-workspace','common',NOW.isoformat(),'secret','{"id":"private","question":"secret"}'))
                self.assertEqual(call(bob,'/api/history')['reports'],[])
                self.assertTrue(call(alice,'/api/history')['reports'])
                self.assertTrue(call(alice,'/api/config')['public_mode'])
                call(alice,'/api/auth/logout',{},users[0]['csrf'])
                with self.assertRaises(HTTPError):call(alice,'/api/desk')
            finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
