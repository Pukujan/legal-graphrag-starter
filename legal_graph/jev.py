"""Minimal client for TypeSafe Jev typed decisions via the OpenRouter Decisions API.

``POST https://openrouter.ai/api/alpha/decisions`` with ``model``, ``state`` and typed
``questions`` (``noul`` = P(yes); ``choice`` = distribution over named options). Note:
``typesafe/jev-router`` is a chat-completions router and is rejected by the Decisions API
("Model typesafe/jev-router does not exist"), so the decision model ``typesafe/jev-1.14``
(the model behind the router) is the default.

Position bias: :func:`choice_questions` emits the same Choice several times with the option
order permuted; :func:`average_choice` averages the per-option probabilities.

Offline tests: pass ``transport=FixtureTransport(path)`` to replay recorded responses keyed by
the canonical request hash; CI never calls the network. The API key is read from the
environment and never logged.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL = "typesafe/jev-1.14"


def request_key(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


class JevError(RuntimeError):
    pass


class HttpTransport:
    def __init__(self, url: str = DECISIONS_URL, api_key: str | None = None, timeout: float = 120.0):
        self.url, self.timeout = url, timeout
        self._key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not self._key:
            raise JevError("OPENROUTER_API_KEY not set")

    def __call__(self, body: dict) -> tuple[int, dict]:
        req = urllib.request.Request(self.url, data=json.dumps(body, ensure_ascii=True).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {self._key}",
                                              "Content-Type": "application/json",
                                              "X-Title": "legal-graphrag-starter"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read())
            except Exception:  # noqa: BLE001
                return e.code, {"error": {"message": str(e)}}
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            return 0, {"error": {"message": f"transport: {type(e).__name__}"}}


class FixtureTransport:
    """Replay recorded {request_key: response} JSONL; raises on a miss (no network)."""

    def __init__(self, path: Path | str):
        self.data = {}
        for line in open(path, encoding="utf-8"):
            x = json.loads(line)
            self.data[x["key"]] = x["response"]

    def __call__(self, body: dict) -> tuple[int, dict]:
        k = request_key(body)
        if k not in self.data:
            raise JevError(f"no recorded fixture for request {k[:12]}")
        return 200, self.data[k]


class JevClient:
    def __init__(self, model: str = DEFAULT_MODEL, transport: Callable[[dict], tuple[int, dict]] | None = None,
                 record: Path | str | None = None, retries: int = 6):
        self.model, self.retries = model, retries
        self.transport = transport or HttpTransport()
        self.record = Path(record) if record else None
        self.calls, self.cost, self.in_tok, self.out_tok = 0, 0.0, 0, 0
        self._lock = threading.Lock()

    def decide(self, state, questions: dict) -> dict:
        body = {"model": self.model, "state": state, "questions": questions}
        delay = 2.0
        for attempt in range(self.retries + 1):
            t0 = time.perf_counter()
            status, resp = self.transport(body)
            ms = (time.perf_counter() - t0) * 1000
            if status == 200 and "answers" in resp:
                break
            if (status in (0, 408, 409, 429, 499) or status >= 500) and attempt < self.retries:
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            raise JevError(f"status {status}: {str(resp.get('error', resp))[:300]}")
        u = resp.get("usage", {})
        with self._lock:
            self.calls += 1
            self.cost += float(u.get("cost") or 0)
            self.in_tok += int(u.get("input_tokens") or 0)
            self.out_tok += int(u.get("output_tokens") or 0)
            if self.record:
                with open(self.record, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"key": request_key(body), "response": resp}, ensure_ascii=True) + "\n")
        resp["_latency_ms"] = ms
        return resp

    def usage(self) -> dict:
        return {"calls": self.calls, "cost_usd": round(self.cost, 6), "input_tokens": self.in_tok,
                "output_tokens": self.out_tok,
                "cost_per_1k_calls_usd": round(1000 * self.cost / self.calls, 4) if self.calls else None}


def choice_questions(prefix: str, instructions, options: dict, n_perm: int = 2, seed: int = 0) -> dict:
    """The same Choice ``n_perm`` times, option order permuted (perm 0 = given order, 1 = reversed)."""
    names = list(options)
    rng = random.Random(seed)
    out = {}
    for p in range(n_perm):
        order = names if p == 0 else names[::-1] if p == 1 else rng.sample(names, len(names))
        out[f"{prefix}_p{p}"] = {"type": "choice", "instructions": instructions,
                                 "criteria": {k: options[k] for k in order}}
    return out


def average_choice(answers: dict, prefix: str, options: Sequence[str]) -> dict[str, float]:
    ps = [a.get("probabilities", {}) for k, a in answers.items() if k.startswith(prefix + "_p")]
    if not ps:
        raise JevError(f"no choice answers for {prefix}")
    return {o: sum(float(p.get(o, 0.0)) for p in ps) / len(ps) for o in options}


def noul(answers: dict, key: str) -> float:
    a = answers.get(key)
    if not a or a.get("type") != "noul":
        raise JevError(f"missing noul answer {key}")
    return float(a["noul"])


class JevScorer:
    """Answer-check SupportScorer: P(supported) = mean(Noul, order-averaged Choice)."""

    INSTR = ("Does the passage support the claim? The claim is supported only if the passage states it; "
             "a negated or reversed holding, or a holding about a different matter, is not supported.")
    OPTIONS = {"supported": "The passage states or directly entails the claim.",
               "not_supported": "The passage contradicts the claim or does not state it."}

    def __init__(self, client: JevClient | None = None):
        self.client = client or JevClient()
        self.model, self.model_version = self.client.model, self.client.model
        self.last: list[dict] = []

    def score_one(self, doc: str, claim: str) -> dict:
        qs = {"support": {"type": "noul", "instructions": self.INSTR,
                          "criteria": {"true": self.OPTIONS["supported"], "false": self.OPTIONS["not_supported"]}},
              **choice_questions("verdict", self.INSTR, self.OPTIONS)}
        r = self.client.decide({"passage": doc, "claim": claim}, qs)
        n = noul(r["answers"], "support")
        c = average_choice(r["answers"], "verdict", list(self.OPTIONS))["supported"]
        self.model_version = r.get("model", self.model_version)
        return {"prob": (n + c) / 2, "noul": n, "choice": c, "latency_ms": r["_latency_ms"],
                "model_version": self.model_version, "cost": r.get("usage", {}).get("cost")}

    def score(self, pairs):
        self.last = [self.score_one(d, c) for d, c in pairs]
        return [x["prob"] for x in self.last]
