"""Opt-in paid client. No credentials or network calls at import time."""
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

PRICE_IN = 0.10 / 1_000_000
PRICE_OUT = 0.40 / 1_000_000
FINAL_RESERVE = (160_000 + 4096) * PRICE_IN + 2048 * PRICE_OUT
MODEL = "google/gemma-4-26b-a4b-it"
PROVIDER = "primeintellect/bf16"

def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

class Client:
    def __init__(self, out, cap_usd):
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.ledger = self.out / "cost.json"
        if self.ledger.exists():
            raise ValueError("Use a fresh output directory; paid runs never silently reset a ledger")
        self.key = os.environ.get("OPENROUTER_API_KEY")
        if not self.key:
            raise ValueError("Set OPENROUTER_API_KEY in your process environment; never commit it")
        self.cap_usd = float(cap_usd)
        if not 0 < self.cap_usd <= 100:
            raise ValueError("An explicit positive budget <=100 USD is required")
        self.account = {"cap_usd": self.cap_usd, "reported_usd": 0.0,
                        "accounted_usd": 0.0, "requests_v2": 0}
        self.calls = 0

    def request(self, payload, destination):
        destination = Path(destination)
        encoded = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        if len(encoded) > 160000:
            raise RuntimeError('Context validity gate: payload exceeds development limit')
        reserve = (len(encoded) + 4096) * PRICE_IN + payload['max_tokens'] * PRICE_OUT
        protected_final = 0 if payload.get('tool_choice') == 'none' else FINAL_RESERVE
        if self.account['accounted_usd'] + reserve + protected_final > self.cap_usd:
            raise RuntimeError('Project API cap would be exceeded before next request')
        self.account['accounted_usd'] += reserve
        self.account['requests_v2'] += 1
        write_json(self.ledger, self.account)
        write_json(destination.with_suffix('.request.json'), payload)
        started = time.perf_counter()
        req = urllib.request.Request('https://openrouter.ai/api/v1/chat/completions', data=encoded, headers={'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                raw = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            text = exc.read().decode('utf-8', errors='replace').replace(self.key, '[REDACTED]')
            write_json(destination.with_suffix('.error.json'), {'status': exc.code, 'body': text})
            raise RuntimeError(f'API engineering failure HTTP {exc.code}; preserved locally') from None
        write_json(destination.with_suffix('.response.json'), raw)
        write_json(destination.with_suffix('.timing.json'), {'wall_seconds': time.perf_counter() - started, 'recorded_utc': datetime.now(timezone.utc).isoformat()})
        cost = raw.get('usage', {}).get('cost')
        if cost is not None:
            cost = float(cost)
            self.account['accounted_usd'] += cost - reserve
            self.account['reported_usd'] += cost
        write_json(self.ledger, self.account)
        write_json(self.out / 'cost.json', self.account)
        self.calls += 1
        if raw.get('error') or not raw.get('choices'):
            raise RuntimeError('API returned no model choice; not a research outcome')
        return raw

class RetryClient(Client):
    def request(self,payload,destination):
        destination=Path(destination);failures=[]
        for attempt in range(3):
            target=destination if attempt==0 else destination.with_name(destination.name+f"_transport_retry{attempt}")
            try:return super().request(payload,target)
            except (RuntimeError,TimeoutError,urllib.error.URLError,ConnectionError) as exc:
                message=str(exc)
                transient=isinstance(exc,(TimeoutError,urllib.error.URLError,ConnectionError)) or any(s in message for s in
                    ("HTTP 429","HTTP 500","HTTP 502","HTTP 503","HTTP 504","HTTP 520","HTTP 522","HTTP 529","returned no model choice"))
                failures.append({"attempt":attempt,"error":type(exc).__name__+": "+message,"transient":transient})
                write_json(destination.with_suffix(".transport_journal.json"),failures)
                if not transient or attempt==2:raise
                time.sleep(2**attempt)
