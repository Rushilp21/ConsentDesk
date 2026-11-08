"""Build static assets for the root Vercel project; APIs run on the same origin."""
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]

def prepare(destination=None):
    destination=Path(destination or ROOT/'public').resolve()
    if destination in (ROOT,ROOT/'web'):raise ValueError('Use a separate output directory')
    destination.mkdir(parents=True,exist_ok=True)
    for path in (ROOT/'web').iterdir():
        if path.is_file() and path.name!='desk-section.html':shutil.copyfile(path,destination/path.name)
    html=(ROOT/'web/index.html').read_text(encoding='utf-8').replace('<section id="desk-view" hidden></section>',(ROOT/'web/desk-section.html').read_text(encoding='utf-8'))
    (destination/'index.html').write_text(html,encoding='utf-8')
    return destination

if __name__=='__main__':print(prepare())
