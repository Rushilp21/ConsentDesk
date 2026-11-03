import copy
import json
import threading
import time
import weakref
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

class ProviderError(Exception):
    pass

class TTLCache:
    def __init__(self):
        self.items = {}
        self.lock = threading.Lock()
        self.loading=weakref.WeakValueDictionary()

    def get_or_load(self, key, ttl, load):
        with self.lock:gate=self.loading.setdefault(key,threading.Lock())
        with gate:return self._get_or_load(key,ttl,load)

    def _get_or_load(self,key,ttl,load):
        with self.lock:
            entry = self.items.get(key)
            if entry and time.monotonic() - entry[0] < ttl:
                return copy.deepcopy(entry[1])
        value = load()
        with self.lock:
            self.items[key] = (time.monotonic(), copy.deepcopy(value))
            if len(self.items) > 200:
                oldest = min(self.items, key=lambda k: self.items[k][0])
                del self.items[oldest]
        return value

CACHE = TTLCache()

def request(url, *, headers=None, payload=None, timeout=12, max_bytes=4_000_000):
    merged = {'User-Agent': 'Mozilla/5.0 ConsentDesk/1.0', 'Accept': 'application/json'}
    merged.update(headers or {})
    data = None
    if payload is not None:
        data = json.dumps(payload, allow_nan=False).encode()
        merged['Content-Type'] = 'application/json'
    try:
        with urlopen(Request(url, data=data, headers=merged), timeout=timeout) as response:
            result = response.read(max_bytes+1)
            if len(result) > max_bytes:
                raise ProviderError('Provider response exceeds the size limit')
            return result
    except HTTPError as e:
        raise ProviderError(f'Provider returned HTTP {e.code}') from None
    except (URLError, TimeoutError, OSError):
        raise ProviderError('Provider unavailable or request timed out') from None

def request_json(url, **kwargs):
    try:
        return json.loads(request(url, **kwargs))
    except (ValueError, UnicodeError):
        raise ProviderError('Provider returned invalid JSON') from None
