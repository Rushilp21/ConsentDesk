import copy
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from consentdesk.trade_views import option_views, holding_plan
from consentdesk.analysis import analyse_options
from consentdesk.daily import DailyBrief
from consentdesk.providers import Providers
from consentdesk.config import Settings
from consentdesk.net import CACHE

NOW=datetime.fromisoformat('2026-11-04T12:00:00+05:30')


def stock(bearish=False):
    side='PUT' if bearish else 'CALL'
    return {'symbol':'FIXTURE','name':'Fixture','status':'RESEARCH ONLY',
        'metrics':{'available':True,'trend':'Bearish' if bearish else 'Bullish','price':100,'atr14':2,'age_minutes':2,'rsi14':48 if bearish else 55,'relative_volume':2},
        'chart_structure':{'levels':{'support':95,'resistance':105},'patterns':[{'name':'Breakdown' if bearish else 'Breakout','direction':'bearish' if bearish else 'bullish'}]},
        'quote':{'live':True},'news':[{'id':str(i),'title':('Profit loss warning' if bearish else 'Profit rises order win')+str(i),
            'published_at':NOW.isoformat(),'publisher':str(i),'sentiment':-1 if bearish else 1,'url':'https://example.com/news'} for i in range(2)],
        'option_analysis':{'contracts':[{'side':side,'strike':100,'spread_pct':1,'ask':10,'bid':9.9,'lot_size':50,'as_of':NOW.isoformat(),'expiry':'2026-11-23'}]}}


class TradeViewsTests(unittest.TestCase):
    def test_upstox_lot_metadata_matched_by_contract_key_without_invented_timestamp(self):
        settings=Settings();settings.broker='upstox';settings.upstox_token='fixture'
        provider=Providers(settings)
        metadata=[{'instrument_key':'NSE_FO|FIXTURE','expiry':'2026-11-23','lot_size':50}]
        rows=[{'strike_price':100,'expiry':'2026-11-23','call_options':{'instrument_key':'NSE_FO|FIXTURE'},'put_options':{'instrument_key':'NSE_FO|UNKNOWN'}}]
        with CACHE.lock:CACHE.items.clear()
        try:
            with patch.object(provider,'_broker',side_effect=lambda path:metadata if '/contract?' in path else rows):
                chain=provider.options('NIFTY','2026-11-23')
            self.assertEqual(chain['rows'][0]['call_options']['lot_size'],50)
            self.assertNotIn('lot_size',chain['rows'][0]['put_options'])
            self.assertNotIn('as_of',chain['rows'][0]['call_options'])
            self.assertNotIn('lot_size',rows[0]['call_options'])
        finally:
            with CACHE.lock:CACHE.items.clear()

    def test_bearish_put_uses_reverse_stop_and_target_without_short_option(self):
        v=option_views(stock(True),{'verified':True,'open':True},now=NOW)['sell']
        self.assertEqual(v['action'],'BUY PUT (PE)')
        self.assertLess(v['underlying_target'],v['underlying_entry'])
        self.assertGreater(v['underlying_stop'],v['underlying_entry'])
        self.assertEqual(v['one_lot_cost'],500)
        self.assertEqual(v['score'],5)
        self.assertEqual(v['status'],'WATCH ONLY')
        self.assertIn('Premium stop/target and expiry risk need broker review',v['blocked_by'])

    def test_bullish_call_and_opposite_side_are_not_interchangeable(self):
        views=option_views(stock(),{'verified':True,'open':True},now=NOW)
        v=views['buy'];self.assertEqual(v['action'],'BUY CALL (CE)')
        self.assertLess(v['underlying_stop'],v['underlying_entry'])
        self.assertGreater(v['underlying_target'],v['underlying_entry'])
        self.assertEqual(views['sell']['status'],'WAIT')

    def test_missing_metadata_never_invents_lots_or_premium_targets(self):
        rec=stock();c=rec['option_analysis']['contracts'][0];c.pop('lot_size');c.pop('as_of')
        v=option_views(rec,{'verified':True,'open':True},now=NOW)['buy']
        self.assertIsNone(v['one_lot_cost']);self.assertIsNone(v['lot_size'])
        self.assertIn('Lot size unverified',v['blocked_by'])
        self.assertIn('Fresh contract exchange timestamp required',v['blocked_by'])
        self.assertNotIn('premium_target',v)

    def test_premarket_and_stale_evidence_stays_blocked(self):
        rec=stock();rec['metrics']['age_minutes']=8*1440;rec['quote']['live']=False
        v=option_views(rec,{'verified':True,'open':False},daily_basis=True,now=NOW)['buy']
        self.assertIn('Chart data missing or stale',v['blocked_by'])
        self.assertIn('Market not verified open',v['blocked_by'])
        self.assertIn('Fresh underlying quote required',v['blocked_by'])
        self.assertEqual(v['chart_basis'],'Daily')

    def test_direction_comes_from_price_news_and_patterns_not_pcr(self):
        rec=stock(True);rec['oi_analysis']={'expiries':[{'pcr_oi':100}]}
        first=option_views(rec,{},now=NOW)
        rec['oi_analysis']['expiries'][0]['pcr_oi']=.01
        self.assertEqual(first,option_views(rec,{},now=NOW))
        rec['news'][0]['sentiment']=1
        self.assertIn('News does not confirm',option_views(rec,{},now=NOW)['sell']['blocked_by'])

    def test_horizon_is_consistent_across_sectors_and_not_a_promised_exit_date(self):
        for sector in ['Healthcare','Energy','Railways']:
            plan=holding_plan({'sector':sector},'longterm',NOW)
            self.assertEqual(plan['period'],'12–36 months')
            self.assertEqual(plan['next_review'],'2026-12-04')
            self.assertFalse(plan['entry_ready'])
            self.assertIn('not a predicted target date',plan['basis'])

    def test_liquidity_filter_keeps_both_sides(self):
        rows=[]
        for strike in range(90,110):
            market={'bid_price':10,'ask_price':10.1,'volume':1000,'oi':1000}
            rows.append({'expiry':'2026-11-23','strike_price':strike,'call_options':{'market_data':market},'put_options':{'market_data':market}})
        result=analyse_options({'rows':rows,'fetched_at':NOW.isoformat(),'source':'Fixture','note':''},{},NOW)
        self.assertEqual([c['side'] for c in result['contracts']].count('CALL'),3)
        self.assertEqual([c['side'] for c in result['contracts']].count('PUT'),3)

    def test_daily_fno_partitions_both_sides_and_limits_each_to_three(self):
        records=[]
        for i in range(8):
            rec=stock(i%2==0);rec.update(symbol='FIX'+str(i),score=55,reasons=[],risks=[],gaps=[])
            records.append(rec)
        provider=SimpleNamespace(kite=SimpleNamespace(instruments=lambda:[]),market_news=lambda:[],candles=lambda *a:{'candles':[]})
        agent=SimpleNamespace(providers=provider,run=lambda *a,**k:{'market':{'open':True,'verified':True},'recommendations':copy.deepcopy(records)})
        settings=SimpleNamespace(daily_symbols=['INFY'],daily_candidate_limit=18)
        with patch('consentdesk.daily.now_ist',return_value=NOW),patch('consentdesk.daily.indicators',return_value={'available':True,'trend':'Mixed'}):
            report=DailyBrief(agent,settings).run('options')
        self.assertEqual(len(report['fno_sections']['buy']),3)
        self.assertEqual(len(report['fno_sections']['sell']),3)
        self.assertTrue(all(r['metrics']['trend']=='Bearish' for r in report['fno_sections']['sell']))


if __name__=='__main__':unittest.main()
