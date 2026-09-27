"""TypeSafe System One client with an on-disk cache and usage accounting.

Every request and answer is stored in results/cache/jev.sqlite keyed by a hash of
the request body, so re-running an analysis never re-bills a request.
"""

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

import chess
import httpx

from . import formats

API = "https://api.typesafe.ai/v1/systemone"
MODEL = os.environ.get("JEV_MODEL", "jev-latest")
PRICE_PER_MTOK = 0.042  # USD per million input tokens, docs.typesafe.ai/models (Jev 1.13); output tokens are free
ROOT = Path(__file__).resolve().parents[2]


def _load_key() -> str:
    key = os.environ.get("TYPESAFE_API_KEY")
    if key:
        return key
    env = Path.home() / ".config/typesafe/env"
    for line in env.read_text().splitlines():
        line = line.strip().removeprefix("export ")
        if line.startswith("TYPESAFE_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("TYPESAFE_API_KEY not found")


class Cache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("pragma journal_mode=wal")
        self.db.execute(
            "create table if not exists jev (key text primary key, request text, response text, latency_ms real, created real)"
        )
        self.lock = threading.Lock()

    def get(self, key: str):
        with self.lock:
            row = self.db.execute("select response, latency_ms from jev where key=?", (key,)).fetchone()
        return (json.loads(row[0]), row[1]) if row else None

    def put(self, key: str, request: dict, response: dict, latency_ms: float):
        with self.lock:
            self.db.execute(
                "insert or replace into jev values (?,?,?,?,?)",
                (key, json.dumps(request), json.dumps(response), latency_ms, time.time()),
            )
            self.db.commit()


class Usage:
    def __init__(self):
        self.lock = threading.Lock()
        self.requests = 0
        self.cached = 0
        self.input_tokens = 0
        self.billed_input_tokens = 0

    def add(self, tokens: int, cached: bool):
        with self.lock:
            self.requests += 1
            self.input_tokens += tokens
            if cached:
                self.cached += 1
            else:
                self.billed_input_tokens += tokens

    def usd(self, billed_only: bool = True) -> float:
        tokens = self.billed_input_tokens if billed_only else self.input_tokens
        return tokens / 1e6 * PRICE_PER_MTOK

    def summary(self) -> dict:
        return {
            "requests": self.requests,
            "cached": self.cached,
            "input_tokens": self.input_tokens,
            "billed_input_tokens": self.billed_input_tokens,
            "usd_billed_this_run": round(self.usd(True), 4),
            "usd_equivalent_all": round(self.usd(False), 4),
        }


class Jev:
    def __init__(self, model: str = MODEL, cache_path: Path | None = None):
        self.model = model
        self.client = httpx.Client(timeout=120, headers={"Authorization": f"Bearer {_load_key()}"})
        self.cache = Cache(cache_path or ROOT / "results/cache/jev.sqlite")
        self.usage = Usage()

    def ask(self, state, questions: dict, tag: str = "") -> tuple[dict, float, bool]:
        body = {"state": state, "model": self.model, "questions": questions}
        key = hashlib.sha256((tag + json.dumps(body, sort_keys=True)).encode()).hexdigest()
        hit = self.cache.get(key)
        if hit:
            response, latency = hit
            self.usage.add(response.get("usage", {}).get("input_tokens", 0), cached=True)
            return response, latency, True
        delay = 1.0
        for attempt in range(8):
            t0 = time.perf_counter()
            r = self.client.post(API, json=body)
            latency = (time.perf_counter() - t0) * 1000
            if r.status_code in (429, 529, 500, 502, 503, 504):
                wait = float(r.headers.get("retry-after", delay))
                time.sleep(wait)
                delay = min(delay * 2, 30)
                continue
            if r.status_code != 200:
                raise RuntimeError(f"TypeSafe {r.status_code}: {r.text[:500]}")
            response = r.json()
            self.cache.put(key, body, response, latency)
            self.usage.add(response.get("usage", {}).get("input_tokens", 0), cached=False)
            return response, latency, False
        raise RuntimeError("TypeSafe: too many retries")

    def choose_move(self, board: chess.Board, board_format: str = "grid", option_style: str = "san", tag: str = "") -> dict:
        """Ask Jev for the best move among all legal moves. Returns the move and the raw answer."""
        options = formats.move_options(board, option_style)
        question = {
            "type": "choice",
            "instructions": formats.instructions(board),
            "criteria": options,
        }
        response, latency, cached = self.ask(formats.board_state(board, board_format), {"move": question}, tag=tag)
        answer = response["answers"]["move"]
        san = answer["choice"]
        return {
            "move": board.parse_san(san),
            "san": san,
            "probabilities": answer["probabilities"],
            "confidence": answer.get("confidence"),
            "input_tokens": response.get("usage", {}).get("input_tokens", 0),
            "model": response.get("model"),
            "latency_ms": latency,
            "cached": cached,
        }
