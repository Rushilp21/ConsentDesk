"""Consistent SQLite backups, including private account workspaces.

Run nightly: python scripts/backup.py --destination backups
Keep the destination private: it contains account hashes and personal journals.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from consentdesk.config import Settings


def backup(source,destination):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if destination==source or source in destination.parents:raise ValueError('Place backups outside the data directory')
    target=destination/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');target.mkdir(parents=True,exist_ok=False)
    files=list(source.rglob('*.sqlite3'))
    for path in files:
        output=target/path.relative_to(source);output.parent.mkdir(parents=True,exist_ok=True)
        original=sqlite3.connect(str(path));copy=sqlite3.connect(output)
        try:original.backup(copy)
        finally:copy.close();original.close()
    return target,len(files)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--destination',default='backups');args=parser.parse_args()
    path,count=backup(Settings().data_dir,args.destination);print(f'{count} databases backed up to {path}')
