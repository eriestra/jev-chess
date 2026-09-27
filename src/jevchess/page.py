"""Build the results page (results/page/index.html) from analysis.json and summary.json."""

import html
import json
import sqlite3
from pathlib import Path

import chess

from .jev import Jev

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "results"
CONFIG = json.loads((ROOT / "experiment.json").read_text())
REPO = "https://github.com/eriestra/jev-chess"

EXAMPLES = [
    ("lichess-2013-01-3514", "Jev's pick is the best move"),
    ("lichess-2013-01-12643", "The queen takes a defended knight"),
    ("lichess-2013-01-16648", "The queen takes a defended knight, with check"),
    ("lichess-2013-01-10", "A check instead of the quiet best move"),
]

NAMES = {
    "SF1320@120+1": "Stockfish 1320, 120 + 1 (anchor)",
    "SF1320": "Stockfish 1320, 10 + 0.1",
    "SF1320-rand10": "Stockfish 1320, 10% random moves",
    "SF1320-rand25": "Stockfish 1320, 25% random moves",
    "SF1320-rand50": "Stockfish 1320, 50% random moves",
    "SF1320-rand75": "Stockfish 1320, 75% random moves",
    "Capture-first": "Capture-first rule",
    "Random": "Random legal move",
    "Jev": "Jev",
    "Sonnet 5": "Sonnet 5",
}
ENTITY = {"Jev": "jev", "Sonnet 5": "llm", "Capture-first": "cap", "Random": "rnd"}


def examples() -> list[dict]:
    jev = Jev()
    db = sqlite3.connect(RES / "cache/grades.sqlite")
    pos = {p["id"]: p for p in json.loads((ROOT / CONFIG["positions_file"]).read_text())}
    llm = {x["id"]: x for x in json.loads((RES / "moves_llm.json").read_text())}
    out = []
    for pid, title in EXAMPLES:
        p = pos[pid]
        b = chess.Board(p["fen"])
        scores = json.loads(db.execute("select moves from grades where fen=? and depth=?", (p["fen"], CONFIG["grade_depth"])).fetchone()[0])
        best = max(scores.values())
        r = jev.choose_move(b, CONFIG["board_format"], CONFIG["option_style"])
        top = sorted(r["probabilities"].items(), key=lambda kv: -kv[1])[:5]
        rows = []
        for san, prob in top:
            m = b.parse_san(san)
            rows.append({"san": san, "p": round(prob, 3), "loss": min(1000, best - scores[m.uci()]),
                         "from": chess.square_name(m.from_square), "to": chess.square_name(m.to_square)})
        best_uci = max(scores, key=scores.get)
        bm = chess.Move.from_uci(best_uci)
        human = chess.Move.from_uci(p["human_move"])
        out.append({
            "id": pid, "title": title, "fen": p["fen"], "side": "White" if b.turn else "Black",
            "n_moves": b.legal_moves.count(), "rows": rows,
            "best": b.san(bm), "best_from": chess.square_name(bm.from_square), "best_to": chess.square_name(bm.to_square),
            "llm": llm.get(pid, {}).get("san"), "human": b.san(human), "human_rating": p["human_rating"],
            "human_loss": min(1000, best - scores[p["human_move"]]), "url": p["url"],
        })
    assert jev.usage.billed_input_tokens == 0, "examples must come from the cache"
    return out


def request_example() -> str:
    """A real request body, with the option list shortened for display."""
    pid = EXAMPLES[1][0]
    p = next(x for x in json.loads((ROOT / CONFIG["positions_file"]).read_text()) if x["id"] == pid)
    from . import formats
    b = chess.Board(p["fen"])
    opts = formats.move_options(b, CONFIG["option_style"])
    shown = dict(list(opts.items())[:4])
    body = {
        "model": "jev-latest",
        "state": formats.board_state(b, CONFIG["board_format"]),
        "questions": {"move": {"type": "choice", "instructions": formats.instructions(b), "criteria": shown}},
    }
    text = json.dumps(body, indent=2)
    last = list(shown)[-1]
    marker = f'"{last}": {json.dumps(shown[last])}'
    return text.replace(marker, marker + f",\n        … {len(opts) - len(shown)} more legal moves")


SENTENCE = {
    "SF1320@120+1": "the Stockfish 1320 anchor",
    "SF1320": "Stockfish 1320 on the fast clock",
    "SF1320-rand10": "Stockfish 1320 with 10% random moves",
    "SF1320-rand25": "Stockfish 1320 with 25% random moves",
    "SF1320-rand50": "Stockfish 1320 with 50% random moves",
    "SF1320-rand75": "Stockfish 1320 with 75% random moves",
    "Capture-first": "the capture-first rule",
    "Random": "the random mover",
    "Sonnet 5": "Sonnet 5",
}


def game_finding(elo: dict, matches: dict) -> str:
    """Where Jev sits among the reference players, with its head-to-head scores."""
    j = elo["Jev"]["rating"]
    refs = {k: v["rating"] for k, v in elo.items() if k not in ("Jev", "Sonnet 5")}
    lower = max((k for k in refs if refs[k] < j), key=refs.get, default=None)
    upper = min((k for k in refs if refs[k] > j), key=refs.get, default=None)
    say = lambda k: f"{SENTENCE[k]} ({refs[k]:,})"
    if lower and upper:
        text = f"Jev rates between {say(lower)} and {say(upper)}."
    elif upper:
        text = f"Jev rates below {say(upper)}."
    else:
        text = f"Jev rates above {say(lower)}."
    h2h = []
    for k, v in matches.items():
        a, b = k.split(" vs ")
        if a == "Jev":
            h2h.append((refs.get(b, 0), f"{v['points'] / v['games'] * 100:.0f}% against {SENTENCE[b]}"))
    if h2h:
        h2h.sort()
        text += " Head to head it scored " + ", ".join(x for _, x in h2h[:-1]) + " and " + h2h[-1][1] + "."
    return text + " Stockfish's lowest calibrated setting is 1,320."


def game_notes() -> str:
    """How Jev's and the LLM's games ended, from the game records."""
    out = []
    rank = ["Random", "Capture-first", "SF1320-rand75", "SF1320-rand50"]
    for f in sorted((RES / "games").glob("jev_vs_*.jsonl"), key=lambda f: rank.index(f.stem[7:]) if f.stem[7:] in rank else 9):
        g = [json.loads(l) for l in f.read_text().splitlines()]
        opp = SENTENCE[g[0]["b"]]
        won = [x for x in g if x["score_a"] == 1]
        lost = [x for x in g if x["score_a"] == 0]
        drawn = [x for x in g if x["score_a"] == 0.5]
        reps = sum(1 for x in drawn if x["termination"] == "threefold_repetition")
        mates = sum(1 for x in won if x["termination"] == "checkmate")
        out.append(f"against {opp}, Jev won {len(won)} of {len(g)} ({mates} by checkmate), drew {len(drawn)} ({reps} by repetition) and lost {len(lost)}")
    text = "In its games " + "; ".join(out) + "." if out else ""
    for f in sorted((RES / "games").glob("llm_vs_*.jsonl")):
        g = [json.loads(l) for l in f.read_text().splitlines()]
        moves = [m for x in g for m in x["moves"] if "legal" in m]
        bad = sum(1 for m in moves if not m["legal"])
        usd = sum(m.get("usd", 0) for m in moves)
        text += (f" Sonnet 5 played {len(g)} games against {SENTENCE[g[0]['b']]} ({len(moves)} moves, USD {usd:.2f});"
                 f" {bad} of its replies named no legal move and were replaced by a random legal move.")
    return text


def fmt_ci(ci) -> str:
    return f"{ci[0]:,}–{ci[1]:,}"


def build():
    a = json.loads((RES / "analysis.json").read_text())
    s = json.loads((RES / "summary.json").read_text())
    ex = examples()
    elo = s["elo"]
    matches = s["matches"]

    def match_rows():
        rows = []
        for key, v in matches.items():
            x, y = key.split(" vs ")
            rows.append((x, y, v))
        order = ["Jev", "Sonnet 5", "SF1320@120+1", "SF1320", "SF1320-rand10", "SF1320-rand25", "SF1320-rand50", "SF1320-rand75", "Capture-first", "Random"]
        rows.sort(key=lambda r: (order.index(r[0]) if r[0] in order else 99, -elo.get(r[1], {}).get("rating", 0)))
        return rows

    data = {
        "elo": [{"key": k, "name": NAMES.get(k, k), "rating": v["rating"], "ci": v["ci95"], "entity": ENTITY.get(k, "ref"),
                 "anchor": k in CONFIG["anchors"]} for k, v in elo.items()],
        "moves": [
            {"name": "Humans (median 1614)", "entity": "hum", **a["shared"]["human"]},
            {"name": "Sonnet 5", "entity": "llm", **a["shared"]["llm"]},
            {"name": "Capture-first rule", "entity": "cap", **a["shared"]["capture"]},
            {"name": "Random legal move", "entity": "rnd", **a["shared"]["random"]},
            {"name": "Jev", "entity": "jev", **a["shared"]["jev"]},
        ],
        "bands": a["puzzle_bands"],
        "examples": ex,
    }

    jev_elo = elo["Jev"]
    llm_elo = elo.get("Sonnet 5")
    h = a["heldout"]
    pz = a["puzzles"]
    prof = a["profile"]
    cost = a["cost"]
    pilot = a["pilot"]
    dc = a["depth_check"]
    n_games = sum(v["games"] for v in matches.values())
    jev_games = {k: v for k, v in matches.items() if k.startswith("Jev vs ")}
    n_jev_games = sum(v["games"] for v in jev_games.values())

    def pct(x, d=0):
        return f"{x * 100:.{d}f}%"

    variant_names = {"fen/san": ("FEN string", "SAN only"), "fen/described": ("FEN string", "SAN + description"),
                     "grid/san": ("8×8 text grid", "SAN only"), "grid/described": ("8×8 text grid", "SAN + description"),
                     "pieces/san": ("Piece lists", "SAN only"), "pieces/described": ("Piece lists", "SAN + description")}
    pilot_rows = "".join(
        f"<tr{' class=chosen' if k == CONFIG['board_format'] + '/' + CONFIG['option_style'] else ''}><td>{variant_names[k][0]}</td><td>{variant_names[k][1]}</td>"
        f"<td class=num>{v['acpl']:.0f}</td><td class=num>{v['accuracy']:.1f}</td><td class=num>{pct(v['top1'])}</td><td class=num>{pct(v['blunder_rate'])}</td><td class=num>{v['mean_input_tokens']:,}</td></tr>"
        for k, v in pilot["variants"].items() if k in variant_names)
    pr = pilot["variants"]["random legal move"]
    ph = pilot["variants"]["human (source game)"]

    match_html = "".join(
        f"<tr><td>{NAMES.get(x, x)}</td><td>{NAMES.get(y, y)}</td><td class=num>{v['games']}</td>"
        f"<td class=num>{v['w']}</td><td class=num>{v['d']}</td><td class=num>{v['l']}</td><td class=num>{v['points'] / v['games'] * 100:.0f}%</td></tr>"
        for x, y, v in match_rows())

    def prof_row(label, p, d=0):
        return f"<tr><td>{label}</td><td class=num>{pct(p['capture'], d)}</td><td class=num>{pct(p['check'], d)}</td><td class=num>{pct(p['queen_move'], d)}</td><td class=num>{p['n']}</td></tr>"

    sources = [
        ("TypeSafe API reference (Choice, up to 255 options)", "https://docs.typesafe.ai/api.md"),
        ("TypeSafe models and pricing (Jev 1.13, USD 0.042 per million input tokens)", "https://docs.typesafe.ai/models.md"),
        ("Stockfish UCI_Elo calibration: PR #4341, 8moves_v3 book at 120+1, CCRL Blitz scale", "https://github.com/official-stockfish/Stockfish/pull/4341"),
        ("Stockfish Skill implementation (search.h, search.cpp)", "https://github.com/official-stockfish/Stockfish/blob/master/src/search.h"),
        ("Opening book 8moves_v3.pgn", "https://github.com/official-stockfish/books"),
        ("Lichess standard games database, January 2013 (CC0)", "https://database.lichess.org/"),
        ("Lichess puzzle database (CC0)", "https://database.lichess.org/#puzzles"),
        ("Lichess accuracy and win% formulas", "https://lichess.org/page/accuracy"),
        ("Lichess accuracy source (AccuracyPercent.scala)", "https://github.com/lichess-org/lila/blob/master/modules/analyse/src/main/AccuracyPercent.scala"),
        ("Lichess blunder thresholds (Advice.scala)", "https://github.com/lichess-org/lila/blob/master/modules/tree/src/main/Advice.scala"),
        ("python-chess", "https://python-chess.readthedocs.io/"),
        ("Claude API pricing (Sonnet 5: USD 2 per million input tokens, USD 10 per million output tokens)", "https://platform.claude.com/docs/en/about-claude/pricing"),
    ]
    sources_html = "".join(f'<li><a href="{u}">{html.escape(t)}</a></li>' for t, u in sources)

    llm_line = ""
    if llm_elo:
        llm_line = f" Sonnet 5, given the same request as text, rated {llm_elo['rating']:,} ({fmt_ci(llm_elo['ci95'])})."

    page = TEMPLATE
    repl = {
        "{{DATA}}": json.dumps(data).replace("</", "<\\/"),
        "{{JEV_ELO}}": f"{jev_elo['rating']:,}",
        "{{JEV_ELO_CI}}": fmt_ci(jev_elo["ci95"]),
        "{{RANDOM_ELO}}": f"{elo['Random']['rating']:,}",
        "{{CAP_ELO}}": f"{elo['Capture-first']['rating']:,}",
        "{{LLM_LINE}}": llm_line,
        "{{PZ_JEV}}": f"{pz['jev']['rating']:,}",
        "{{PZ_JEV_CI}}": fmt_ci(pz["jev"]["ci95"]),
        "{{PZ_CAP}}": f"{pz['capture']['rating']:,}",
        "{{PZ_CAP_CI}}": fmt_ci(pz["capture"]["ci95"]),
        "{{PZ_RND}}": f"{pz['random']['rating']:,}",
        "{{PZ_JEV_SOLVED}}": str(pz["jev"]["solved"]),
        "{{PZ_CAP_SOLVED}}": str(pz["capture"]["solved"]),
        "{{PZ_N}}": str(pz["jev"]["n"]),
        "{{PZ_ONLY_J}}": str(pz["jev_vs_capture"]["only_jev"]),
        "{{PZ_ONLY_C}}": str(pz["jev_vs_capture"]["only_capture"]),
        "{{PZ_P}}": f"{pz['jev_vs_capture']['p_value']:.3f}",
        "{{PZ_LLM}}": f"{pz['llm']['rating']:,}" if "llm" in pz else "",
        "{{PZ_LLM_CI}}": fmt_ci(pz["llm"]["ci95"]) if "llm" in pz else "",
        "{{PZ_LLM_N}}": str(pz["llm"]["n"]) if "llm" in pz else "",
        "{{PZ_LLM_SOLVED}}": str(pz["llm"]["solved"]) if "llm" in pz else "",
        "{{PZ_LLM_JEV_SAME}}": f"{pz['llm_subset']['jev_rating_same']['rating']:,}" if "llm_subset" in pz else "",
        "{{H_JEV_ACPL}}": f"{h['jev']['acpl']:.0f}",
        "{{H_RND_ACPL}}": f"{h['random']['acpl']:.0f}",
        "{{H_CAP_ACPL}}": f"{h['capture']['acpl']:.0f}",
        "{{H_HUM_ACPL}}": f"{h['human']['acpl']:.0f}",
        "{{H_DIFF}}": f"{h['jev_minus_random_acpl']['mean']:+.0f}",
        "{{H_DIFF_CI}}": f"{h['jev_minus_random_acpl']['ci95'][0]:+.0f} to {h['jev_minus_random_acpl']['ci95'][1]:+.0f}".replace("-", "\u2212"),
        "{{LLM_POS}}": str(CONFIG["llm_positions"]),
        "{{H_JEV_TOP1}}": pct(h["jev"]["top1"], 1),
        "{{H_RND_TOP1}}": pct(h["random"]["top1"], 1),
        "{{H_HUM_TOP1}}": pct(h["human"]["top1"], 1),
        "{{H_CAP_TOP1}}": pct(h["capture"]["top1"], 1),
        "{{H_JEV_BL}}": pct(h["jev"]["blunder_rate"]),
        "{{H_RND_BL}}": pct(h["random"]["blunder_rate"]),
        "{{H_HUM_BL}}": pct(h["human"]["blunder_rate"]),
        "{{H_CAP_BL}}": pct(h["capture"]["blunder_rate"]),
        "{{H_JEV_ACC}}": f"{h['jev']['accuracy']:.1f}",
        "{{H_RND_ACC}}": f"{h['random']['accuracy']:.1f}",
        "{{H_HUM_ACC}}": f"{h['human']['accuracy']:.1f}",
        "{{H_CAP_ACC}}": f"{h['capture']['accuracy']:.1f}",
        "{{H_N}}": str(h["jev"]["n"]),
        "{{H_HUM_MED}}": f"{h['human_rating_median']:,.0f}",
        "{{SH_N}}": str(a["shared"]["jev"]["n"]),
        "{{SH_LLM_ACPL}}": f"{a['shared']['llm']['acpl']:.0f}",
        "{{SH_JEV_ACPL}}": f"{a['shared']['jev']['acpl']:.0f}",
        "{{SH_HUM_ACPL}}": f"{a['shared']['human']['acpl']:.0f}",
        "{{LLM_BAD}}": str(a["shared"]["llm_unusable_replies"]),
        "{{PROF_ROWS}}": prof_row("Jev", prof["jev"]) + prof_row("Capture-first rule", prof["capture"]) + prof_row("Sonnet 5", prof["llm"])
        + prof_row(f"Humans (median {h['human_rating_median']:,.0f})", prof["human"]) + prof_row("Random legal move (share of legal moves)", prof["random"], 1),
        "{{CONF_TOP}}": f"{a['confidence'][2]['acpl']:.0f}",
        "{{CONF_BOTTOM}}": f"{a['confidence'][0]['acpl']:.0f}",
        "{{CONF_TOP_MIN}}": f"{a['confidence'][2]['range'][0]:.2f}",
        "{{CONF_BOTTOM_MAX}}": f"{a['confidence'][0]['range'][1]:.2f}",
        "{{PILOT_ROWS}}": pilot_rows,
        "{{PILOT_RND}}": f"{pr['acpl']:.0f}",
        "{{PILOT_HUM}}": f"{ph['acpl']:.0f}",
        "{{DET_SAME}}": str(pilot["determinism"]["same_pick"]),
        "{{DET_N}}": str(pilot["determinism"]["n"]),
        "{{DET_DIFF}}": f"{pilot['determinism']['max_prob_diff']:.2f}",
        "{{DC_RHO}}": f"{dc['median_spearman']:.2f}",
        "{{DC_BEST}}": str(dc["same_best_move"]),
        "{{DC_16}}": f"{dc['jev_acpl_depth16']:.0f}",
        "{{DC_12}}": f"{dc['jev_acpl_depth12']:.0f}",
        "{{MATCH_ROWS}}": match_html,
        "{{N_GAMES}}": f"{n_games:,}",
        "{{N_JEV_GAMES}}": f"{n_jev_games:,}",
        "{{J_TOK}}": f"{cost['jev']['tokens_per_move']:,}",
        "{{J_USD_MOVE}}": f"{cost['jev']['usd_per_move']:.6f}",
        "{{J_MS}}": f"{cost['jev']['median_latency_ms']:,}",
        "{{J_REQ}}": f"{cost['jev']['requests']:,}",
        "{{J_USD}}": f"{cost['jev']['usd']:.2f}",
        "{{L_USD_MOVE}}": f"{cost['llm']['usd_per_move']:.4f}",
        "{{L_MS}}": f"{cost['llm']['median_latency_ms']:,}",
        "{{L_OUT}}": f"{cost['llm']['mean_output_tokens']:,}",
        "{{L_REQ}}": f"{cost['llm']['requests']:,}",
        "{{L_USD}}": f"{cost['llm']['usd']:.2f}",
        "{{RATIO}}": f"{cost['llm']['usd_per_move'] / cost['jev']['usd_per_move']:,.0f}",
        "{{SPEED}}": f"{cost['llm']['median_latency_ms'] / cost['jev']['median_latency_ms']:.0f}",
        "{{REQUEST}}": html.escape(request_example()),
        "{{SOURCES}}": sources_html,
        "{{REPO}}": REPO,
        "{{BOOK_N}}": str(CONFIG["jev_openings"]),
        "{{TOP1_RATIO}}": f"{h['jev']['top1'] / h['random']['top1']:.0f}",
        "{{PROF_JEV_CAP}}": pct(prof["jev"]["capture"]),
        "{{PROF_HUM_CAP}}": pct(prof["human"]["capture"]),
        "{{PROF_RND_CAP}}": pct(prof["random"]["capture"], 1),
        "{{GAME_FINDING}}": game_finding(elo, matches),
        "{{GAME_NOTES}}": game_notes(),
        "{{VAL_N}}": str(matches.get("SF1320@120+1 vs SF1320", {}).get("games", 0)),
        "{{SF_FAST}}": f"{elo['SF1320']['rating']:,}",
        "{{SF_FAST_CI}}": fmt_ci(elo["SF1320"]["ci95"]),
    }
    for k, v in repl.items():
        page = page.replace(k, v)
    assert "{{" not in page, [x for x in page.split("{{")[1:]][:3]
    out = RES / "page"
    out.mkdir(exist_ok=True)
    (out / "index.html").write_text(page)
    print(out / "index.html", len(page))


TEMPLATE = (Path(__file__).parent / "page_template.html").read_text() if (Path(__file__).parent / "page_template.html").exists() else ""

if __name__ == "__main__":
    build()
