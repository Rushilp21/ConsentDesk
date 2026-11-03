"""Read-only FYERS v3 quote/history adapter. No order APIs."""
from datetime import datetime, timedelta
from urllib.parse import urlencode
from .net import CACHE, ProviderError, request_json
from .providers import IST, finite, now_ist


class FyersData:
    def __init__(self,settings):self.settings=settings

    @staticmethod
    def symbol(symbol):
        return {'NIFTY':'NSE:NIFTY50-INDEX','BANKNIFTY':'NSE:NIFTYBANK-INDEX'}.get(symbol,'NSE:'+symbol+'-EQ')

    def get(self,path,params=None,data=True):
        if not self.settings.fyers_app_id or not self.settings.fyers_token:raise ProviderError('Configure FYERS_APP_ID and FYERS_ACCESS_TOKEN')
        base='https://api-t1.fyers.in/'+('data/' if data else 'api/v3/')
        result=request_json(base+path+('?' + urlencode(params) if params else ''),headers={'Authorization':self.settings.fyers_app_id+':'+self.settings.fyers_token})
        if result.get('s')!='ok':raise ProviderError('FYERS request failed; check token expiry, app permissions and plan limits')
        return result

    def quotes(self,symbols):
        raw=CACHE.get_or_load(('fyers-quotes',tuple(symbols)),10,lambda:self.get('quotes',{'symbols':','.join(self.symbol(s) for s in symbols)}))
        result={}
        for item in raw.get('d',[]):
            value=item.get('v') or {};symbol=next((s for s in symbols if self.symbol(s)==item.get('n',value.get('symbol'))),None)
            if not symbol or not finite(value.get('lp')) or value['lp']<=0:continue
            ts=value.get('tt')
            try:dated=datetime.fromtimestamp(float(ts),IST).isoformat() if ts else None
            except (TypeError,ValueError,OverflowError,OSError):dated=None
            result[symbol]={'symbol':symbol,'price':value['lp'],'as_of':dated,'last_trade_at':dated,'change_pct':value.get('chp'),
                'previous_close':value.get('prev_close_price'),'volume':value.get('volume'),'bid':value.get('bid'),'ask':value.get('ask'),
                'source':'FYERS authenticated quote snapshot','broker':True,'synthetic':False,'live':False}
        return result

    def market_status(self):
        try:
            raw=CACHE.get_or_load(('fyers-session',),20,lambda:self.get('market-status',data=False))
            row=next((r for r in raw.get('marketStatus',[]) if r.get('exchange')==10 and r.get('segment')==10 and r.get('market_type')=='NORMAL'),None)
            if not row:raise ProviderError('NSE status unavailable')
            return {'verified':True,'open':row['status']=='OPEN','label':'FYERS NSE '+row['status'],'checked_at':now_ist().isoformat()}
        except (ProviderError,KeyError,TypeError):return {'verified':False,'open':False,'label':'FYERS session unavailable · check Transactions Info permission','checked_at':now_ist().isoformat()}

    def candles(self,symbol,mode,validation=False):
        from .providers import Providers
        today=now_ist().date();days=(730 if validation else 365) if mode=='longterm' else 30
        def load():
            rows=[];start=today-timedelta(days=days)
            while start<=today:
                end=min(today,start+timedelta(days=365 if mode=='longterm' else 90))
                raw=self.get('history',{'symbol':self.symbol(symbol),'resolution':'D' if mode=='longterm' else '5','date_format':'1',
                    'range_from':str(start),'range_to':str(end),'cont_flag':'1'})
                try:rows.extend({'t':c[0],'o':c[1],'h':c[2],'l':c[3],'c':c[4],'v':c[5]} for c in raw['candles'])
                except (KeyError,TypeError,IndexError):raise ProviderError('Malformed FYERS historical candles') from None
                start=end+timedelta(days=1)
            return Providers._package(rows,'FYERS broker historical candles',False,True)
        return CACHE.get_or_load(('fyers-history',symbol,mode,validation),300 if mode=='longterm' else 30,load)
