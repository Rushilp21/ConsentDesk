import copy
from datetime import timedelta
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from consentdesk.config import Settings
from consentdesk.providers import Providers
from consentdesk.kite import broker_time
from consentdesk.learning import PatternLearner, dataset, feature_at, evaluate, fit, completed_rows
from consentdesk.patterns import oi_summary, chart_structure
from consentdesk.storage import Store
from test_research import NOW, temporary_directory

def history(count=600):
    rows=[]
    start=NOW-timedelta(days=count+10)
    for i in range(count):
        p=100+5*math.sin(i/13)+.01*i
        rows.append({'t':int((start+timedelta(days=i)).timestamp()),'o':p-.1,'h':p+1,'l':p-1,'c':p,'v':1000+i%31*100})
    return rows

class DashboardTests(unittest.TestCase):
    def test_quote_live_only_for_fresh_broker_packet(self):
        settings=Settings();settings.kite_api_key='fixture';settings.kite_access_token='fixture';settings.broker='zerodha'
        provider=Providers(settings)
        quote={'symbol':'INFY','price':100,'as_of':NOW.isoformat(),'broker':True,'synthetic':False}
        for age,expected in [(0,True),(31,False),(-1,False),(86400,False)]:
            quote['as_of']=(NOW-timedelta(seconds=age)).isoformat()
            with patch.object(provider.kite,'quotes',return_value={'INFY':copy.deepcopy(quote)}),patch('consentdesk.providers.now_ist',return_value=NOW):
                result=provider.quotes(['INFY'])
            self.assertEqual(result['quotes'][0]['live'],expected)
        quote.update(broker=False,as_of=NOW.isoformat())
        with patch.object(provider.kite,'quotes',return_value={'INFY':quote}),patch('consentdesk.providers.now_ist',return_value=NOW):
            self.assertFalse(provider.quotes(['INFY'])['quotes'][0]['live'])

    def test_kite_normalises_quote_exchange_timestamp(self):
        provider=Providers(Settings())
        raw={'NSE:INFY':{'last_price':110,'timestamp':'2026-10-09 12:59:59','last_trade_time':'2026-10-09 12:59:55','ohlc':{'close':100}}}
        with patch.object(provider.kite,'raw_quotes',return_value=raw):
            q=provider.kite.quotes(['INFY'])['INFY']
        self.assertEqual(q['change_pct'],10)
        self.assertTrue(q['as_of'].endswith('+05:30'))
        self.assertNotEqual(q['last_trade_at'],q['as_of'])
        self.assertIsNone(broker_time('invalid'))

    def test_oi_window_totals_walls_and_missing_baseline(self):
        chain={'source':'fixture','rows':[{'expiry':'2026-10-15','strike_price':100,'call_options':{'market_data':{'oi':100,'volume':10}},'put_options':{'market_data':{'oi':200,'volume':20}}},{'expiry':'2026-10-15','strike_price':110,'call_options':{'market_data':{'oi':float('nan'),'volume':float('inf')}}}]}
        result=oi_summary(chain)['expiries'][0]
        self.assertEqual(result['pcr_oi'],2)
        self.assertEqual(result['call_wall']['strike'],100)
        self.assertFalse(result['changes_available'])
        self.assertEqual(result['call_volume'],10)

    def test_features_do_not_read_future_prices(self):
        rows=history();before=feature_at(rows,100)
        for row in rows[101:]:row.update(o=1000,h=1100,l=900,c=1000,v=99999)
        self.assertEqual(before,feature_at(rows,100))

    def test_kite_chain_uses_listed_expiry_and_actual_contract_fields(self):
        provider=Providers(Settings())
        instruments=[{'instrument_type':side,'name':'INFY','expiry':'2026-10-29','strike':'100','tradingsymbol':side,'lot_size':'400'} for side in ['CE','PE']]
        def quotes(ids):
            if ids==['NSE:INFY']:return {'NSE:INFY':{'last_price':102},'_fetched_at':NOW.isoformat()}
            return {**{'NFO:'+side:{'last_price':10,'oi':1000,'volume':20,'timestamp':'2026-10-09 13:00:00','depth':{'buy':[{'price':9.9}],'sell':[{'price':10.1}]}} for side in ['CE','PE']},'_fetched_at':NOW.isoformat()}
        with patch.object(provider.kite,'instruments',return_value=instruments),patch.object(provider.kite,'raw_quotes',side_effect=quotes),patch('consentdesk.kite.now_ist',return_value=NOW):
            chain=provider.kite.options('INFY','current_week')
        self.assertEqual(chain['expiry'],'2026-10-29')
        self.assertEqual(chain['rows'][0]['call_options']['lot_size'],400)
        self.assertEqual(chain['rows'][0]['put_options']['market_data']['oi'],1000)
        self.assertEqual(chain['rows'][0]['call_options']['option_greeks'],{})
        self.assertIn('not the full chain',chain['coverage'])

    def test_next_open_labels_and_same_session(self):
        rows=history(100)
        sample=dataset(rows,'longterm')[0]
        self.assertAlmostEqual(sample['return'],rows[70]['c']/rows[51]['o']-1-.002)
        self.assertEqual(dataset(rows,'intraday'),[])
        self.assertTrue(all(r['t']+300<=NOW.timestamp() for r in completed_rows(rows,'intraday',NOW)))

    def test_training_purges_overlap_and_never_claims_live_validation(self):
        rows=history();samples=dataset(rows,'longterm');split=int(len(samples)*.7)
        expected=[r for r in samples[:split] if r['target_t']<samples[split]['t']]
        with temporary_directory() as tmp:
            learner=PatternLearner(tmp)
            result=learner.inspect('FIXTURE',{'candles':rows,'synthetic':False},'longterm',NOW)
            self.assertEqual(result['status'],'EXPERIMENTAL MODEL')
            self.assertEqual(result['train_samples'],len(expected))
            self.assertLess(len(expected),split)
            self.assertFalse(result['validated_for_live_trading'])
            self.assertNotIn('model',result)
            self.assertGreater(result['validation']['holdout_samples'],100)
            self.assertEqual(learner.inspect('FIXTURE',{'candles':rows,'synthetic':True},'longterm')['status'],'NOT TRAINED')

    def test_scaler_only_uses_training_rows(self):
        train=dataset(history(250),'longterm')[:100]
        model=fit(train)
        for j in range(10):self.assertAlmostEqual(model['means'][j],sum(r['x'][j] for r in train)/len(train))

    def test_workspace_migration_preserves_reports(self):
        with temporary_directory() as tmp:
            store=Store(Path(tmp))
            for profile in ['rushil','parents']:store.save('old',profile,'q',{'created_at':NOW.isoformat(),'question':'q','mode':'intraday','daily_scan':True})
            store.unify_workspace()
            self.assertEqual(len(store.history('common-workspace','common')),2)
            self.assertIsNotNone(store.daily_report('intraday',str(NOW.date())))
            self.assertIsNone(store.daily_report('intraday','2020-01-01'))

if __name__=='__main__':unittest.main()
