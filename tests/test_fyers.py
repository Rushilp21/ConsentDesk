from pathlib import Path
import unittest
from unittest.mock import patch
from consentdesk.config import Settings
from consentdesk.providers import Providers
from consentdesk.fyers import FyersData
from consentdesk.net import CACHE,ProviderError
from scripts.connect_fyers import save_token
from test_research import NOW,temporary_directory


class FyersTests(unittest.TestCase):
    def setUp(self):
        self.settings=Settings();self.settings.broker='fyers';self.settings.fyers_app_id='APP-100';self.settings.fyers_token='fixture'
        self.p=Providers(self.settings)
        with CACHE.lock:CACHE.items.clear()

    def test_quotes_use_exchange_time_and_require_verified_session(self):
        raw={'s':'ok','d':[{'n':'NSE:INFY-EQ','v':{'lp':100,'tt':int(NOW.timestamp()),'chp':1,'bid':99.9,'ask':100.1}}]}
        with patch('consentdesk.fyers.request_json',return_value=raw),patch('consentdesk.providers.now_ist',return_value=NOW):
            for opened in [True,False]:
                with patch.object(self.p,'market_status',return_value={'verified':True,'open':opened}):
                    q=self.p.quotes(['INFY'])['quotes'][0];self.assertEqual(q['live'],opened);self.assertEqual(q['as_of'],NOW.isoformat())

    def test_nse_normal_session_only(self):
        raw={'s':'ok','marketStatus':[{'exchange':10,'segment':10,'market_type':'AUCTION','status':'OPEN'},{'exchange':10,'segment':10,'market_type':'NORMAL','status':'CLOSE'}]}
        with patch.object(self.p.fyers,'get',return_value=raw):self.assertFalse(self.p.fyers.market_status()['open'])

    def test_history_chunks_and_preserves_broker_provenance(self):
        raw={'s':'ok','candles':[[int(NOW.timestamp())-86400,100,102,99,101,1000]]}
        with patch.object(self.p.fyers,'get',return_value=raw) as get:
            b=self.p.validation_candles('INFY','longterm');self.assertTrue(b['broker']);self.assertEqual(len(b['candles']),1);self.assertEqual(get.call_count,2)
        self.assertEqual(FyersData.symbol('NIFTY'),'NSE:NIFTY50-INDEX')

    def test_errors_do_not_expose_credentials_and_token_save_preserves_settings(self):
        with patch('consentdesk.fyers.request_json',return_value={'s':'error','message':'sensitive fixture'}):
            with self.assertRaises(ProviderError) as e:self.p.fyers.get('quotes')
            self.assertNotIn('sensitive',str(e.exception))
        with temporary_directory() as tmp:
            path=Path(tmp)/'.env';path.write_text('PORT=8000\nMARKET_BROKER=upstox\n')
            save_token('APP-100','fixture',path);self.assertIn('PORT=8000',path.read_text());self.assertIn('MARKET_BROKER=fyers',path.read_text())
