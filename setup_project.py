#!/usr/bin/env python3
"""
ONE-FILE SETUP: run `python3 setup_project.py` to create the whole project
(Serverless API Gateway - Distributed Rate-Limiting & Throttling).
Then: cd serverless-api-gateway-ratelimiter && python3 -m unittest discover -v && python3 simulate.py
"""
import os

ROOT = "serverless-api-gateway-ratelimiter"

FILES = {}

FILES['README.md'] = r'''# Serverless API Gateway — Distributed Rate-Limiting & Throttling

Cloud Computing mini-project. A serverless (AWS Lambda-style) API gateway layer that enforces
**per-client rate limits** and a **global throttle** across many stateless function instances,
using a shared atomic store (Redis + Lua in production, an in-memory atomic store for the offline demo).

> Author: <your name> · Reg. No: <your register number> · Course: Cloud Computing

## Problem
Serverless functions scale out into many short-lived, stateless containers. A counter kept in one
container's memory cannot see traffic hitting the others, so **local limiting over-admits by ≈ the number
of instances**. Rate limiting therefore has to be *distributed*: state lives in a shared store and each
check must be an **atomic** read-modify-write.

## Architecture
```
Client ──► API Gateway ──► Lambda (gateway.handler) ──► Redis / ElastiCache (atomic Lua)
                                 │  200 + X-RateLimit-* headers
                                 └► 429 + Retry-After when limited ──► backend service
```
Two layers per request: (1) **global throttle** (protects the backend), (2) **per-client tier limit**
(isolates noisy neighbours). Tiers: free / pro / enterprise.

## Algorithms implemented (`gateway/store.py`)
| Algorithm | Memory | Accuracy | Notes |
|---|---|---|---|
| Fixed window | O(1) | Poor | 2x burst at window edge |
| Sliding window log | O(limit) | Exact | Best accuracy, most memory |
| Sliding window counter | O(1) | Good approx. | Weighted blend of 2 windows |
| **Token bucket** (default) | O(1) | Good | Controlled burst + steady refill |

## Project layout
```
gateway/store.py      algorithms, InMemoryStore (atomic), RedisStore (Lua scripts)
gateway/limiter.py    tiers + global throttle policy
gateway/handler.py    Lambda handler -> 200 / 401 / 429 with Retry-After
simulate.py           5 experiments (virtual clock + real threads)
tests/                11 unit tests
template.yaml         AWS SAM template (Lambda + HTTP API)
docs/output.txt       captured simulation output
```

## Run
```bash
python3 -m unittest discover -v     # 11 tests
python3 simulate.py                 # all experiments
```
No third-party packages needed for the demo. For production: `pip install redis`, set `REDIS_URL`,
then `sam build && sam deploy --guided` (the Redis backend was not run in the offline demo).

## Results (see `docs/output.txt`)
* **Exp 1** – fixed window admitted 20/20 across a window edge (limit 10); sliding window admitted exactly 10.
* **Exp 2** – 5 instances, limit 20 req/s: local limiting admitted **1095**, shared store admitted **219** (≈ configured).
* **Exp 3** – tiered clients: pro users get higher quota than free; global ceiling is never exceeded.
* **Exp 4** – 60 simultaneous clients vs 10 req/s: backoff + jitter serves all requests eventually.
* **Exp 5** – 200 real threads on a 50-token bucket: exactly 50 allowed → operation is atomic.

## Design trade-offs / future work
Redis adds ~1 ms latency per request (mitigate with local pre-filter + periodic sync); Redis is a single point of
failure (fail-open vs fail-closed policy); clock skew between nodes (use Redis `TIME`); DynamoDB atomic counters
as a serverless-native alternative; per-route limits and API-key storage in a real secrets store.
'''

FILES['requirements.txt'] = r'''# Optional - only needed for the production Redis backend
redis>=5.0
'''

FILES['.gitignore'] = r'''__pycache__/
*.pyc
.venv/
.aws-sam/
'''

FILES['template.yaml'] = r'''AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31
Description: Serverless API Gateway with distributed rate limiting (Lambda + ElastiCache Redis)

Parameters:
  RedisUrl: {Type: String, Description: "redis://<elasticache-endpoint>:6379"}

Globals:
  Function: {Runtime: python3.12, Timeout: 5, MemorySize: 256}

Resources:
  ApiFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: .
      Handler: gateway.handler.lambda_handler
      Environment: {Variables: {REDIS_URL: !Ref RedisUrl}}
      Events:
        Any:
          Type: HttpApi
          Properties: {Path: /{proxy+}, Method: ANY}

  # Coarse edge-level throttle (defence in depth) - fine-grained per-client logic is in Lambda+Redis
  # Configure HttpApi DefaultRouteSettings ThrottlingBurstLimit / ThrottlingRateLimit as needed.
'''

FILES['simulate.py'] = r'''"""Deterministic simulations (virtual clock) + one real multi-threaded atomicity test."""
import random, threading, time
from gateway import RateLimiter, Rule, InMemoryStore

class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t

def hr(title): print("\n" + "=" * 72 + f"\n{title}\n" + "=" * 72)

# ---------------------------------------------------------------- 1
def exp_algorithms():
    hr("EXPERIMENT 1: Algorithm comparison  (limit = 10 req / 1 s)")
    print("Traffic: 10 requests at t=0.9s and 10 more at t=1.1s (burst straddling a window edge)\n")
    print(f"{'algorithm':<18}{'allowed':>8}{'rejected':>10}   note")
    notes = {"fixed_window": "allows 2x limit across the boundary (flaw)",
             "sliding_window": "exact: never exceeds limit in any 1s span",
             "sliding_counter": "close approximation, O(1) memory",
             "token_bucket": "burst=10 then refills 10/s; smooth"}
    for algo in ["fixed_window", "sliding_window", "sliding_counter", "token_bucket"]:
        c = Clock(); s = InMemoryStore(); c.t = 1000.0
        a = 0
        for t in (1000.9, 1001.1):
            c.t = t
            for _ in range(10):
                a += s.check(algo, "k", 10, 1, c.t).allowed
        print(f"{algo:<18}{a:>8}{20 - a:>10}   {notes[algo]}")

# ---------------------------------------------------------------- 2
def exp_distributed():
    hr("EXPERIMENT 2: Local (per-instance) vs Distributed (shared store) limiting")
    LIMIT, NODES, DUR, OFFER = 20, 5, 10, 200   # 20 req/s allowed, 5 Lambda instances, 200 req/s offered
    rule = {"free": Rule("token_bucket", limit=LIMIT, window=1, burst=LIMIT)}
    print(f"Policy: {LIMIT} req/s per client | {NODES} gateway instances | offered load {OFFER} req/s for {DUR}s\n")
    results = {}
    for mode in ("local", "distributed"):
        c = Clock(); shared = InMemoryStore()
        nodes = [RateLimiter(shared if mode == "distributed" else InMemoryStore(), rule, clock=c) for _ in range(NODES)]
        allowed = 0; per_sec = []
        for sec in range(DUR):
            n = 0
            for i in range(OFFER):
                c.t = 1000 + sec + i / OFFER
                n += nodes[i % NODES].check("alice", "free").allowed
            per_sec.append(n); allowed += n
        results[mode] = (allowed, per_sec)
        print(f"{mode:<12} allowed={allowed:>5}  expected~{LIMIT*DUR + LIMIT}  per-second={per_sec}")
    print(f"\n=> Local limiting over-admits by ~{results['local'][0]/results['distributed'][0]:.1f}x (≈ number of instances).")
    print("=> Shared atomic store keeps the true global rate at the configured limit.")

# ---------------------------------------------------------------- 3
def exp_tiers_global():
    hr("EXPERIMENT 3: Tiered clients + global throttle (backend capacity = 100 req/s)")
    tiers = {"free": Rule(limit=5, window=1, burst=5), "pro": Rule(limit=50, window=1, burst=50)}
    c = Clock(); store = InMemoryStore()
    rl = RateLimiter(store, tiers, global_rule=Rule(limit=100, window=1, burst=100), clock=c)
    clients = [("free-user-%d" % i, "free") for i in range(10)] + [("pro-user-%d" % i, "pro") for i in range(3)]
    stats = {cid: [0, 0] for cid, _ in clients}
    random.seed(7)
    for step in range(5 * 400):                 # 5 seconds, 400 req/s aggregate
        c.t = 1000 + step / 400
        cid, tier = random.choice(clients)
        d = rl.check(cid, tier)
        stats[cid][0 if d.allowed else 1] += 1
    print(f"{'client':<14}{'tier':<6}{'allowed':>8}{'throttled(429)':>16}")
    for cid, tier in clients:
        print(f"{cid:<14}{tier:<6}{stats[cid][0]:>8}{stats[cid][1]:>16}")
    tot = sum(v[0] for v in stats.values())
    print(f"\nTotal admitted in 5s: {tot}  (<= global ceiling of 600; per-tier limits are the binding constraint here)")

# ---------------------------------------------------------------- 4
def exp_backoff():
    hr("EXPERIMENT 4: Client retry with exponential backoff + jitter after 429")
    c = Clock(); s = InMemoryStore()
    random.seed(1)
    pending = [(1000.0, 0, i) for i in range(60)]   # 60 clients all fire at t=1000
    done = 0; attempts = 0; first_try_ok = 0; last_t = 1000
    while pending:
        pending.sort()
        t, k, i = pending.pop(0); c.t = t; attempts += 1
        if s.check("token_bucket", "shared", 10, 1, t, burst=10).allowed:
            done += 1; last_t = t
            first_try_ok += (k == 0)
        else:
            delay = random.uniform(0, min(8, 0.25 * 2 ** k))
            pending.append((t + delay, k + 1, i))
    print(f"60 simultaneous requests vs 10 req/s shared limit")
    print(f"first-attempt success: {first_try_ok}/60 | total attempts: {attempts} | all served after {last_t-1000:.2f}s")
    print("=> Backoff+jitter spreads retries over time; every request is eventually served (at the cost of extra attempts).")

# ---------------------------------------------------------------- 5
def exp_atomic():
    hr("EXPERIMENT 5: Atomicity under real concurrency (200 threads, bucket of 50, no refill)")
    s = InMemoryStore(); allowed = []; lock = threading.Lock()
    def worker():
        d = s.check("token_bucket", "k", 50, 10**9, time.time(), burst=50)
        with lock: allowed.append(d.allowed)
    ts = [threading.Thread(target=worker) for _ in range(200)]
    [t.start() for t in ts]; [t.join() for t in ts]
    print(f"allowed={sum(allowed)} rejected={len(allowed)-sum(allowed)}  (expected exactly 50 / 150)")
    assert sum(allowed) == 50

if __name__ == "__main__":
    exp_algorithms(); exp_distributed(); exp_tiers_global(); exp_backoff(); exp_atomic()
'''

FILES['gateway/__init__.py'] = r'''from .limiter import RateLimiter, Rule
from .store import InMemoryStore, RedisStore, Decision

__all__ = ["RateLimiter", "Rule", "InMemoryStore", "RedisStore", "Decision"]
'''

FILES['gateway/store.py'] = r'''"""
Shared state stores for distributed rate limiting.

Every serverless function instance (Lambda container) is stateless and short-lived,
so counters must live in an external, shared, ATOMIC store (Redis / DynamoDB).

* InMemoryStore - thread-safe stand-in for the shared store (used for tests/simulation).
                  Each check() runs under a lock, mimicking a Redis Lua script (atomic).
* RedisStore    - production backend. Every algorithm is one Lua script, so the
                  read-modify-write cycle is atomic on the Redis server.
"""
from __future__ import annotations
import threading
from dataclasses import dataclass


@dataclass
class Decision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: float  # seconds until a retry may succeed (0 if allowed)


class InMemoryStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._d: dict = {}

    def check(self, algo, key, limit, window, now, cost=1, burst=None) -> Decision:
        with self._lock:  # atomic section == Redis Lua script
            fn = getattr(self, f"_{algo}")
            return fn(key, limit, window, now, cost, burst)

    # --- Token bucket: allows bursts up to `capacity`, refills at limit/window per sec
    def _token_bucket(self, key, limit, window, now, cost, burst):
        capacity = burst or limit
        rate = limit / window
        tokens, last = self._d.get(key, (float(capacity), now))
        tokens = min(capacity, tokens + (now - last) * rate)
        if tokens >= cost:
            tokens -= cost
            self._d[key] = (tokens, now)
            return Decision(True, limit, int(tokens), 0.0)
        self._d[key] = (tokens, now)
        return Decision(False, limit, int(tokens), (cost - tokens) / rate)

    # --- Fixed window: cheap, but allows 2x burst at window boundary
    def _fixed_window(self, key, limit, window, now, cost, burst):
        wid = int(now // window)
        k = (key, wid)
        count = self._d.get(k, 0)
        if count + cost <= limit:
            self._d[k] = count + cost
            return Decision(True, limit, limit - count - cost, 0.0)
        return Decision(False, limit, 0, (wid + 1) * window - now)

    # --- Sliding window log: exact, memory O(limit) per key
    def _sliding_window(self, key, limit, window, now, cost, burst):
        log = [t for t in self._d.get(key, []) if t > now - window]
        if len(log) + cost <= limit:
            log.extend([now] * cost)
            self._d[key] = log
            return Decision(True, limit, limit - len(log), 0.0)
        self._d[key] = log
        return Decision(False, limit, 0, max(0.0, log[0] + window - now))

    # --- Sliding window counter: weighted blend of 2 fixed windows, O(1) memory
    def _sliding_counter(self, key, limit, window, now, cost, burst):
        wid = int(now // window)
        cur = self._d.get((key, wid), 0)
        prev = self._d.get((key, wid - 1), 0)
        weight = 1 - (now % window) / window
        est = prev * weight + cur
        if est + cost <= limit:
            self._d[(key, wid)] = cur + cost
            return Decision(True, limit, int(limit - est - cost), 0.0)
        return Decision(False, limit, 0, window - (now % window))


# ---------------------------------------------------------------- Redis (production)
TOKEN_BUCKET_LUA = """
local key, cap, rate, now, cost = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3]), tonumber(ARGV[4])
local s = redis.call('HMGET', key, 't', 'ts')
local tokens, ts = tonumber(s[1]), tonumber(s[2])
if tokens == nil then tokens = cap; ts = now end
tokens = math.min(cap, tokens + (now - ts) * rate)
local allowed, retry = 0, 0
if tokens >= cost then tokens = tokens - cost; allowed = 1 else retry = (cost - tokens) / rate end
redis.call('HSET', key, 't', tokens, 'ts', now)
redis.call('EXPIRE', key, math.ceil(cap / rate) * 2)
return {allowed, math.floor(tokens), tostring(retry)}
"""

SLIDING_WINDOW_LUA = """
local key, limit, window, now, cost = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3]), tonumber(ARGV[4])
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local n = redis.call('ZCARD', key)
if n + cost <= limit then
  for i = 1, cost do redis.call('ZADD', key, now, now .. '-' .. i .. '-' .. math.random(1e9)) end
  redis.call('EXPIRE', key, math.ceil(window))
  return {1, limit - n - cost, '0'}
end
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')[2]
return {0, 0, tostring(oldest + window - now)}
"""


class RedisStore:
    """Requires `pip install redis` and a reachable Redis (e.g. ElastiCache). Not exercised in the offline demo."""

    def __init__(self, url: str):
        import redis  # lazy import
        self.r = redis.Redis.from_url(url)
        self._tb = self.r.register_script(TOKEN_BUCKET_LUA)
        self._sw = self.r.register_script(SLIDING_WINDOW_LUA)

    def check(self, algo, key, limit, window, now, cost=1, burst=None) -> Decision:
        if algo == "token_bucket":
            a, rem, retry = self._tb(keys=[key], args=[burst or limit, limit / window, now, cost])
        elif algo == "sliding_window":
            a, rem, retry = self._sw(keys=[key], args=[limit, window, now, cost])
        else:
            raise ValueError(f"{algo} not implemented for Redis in this demo")
        return Decision(bool(a), limit, int(rem), float(retry))
'''

FILES['gateway/limiter.py'] = r'''"""Policy layer: per-client tier limits + global throttle (like API Gateway usage plans + stage throttling)."""
from __future__ import annotations
import time
from dataclasses import dataclass
from .store import Decision


@dataclass(frozen=True)
class Rule:
    algorithm: str = "token_bucket"
    limit: int = 10          # requests per `window`
    window: float = 1.0      # seconds
    burst: int | None = None # token-bucket burst capacity


DEFAULT_TIERS = {
    "free":       Rule("token_bucket", limit=5,   window=1, burst=10),
    "pro":        Rule("token_bucket", limit=50,  window=1, burst=100),
    "enterprise": Rule("token_bucket", limit=500, window=1, burst=1000),
}


class RateLimiter:
    def __init__(self, store, tiers=None, global_rule: Rule | None = None, clock=time.time):
        self.store = store
        self.tiers = tiers or DEFAULT_TIERS
        self.global_rule = global_rule
        self.clock = clock

    def check(self, client_id: str, tier: str = "free", route: str = "*") -> Decision:
        now = self.clock()
        # 1) global throttle protects the backend regardless of who calls
        if self.global_rule:
            g = self.global_rule
            gd = self.store.check(g.algorithm, "global", g.limit, g.window, now, burst=g.burst)
            if not gd.allowed:
                return gd
        # 2) per-client limit (isolates noisy neighbours)
        r = self.tiers[tier]
        return self.store.check(r.algorithm, f"rl:{client_id}:{route}", r.limit, r.window, now, burst=r.burst)
'''

FILES['gateway/handler.py'] = r'''"""
AWS Lambda-style API Gateway proxy handler.
Deployed behind API Gateway (REST/HTTP API); returns HTTP 429 + Retry-After when throttled.
Set REDIS_URL to use a shared Redis; otherwise falls back to the in-memory store (single instance only!).
"""
import json, os, math
from .limiter import RateLimiter, Rule
from .store import InMemoryStore, RedisStore

_store = RedisStore(os.environ["REDIS_URL"]) if os.environ.get("REDIS_URL") else InMemoryStore()
# module-level => reused across warm invocations of the same container
_limiter = RateLimiter(_store, global_rule=Rule("token_bucket", limit=1000, window=1, burst=2000))

API_KEYS = {"key-free-1": ("alice", "free"), "key-pro-1": ("bob", "pro")}  # demo only; use a real key store


def lambda_handler(event, context=None):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    client = API_KEYS.get(headers.get("x-api-key", ""))
    if not client:
        return _resp(401, {"message": "invalid api key"})
    cid, tier = client
    d = _limiter.check(cid, tier, route=event.get("path", "*"))
    rl = {"X-RateLimit-Limit": str(d.limit), "X-RateLimit-Remaining": str(d.remaining)}
    if not d.allowed:
        rl["Retry-After"] = str(max(1, math.ceil(d.retry_after)))
        return _resp(429, {"message": "Too Many Requests", "retry_after_s": round(d.retry_after, 3)}, rl)
    return _resp(200, {"message": "ok", "client": cid, "tier": tier}, rl)


def _resp(code, body, extra=None):
    return {"statusCode": code, "headers": {"Content-Type": "application/json", **(extra or {})}, "body": json.dumps(body)}
'''

FILES['tests/__init__.py'] = r''''''

FILES['tests/test_limiter.py'] = r'''import json, threading, unittest
from gateway import InMemoryStore, RateLimiter, Rule
from gateway import handler


class Clk:
    t = 0.0
    def __call__(self): return self.t


class TestAlgorithms(unittest.TestCase):
    def test_token_bucket_burst_then_reject(self):
        s = InMemoryStore()
        r = [s.check("token_bucket", "k", 5, 1, 0.0, burst=5).allowed for _ in range(7)]
        self.assertEqual(r, [True] * 5 + [False] * 2)

    def test_token_bucket_refill(self):
        s = InMemoryStore()
        for _ in range(5): s.check("token_bucket", "k", 5, 1, 0.0)
        self.assertFalse(s.check("token_bucket", "k", 5, 1, 0.0).allowed)
        self.assertTrue(s.check("token_bucket", "k", 5, 1, 0.3).allowed)  # 0.3s * 5/s = 1.5 tokens

    def test_fixed_window_boundary_flaw(self):
        s = InMemoryStore()
        a = sum(s.check("fixed_window", "k", 10, 1, 0.9).allowed for _ in range(10))
        b = sum(s.check("fixed_window", "k", 10, 1, 1.1).allowed for _ in range(10))
        self.assertEqual(a + b, 20)

    def test_sliding_window_exact(self):
        s = InMemoryStore()
        a = sum(s.check("sliding_window", "k", 10, 1, 0.9).allowed for _ in range(10))
        b = sum(s.check("sliding_window", "k", 10, 1, 1.1).allowed for _ in range(10))
        self.assertEqual(a + b, 10)

    def test_retry_after_positive_when_rejected(self):
        s = InMemoryStore()
        s.check("token_bucket", "k", 1, 1, 0.0)
        d = s.check("token_bucket", "k", 1, 1, 0.0)
        self.assertFalse(d.allowed); self.assertAlmostEqual(d.retry_after, 1.0)


class TestDistributed(unittest.TestCase):
    def test_shared_store_across_nodes(self):
        c, shared = Clk(), InMemoryStore()
        tiers = {"free": Rule(limit=10, window=1, burst=10)}
        nodes = [RateLimiter(shared, tiers, clock=c) for _ in range(5)]
        ok = sum(nodes[i % 5].check("u").allowed for i in range(100))
        self.assertEqual(ok, 10)

    def test_local_stores_over_admit(self):
        c = Clk(); tiers = {"free": Rule(limit=10, window=1, burst=10)}
        nodes = [RateLimiter(InMemoryStore(), tiers, clock=c) for _ in range(5)]
        ok = sum(nodes[i % 5].check("u").allowed for i in range(100))
        self.assertEqual(ok, 50)

    def test_thread_safety(self):
        s, res, lk = InMemoryStore(), [], threading.Lock()
        def w():
            d = s.check("token_bucket", "k", 20, 10**9, 0.0, burst=20)
            with lk: res.append(d.allowed)
        ts = [threading.Thread(target=w) for _ in range(100)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(sum(res), 20)

    def test_global_throttle(self):
        c = Clk()
        rl = RateLimiter(InMemoryStore(), {"pro": Rule(limit=100, window=1, burst=100)},
                         global_rule=Rule(limit=10, window=1, burst=10), clock=c)
        ok = sum(rl.check(f"u{i}", "pro").allowed for i in range(50))
        self.assertEqual(ok, 10)


class TestHandler(unittest.TestCase):
    def setUp(self):
        handler._limiter.clock = Clk()
        handler._store._d.clear()

    def ev(self, key="key-free-1"):
        return {"headers": {"X-API-Key": key}, "path": "/items"}

    def test_401(self):
        self.assertEqual(handler.lambda_handler(self.ev("bad"))["statusCode"], 401)

    def test_200_then_429_with_headers(self):
        codes = [handler.lambda_handler(self.ev()) for _ in range(12)]
        self.assertEqual([r["statusCode"] for r in codes], [200] * 10 + [429] * 2)
        self.assertIn("Retry-After", codes[-1]["headers"])
        self.assertEqual(json.loads(codes[-1]["body"])["message"], "Too Many Requests")


if __name__ == "__main__":
    unittest.main()
'''

FILES['docs/output.txt'] = r'''
========================================================================
EXPERIMENT 1: Algorithm comparison  (limit = 10 req / 1 s)
========================================================================
Traffic: 10 requests at t=0.9s and 10 more at t=1.1s (burst straddling a window edge)

algorithm          allowed  rejected   note
fixed_window            20         0   allows 2x limit across the boundary (flaw)
sliding_window          10        10   exact: never exceeds limit in any 1s span
sliding_counter         11         9   close approximation, O(1) memory
token_bucket            12         8   burst=10 then refills 10/s; smooth

========================================================================
EXPERIMENT 2: Local (per-instance) vs Distributed (shared store) limiting
========================================================================
Policy: 20 req/s per client | 5 gateway instances | offered load 200 req/s for 10s

local        allowed= 1095  expected~220  per-second=[195, 100, 100, 100, 100, 100, 100, 100, 100, 100]
distributed  allowed=  219  expected~220  per-second=[39, 20, 20, 20, 20, 20, 20, 20, 20, 20]

=> Local limiting over-admits by ~5.0x (≈ number of instances).
=> Shared atomic store keeps the true global rate at the configured limit.

========================================================================
EXPERIMENT 3: Tiered clients + global throttle (backend capacity = 100 req/s)
========================================================================
client        tier   allowed  throttled(429)
free-user-0   free        29             143
free-user-1   free        28             141
free-user-2   free        26             129
free-user-3   free        29             141
free-user-4   free        28             135
free-user-5   free        29             114
free-user-6   free        28             125
free-user-7   free        27             126
free-user-8   free        28             126
free-user-9   free        28             100
pro-user-0    pro         44             109
pro-user-1    pro         36             104
pro-user-2    pro         40             107

Total admitted in 5s: 400  (<= global ceiling of 600; per-tier limits are the binding constraint here)

========================================================================
EXPERIMENT 4: Client retry with exponential backoff + jitter after 429
========================================================================
60 simultaneous requests vs 10 req/s shared limit
first-attempt success: 10/60 | total attempts: 271 | all served after 10.67s
=> Backoff+jitter spreads retries over time; every request is eventually served (at the cost of extra attempts).

========================================================================
EXPERIMENT 5: Atomicity under real concurrency (200 threads, bucket of 50, no refill)
========================================================================
allowed=50 rejected=150  (expected exactly 50 / 150)
'''

for path, content in FILES.items():
    full = os.path.join(ROOT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(content)
    print("created", full)
print("\nDone. Next: cd", ROOT, "&& python3 -m unittest discover -v && python3 simulate.py")
