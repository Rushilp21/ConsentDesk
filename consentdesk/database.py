"""Short-lived PostgreSQL transactions for Supabase; SQLite for local use.

Namespaces are private schemas, never Supabase's browser-exposed public schema.
The adapter supports the deliberately small SQL vocabulary used by this app.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import sqlite3

COLUMNS = {
    'reports':'id,session,profile,created_at,question,payload', 'jobs':'id,payload',
    'users':'id,email,salt,password,created', 'sessions':'token,user_id,csrf,expires',
    'usage':'user_id,day,kind,count',
    'cd_queue':'id,task_key,owner,kind,status,payload,lease,lease_until,created,updated',
}
for name in ('desk_settings','paper_trades','desk_alerts','desk_events','desk_ideas','desk_quotes','desk_financials','desk_validations','desk_checks'):
    COLUMNS[name]='id,payload'


def namespace(path):
    path=Path(path)
    prefix=os.getenv('DATABASE_NAMESPACE','cd')
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,23}',prefix):raise ValueError('Invalid database namespace')
    if path.name=='accounts.sqlite3':return prefix+'_accounts'
    if path.parent.parent.name=='users':
        if not re.fullmatch(r'[a-f0-9]{32}',path.parent.name):raise ValueError('Invalid database workspace')
        return prefix+'_user_'+path.parent.name
    return prefix+'_shared'


def postgres_sql(query):
    if query.strip().upper()=='BEGIN IMMEDIATE':return 'SELECT 1'
    query=re.sub(r'\bREAL\b','DOUBLE PRECISION',query,flags=re.I)
    query=re.sub(r'\browid\b','_order',query,flags=re.I)
    create=re.match(r'CREATE TABLE IF NOT EXISTS (\w+)\s*\(',query,re.I)
    if create:
        # Extra defaulted ordering column replaces SQLite's implicit rowid.
        end=query.rfind(')')
        query=query[:end]+', _order BIGSERIAL UNIQUE'+query[end:]
    ignore=bool(re.match(r'INSERT OR IGNORE',query,re.I))
    query=re.sub(r'^INSERT OR IGNORE','INSERT',query,flags=re.I)
    insert=re.match(r'INSERT INTO (\w+)\s+VALUES',query,re.I)
    if insert:
        table=insert.group(1)
        if table not in COLUMNS:raise ValueError('Unknown insert table')
        query=query[:insert.start(1)]+table+' ('+COLUMNS[table]+') VALUES'+query[insert.end():]
    if ignore:query+=' ON CONFLICT DO NOTHING'
    return query.replace('%','%%').replace('?', '%s')


class PostgresConnection:
    def __init__(self, raw):self.raw=raw
    def execute(self, query, args=()):
        import psycopg
        try:return self.raw.execute(postgres_sql(query),args)
        except psycopg.IntegrityError as error:
            raise sqlite3.IntegrityError('Database constraint failed') from error


@contextmanager
def connect(path):
    url=os.getenv('DATABASE_URL','')
    if not url:
        raw=sqlite3.connect(path,timeout=15)
        try:
            with raw:yield raw
        finally:raw.close()
        return
    import psycopg
    schema=namespace(path)
    # Disable prepared statements for Supabase's transaction pooler.
    # No idle/session connection pool is retained by a function instance.
    with psycopg.connect(url,sslmode='require',connect_timeout=8,prepare_threshold=None) as raw:
        raw.execute("SET LOCAL statement_timeout = '15s'")
        raw.execute("SET LOCAL lock_timeout = '10s'")
        # Serializes short read/write transactions per workspace, including
        # budgets and paper limits, across different function instances.
        raw.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(schema,))
        raw.execute('CREATE SCHEMA IF NOT EXISTS '+schema)
        raw.execute('SET LOCAL search_path TO '+schema)
        yield PostgresConnection(raw)
