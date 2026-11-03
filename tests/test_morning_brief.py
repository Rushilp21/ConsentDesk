import copy
import threading
import time
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from consentdesk.daily import discover, morning_levels, DailyBrief, BRIEF_VERSION
from consentdesk.config import Settings
from consentdesk.storage import Store
from server import Application
from test_research import NOW, temporary_directory
from pathlib import Path

def candles(volume=1_000_000):
    rows=[{'t':int((NOW-timedelta(days=30-i)).timestamp()),'o':100,'h':102,'l':98,'c':101,'v':volume} for i in range(30)]
    return {'candles':rows,'synthetic':False,'broker':False}

class MorningBriefTests(unittest.TestCase):
    def saved_brief(self, app, created=NOW, mode='intraday'):
        return app.store.save('common-workspace','common','q',{
            'created_at':created.isoformat(),'question':'morning','mode':mode,
            'symbols':['INFY'],'demo':False,'daily_scan':True,'brief_version':BRIEF_VERSION})

    def test_previous_brief_survives_midnight_weekend_and_restart(self):
        with temporary_directory() as tmp:
            settings=Settings();settings.data_dir=Path(tmp)
            app=Application(settings);previous=self.saved_brief(app)
            for offset in (1,2,3):
                with patch('server.now_ist',return_value=NOW+timedelta(days=offset)):
                    snapshot=Application(settings).daily_snapshot('intraday')
                self.assertEqual(snapshot['report']['id'],previous['id'])
                self.assertTrue(snapshot['is_previous_day'])
                self.assertEqual(snapshot['brief_date'],str(NOW.date()))
                self.assertIsNone(snapshot['job_id'])

    def test_daily_lookup_survives_more_than_two_hundred_chat_reports(self):
        with temporary_directory() as tmp:
            settings=Settings();settings.data_dir=Path(tmp)
            app=Application(settings);previous=self.saved_brief(app)
            for i in range(205):
                app.store.save('common-workspace','common','chat',{'created_at':NOW.isoformat(),'mode':'intraday','daily_scan':False})
            self.assertEqual(app.daily_snapshot('intraday')['report']['id'],previous['id'])
            self.assertIsNone(app.store.daily_report('longterm',brief_version=BRIEF_VERSION))
            self.assertIsNone(app.store.daily_report('intraday',brief_version=BRIEF_VERSION+1))

    def test_new_daily_job_returns_previous_brief_and_ignores_missing_job_state(self):
        with temporary_directory() as tmp:
            settings=Settings();settings.data_dir=Path(tmp)
            app=Application(settings);previous=self.saved_brief(app)
            with patch('server.now_ist',return_value=NOW+timedelta(days=3)),patch.object(app,'submit',return_value='new') as submit:
                result=app.daily('intraday')
            submit.assert_called_once()
            self.assertEqual(result['report']['id'],previous['id'])
            self.assertIsNone(app.daily_snapshot('intraday')['job_id'])

    def test_old_brief_stays_until_worker_saves_complete_replacement(self):
        with temporary_directory() as tmp:
            settings=Settings();settings.data_dir=Path(tmp)
            app=Application(settings);previous=self.saved_brief(app)
            entered=threading.Event();release=threading.Event();next_day=NOW+timedelta(days=3)
            def scan(*args):
                entered.set();release.wait(3)
                return {'created_at':next_day.isoformat(),'question':'new','mode':'intraday','symbols':['TCS'],'demo':False,'brief_version':BRIEF_VERSION}
            try:
                with patch('server.now_ist',return_value=next_day),patch('server.DailyBrief.run',side_effect=scan):
                    job_id=app.daily('intraday')['job_id'];self.assertTrue(entered.wait(2))
                    during=app.daily_snapshot('intraday')
                    self.assertEqual(during['report']['id'],previous['id'])
                    self.assertEqual(during['job_id'],job_id)
                    release.set()
                    for _ in range(100):
                        if app.daily_snapshot('intraday')['status']!='running':break
                        time.sleep(.01)
                    complete=app.daily_snapshot('intraday')
                self.assertEqual(complete['status'],'complete')
                self.assertNotEqual(complete['report']['id'],previous['id'])
                self.assertEqual(complete['report']['symbols'],['TCS'])
                self.assertFalse(complete['is_previous_day'])
                self.assertEqual(len(app.store.history('common-workspace','common')),2)
            finally:release.set()

    def test_failed_worker_keeps_previous_brief(self):
        with temporary_directory() as tmp:
            settings=Settings();settings.data_dir=Path(tmp)
            app=Application(settings);previous=self.saved_brief(app)
            with patch('server.now_ist',return_value=NOW+timedelta(days=3)),patch('server.DailyBrief.run',side_effect=RuntimeError('fixture failure')),self.assertLogs('consentdesk',level='ERROR'):
                app.daily('intraday')
                for _ in range(100):
                    if app.daily_snapshot('intraday')['status']=='error':break
                    time.sleep(.01)
            snapshot=app.daily_snapshot('intraday')
            self.assertEqual(snapshot['status'],'error')
            self.assertEqual(snapshot['report']['id'],previous['id'])
            self.assertIsNone(snapshot['job_id'])
            self.assertEqual(len(app.store.history('common-workspace','common')),1)

    def test_news_discovery_includes_non_default_equities_and_dedupes(self):
        items=[{'tradingsymbol':s,'name':n,'instrument_type':'EQ'} for s,n in [('COFORGE','COFORGE LIMITED'),('DIXON','DIXON TECHNOLOGIES INDIA LTD')]]
        articles=[{'title':'Coforge wins major order','sentiment':1},{'title':'Dixon Technologies profit rises','sentiment':1}]
        result=discover(items,articles,['INFY','INFY'],18)
        self.assertIn('COFORGE',result['symbols']);self.assertIn('DIXON',result['symbols'])
        self.assertEqual(len(result['symbols']),len(set(result['symbols'])))
        self.assertEqual(result['news_matched_symbols'],2)

    def test_premarket_levels_ignore_incomplete_daily_bar(self):
        bundle=candles();today={'t':int(NOW.timestamp()),'o':1000,'h':1200,'l':900,'c':1100,'v':1000000}
        bundle['candles'].append(today)
        levels=morning_levels(bundle,NOW.replace(hour=7))
        self.assertEqual(levels['entry'],102.11)
        self.assertLess(levels['invalidation'],levels['entry'])
        self.assertGreater(levels['target'],levels['entry'])
        self.assertEqual(levels['risk_reward'],2)
        self.assertEqual(levels['expires_at'][11:16],'15:15')
        self.assertIn('09:15',levels['trigger'])

    def test_quote_pages_and_general_exchange_mentions_are_not_catalysts(self):
        items=[{'tradingsymbol':'BSE','name':'BSE LTD','instrument_type':'EQ'},{'tradingsymbol':'DIXON','name':'DIXON TECHNOLOGIES LTD','instrument_type':'EQ'}]
        articles=[{'title':'Sensex falls 100 points on BSE','sentiment':-1},{'title':'DIXON Share Price Today on NSE/BSE','sentiment':0}]
        result=discover(items,articles,['INFY'],6)
        self.assertEqual(result['matches'],{})
        articles.append({'title':'BSE shares rise after profit jumps','sentiment':1})
        self.assertIn('BSE',discover(items,articles,[],6)['matches'])

    def test_stale_daily_history_has_no_levels(self):
        bundle=candles()
        for row in bundle['candles']:row['t']-=10*86400
        self.assertIsNone(morning_levels(bundle,NOW))

    def test_scheduled_daily_report_is_reused_and_new_schema_filters_old(self):
        with temporary_directory() as tmp:
            settings=Settings();settings.data_dir=Path(tmp);settings.daily_scan_time='07:00'
            app=Application(settings)
            report={'created_at':NOW.replace(hour=7).isoformat(),'question':'morning','mode':'intraday','daily_scan':True,'brief_version':BRIEF_VERSION}
            app.store.save('common-workspace','common','q',report)
            with patch('server.now_ist',return_value=NOW),patch.object(app,'submit') as submit:
                self.assertEqual(app.daily('intraday')['status'],'complete');submit.assert_not_called()
            report['brief_version']=1
            self.assertIsNone(app.store.daily_report('longterm',str(NOW.date()),BRIEF_VERSION))

    def test_early_manual_report_does_not_suppress_seven_am_scan(self):
        with temporary_directory() as tmp:
            settings=Settings();settings.data_dir=Path(tmp);settings.daily_scan_time='07:00'
            app=Application(settings)
            report={'created_at':NOW.replace(hour=6).isoformat(),'question':'early','mode':'intraday','daily_scan':True,'brief_version':BRIEF_VERSION}
            app.store.save('common-workspace','common','q',report)
            with patch('server.now_ist',return_value=NOW),patch.object(app,'submit',return_value='new') as submit:
                self.assertEqual(app.daily('intraday')['job_id'],'new');submit.assert_called_once()

    def test_premarket_pipeline_never_emits_execution_ready_setup(self):
        setting=Settings();setting.daily_symbols=['COFORGE'];setting.daily_candidate_limit=6
        provider=SimpleNamespace(kite=SimpleNamespace(instruments=lambda:[{'tradingsymbol':'COFORGE','name':'COFORGE LTD','instrument_type':'EQ'}]),market_news=lambda:[{'title':'COFORGE wins new orders','sentiment':1}],candles=lambda *args:candles())
        def run(*args,**kwargs):
            rec={'symbol':'COFORGE','name':'COFORGE','score':70,'metrics':{'available':True,'trend':'Bullish','age_minutes':1440},'news':[],'reasons':['Bullish trend','Order news'],'risks':['Opening gap risk'],'status':'RESEARCH ONLY','scenario':None,'plan':None}
            return {'market':{'verified':False,'open':False,'label':'Unverified'},'recommendations':[rec]}
        agent=SimpleNamespace(providers=provider,run=run)
        with patch('consentdesk.daily.now_ist',return_value=NOW.replace(hour=7)):
            result=DailyBrief(agent,setting).run('intraday')
        rec=result['recommendations'][0]
        self.assertEqual(result['brief_timing'],'PRE-OPEN');self.assertIsNone(rec['plan'])
        self.assertEqual(rec['status'],'WATCH · CONFIRM AT OPEN');self.assertLessEqual(len(rec['brief_reasons']),2)

if __name__=='__main__':unittest.main()
