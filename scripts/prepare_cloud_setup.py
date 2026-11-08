"""Generate PRIVATE Vercel env + scheduler setup files. Never print secrets."""
from pathlib import Path
import os
import secrets
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from consentdesk.config import Settings,ROOT


def prepare(root=ROOT):
    root=Path(root);path=root/'.env.vercel'
    values={}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key,value=line.split('=',1);values[key]=value
    settings=Settings()
    values.setdefault('DATABASE_URL','')
    values.setdefault('CRON_SECRET',secrets.token_urlsafe(48))
    if len(values['CRON_SECRET'])<32:raise ValueError('CRON_SECRET must be at least 32 characters')
    values.update(PUBLIC_MODE='true',SECURE_COOKIES='true',PUBLIC_ORIGIN='https://consent-desk-tan.vercel.app',
        DATA_DIR='/tmp/consentdesk',MARKET_STREAM_ENABLED='false',DAILY_SCAN_TIME='07:00',DAILY_SCAN_ENABLED='true',
        DATABASE_NAMESPACE=values.get('DATABASE_NAMESPACE','cd'))
    values.setdefault('PUBLIC_DATA_APPROVED','false')
    values.setdefault('MARKET_BROKER',settings.broker)
    values.setdefault('UPSTOX_ANALYTICS_TOKEN',settings.upstox_token)
    values.setdefault('OPENAI_API_KEY',settings.ai_key)
    values.setdefault('OPENAI_MODEL',settings.ai_model)
    path.write_text('# PRIVATE: import into Vercel Production environment; never commit this file.\n'+'\n'.join(key+'='+value for key,value in values.items())+'\n',encoding='utf-8')
    out=root/'output';out.mkdir(exist_ok=True)
    template=(ROOT/'deployment/supabase/scheduler.sql').read_text(encoding='utf-8')
    # Generated secrets are URL-safe; reject quotes if a custom secret was used.
    secret=values['CRON_SECRET']
    if any(c in secret for c in "'\r\n"):raise ValueError('Use a URL-safe CRON_SECRET')
    (out/'supabase-scheduler.private.sql').write_text(template.replace('REPLACE_WITH_CRON_SECRET',secret),encoding='utf-8')
    return path,out/'supabase-scheduler.private.sql'


if __name__=='__main__':
    for path in prepare():print('Prepared private setup file: '+str(path))
