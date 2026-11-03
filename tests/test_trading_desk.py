import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from pathlib import Path
import os
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from consentdesk.desk import TradingDesk, costs, DEFAULT_LIMITS, usable_quote
from consentdesk.storage import Store
from consentdesk.config import Settings
from consentdesk.context import relative_strength, regime, event_flags, enrich
from consentdesk.learning import walk_forward
from consentdesk.upstox_research import financial_review, growth
from consentdesk.providers import Providers, ProviderError
from server import Application, make_handler
from test_research import NOW, temporary_directory
from test_dashboard import history


def draft(**changes):
    return {'symbol':'INFY','mode':'longterm','entry':100,'stop':98,'target':104,'quantity':100,**changes}


def quote(**changes):
    return {'symbol':'INFY','price':100,'broker':True,'synthetic':False,'live':True,
            'as_of':NOW.isoformat(),'bid':99.95,'ask':100.05,'label':'LIVE',**changes}


class DeskTests(unittest.TestCase):
    def setUp(self):
        self.tmp=temporary_directory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name));self.desk=TradingDesk(self.store)
        self.clock=patch('consentdesk.desk.now_ist',return_value=NOW);self.clock.start();self.addCleanup(self.clock.stop)

    def test_costs_apply_both_sides_and_unchanged_price_loses_money(self):
        result=costs(100,100,100,DEFAULT_LIMITS)
        self.assertAlmostEqual(result['net_pnl'],-20,places=3)
        self.assertEqual(result['gross_pnl'],0)
        self.assertEqual(result['fees'],10)
        self.assertEqual(result['slippage'],10)
        self.assertGreater(result['entry_fill'],100)
        self.assertLess(result['exit_fill'],100)

    def test_sizing_caps_cash_sector_and_net_stop_risk(self):
        p=self.desk.preview(draft(quantity=None))
        self.assertEqual(p['quantity'],199)
        self.assertLessEqual(p['capital_required'],20000)
        self.assertLessEqual(p['planned_risk'],500)
        self.assertLess(p['reward_risk_net'],2)
        self.desk.open_trade(draft(quantity=190))
        next_plan=self.desk.preview(draft(symbol='TCS',quantity=None))
        self.assertLess(next_plan['quantity'],10)  # Same IT sector budget.
        self.assertFalse(self.desk.preview(draft(symbol='TCS',quantity=100))['allowed'])

    def test_unknown_sectors_share_one_budget(self):
        self.desk.open_trade(draft(symbol='CUSTOMA',quantity=190))
        self.assertFalse(self.desk.preview(draft(symbol='CUSTOMB'))['allowed'])

    def test_journal_failure_rolls_back_new_daily_brief(self):
        report={'mode':'intraday','demo':False,'created_at':NOW.isoformat(),'daily_scan':True,'recommendations':[]}
        old=self.store.save('common-workspace','common','Old brief',copy.deepcopy(report))
        def failed_journal(saved,db):
            self.desk.put(db,'desk_ideas',{'id':'fixture','symbol':'INFY'})
            raise RuntimeError('Fixture disk failure')
        with self.assertRaises(RuntimeError):self.store.save('common-workspace','common','New brief',copy.deepcopy(report),on_saved=failed_journal)
        self.assertEqual(self.store.daily_report('intraday')['id'],old['id'])
        self.assertEqual(self.desk.snapshot()['ideas'],[])

    def test_limits_lock_capital_and_trades_keep_original_costs(self):
        t=self.desk.open_trade(draft())
        with self.assertRaises(ValueError):self.desk.set_limits({'capital':60000})
        self.desk.set_limits({'fee_bps':50,'slippage_bps':50})
        closed=self.desk.close_trade({'id':t['id'],'price':100})
        self.assertAlmostEqual(closed['result']['net_pnl'],-20,places=3)
        with self.assertRaises(ValueError):self.desk.close_trade({'id':t['id'],'price':100})
        again=TradingDesk(Store(Path(self.tmp.name))).snapshot()
        self.assertEqual(again['trade_count'],1)
        self.assertEqual(again['realised_net'],-20)

    def test_daily_budget_reserves_open_risk_and_losses(self):
        self.desk.set_limits({'daily_loss_pct':.5})
        self.desk.open_trade(draft())
        self.assertLess(self.desk.snapshot()['daily_risk_room'],40)
        self.assertFalse(self.desk.preview(draft(symbol='SBIN'))['allowed'])

    def test_atomic_open_cannot_overrun_sector_budget(self):
        def submit():
            try:self.desk.open_trade(draft(quantity=150));return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:submit(),range(2)))
        self.assertEqual(sum(results),1)
        self.assertEqual(len(self.desk.snapshot()['open_trades']),1)

    def test_validation_rejects_invalid_numbers_quantities_and_indices(self):
        for d in [draft(entry=float('nan')),draft(entry=True),draft(quantity=1.2),draft(quantity=-1),draft(stop=101),draft(symbol='NIFTY'),draft(symbol='<script>')]:
            with self.subTest(d=d),self.assertRaises(ValueError):self.desk.preview(d)
        for value in [float('inf'),True,-1]:
            with self.assertRaises(ValueError):self.desk.set_limits({'fee_bps':value})

    def test_alerts_ignore_public_stale_demo_future_and_closed_session(self):
        alert=self.desk.add_alert({'symbol':'INFY','level':99})
        provider=Mock()
        cases=[quote(broker=False),quote(synthetic=True),quote(as_of=(NOW-timedelta(seconds=31)).isoformat()),quote(as_of=(NOW+timedelta(seconds=1)).isoformat())]
        for q in cases:
            self.assertFalse(usable_quote(q,NOW))
            provider.quotes.return_value={'quotes':[q],'market':{'verified':True,'open':True}}
            self.desk.refresh(provider)
            self.assertEqual(self.desk.snapshot()['alerts'][0]['status'],'ARMED')
        provider.quotes.return_value={'quotes':[quote()],'market':{'verified':False,'open':True}}
        self.desk.refresh(provider);self.assertEqual(self.desk.snapshot()['alerts'][0]['status'],'ARMED')
        provider.quotes.return_value={'quotes':[quote()],'market':{'verified':True,'open':True}}
        self.desk.refresh(provider);self.desk.refresh(provider)
        alerts=self.desk.snapshot()['alerts'];self.assertEqual(len(alerts),1)
        self.assertEqual(alerts[0]['id'],alert['id']);self.assertEqual(alerts[0]['status'],'TRIGGERED')

    def test_paper_stop_uses_sampled_gap_price_not_invented_stop_fill(self):
        t=self.desk.open_trade(draft())
        provider=Mock();provider.quotes.return_value={'quotes':[quote(price=90)],'market':{'verified':True,'open':True}}
        self.desk.refresh(provider)
        closed=self.desk.snapshot()['closed_trades'][0]
        self.assertEqual(closed['exit_reference'],90)
        self.assertLess(closed['result']['exit_fill'],90)
        self.assertEqual(closed['exit_reason'],'Stop reached')
        self.assertEqual(closed['exit_packet_at'],NOW.isoformat())

    def test_previous_intraday_position_has_no_next_day_auto_fill(self):
        with patch('consentdesk.desk.now_ist',return_value=NOW-timedelta(days=1)):self.desk.open_trade(draft(mode='intraday'))
        provider=Mock();provider.quotes.return_value={'quotes':[quote(price=90)],'market':{'verified':True,'open':True}}
        self.desk.refresh(provider)
        self.assertEqual(len(self.desk.snapshot()['open_trades']),1)
        self.assertTrue(self.desk.snapshot()['open_trades'][0]['exit_due'])

    def test_same_day_session_exit_uses_live_quote(self):
        self.desk.open_trade(draft(mode='intraday'))
        later=NOW.replace(hour=15,minute=21)
        provider=Mock();provider.quotes.return_value={'quotes':[quote(as_of=later.isoformat())],'market':{'verified':True,'open':True}}
        with patch('consentdesk.desk.now_ist',return_value=later):self.desk.refresh(provider)
        self.assertEqual(self.desk.snapshot()['closed_trades'][0]['exit_reason'],'Session exit')

    def test_alert_expiry_updates_without_credentials(self):
        self.desk.add_alert({'symbol':'INFY','level':101,'expires_at':(NOW+timedelta(seconds=1)).isoformat()})
        with patch('consentdesk.desk.now_ist',return_value=NOW+timedelta(seconds=2)):
            self.assertEqual(self.desk.snapshot()['alerts'][0]['status'],'EXPIRED')

    def report(self,**changes):
        report={'mode':'intraday','demo':False,'created_at':NOW.isoformat(),'recommendations':[{'symbol':'INFY','status':'WATCH · CONFIRM AT OPEN',
            'plan':None,'scenario':{'entry':101,'invalidation':98,'target':107,'gap_skip_above':103}}],**changes}
        return self.store.save('common-workspace','common','Fixture',report)

    def test_setup_requires_current_saved_report_and_clamps_expiry(self):
        with self.assertRaises(ValueError):self.desk.add_alert({'symbol':'INFY','kind':'setup','level':101})
        r=self.report();a=self.desk.add_alert({'symbol':'INFY','kind':'setup','level':1,'report_id':r['id']})
        self.assertEqual(a['level'],101);self.assertEqual(a['stop'],98)
        self.assertIn('15:15:00',a['expires_at'])
        r=self.report(created_at=(NOW-timedelta(days=1)).isoformat())
        with self.assertRaises(ValueError):self.desk.add_alert({'symbol':'INFY','kind':'setup','level':101,'report_id':r['id']})
        r=self.report(recommendations=[{'symbol':'INFY','status':'CONDITIONAL SETUP','plan':{'status':'CONDITIONAL SETUP','entry_above':101,'expires_at':(NOW-timedelta(minutes=1)).isoformat()}}])
        with self.assertRaises(ValueError):self.desk.add_alert({'symbol':'INFY','kind':'setup','level':101,'report_id':r['id']})

    def test_new_event_invalidates_saved_setup_and_cancelled_event_is_ignored(self):
        r=self.report();self.desk.add_alert({'symbol':'INFY','kind':'setup','level':101,'report_id':r['id']})
        event=self.desk.add_event({'symbol':'INFY','kind':'earnings','date':str(NOW.date()),'title':'Quarterly results','source_url':'https://example.com/filing'})
        self.desk.refresh(Mock(quotes=Mock(return_value={'quotes':[quote()],'market':{'verified':True,'open':True}})))
        self.assertEqual(self.desk.snapshot()['alerts'][0]['status'],'INVALIDATED')
        self.desk.cancel('desk_events',event['id'])
        self.assertEqual(self.desk.snapshot()['events'],[])
        self.assertFalse(event_flags(self.desk.events(),[],NOW)['high_event_risk'])

    def test_setup_confirmation_rechecks_current_news_and_daily_trend(self):
        r=self.report();self.desk.add_alert({'symbol':'INFY','kind':'setup','level':101,'report_id':r['id']})
        provider=Mock();provider.quotes.return_value={'quotes':[quote(price=102)],'market':{'verified':True,'open':True}}
        provider.news.return_value={'articles':[]}
        with patch.object(self.desk,'confirm_setup',return_value=(True,'Technical breakout confirmed')),patch('consentdesk.analysis.indicators',return_value={'available':True,'trend':'Bullish','age_minutes':60}):
            self.desk.refresh(provider);self.assertEqual(self.desk.snapshot()['alerts'][0]['status'],'ARMED')
            provider.news.return_value={'articles':[{'title':'Company wins major contract '+str(i),'publisher':'Publisher '+str(i),'published_at':NOW.isoformat(),'sentiment':1} for i in range(2)]}
            self.desk.refresh(provider);self.assertEqual(self.desk.snapshot()['alerts'][0]['status'],'TRIGGERED')

    def test_opening_confirmation_needs_range_volume_vwap_spread(self):
        now=NOW.replace(hour=9,minute=35)
        prior=[{'t':int((now-timedelta(days=1,minutes=i*5)).timestamp()),'o':100,'h':101,'l':99,'c':100,'v':1000} for i in range(20,0,-1)]
        session=[{'t':int(now.replace(minute=15).timestamp())+i*300,'o':100,'h':101 if i<3 else 103,'l':99,'c':100 if i<3 else 102,'v':1000 if i<3 else 2000} for i in range(4)]
        bundle={'broker':True,'synthetic':False,'candles':prior+session}
        q=quote(price=102,bid=101.95,ask=102.05)
        alert={'level':101,'gap_skip_above':105}
        self.assertTrue(self.desk.confirm_setup(alert,q,bundle,now)[0])
        for bad in [dict(bundle,broker=False),dict(bundle,candles=prior+session[:3])]:self.assertFalse(self.desk.confirm_setup(alert,q,bad,now)[0])
        self.assertFalse(self.desk.confirm_setup(alert,dict(q,ask=105),bundle,now)[0])
        self.assertFalse(self.desk.confirm_setup(alert,dict(q,price=106),bundle,now)[0])
        bundle['candles'][-1]['v']=100
        self.assertFalse(self.desk.confirm_setup(alert,q,bundle,now)[0])

    def test_financial_metrics_require_recent_date_source_and_units(self):
        record={'symbol':'INFY','as_of':str(NOW.date()),'source_url':'https://example.com/filing','revenue_growth_pct':10,'earnings_growth_pct':12,'roe_pct':20,'debt_to_equity':.1,'pe':25,'operating_cash_flow':10000000}
        self.assertTrue(self.desk.save_financials(record)['complete'])
        for changes in [{'as_of':str((NOW-timedelta(days=181)).date())},{'pe':-1},{'source_url':'javascript:bad'},{'roe_pct':float('nan')}]:
            with self.assertRaises(ValueError):self.desk.save_financials({**record,**changes})


class ResearchCheckTests(unittest.TestCase):
    def test_free_analytics_token_preferred_and_no_trading_access_created(self):
        with patch.dict(os.environ,{'MARKET_BROKER':'upstox','UPSTOX_ANALYTICS_TOKEN':'fixture-analytics','UPSTOX_ACCESS_TOKEN':'fixture-standard'}):
            settings=Settings()
            self.assertEqual(settings.upstox_token,'fixture-analytics');self.assertTrue(settings.broker_enabled)
        with patch.dict(os.environ,{'UPSTOX_ANALYTICS_TOKEN':'','UPSTOX_ACCESS_TOKEN':'fixture-standard'}):
            self.assertEqual(Settings().upstox_token,'fixture-standard')

    def test_annual_growth_is_not_fabricated_across_missing_years(self):
        with patch('consentdesk.upstox_research.now_ist',return_value=NOW):
            self.assertIsNone(growth([{'period':'Mar 2026','value':110},{'period':'Mar 2024','value':100}]))
            self.assertEqual(growth([{'period':'Mar 2026','value':110},{'period':'Mar 2025','value':100}]),10)

    def test_upstox_live_label_requires_verified_open_session(self):
        settings=Settings();settings.broker='upstox';settings.upstox_token='fixture'
        provider=Providers(settings)
        raw={'NSE_EQ:INFY':{'instrument_token':settings.instrument('INFY'),'last_price':100,'timestamp':int(NOW.timestamp()*1000),'ohlc':{'close':99}}}
        with patch.object(provider,'_broker',return_value=raw),patch('consentdesk.providers.now_ist',return_value=NOW):
            for opened in (False,True):
                with patch.object(provider,'market_status',return_value={'verified':True,'open':opened}):
                    self.assertEqual(provider.quotes(['INFY'])['quotes'][0]['live'],opened)

    def test_relative_strength_aligns_dates_and_rejects_stale_history(self):
        start=NOW-timedelta(days=30)
        def rows(step):return [{'t':int((start+timedelta(days=i)).timestamp()),'o':100+i*step,'h':101+i*step,'l':99+i*step,'c':100+i*step,'v':1000} for i in range(30)]
        stock={'candles':rows(1)};index={'candles':rows(.5)[2:]}
        value=relative_strength(stock,index,NOW)
        self.assertTrue(value['aligned_dates']);self.assertGreater(value['excess_return_pp'],0)
        self.assertIsNone(relative_strength(stock,index,NOW+timedelta(days=10)))
        self.assertIsNone(relative_strength({'candles':rows(1)[-10:]},index,NOW))

    def test_market_regime_uses_completed_bars_and_event_filter_blocks_long_setup(self):
        rows=[]
        for i in range(80):
            p=200-i
            rows.append({'t':int((NOW-timedelta(days=80-i)).timestamp()),'o':p,'h':p+1,'l':p-1,'c':p,'v':1000})
        b={'candles':rows,'source':'fixture'}
        self.assertEqual(regime(b,NOW)['label'],'TRENDING DOWN')
        today={'t':int(NOW.timestamp()),'o':1000,'h':1001,'l':999,'c':1000,'v':1000}
        self.assertEqual(regime({**b,'candles':rows+[today]},NOW),regime(b,NOW))
        providers=Mock(candles=Mock(return_value=b));desk=Mock(events=Mock(return_value=[]))
        report={'mode':'intraday','recommendations':[{'symbol':'INFY','sector':'IT','status':'CONDITIONAL SETUP','plan':{'status':'CONDITIONAL SETUP'}}]}
        with patch('consentdesk.context.now_ist',return_value=NOW),patch('consentdesk.learning.now_ist',return_value=NOW):enrich(report,providers,desk)
        self.assertIn('WAIT',report['recommendations'][0]['status'])
        self.assertEqual(report['recommendations'][0]['plan']['status'],'WAIT')

    def test_news_flags_never_create_future_calendar_dates(self):
        result=event_flags([], [{'title':'Company earnings to rise','published_at':NOW.isoformat()}],NOW)
        self.assertEqual(result['upcoming'],[]);self.assertEqual(len(result['news_flags']),1)
        self.assertFalse(result['high_event_risk'])

    def test_financial_parser_does_not_treat_liabilities_or_share_capital_as_equity(self):
        h=[{'period':'Mar 2026','value':110},{'period':'Mar 2025','value':100}]
        parts={'income-statement':{'income_statement':[{'category':k,'history':h} for k in ('revenue','net_profit')]},
            'cash-flow':{'units_in':'crore','cash_flow':[{'category':'operating','history':h}]},
            'key-ratios':[{'name':'P/E','company_value':'25','sector_value':'30'},{'name':'ROE','company_value':'20%'}],
            'balance-sheet':{'full_statement':[{'particular':'Total Liabilities','history':h},{'particular':'Equity Capital','history':h}]}}
        with patch('consentdesk.upstox_research.now_ist',return_value=NOW):r=financial_review('INE000000001',parts,'INFY')
        self.assertEqual(r['operating_cash_flow'],110*10000000)
        self.assertIsNone(r['debt_to_equity']);self.assertFalse(r['complete'])
        parts['balance-sheet']['full_statement']=[{'particular':'Total Borrowings','history':h},{'particular':'Total Equity','history':h}]
        with patch('consentdesk.upstox_research.now_ist',return_value=NOW):r=financial_review('INE000000001',parts,'INFY')
        self.assertEqual(r['debt_to_equity'],1);self.assertTrue(r['complete'])
        parts['cash-flow']['cash_flow'][0]['history']=[{'period':'Mar 2025','value':100}]
        with patch('consentdesk.upstox_research.now_ist',return_value=NOW):r=financial_review('INE000000001',parts,'INFY')
        self.assertFalse(r['complete']);self.assertTrue(any('different reporting' in w for w in r['warnings']))

    def test_walk_forward_purges_train_and_test_boundaries_and_stresses_costs(self):
        bundle={'candles':history(600),'source':'fixture','synthetic':False}
        result=walk_forward(bundle,'longterm',20,NOW)
        self.assertEqual(len(result['folds']),3)
        for f in result['folds']:self.assertLess(f['train_labels_end'],f['test_from'])
        for first,second in zip(result['folds'],result['folds'][1:]):self.assertLess(first['test_to'],second['test_from'])
        self.assertFalse(result['validated_for_live_trading'])
        self.assertEqual(result['simulated_trades'],result['cost_stress']['simulated_trades'])
        if result['simulated_trades']:self.assertLessEqual(result['cost_stress']['mean_net_return_pct'],result['mean_net_return_pct'])
        self.assertEqual(walk_forward(dict(bundle,synthetic=True),'longterm')['status'],'NOT VALIDATED')
        self.assertEqual(walk_forward({'candles':history(100)},'longterm',now=NOW)['status'],'INSUFFICIENT HISTORY')


class DeskHTTPTests(unittest.TestCase):
    def test_desk_endpoints_persist_paper_and_sourced_data(self):
        with temporary_directory() as tmp,patch('consentdesk.desk.now_ist',return_value=NOW):
            settings=Settings();settings.data_dir=Path(tmp)
            app=Application(settings);server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(app));thread=threading.Thread(target=server.serve_forever);thread.start()
            base='http://127.0.0.1:'+str(server.server_port)
            def get(path):
                with urlopen(base+path) as r:return json.load(r)
            def post(path,data):
                with urlopen(Request(base+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})) as r:return json.load(r)
            try:
                with urlopen(base+'/') as r:html=r.read().decode()
                self.assertIn('id="paper-form"',html);self.assertEqual(html.count('id="desk-view"'),1)
                with urlopen(base+'/desk.js') as r:self.assertEqual(r.status,200)
                self.assertEqual(get('/api/desk')['trade_count'],0)
                self.assertTrue(post('/api/paper/preview',draft())['allowed'])
                t=post('/api/paper/open',draft());post('/api/paper/close',{'id':t['id'],'price':104})
                self.assertEqual(get('/api/desk')['trade_count'],1)
                a=post('/api/alerts',{'symbol':'INFY','level':105});post('/api/alerts/cancel',{'id':a['id']})
                e=post('/api/events',{'symbol':'ALL','kind':'policy','date':str(NOW.date()),'title':'Sourced event','source_url':'https://example.com/source'})
                self.assertEqual(len(get('/api/desk')['events']),1)
                post('/api/events/cancel',{'id':e['id']});self.assertEqual(get('/api/desk')['events'],[])
                with patch.object(app.providers,'validation_candles',return_value={'candles':history(100),'source':'Fixture'}):
                    job=post('/api/validation',{'symbol':'INFY','mode':'longterm'})
                    for _ in range(100):
                        result=get('/api/jobs/'+job['job_id']+'?session=test-session-123456&profile=common')
                        if result['status']!='running':break
                        time.sleep(.01)
                self.assertEqual(result['status'],'complete');self.assertEqual(result['validation']['status'],'INSUFFICIENT HISTORY')
                self.assertEqual(len(get('/api/desk')['validations']),1)
            finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
