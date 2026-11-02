"""One authorized USD 10 campaign, separate durable reservation ledger."""
from datetime import datetime, timezone
import hashlib
import json
import math
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from agent_runner_v2 import load_key

CAP = 10.0
PRICE_IN = 0.10 / 1_000_000
PRICE_OUT = 0.40 / 1_000_000
FINAL_RESERVE = (160_000 + 4096) * PRICE_IN + 2048 * PRICE_OUT


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.pending')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


class BudgetLimit(RuntimeError):
    pass


class AblationClient:
    def __init__(self, out, *, key=None, transport=None):
        self.out = Path(out)
        self.ledger = self.out / 'budget_10usd.json'
        self.key = load_key() if key is None else key
        self.transport = transport or urllib.request.urlopen
        self.lock = threading.RLock()
        if self.ledger.exists():
            self.account = json.loads(self.ledger.read_text(encoding='utf-8'))
            assert self.account['cap_usd'] == CAP
        else:
            self.account = {'cap_usd': CAP, 'accounted_usd': 0.0, 'reported_usd': 0.0,
                            'authorized_by': 'User explicitly approved new experiments up to USD 10',
                            'requests': [], 'finalized_episodes': []}
            atomic_json(self.ledger, self.account)

    def remaining(self):
        with self.lock:
            return CAP - self.account['accounted_usd']

    def request(self, payload, destination):
        destination = Path(destination)
        assert payload['model'] == 'google/gemma-4-31b-it'
        assert payload['provider']['only'] == ['coreweave/fp4']
        assert payload['provider']['allow_fallbacks'] is False
        assert payload['provider']['max_price'] == {'prompt': 0.10, 'completion': 0.40, 'request': 0}
        final = 'tools' not in payload
        assert payload['max_tokens'] == (2048 if final else 4096)
        encoded = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        if len(encoded) > 160_000:
            raise RuntimeError('Context validity gate: payload exceeds 160000 bytes')
        reserve = (len(encoded) + 4096) * PRICE_IN + payload['max_tokens'] * PRICE_OUT
        for retry in range(3):
            target = destination if retry == 0 else destination.with_name(destination.name + f'_transport_retry{retry}')
            with self.lock:
                if target.with_suffix('.request.json').exists():
                    raise RuntimeError('Refusing to overwrite an archived paid request')
                unfinished = 64 - len(self.account['finalized_episodes'])
                protected = max(0, unfinished - int(final)) * FINAL_RESERVE
                if self.account['accounted_usd'] + reserve + protected > CAP:
                    raise BudgetLimit('Ablation budget cannot cover this request and remaining final reports')
                event = {'id': len(self.account['requests']), 'destination': target.relative_to(self.out).as_posix(),
                         'request_sha256': hashlib.sha256(encoded).hexdigest(), 'is_final': final,
                         'reserved_usd': reserve, 'accounted_usd': reserve, 'reported_usd': None,
                         'status': 'reserved_before_network', 'utc': datetime.now(timezone.utc).isoformat()}
                self.account['requests'].append(event)
                self.account['accounted_usd'] += reserve
                atomic_json(self.ledger, self.account)
                atomic_json(target.with_suffix('.request.json'), payload)
            started = time.perf_counter()
            request = urllib.request.Request('https://openrouter.ai/api/v1/chat/completions', data=encoded,
                headers={'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'}, method='POST')
            try:
                with self.transport(request, timeout=60) as response:
                    raw = json.loads(response.read())
                atomic_json(target.with_suffix('.response.json'), raw)
                atomic_json(target.with_suffix('.timing.json'), {'wall_seconds': time.perf_counter() - started,
                            'recorded_utc': datetime.now(timezone.utc).isoformat()})
                cost = raw.get('usage', {}).get('cost')
                with self.lock:
                    if cost is not None:
                        cost = float(cost)
                        if not math.isfinite(cost) or cost < 0:
                            raise RuntimeError('Invalid nonfinite or negative API cost')
                        self.account['accounted_usd'] += cost - reserve
                        self.account['reported_usd'] += cost
                        event.update(reported_usd=cost, accounted_usd=cost)
                    event['status'] = 'response_received'
                    atomic_json(self.ledger, self.account)
                    if cost is not None and cost > reserve + 1e-9:
                        raise BudgetLimit('Provider charge exceeds conservative request reservation; stop for reconciliation')
                if raw.get('error') or not raw.get('choices'):
                    raise RuntimeError('API returned no model choice')
                provider = str(raw.get('provider', '')).lower()
                if provider != 'coreweave':
                    raise RuntimeError('Pinned provider identity mismatch')
                if final:
                    with self.lock:
                        episode = destination.parent.name
                        if episode not in self.account['finalized_episodes']:
                            self.account['finalized_episodes'].append(episode)
                        atomic_json(self.ledger, self.account)
                return raw
            except BudgetLimit:
                raise
            except Exception as exc:
                status = exc.code if isinstance(exc, urllib.error.HTTPError) else None
                text = type(exc).__name__ + ': ' + str(exc)
                if isinstance(exc, urllib.error.HTTPError):
                    text += ' ' + exc.read().decode('utf-8', errors='replace')
                text = text.replace(self.key, '[REDACTED]')
                transient = status in (429, 500, 502, 503, 504, 520, 522, 529) or isinstance(exc, (TimeoutError, ConnectionError)) or (isinstance(exc, urllib.error.URLError) and status is None) or 'no model choice' in str(exc)
                atomic_json(target.with_suffix('.error.json'), {'error': text, 'http_status': status, 'transient': transient})
                with self.lock:
                    event['status'] = 'error_retained'
                    atomic_json(self.ledger, self.account)
                if not transient or retry == 2:
                    raise RuntimeError('Paid request engineering failure; archived, not a semantic failure') from None
                time.sleep(2 ** retry)
