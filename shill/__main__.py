import argparse
import os
import uvicorn
from .config import load_settings
from .db import Database


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description="Signal Garden: local-first X content review")
    p.add_argument('command',choices=['serve','backup','check'],nargs='?',default='serve')
    p.add_argument('--config',default=None)
    p.add_argument('--output',help='New backup file path')
    args=p.parse_args()
    settings=load_settings(args.config)
    if args.command=='backup':
        if not args.output: p.error('--output is required for backup')
        Database(settings.database).backup(args.output)
        print('Consistent SQLite backup created (includes WAL content).')
    elif args.command=='check':
        with Database(settings.database).connect() as c:
            result=c.execute('PRAGMA integrity_check').fetchone()[0]
        if result!='ok': raise SystemExit('Database integrity check failed')
        print('Configuration and SQLite integrity: OK. Live X remains unavailable.')
    else:
        from .app import create_app
        uvicorn.run(create_app(settings),host=settings.host,port=settings.port,access_log=False,proxy_headers=False)


if __name__=='__main__': main()
