"""User-run FYERS OAuth helper. Secrets are hidden and never printed."""
import getpass
import hashlib
import json
from pathlib import Path
import re
import secrets
import sys
from urllib.parse import urlencode,urlparse,parse_qs
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from consentdesk.config import ROOT,Settings
from consentdesk.net import request_json,ProviderError


def save_token(app_id,token,path):
    text=path.read_text(encoding='utf-8') if path.exists() else ''
    for key,value in {'MARKET_BROKER':'fyers','FYERS_APP_ID':app_id,'FYERS_ACCESS_TOKEN':token}.items():
        if not isinstance(value,str) or '\n' in value or '\r' in value:raise ValueError('Invalid credential value')
        if re.search('^'+key+'=',text,re.M):text=re.sub('^'+key+'=.*$',lambda m:key+'='+value,text,flags=re.M)
        else:text+='\n'+key+'='+value+'\n'
    tmp=path.with_suffix('.tmp');tmp.write_text(text,encoding='utf-8');tmp.replace(path)


if __name__=='__main__':
    try:
        app_id=input('App ID (including -100): ').strip()
        if not re.fullmatch(r'[A-Za-z0-9-]{5,64}',app_id):raise ValueError('Enter the App ID from FYERS dashboard')
        secret=getpass.getpass('App secret (hidden): ').strip()
        redirect=input('Exact registered redirect URL: ').strip()
        if urlparse(redirect).scheme!='https':raise ValueError('Use your registered HTTPS redirect URL')
        state=secrets.token_urlsafe(24)
        print('Open this official FYERS login URL in your browser:')
        print('https://api-t1.fyers.in/api/v3/generate-authcode?'+urlencode({'client_id':app_id,'redirect_uri':redirect,'response_type':'code','state':state}))
        result=getpass.getpass('Paste the FULL redirected URL after login (hidden): ').strip()
        parsed=urlparse(result);expected=urlparse(redirect);query=parse_qs(parsed.query)
        if (parsed.scheme,parsed.netloc,parsed.path)!=(expected.scheme,expected.netloc,expected.path) or query.get('state')!=[state]:raise ValueError('Redirect/state mismatch; start login again')
        code=query.get('auth_code',[None])[0]
        if not code:raise ValueError('No auth_code in redirect URL')
        response=request_json('https://api-t1.fyers.in/api/v3/validate-authcode',payload={'grant_type':'authorization_code','appIdHash':hashlib.sha256((app_id+':'+secret).encode()).hexdigest(),'code':code})
        if response.get('s')!='ok' or not response.get('access_token'):raise ValueError('FYERS token exchange failed; check app credentials and repeat login')
        save_token(app_id,response['access_token'],ROOT/'.env')
        print('FYERS credentials saved in server .env. Restart ConsentDesk. Renew the access token when it expires.')
    except (ValueError,ProviderError) as e:print(str(e));sys.exit(1)
