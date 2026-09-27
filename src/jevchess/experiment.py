"""Full experiment runner.

  python -m jevchess.experiment ladder      # calibrate rungs below Stockfish's 1320 floor
  python -m jevchess.experiment jev-games   # Jev against the rungs
  python -m jevchess.experiment llm-games   # the LLM against the rungs
  python -m jevchess.experiment puzzles jev|llm
  python -m jevchess.experiment moves jev|llm|grade
  python -m jevchess.experiment report

Every step appends to results/ and is resumable; Jev, LLM and grading answers are cached.
"""

import json
import os
import statistics as st
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import chess

import random

from . import baselines, elo, games, puzzles
from .grade import Grader, random_expectation, summarize_choice
from .jev import Jev
from .llm import LLM
from .players import FAST_TC, JevPlayer, LLMPlayer, RandomPlayer, StockfishPlayer

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "results"
CONFIG = json.loads((ROOT / "experiment.json").read_text())
FMT, STYLE = CONFIG["board_format"], CONFIG["option_style"]
LLM_MODEL, LLM_EFFORT = CONFIG["llm_model"], CONFIG.get("llm_effort")


def rung(name: str, seed: int):
    if name == "Random":
        return RandomPlayer(seed)
    if name == "Capture-first":
        return baselines.CaptureFirstPlayer(seed + 500)
    if name.startswith("SF"):
        name, _, clock = name.partition("@")
        base, _, rand = name.partition("-rand")
        tc = tuple(float(x) for x in clock.split("+")) if clock else FAST_TC
        return StockfishPlayer(elo=int(base[2:]), epsilon=int(rand) / 100 if rand else 0.0, seed=seed + 1000, tc=tc)
    raise ValueError(name)


def ladder():
    for pair in CONFIG["ladder_pairs"]:
        a, b = pair[:2]
        n = pair[2] if len(pair) > 2 else CONFIG["ladder_openings"]
        openings = games.load_book(n, seed=CONFIG["book_seed"])
        games.run_match(f"ladder_{a}_vs_{b}", lambda s, a=a: rung(a, s), lambda s, b=b: rung(b, s), openings,
                        workers=int(os.environ.get("WORKERS", 4)))


def jev_games():
    jev = Jev()
    n = CONFIG["jev_openings"]
    openings = games.load_book(n, seed=CONFIG["book_seed"] + 1)
    for opp in CONFIG["jev_opponents"]:
        games.run_match(f"jev_vs_{opp}", lambda s: JevPlayer(jev, FMT, STYLE), lambda s, o=opp: rung(o, s), openings,
                        workers=int(os.environ.get("WORKERS", 6)))
    print(json.dumps(jev.usage.summary()))


def llm_games():
    llm = LLM(LLM_MODEL, LLM_EFFORT)
    n = CONFIG["llm_openings"]
    openings = games.load_book(n, seed=CONFIG["book_seed"] + 2)
    label = CONFIG["llm_label"]
    for opp in CONFIG["llm_opponents"]:
        games.run_match(f"llm_vs_{opp}", lambda s: LLMPlayer(llm, FMT, STYLE, label), lambda s, o=opp: rung(o, s), openings,
                        workers=int(os.environ.get("WORKERS", 4)))
    print(f"LLM cost this run ${llm.billed:.2f}, all ${llm.cost:.2f}")


def run_puzzles(who: str):
    sample = json.loads((ROOT / "data/puzzles_sample.json").read_text())
    if who == "random":
        out = [{"id": p["PuzzleId"], "rating": int(p["Rating"]), "themes": p["Themes"],
                "solved": baselines.random_puzzle_solve_probability(p)} for p in sample]
        (RES / "puzzles_random.json").write_text(json.dumps(out, indent=1))
        print(who, puzzles.rating([(o["rating"], o["solved"]) for o in out]))
        return
    if who == "capture":
        rng = random.Random(5)
        pick = lambda b: baselines.capture_first(b, rng)
    elif who == "llm":
        sample = [p for i, p in enumerate(sample) if i % 40 < CONFIG["llm_puzzles_per_bin"]]
        llm = LLM(LLM_MODEL, LLM_EFFORT)
        pick = lambda b: llm.choose_move(b, FMT, STYLE)["move"] or next(iter(b.legal_moves))
    else:
        jev = Jev()
        pick = lambda b: jev.choose_move(b, FMT, STYLE)["move"]

    def one(p):
        r = puzzles.solve(pick, p)
        return {"id": p["PuzzleId"], "rating": int(p["Rating"]), "themes": p["Themes"], **r}

    with ThreadPoolExecutor({"llm": 8, "capture": 1}.get(who, 16)) as ex:
        out = list(ex.map(one, sample))
    (RES / f"puzzles_{who}.json").write_text(json.dumps(out, indent=1))
    est = puzzles.rating([(o["rating"], o["solved"]) for o in out])
    print(who, est)
    if who == "llm":
        print(f"LLM cost this run ${llm.billed:.2f}, all ${llm.cost:.2f}")


_local = threading.local()
_graders: list[Grader] = []


def _grader():
    if not hasattr(_local, "g"):
        _local.g = Grader(depth=CONFIG["grade_depth"])
        _graders.append(_local.g)
    return _local.g


def moves(who: str):
    positions = json.loads((ROOT / CONFIG["positions_file"]).read_text())
    if who == "grade":
        with ThreadPoolExecutor(int(os.environ.get("WORKERS", 6))) as ex:
            list(ex.map(lambda p: _grader().scores(chess.Board(p["fen"])), positions))
        for g in _graders:
            g.close()  # open engines keep the interpreter alive
        return
    if who == "llm":
        positions = positions[: CONFIG["llm_positions"]]
        llm = LLM(LLM_MODEL, LLM_EFFORT)
        ask = lambda b: llm.choose_move(b, FMT, STYLE)
        workers = 8
    else:
        jev = Jev()
        ask = lambda b: jev.choose_move(b, FMT, STYLE)
        workers = 16

    def one(p):
        board = chess.Board(p["fen"])
        r = ask(board)
        rec = {"id": p["id"], "uci": r["move"].uci() if r["move"] else None, "san": r["san"]}
        for k in ("confidence", "input_tokens", "output_tokens", "usd", "latency_ms", "legal_reply"):
            if k in r:
                rec[k] = r[k]
        return rec

    with ThreadPoolExecutor(workers) as ex:
        out = list(ex.map(one, positions))
    (RES / f"moves_{who}.json").write_text(json.dumps(out, indent=1))
    if who == "llm":
        print(f"LLM cost this run ${llm.billed:.2f}, all ${llm.cost:.2f}")
    else:
        print(json.dumps(jev.usage.summary()))


def report():
    summary = {"config": CONFIG}
    # Games and Elo
    rows = []
    for f in sorted((RES / "games").glob("*.jsonl")):
        for line in f.read_text().splitlines():
            g = json.loads(line)
            rows.append((g["a"], g["b"], g["score_a"]))
    anchors = CONFIG["anchors"]
    ratings = elo.fit(rows, anchors, prior=CONFIG["elo_prior"])
    ci = elo.bootstrap(rows, anchors, prior=CONFIG["elo_prior"], n=CONFIG["bootstrap"])
    summary["elo"] = {p: {"rating": round(r), "ci95": [round(ci[p][0]), round(ci[p][1])]} for p, r in sorted(ratings.items(), key=lambda kv: -kv[1])}
    summary["matches"] = elo.score_table(rows)
    # Puzzles
    for who in ("jev", "llm", "capture", "random"):
        f = RES / f"puzzles_{who}.json"
        if f.exists():
            out = json.loads(f.read_text())
            summary[f"puzzles_{who}"] = puzzles.rating([(o["rating"], o["solved"]) for o in out])
    # Move quality
    positions = {p["id"]: p for p in json.loads((ROOT / CONFIG["positions_file"]).read_text())}
    g = Grader(depth=CONFIG["grade_depth"])
    mq = {}
    for who in ("jev", "llm"):
        f = RES / f"moves_{who}.json"
        if not f.exists():
            continue
        picks = json.loads(f.read_text())
        graded = [summarize_choice(g.scores(chess.Board(positions[x["id"]]["fen"])), x["uci"]) for x in picks if x["uci"]]
        mq[who] = aggregate(graded) | {"illegal_or_unparsed": sum(1 for x in picks if not x["uci"])}
        ids = [x["id"] for x in picks]
        mq[f"human@{who}"] = aggregate([summarize_choice(g.scores(chess.Board(positions[i]["fen"])), positions[i]["human_move"]) for i in ids])
        rnd = [random_expectation(g.scores(chess.Board(positions[i]["fen"]))) for i in ids]
        mq[f"random@{who}"] = {"n": len(rnd), "acpl": round(st.mean(r["cp_loss"] for r in rnd), 1),
                                "accuracy": round(st.mean(r["accuracy"] for r in rnd), 1),
                                "top1": round(st.mean(r["is_best"] for r in rnd), 3),
                                "blunder_rate": round(st.mean(r["blunder"] for r in rnd), 3)}
    g.close()
    summary["move_quality"] = mq
    (RES / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


def aggregate(v):
    return {
        "n": len(v),
        "acpl": round(st.mean(x["cp_loss"] for x in v), 1),
        "median_cp_loss": st.median(x["cp_loss"] for x in v),
        "accuracy": round(st.mean(x["accuracy"] for x in v), 1),
        "top1": round(st.mean(x["is_best"] for x in v), 3),
        "blunder_rate": round(st.mean(x["label"] == "blunder" for x in v), 3),
    }


if __name__ == "__main__":
    cmd, arg = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else None)
    commands = {"ladder": ladder, "jev-games": jev_games, "llm-games": llm_games, "report": report,
                "puzzles": lambda: run_puzzles(arg), "moves": lambda: moves(arg)}
    commands[cmd]()
