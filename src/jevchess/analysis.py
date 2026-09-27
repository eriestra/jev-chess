"""Everything the results page reports except the game ratings (see experiment.report).

Reads cached answers only: re-running this makes no Jev, LLM or Stockfish requests
beyond what is already stored.
"""

import json
import random
import sqlite3
import statistics as st
from pathlib import Path

import chess
import numpy as np
from scipy.stats import binomtest, spearmanr

from . import formats, puzzles
from .baselines import capture_first
from .grade import CP_CAP, summarize_choice, random_expectation

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "results"
CONFIG = json.loads((ROOT / "experiment.json").read_text())


class Scores:
    def __init__(self):
        self.db = sqlite3.connect(RES / "cache/grades.sqlite")

    def get(self, fen: str, depth: int) -> dict:
        row = self.db.execute("select moves from grades where fen=? and depth=?", (fen, depth)).fetchone()
        return json.loads(row[0]) if row else None


def aggregate(v: list[dict]) -> dict:
    return {
        "n": len(v),
        "acpl": round(st.mean(x["cp_loss"] for x in v), 1),
        "median_cp_loss": float(st.median(x["cp_loss"] for x in v)),
        "accuracy": round(st.mean(x["accuracy"] for x in v), 1),
        "top1": round(st.mean(x["is_best"] for x in v), 3),
        "blunder_rate": round(st.mean(x["label"] == "blunder" for x in v), 3),
    }


def random_aggregate(v: list[dict]) -> dict:
    return {
        "n": len(v),
        "acpl": round(st.mean(r["cp_loss"] for r in v), 1),
        "accuracy": round(st.mean(r["accuracy"] for r in v), 1),
        "top1": round(st.mean(r["is_best"] for r in v), 3),
        "blunder_rate": round(st.mean(r["blunder"] for r in v), 3),
    }


def profile(pairs: list[tuple[chess.Board, chess.Move]]) -> dict:
    n = len(pairs)
    return {
        "n": n,
        "capture": round(sum(b.is_capture(m) for b, m in pairs) / n, 3),
        "check": round(sum(b.gives_check(m) for b, m in pairs) / n, 3),
        "queen_move": round(sum(b.piece_type_at(m.from_square) == chess.QUEEN for b, m in pairs) / n, 3),
    }


def random_profile(boards: list[chess.Board]) -> dict:
    cap = chk = q = 0.0
    for b in boards:
        ms = list(b.legal_moves)
        cap += sum(b.is_capture(m) for m in ms) / len(ms)
        chk += sum(b.gives_check(m) for m in ms) / len(ms)
        q += sum(b.piece_type_at(m.from_square) == chess.QUEEN for m in ms) / len(ms)
    n = len(boards)
    return {"n": n, "capture": round(cap / n, 3), "check": round(chk / n, 3), "queen_move": round(q / n, 3)}


def run() -> dict:
    sc = Scores()
    depth = CONFIG["grade_depth"]
    out = {}

    # ---- held-out move quality -------------------------------------------------
    positions = json.loads((ROOT / CONFIG["positions_file"]).read_text())
    pos = {p["id"]: p for p in positions}
    jev = {x["id"]: x for x in json.loads((RES / "moves_jev.json").read_text())}
    llm = {x["id"]: x for x in json.loads((RES / "moves_llm.json").read_text())}
    rng = random.Random(9)
    rows = {k: [] for k in ("jev", "human", "capture", "random")}
    prof = {k: [] for k in ("jev", "human", "capture")}
    boards = []
    conf = []
    for p in positions:
        s = sc.get(p["fen"], depth)
        b = chess.Board(p["fen"])
        boards.append(b)
        j = chess.Move.from_uci(jev[p["id"]]["uci"])
        h = chess.Move.from_uci(p["human_move"])
        c = capture_first(b, rng)
        rows["jev"].append(summarize_choice(s, j.uci()))
        rows["human"].append(summarize_choice(s, h.uci()))
        rows["capture"].append(summarize_choice(s, c.uci()))
        rows["random"].append(random_expectation(s))
        prof["jev"].append((b, j))
        prof["human"].append((b, h))
        prof["capture"].append((b, c))
        conf.append((jev[p["id"]]["confidence"], rows["jev"][-1]["cp_loss"]))
    out["heldout"] = {
        "jev": aggregate(rows["jev"]),
        "human": aggregate(rows["human"]),
        "capture": aggregate(rows["capture"]),
        "random": random_aggregate(rows["random"]),
        "human_rating_median": st.median(p["human_rating"] for p in positions),
    }
    # Jev vs random on the same positions: paired difference in centipawn loss
    diffs = [a["cp_loss"] - r["cp_loss"] for a, r in zip(rows["jev"], rows["random"])]
    boot = sorted(st.mean(random.Random(i).choices(diffs, k=len(diffs))) for i in range(2000))
    out["heldout"]["jev_minus_random_acpl"] = {"mean": round(st.mean(diffs), 1), "ci95": [round(boot[50], 1), round(boot[1949], 1)]}
    # top-1 against chance
    exp_top1 = sum(r["is_best"] for r in rows["random"])
    obs_top1 = sum(a["is_best"] for a in rows["jev"])
    out["heldout"]["jev_top1_vs_chance"] = {"observed": obs_top1, "expected_random": round(exp_top1, 1), "n": len(positions),
                                            "p_value": binomtest(obs_top1, len(positions), exp_top1 / len(positions), alternative="greater").pvalue}
    out["profile"] = {k: profile(v) for k, v in prof.items()} | {"random": random_profile(boards)}
    # confidence vs quality
    conf.sort()
    third = len(conf) // 3
    out["confidence"] = [
        {"range": [round(conf[i * third][0], 2), round(conf[min(len(conf) - 1, (i + 1) * third - 1)][0], 2)],
         "acpl": round(st.mean(c for _, c in conf[i * third:(i + 1) * third]), 1)}
        for i in range(3)
    ]
    out["confidence_spearman"] = round(float(spearmanr([c for c, _ in conf], [l for _, l in conf]).statistic), 3)

    # ---- shared positions with the LLM -----------------------------------------
    shared = [i for i in llm if llm[i]["uci"]]
    sh = {k: [] for k in ("jev", "llm", "human", "capture", "random")}
    llm_prof = []
    rng = random.Random(9)
    for i in shared:
        p = pos[i]
        s = sc.get(p["fen"], depth)
        b = chess.Board(p["fen"])
        sh["jev"].append(summarize_choice(s, jev[i]["uci"]))
        sh["llm"].append(summarize_choice(s, llm[i]["uci"]))
        sh["human"].append(summarize_choice(s, p["human_move"]))
        sh["capture"].append(summarize_choice(s, capture_first(b, rng).uci()))
        sh["random"].append(random_expectation(s))
        llm_prof.append((b, chess.Move.from_uci(llm[i]["uci"])))
    out["shared"] = {k: (random_aggregate(v) if k == "random" else aggregate(v)) for k, v in sh.items()}
    out["shared"]["llm_unusable_replies"] = sum(1 for x in llm.values() if not x["uci"])
    out["profile"]["llm"] = profile(llm_prof)

    # ---- pilot: variants and grading depth agreement ----------------------------
    pilot = json.loads((RES / "pilot/report.json").read_text())
    out["pilot"] = {k: v for k, v in pilot.items() if k in ("variants", "determinism", "usage", "human_rating_median")}
    ppos = json.loads((ROOT / "data/positions_100_s7.json").read_text())
    rho, best_agree = [], 0
    for p in ppos:
        a, b = sc.get(p["fen"], 16), sc.get(p["fen"], 12)
        if not (a and b):
            continue
        keys = sorted(a)
        ca = [max(-CP_CAP, min(CP_CAP, a[k])) for k in keys]
        cb = [max(-CP_CAP, min(CP_CAP, b.get(k, 0))) for k in keys]
        if len(set(ca)) > 1 and len(set(cb)) > 1:
            rho.append(spearmanr(ca, cb).statistic)
        best_agree += max(a, key=a.get) == max(b, key=b.get)
    picks = [x for x in json.loads((RES / "pilot/picks.json").read_text()) if x["variant"] == f"{CONFIG['board_format']}/{CONFIG['option_style']}"]
    by = {p["id"]: p for p in ppos}
    acpl12 = st.mean(summarize_choice(sc.get(by[x["id"]]["fen"], 12), x["uci"])["cp_loss"] for x in picks)
    out["depth_check"] = {"n": len(ppos), "median_spearman": round(float(np.median(rho)), 3), "same_best_move": best_agree,
                          "jev_acpl_depth16": round(st.mean(x["cp_loss"] for x in picks), 1), "jev_acpl_depth12": round(acpl12, 1)}

    # ---- puzzles ------------------------------------------------------------------
    pz = {}
    bands = {}
    for who in ("jev", "llm", "capture", "random"):
        f = RES / f"puzzles_{who}.json"
        if not f.exists():
            continue
        res = json.loads(f.read_text())
        pz[who] = puzzles.rating([(o["rating"], o["solved"]) for o in res])
        b = {}
        for o in res:
            b.setdefault(600 + (o["rating"] - 600) // 200 * 200, []).append(float(o["solved"]))
        bands[who] = {k: round(st.mean(v), 3) for k, v in sorted(b.items())}
    jres = {o["id"]: o["solved"] for o in json.loads((RES / "puzzles_jev.json").read_text())}
    cres = {o["id"]: o["solved"] for o in json.loads((RES / "puzzles_capture.json").read_text())}
    only_j = sum(1 for k in jres if jres[k] and not cres[k])
    only_c = sum(1 for k in jres if cres[k] and not jres[k])
    pz["jev_vs_capture"] = {"only_jev": only_j, "only_capture": only_c, "both": sum(1 for k in jres if jres[k] and cres[k]),
                            "p_value": binomtest(only_j, only_j + only_c, 0.5).pvalue if only_j + only_c else None}
    if (RES / "puzzles_llm.json").exists():
        lres = {o["id"]: o["solved"] for o in json.loads((RES / "puzzles_llm.json").read_text())}
        sub = [k for k in lres]
        pz["llm_subset"] = {"n": len(sub), "llm_solved": sum(lres[k] for k in sub), "jev_solved_same": sum(jres[k] for k in sub),
                            "capture_solved_same": sum(cres[k] for k in sub),
                            "jev_rating_same": puzzles.rating([(o["rating"], o["solved"]) for o in json.loads((RES / "puzzles_jev.json").read_text()) if o["id"] in lres])}
    out["puzzles"] = pz
    out["puzzle_bands"] = bands

    # ---- cost and speed ---------------------------------------------------------------
    jdb = sqlite3.connect(RES / "cache/jev.sqlite")
    toks, lat, n = 0, [], 0
    for resp, ms in jdb.execute("select response, latency_ms from jev"):
        toks += json.loads(resp).get("usage", {}).get("input_tokens", 0)
        lat.append(ms)
        n += 1
    ldb = sqlite3.connect(RES / "cache/llm.sqlite")
    lcost, lcalls, lout, llat = 0.0, 0, [], []
    for (resp,) in ldb.execute("select response from llm"):
        r = json.loads(resp)
        lcost += r.get("total_cost_usd", 0)
        lcalls += 1
        lout.append(r.get("usage", {}).get("output_tokens", 0))
        llat.append(r.get("duration_api_ms") or r.get("wall_ms"))
    out["cost"] = {
        "jev": {"requests": n, "input_tokens": toks, "usd": round(toks / 1e6 * 0.042, 4),
                "usd_per_move": toks / n / 1e6 * 0.042, "median_latency_ms": round(st.median(lat)), "tokens_per_move": round(toks / n)},
        "llm": {"requests": lcalls, "usd": round(lcost, 2), "usd_per_move": lcost / max(1, lcalls),
                "median_latency_ms": round(st.median(llat)) if llat else None, "mean_output_tokens": round(st.mean(lout)) if lout else None},
    }
    (RES / "analysis.json").write_text(json.dumps(out, indent=1, default=float))
    return out


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, default=float))
