"""An LLM player that gets the same information Jev gets, through the Claude Code CLI.

The prompt is the Jev state (as JSON), the same instruction, and the same list of
legal moves. Cost is the CLI's `total_cost_usd`, which is computed at API list
price. Replies are cached per prompt in results/cache/llm.sqlite.
"""

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import chess

from . import formats

ROOT = Path(__file__).resolve().parents[2]
CLAUDE = str(Path.home() / ".local/bin/claude")
SYSTEM = "Answer with exactly one option from the list you are given."


def prompt_for(board: chess.Board, board_format: str, option_style: str) -> str:
    state = formats.board_state(board, board_format)
    options = formats.move_options(board, option_style)
    if option_style == "described":
        listing = "\n".join(f"- {san}: {desc}" for san, desc in options.items())
    else:
        listing = ", ".join(options)
    return (
        f"State:\n{json.dumps(state, indent=1)}\n\n"
        f"{formats.instructions(board)}\n\n"
        f"Legal moves:\n{listing}\n\n"
        "Reply with exactly one move from the list, written as it appears there, and nothing else."
    )


class LLM:
    def __init__(self, model: str = "claude-sonnet-5", effort: str | None = None):
        self.model = model
        self.effort = effort
        path = ROOT / "results/cache/llm.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=60)
        self.db.execute("pragma journal_mode=wal")
        self.db.execute("create table if not exists llm (key text primary key, prompt text, response text, created real)")
        self.lock = threading.Lock()
        self.cost = 0.0
        self.billed = 0.0

    def _call(self, prompt: str) -> dict:
        key = hashlib.sha256(json.dumps([self.model, self.effort, SYSTEM, prompt]).encode()).hexdigest()
        with self.lock:
            row = self.db.execute("select response from llm where key=?", (key,)).fetchone()
        if row:
            out = json.loads(row[0])
            with self.lock:
                self.cost += out.get("total_cost_usd", 0)
            return out
        args = [CLAUDE, "-p", prompt, "--model", self.model, "--system-prompt", SYSTEM, "--tools", "",
                "--strict-mcp-config", "--no-session-persistence", "--output-format", "json"]
        if self.effort:
            args += ["--effort", self.effort]
        for attempt in range(4):
            t0 = time.perf_counter()
            proc = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
            wall = (time.perf_counter() - t0) * 1000
            if proc.returncode == 0:
                out = json.loads(proc.stdout)
                out["wall_ms"] = wall
                with self.lock:
                    self.db.execute("insert or replace into llm values (?,?,?,?)", (key, prompt, json.dumps(out), time.time()))
                    self.db.commit()
                    self.cost += out.get("total_cost_usd", 0)
                    self.billed += out.get("total_cost_usd", 0)
                return out
            time.sleep(5 * (attempt + 1))
        raise RuntimeError(f"claude CLI failed: {proc.stderr[:300]}")

    def choose_move(self, board: chess.Board, board_format: str = "grid", option_style: str = "san") -> dict:
        out = self._call(prompt_for(board, board_format, option_style))
        text = (out.get("result") or "").strip()
        legal = {board.san(m): m for m in board.legal_moves}
        move = parse_reply(text, legal)
        usage = out.get("usage", {})
        return {
            "move": move,
            "san": board.san(move) if move else None,
            "reply": text[:200],
            "legal_reply": move is not None,
            "usd": out.get("total_cost_usd", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "input_tokens": usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0) + usage.get("cache_read_input_tokens", 0),
            "latency_ms": out.get("duration_api_ms") or out.get("wall_ms"),
        }


def parse_reply(text: str, legal: dict) -> chess.Move | None:
    """Accept the reply only if it names exactly one legal move (checks and trailing punctuation tolerated)."""
    token = text.strip().strip("`*. ").split()[0] if text.strip() else ""
    if token in legal:
        return legal[token]
    bare = {re.sub(r"[+#]", "", s): m for s, m in legal.items()}
    token = re.sub(r"[+#!?]", "", token)
    return bare.get(token)
