"""Build a credential-free Vercel frontend with same-origin API rewrites."""
import argparse
import json
from pathlib import Path
import shutil
from urllib.parse import urlparse

ROOT=Path(__file__).resolve().parents[1]


def prepare(backend,destination):
    url=urlparse(backend)
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
        raise ValueError('Use the public HTTPS backend origin without credentials, query or path')
    destination=Path(destination).resolve()
    if destination==ROOT or ROOT/'web'==destination:raise ValueError('Use a separate deployment directory')
    destination.mkdir(parents=True,exist_ok=True)
    for path in (ROOT/'web').iterdir():
        if path.is_file() and path.name!='desk-section.html':shutil.copyfile(path,destination/path.name)
    html=(ROOT/'web/index.html').read_text(encoding='utf-8').replace('<section id="desk-view" hidden></section>',(ROOT/'web/desk-section.html').read_text(encoding='utf-8'))
    (destination/'index.html').write_text(html,encoding='utf-8')
    config={'$schema':'https://openapi.vercel.sh/vercel.json','rewrites':[{'source':'/api/:path*','destination':backend.rstrip('/')+'/api/:path*'}],
        'headers':[{'source':'/(.*)','headers':[{'key':'X-Content-Type-Options','value':'nosniff'},{'key':'Referrer-Policy','value':'same-origin'}]}]}
    (destination/'vercel.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
    (destination/'DEPLOYMENT.txt').write_text('Deploy this directory as Other/static, no build command, output directory dot. The HTTPS backend must run separately with persistent data. No broker secrets belong in Vercel frontend files.\n',encoding='utf-8')
    return destination


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--backend',required=True);parser.add_argument('--destination',default=str(ROOT/'deployment/vercel'))
    args=parser.parse_args();print(prepare(args.backend,args.destination))
