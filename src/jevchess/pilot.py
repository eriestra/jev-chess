"""Pilot: which board format and option style give Jev its best moves?

For each test position, Jev picks a move under every (board format, option style)
variant. Full-strength Stockfish scores all legal moves once per position; each
pick is graded against that analysis. The human move from the source game and a
uniformly random move are graded the same way.
"""

import json
import os
import statistics as st
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import chess

from .grade import Grader, random_expectation, summarize_choice
from .jev import Jev

ROOT = Path(__file__).resolve().parents[2]
VARIANTS = [(f, s) for f in ("fen", "grid", "pieces") for s in ("san", "described")]
_local = threading.local()
_graders: list[Grader] = []


def grader() -> Grader:
    if not hasattr(_local, "g"):
        _local.g = Grader()
        _graders.append(_local.g)
    return _local.g


def grade_all(positions: list[dict], workers: int) -> dict[str, dict]:
    def one(p):
        return p["id"], grader().scores(chess.Board(p["fen"]))

    with ThreadPoolExecutor(workers) as ex:
        out = dict(ex.map(one, positions))
    for g in _graders:
        g.close()  # open engines keep the interpreter alive
    return out


def run(positions_file: str, workers: int = 8, repeat: int = 20):
    positions = json.loads((ROOT / positions_file).read_text())
    out_dir = ROOT / "results/pilot"
    out_dir.mkdir(parents=True, exist_ok=True)
    jev = Jev()

    print(f"grading {len(positions)} positions with {workers} Stockfish workers", flush=True)
    scores = grade_all(positions, workers)

    jobs = [(p, f, s) for p in positions for (f, s) in VARIANTS]

    def ask(job):
        p, f, s = job
        board = chess.Board(p["fen"])
        r = jev.choose_move(board, f, s)
        sc = scores[p["id"]]
        best_uci = max(sc, key=sc.get)
        best_san = board.san(chess.Move.from_uci(best_uci))
        return {
            "id": p["id"],
            "variant": f"{f}/{s}",
            "san": r["san"],
            "uci": r["move"].uci(),
            "confidence": r["confidence"],
            "p_best": r["probabilities"].get(best_san, 0.0),
            "input_tokens": r["input_tokens"],
            "latency_ms": r["latency_ms"],
            "model": r["model"],
            **summarize_choice(sc, r["move"].uci()),
        }

    print(f"asking Jev {len(jobs)} times", flush=True)
    with ThreadPoolExecutor(16) as ex:
        picks = list(ex.map(ask, jobs))

    # Determinism: re-ask a subset outside the cache and compare picks and probabilities.
    rep = []
    for p in positions[:repeat]:
        board = chess.Board(p["fen"])
        a = jev.choose_move(board, "grid", "san")
        b = jev.choose_move(board, "grid", "san", tag="repeat-1")
        diff = max(abs(a["probabilities"][k] - b["probabilities"].get(k, 0)) for k in a["probabilities"])
        rep.append({"id": p["id"], "same_pick": a["san"] == b["san"], "max_prob_diff": diff})

    rows = {}
    for f, s in VARIANTS:
        v = [x for x in picks if x["variant"] == f"{f}/{s}"]
        rows[f"{f}/{s}"] = summarize(v)
    human = [summarize_choice(scores[p["id"]], p["human_move"]) for p in positions]
    rows["human (source game)"] = summarize(human)
    rnd = [random_expectation(scores[p["id"]]) for p in positions]
    rows["random legal move"] = {
        "n": len(rnd),
        "acpl": round(st.mean(r["cp_loss"] for r in rnd), 1),
        "accuracy": round(st.mean(r["accuracy"] for r in rnd), 1),
        "top1": round(st.mean(r["is_best"] for r in rnd), 3),
        "blunder_rate": round(st.mean(r["blunder"] for r in rnd), 3),
    }
    report = {
        "positions": positions_file,
        "grader_depth": Grader.__init__.__defaults__[0],
        "variants": rows,
        "determinism": {
            "n": len(rep),
            "same_pick": sum(r["same_pick"] for r in rep),
            "max_prob_diff": max(r["max_prob_diff"] for r in rep),
        },
        "usage": jev.usage.summary(),
        "human_rating_median": st.median(p["human_rating"] for p in positions),
    }
    (out_dir / "picks.json").write_text(json.dumps(picks, indent=1))
    (out_dir / "report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


def summarize(v: list[dict]) -> dict:
    out = {
        "n": len(v),
        "acpl": round(st.mean(x["cp_loss"] for x in v), 1),
        "median_cp_loss": st.median(x["cp_loss"] for x in v),
        "accuracy": round(st.mean(x["accuracy"] for x in v), 1),
        "top1": round(st.mean(x["is_best"] for x in v), 3),
        "blunder_rate": round(st.mean(x["label"] == "blunder" for x in v), 3),
        "mean_rank_pct": round(st.mean((x["rank"] - 1) / max(1, x["n_moves"] - 1) for x in v), 3),
    }
    if v and "p_best" in v[0]:
        out["mean_p_best"] = round(st.mean(x["p_best"] for x in v), 3)
        out["mean_input_tokens"] = round(st.mean(x["input_tokens"] for x in v))
        out["median_latency_ms"] = round(st.median(x["latency_ms"] for x in v))
    return out


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "data/positions_100_s7.json", workers=int(os.environ.get("WORKERS", 8)))
