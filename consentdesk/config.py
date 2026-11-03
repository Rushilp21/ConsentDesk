import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load_env():
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('\"').strip("'"))

load_env()

def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default

UNIVERSE = {
    'RELIANCE': {'name': 'Reliance Industries', 'sector': 'Energy', 'aliases': ['reliance'], 'key': 'NSE_EQ|INE002A01018'},
    'TCS': {'name': 'Tata Consultancy Services', 'sector': 'Technology', 'aliases': ['tata consultancy'], 'key': 'NSE_EQ|INE467B01029'},
    'INFY': {'name': 'Infosys', 'sector': 'Technology', 'aliases': ['infosys'], 'key': 'NSE_EQ|INE009A01021'},
    'HDFCBANK': {'name': 'HDFC Bank', 'sector': 'Banking', 'aliases': ['hdfc bank'], 'key': 'NSE_EQ|INE040A01034'},
    'ICICIBANK': {'name': 'ICICI Bank', 'sector': 'Banking', 'aliases': ['icici bank', 'icici'], 'key': 'NSE_EQ|INE090A01021'},
    'SBIN': {'name': 'State Bank of India', 'sector': 'Banking', 'aliases': ['sbi', 'state bank'], 'key': 'NSE_EQ|INE062A01020'},
    'ITC': {'name': 'ITC', 'sector': 'Consumer', 'aliases': [], 'key': 'NSE_EQ|INE154A01025'},
    'LT': {'name': 'Larsen and Toubro', 'sector': 'Infrastructure', 'aliases': ['larsen', 'l&t'], 'key': 'NSE_EQ|INE018A01030'},
    'BHARTIARTL': {'name': 'Bharti Airtel', 'sector': 'Telecom', 'aliases': ['airtel', 'bharti'], 'key': 'NSE_EQ|INE397D01024'},
    'SUNPHARMA': {'name': 'Sun Pharmaceutical', 'sector': 'Healthcare', 'aliases': ['sun pharma'], 'key': 'NSE_EQ|INE044A01036'},
    'NIFTY': {'name': 'Nifty 50', 'sector': 'Index', 'aliases': ['nifty 50', 'nifty50'], 'key': 'NSE_INDEX|Nifty 50', 'yahoo': '^NSEI'},
    'BANKNIFTY': {'name': 'Nifty Bank', 'sector': 'Index', 'aliases': ['bank nifty', 'nifty bank'], 'key': 'NSE_INDEX|Nifty Bank', 'yahoo': '^NSEBANK'},
}
DEFAULT_SYMBOLS = ['RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'ICICIBANK', 'SBIN']

class Settings:
    def __init__(self):
        self.ai_key = os.getenv('OPENAI_API_KEY', '')
        self.ai_model = os.getenv('OPENAI_MODEL', '')
        self.fyers_app_id=os.getenv('FYERS_APP_ID','')
        self.fyers_token=os.getenv('FYERS_ACCESS_TOKEN','')
        self.upstox_token = os.getenv('UPSTOX_ANALYTICS_TOKEN', '') or os.getenv('UPSTOX_ACCESS_TOKEN', '')
        self.kite_api_key = os.getenv('KITE_API_KEY', '')
        self.kite_access_token = os.getenv('KITE_ACCESS_TOKEN', '')
        self.broker = os.getenv('MARKET_BROKER', 'upstox').lower()
        self.stream_enabled=os.getenv('MARKET_STREAM_ENABLED','true').lower()=='true'
        self.public_mode=os.getenv('PUBLIC_MODE','false').lower()=='true'
        self.public_origin=os.getenv('PUBLIC_ORIGIN','').rstrip('/')
        self.public_data_approved=os.getenv('PUBLIC_DATA_APPROVED','false').lower()=='true'
        self.secure_cookies=os.getenv('SECURE_COOKIES','true').lower()=='true'
        self.trust_proxy=os.getenv('TRUST_PROXY','false').lower()=='true'
        self.registration_enabled=os.getenv('ALLOW_REGISTRATION','true').lower()=='true'
        self.max_users=int(os.getenv('MAX_USERS','100'))
        self.daily_research_limit=int(os.getenv('DAILY_RESEARCH_LIMIT','12'))
        self.ai_daily_calls=int(os.getenv('AI_DAILY_CALL_LIMIT','100'))
        self.daily_symbols = [s.strip().upper() for s in os.getenv('DAILY_SYMBOLS', ','.join(DEFAULT_SYMBOLS)).split(',') if s.strip()][:6]
        self.daily_scan_time = os.getenv('DAILY_SCAN_TIME', '07:00')
        self.daily_candidate_limit = max(6,min(30,int(os.getenv('DAILY_CANDIDATE_LIMIT','18'))))
        self.daily_scan_enabled = os.getenv('DAILY_SCAN_ENABLED', 'true').lower() == 'true'
        self.data_dir = Path(os.getenv('DATA_DIR', str(ROOT / 'data')))
        self.instrument_map = read_json(os.getenv('INSTRUMENTS_FILE', str(ROOT / 'data' / 'instruments.json')), {})
        self.fundamentals_file = os.getenv('FUNDAMENTALS_FILE', str(ROOT / 'data' / 'fundamentals.json'))
        self.host = os.getenv('HOST', '127.0.0.1')
        self.port = int(os.getenv('PORT', '8000'))

    @property
    def ai_enabled(self):
        return bool(self.ai_key and self.ai_model)

    @property
    def kite_enabled(self):
        return self.broker == 'zerodha' and bool(self.kite_api_key and self.kite_access_token)

    @property
    def broker_enabled(self):
        return (self.broker=='fyers' and bool(self.fyers_app_id and self.fyers_token)) or self.kite_enabled or (self.broker == 'upstox' and bool(self.upstox_token))

    def instrument(self, symbol):
        return self.instrument_map.get(symbol, UNIVERSE.get(symbol, {}).get('key'))
