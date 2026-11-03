"""Train the chart model on provider history or an OHLCV CSV and export measured results."""
import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import re
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from consentdesk.config import Settings
from consentdesk.providers import Providers, IST
from consentdesk.learning import PatternLearner

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbol',required=True)
    parser.add_argument('--mode',choices=['intraday','longterm'],default='longterm')
    parser.add_argument('--csv',type=Path,help='Columns: timestamp,open,high,low,close,volume; timestamp ISO with timezone or epoch seconds')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    symbol=args.symbol.upper().removesuffix('.NS')
    if not re.fullmatch(r'[A-Z][A-Z0-9&-]{0,19}',symbol):parser.error('Use an NSE symbol')
    settings=Settings();providers=Providers(settings)
    if args.csv:
        rows=[]
        for r in csv.DictReader(args.csv.open(encoding='utf-8-sig',newline='')):
            try:t=int(float(r['timestamp']))
            except ValueError:
                date=datetime.fromisoformat(r['timestamp']);t=int((date if date.tzinfo else date.replace(tzinfo=IST)).timestamp())
            rows.append({'t':t,**{short:float(r[name]) for short,name in [('o','open'),('h','high'),('l','low'),('c','close'),('v','volume')]}})
        bundle=Providers._package(rows,'User historical CSV (verify adjustments and chronology)',False,False)
    else:bundle=providers.candles(symbol,args.mode)
    result=PatternLearner(settings.data_dir).inspect(symbol,bundle,args.mode)
    result['history_source']=bundle['source']
    destination=args.output or settings.data_dir/'models'/f'{symbol}-{args.mode}-metrics.json'
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(result,indent=2,allow_nan=False))
    print(f'Metrics saved: {destination.resolve()}')

if __name__=='__main__':main()
