"""One Upstox V3 full-feed connection shared by dashboard clients.

Minimal protobuf wire reader follows bundled official MarketDataFeed.proto.
Unknown fields are skipped. No credential or redirect URL reaches the browser.
"""
import copy
from datetime import datetime
import json
import math
import struct
import threading
import time
from .providers import IST, now_ist, finite


def fields(raw):
    pos=0;out={}
    def varint():
        nonlocal pos
        value=0
        for shift in range(0,70,7):
            if pos>=len(raw):raise ValueError('Truncated protobuf')
            byte=raw[pos];pos+=1;value|=(byte&127)<<shift
            if byte<128:return value
        raise ValueError('Oversized protobuf integer')
    while pos<len(raw):
        tag=varint();field,wire=tag>>3,tag&7
        if not field:raise ValueError('Invalid protobuf field')
        if wire==0:value=varint()
        elif wire in (1,5):
            size=8 if wire==1 else 4
            if pos+size>len(raw):raise ValueError('Truncated fixed field')
            value=struct.unpack('<d' if wire==1 else '<f',raw[pos:pos+size])[0];pos+=size
        elif wire==2:
            length=varint()
            if pos+length>len(raw):raise ValueError('Truncated message')
            value=raw[pos:pos+length];pos+=length
        else:raise ValueError('Unsupported protobuf wire type')
        out.setdefault(field,[]).append(value)
    return out


def decode(raw):
    root=fields(raw);stamp=root.get(3,[0])[0];result={}
    # Initial packets are retained with original dates but never treated as live ticks.
    for entry in root.get(2,[]):
        pair=fields(entry);key=pair[1][0].decode();feed=fields(pair[2][0]);depth={};volume=None
        if 2 in feed:
            full=fields(feed[2][0]);market=fields((full.get(1) or full.get(2))[0]);ltpc=fields(market[1][0])
            if 1 in full:
                volume=market.get(6,[None])[0]
                if 2 in market:
                    quotes=fields(market[2][0]).get(1,[])
                    if quotes:depth=fields(quotes[0])
        elif 1 in feed:ltpc=fields(feed[1][0])
        else:continue
        price=ltpc.get(1,[None])[0];prior=ltpc.get(4,[None])[0];last=ltpc.get(2,[None])[0]
        if not finite(price) or price<=0 or not finite(stamp) or stamp<=0:continue
        result[key]={'price':price,'previous_close':prior,'change_pct':round((price/prior-1)*100,2) if finite(prior) and prior>0 else None,
            'as_of':datetime.fromtimestamp(stamp/1000,IST).isoformat(),
            'last_trade_at':datetime.fromtimestamp(last/1000,IST).isoformat() if last else None,
            'bid':depth.get(2,[None])[0],'ask':depth.get(4,[None])[0],'volume':volume,
            'source':'Upstox V3 WebSocket','broker':True,'synthetic':False,'stream_tick':root.get(1,[0])[0]==1}
    return result


class MarketStream:
    def __init__(self,providers,stopping):
        self.providers=providers;self.stopping=stopping;self.lock=threading.RLock()
        self.wanted={};self.subscribed=set();self.quotes={};self.socket=None
        self.state='TOKEN REQUIRED' if not providers.settings.upstox_token else 'CONNECTING'
        self.connected=False;self.last_packet=None

    def start(self):
        if not self.providers.settings.stream_enabled or self.providers.settings.broker!='upstox':self.state='SNAPSHOTS ONLY';return
        if not self.providers.settings.upstox_token:return
        try:import websocket
        except ImportError:self.state='Install requirements.txt for streaming';return
        def loop():
            delay=1
            while not self.stopping.is_set():
                with self.lock:has_work=bool(self.wanted)
                if not has_work:self.stopping.wait(1);continue
                try:
                    redirect=self.providers._broker('v3/feed/market-data-feed/authorize')['authorized_redirect_uri']
                    if not isinstance(redirect,str) or not redirect.startswith('wss://'):raise ValueError('Invalid feed redirect')
                    socket=websocket.create_connection(redirect,timeout=2,enable_multithread=True)
                    with self.lock:self.socket=socket;self.connected=True;self.state='CONNECTED';self.subscribed=set()
                    delay=1
                    while not self.stopping.is_set():
                        with self.lock:
                            now=time.monotonic();active={k for k,v in self.wanted.items() if now-v['seen']<300}
                            add=active-self.subscribed;remove=self.subscribed-active
                            for method,keys in [('sub',add),('unsub',remove)]:
                                if keys:socket.send_binary(json.dumps({'guid':'consentdesk','method':method,'data':{'mode':'full','instrumentKeys':sorted(keys)}}).encode())
                            self.subscribed=active
                        try:
                            raw=socket.recv()
                            if not raw:break
                            if not isinstance(raw,bytes):continue
                            values=decode(raw)
                            with self.lock:
                                for key,q in values.items():
                                    if key in self.wanted:self.quotes[self.wanted[key]['symbol']]={**q,'symbol':self.wanted[key]['symbol']}
                                self.last_packet=now_ist().isoformat()
                        except websocket.WebSocketTimeoutException:continue
                except Exception:
                    # Never log an authorized redirect or authentication-bearing exception.
                    with self.lock:self.state='RECONNECTING · REST fallback';self.connected=False
                finally:
                    with self.lock:
                        if self.socket:
                            try:self.socket.close()
                            except Exception:pass
                        self.socket=None;self.connected=False
                self.stopping.wait(delay);delay=min(30,delay*2)
        threading.Thread(target=loop,daemon=True).start()

    def subscribe(self,symbols):
        for symbol in symbols:
            with self.lock:
                existing=next((key for key,v in self.wanted.items() if v['symbol']==symbol),None)
                if existing:self.wanted[existing]['seen']=time.monotonic();continue
            try:key=self.providers.symbol_key(symbol)
            except Exception:continue
            if key:
                with self.lock:
                    if len(self.wanted)>=200:
                        oldest=min(self.wanted,key=lambda k:self.wanted[k]['seen']);self.wanted.pop(oldest)
                    self.wanted[key]={'symbol':symbol,'seen':time.monotonic()}

    def snapshot(self,symbols):
        with self.lock:
            now=now_ist();items=[]
            for symbol in symbols:
                q=copy.deepcopy(self.quotes.get(symbol))
                if not q:continue
                age=(now-datetime.fromisoformat(q['as_of'])).total_seconds()
                if not 0<=age<=30:continue
                q['live']=bool(self.connected and q['stream_tick'] and now.weekday()<5 and (9,15)<=(now.hour,now.minute)<(15,30))
                q['label']='LIVE STREAM' if q['live'] else 'BROKER STREAM SNAPSHOT'
                items.append(q)
            return {'quotes':items,'stream':{'state':self.state,'connected':self.connected,'last_packet_at':self.last_packet,'subscribed':len(self.subscribed)}}
