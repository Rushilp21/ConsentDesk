import json
import sqlite3
import uuid
from contextlib import contextmanager
from .providers import now_ist
from .database import connect

class Store:
    def __init__(self, data_dir):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / 'research.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, session TEXT, profile TEXT, created_at TEXT, question TEXT, payload TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS reports_session ON reports(session, created_at)')
            db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,payload TEXT)')

    def save_job(self,job_id,job):
        with self.connect() as db:
            db.execute('INSERT INTO jobs VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(job_id,json.dumps(job,allow_nan=False)))
            db.execute('DELETE FROM jobs WHERE id NOT IN (SELECT id FROM jobs ORDER BY rowid DESC LIMIT 100)')

    def load_jobs(self):
        import time
        with self.connect() as db:rows=db.execute('SELECT id,payload FROM jobs ORDER BY rowid DESC LIMIT 100').fetchall()
        result={}
        for key,raw in rows:
            job=json.loads(raw);job['started']=time.monotonic()
            if job['status']=='running':job.update(status='error',error='Server restarted before this job finished. Previous saved brief retained; retry the scan.')
            result[key]=job
        return result

    @contextmanager
    def connect(self):
        with connect(self.path) as connection:yield connection

    def save(self, session, profile, question, report, on_saved=None):
        report['id'] = uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO reports VALUES (?,?,?,?,?,?)', (report['id'], session, profile, report['created_at'], question, json.dumps(report, allow_nan=False)))
            if on_saved:on_saved(report,db)
        return report

    def history(self, session, profile, limit=20):
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM reports WHERE session=? AND profile=? ORDER BY rowid DESC LIMIT ?', (session, profile, limit)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def clear(self, session, profile):
        with self.connect() as db:
            db.execute('DELETE FROM reports WHERE session=? AND profile=?', (session, profile))

    def get(self, report_id, session, profile):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM reports WHERE id=? AND session=? AND profile=?', (report_id, session, profile)).fetchone()
        return json.loads(row[0]) if row else None

    def unify_workspace(self):
        # Preserve every old report while merging the previous preference profiles.
        with self.connect() as db:
            db.execute("UPDATE reports SET session='common-workspace', profile='common' WHERE session<>'common-workspace' OR profile<>'common'")

    def daily_report(self, mode, day=None, brief_version=None,sector='all'):
        # A saved brief must survive any number of ordinary chatbot reports.
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM reports WHERE session=? AND profile=? ORDER BY rowid DESC', ('common-workspace', 'common'))
            for row in rows:
                report = json.loads(row[0])
                if report.get('daily_scan') and report.get('sector','all')==sector and report.get('mode') == mode and (not day or report['created_at'].startswith(day)) and (brief_version is None or report.get('brief_version') == brief_version):
                    return report
        return None
