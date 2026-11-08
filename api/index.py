"""Vercel WSGI entrypoint. Only disposable caches use /tmp."""
import os
os.environ.setdefault('DATA_DIR','/tmp/consentdesk')
os.environ.setdefault('PUBLIC_MODE','true')
os.environ['MARKET_STREAM_ENABLED']='false'
from consentdesk.serverless import create_app
app=create_app()
