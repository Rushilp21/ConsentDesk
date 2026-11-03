"""Read-only provider smoke check; does not use an AI key or place orders."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from consentdesk.config import Settings
from consentdesk.providers import Providers, ProviderError
from consentdesk.analysis import indicators

if __name__ == '__main__':
    providers = Providers(Settings())
    failed = False
    for name, call in [('news', lambda: providers.news('INFY', 'longterm')),
                       ('prices', lambda: providers.candles('INFY', 'longterm'))]:
        try:
            result = call()
            print(name, 'OK', len(result.get('articles', result.get('candles', []))), result.get('as_of', ''))
            if name == 'news':
                print([(a['publisher'], a['title']) for a in result['articles'][:2]])
            else:
                print(indicators(result, 'longterm'))
        except ProviderError as e:
            failed = True
            print(name, 'FAILED', str(e))
    sys.exit(1 if failed else 0)
