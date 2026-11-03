"""Local accounts, opaque cookie sessions and bounded public usage."""
from collections import defaultdict, deque
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager


class Accounts:
    def __init__(self,path,max_users=100):
        self.path=path;self.max_users=max_users;self.lock=threading.Lock();self.attempts={}
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE,salt TEXT,password TEXT,created REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id TEXT,csrf TEXT,expires REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS usage(user_id TEXT,day TEXT,kind TEXT,count INTEGER,PRIMARY KEY(user_id,day,kind))')

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=15)
        try:
            with db:yield db
        finally:db.close()

    @staticmethod
    def password_hash(password,salt):
        return hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()

    def register(self,email,password):
        if not isinstance(email,str) or not re.fullmatch(r'[^\s@]{1,100}@[^\s@]{1,150}\.[^\s@]{2,30}',email):raise ValueError('Enter a valid email address')
        if not isinstance(password,str) or not 12<=len(password)<=128:raise ValueError('Use a password of 12–128 characters')
        salt=secrets.token_hex(16);encoded=self.password_hash(password,salt);email=email.lower().strip()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT count(*) FROM users').fetchone()[0]>=self.max_users:raise ValueError('Registration is full')
            try:db.execute('INSERT INTO users VALUES(?,?,?,?,?)',(secrets.token_hex(16),email,salt,encoded,time.time()))
            except sqlite3.IntegrityError:raise ValueError('Account cannot be created; try signing in') from None

    def login(self,email,password):
        if not isinstance(email,str) or not isinstance(password,str) or len(password)>128:raise ValueError('Invalid email or password')
        with self.db() as db:
            row=db.execute('SELECT id,salt,password FROM users WHERE email=?',(email.strip().lower(),)).fetchone()
        salt=row[1] if row else '0'*32
        encoded=self.password_hash(password,salt)
        if not row or not hmac.compare_digest(encoded,row[2]):raise ValueError('Invalid email or password')
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),row[0],csrf,time.time()+7*86400))
        return token,{'id':row[0],'csrf':csrf}

    def session(self,token):
        if not token or len(token)>200:return None
        with self.db() as db:row=db.execute('SELECT user_id,csrf FROM sessions WHERE token=? AND expires>?',(hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
        return {'id':row[0],'csrf':row[1]} if row else None

    def logout(self,token):
        with self.db() as db:db.execute('DELETE FROM sessions WHERE token=?',(hashlib.sha256(token.encode()).hexdigest(),))

    def allow(self,key,limit,seconds=60):
        with self.lock:
            now=time.monotonic()
            if len(self.attempts)>10000:self.attempts={k:q for k,q in self.attempts.items() if q and now-q[-1]<3600}
            q=self.attempts.setdefault(key,deque())
            while q and now-q[0]>seconds:q.popleft()
            if len(q)>=limit:return False
            q.append(now);return True

    def spend(self,user_id,kind,limit,day):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT count FROM usage WHERE user_id=? AND day=? AND kind=?',(user_id,day,kind)).fetchone()
            count=row[0] if row else 0
            if count>=limit:return False
            db.execute('INSERT INTO usage VALUES(?,?,?,1) ON CONFLICT(user_id,day,kind) DO UPDATE SET count=count+1',(user_id,day,kind))
        return True
