# jev-chess

Measures how well [TypeSafe](https://typesafe.ai)'s **Jev** chooses chess moves when it
sees only the position and the complete list of legal moves. Stockfish 19 grades every
choice.

**Results page:** https://sites.almond.build/jev-chess/

Each move is one TypeSafe request with a single
[Choice](https://docs.typesafe.ai/primitives/choice) question: the `state` is the board,
the options are every legal move in SAN with a plain description, and the player plays
the most probable option. No move history, evaluation or search is given.

## Results (Jev 1.13.0, 27 September 2026)

| Measure | Jev | Comparison |
| --- | --- | --- |
| Game rating (Elo, CCRL Blitz scale) | 448 (241–598) | capture-first rule 442; random mover 103; Sonnet 5 1,129; Stockfish floor 1,320 |
| Puzzle rating (Lichess scale) | 1,114 (1,056–1,172) | capture-first rule 1,017; random mover 219; Sonnet 5 1,440 |
| Average centipawn loss, 559 positions | 397 | random legal move 392; humans (median 1,614) 83 |
| Best move found | 19.3% | random legal move 4.8% |
| Captures among its moves | 77% | humans 30%; legal moves 6.5% |
| Cost and time per move | USD 0.00004, 263 ms | Sonnet 5: USD 0.017, 9,490 ms |

Full tables, charts and method: see the results page.

## What is measured

1. **Game rating.** Games from the `8moves_v3` opening book against players of known
   strength. Stockfish 19 at `UCI_Elo` 1320 on a 120 + 1 clock is the anchor (its scale
   is fitted to CCRL Blitz, [PR #4341](https://github.com/official-stockfish/Stockfish/pull/4341)).
   Below it, reference players (Stockfish 1320 with 25/50/75% random moves, a
   capture-first rule, a random mover) carry the scale down. Ratings are fitted by
   maximum likelihood with bootstrap intervals.
2. **Move quality.** 559 positions from rated Lichess games (January 2013). Stockfish
   scores every legal move; the chosen move is graded by centipawn loss, Lichess accuracy
   and blunder rate, next to the human move from the source game and a random move.
3. **Puzzle rating.** 400 Lichess puzzles, 40 per 200-point band from 600 to 2,599.

Claude Sonnet 5 receives the same request as text through the Claude Code CLI, for
comparison.

## Layout

```
experiment.json          run configuration (input format, matches, anchor)
src/jevchess/
  formats.py             how the board and the options are written
  jev.py                 TypeSafe client with request cache and token accounting
  llm.py                 the same request as text through the Claude Code CLI
  grade.py               Stockfish MultiPV grading, Lichess accuracy and blunder labels
  positions.py           samples positions from the Lichess games database
  puzzles.py             puzzle sample, solving rules, puzzle rating
  baselines.py           capture-first rule; exact random-mover puzzle odds
  players.py, games.py   players and match runner
  elo.py                 maximum-likelihood ratings with anchors and bootstrap
  pilot.py               the six-format pilot
  experiment.py          main run: games, moves, puzzles, report
  analysis.py, page.py   everything the results page shows
tests/                   unit tests
results/                 pilot, move picks, puzzle results, games (JSON lines with PGN), summaries
results/cache/           every Jev request and answer, every LLM reply, every Stockfish analysis
docs/spec.md, plan.md    specification and plan
```

## Reproduce

Requires Python 3.11+, Stockfish 19 on the `PATH`, and a TypeSafe API key in
`TYPESAFE_API_KEY` (or `~/.config/typesafe/env`).

```sh
uv venv && uv pip install -e . pytest
scripts/download.sh                          # Lichess games and puzzles (CC0), opening book
.venv/bin/python -m pytest -q tests
.venv/bin/python -m jevchess.experiment report
.venv/bin/python -m jevchess.analysis
.venv/bin/python -m jevchess.page            # writes results/page/index.html
```

The caches in `results/cache/` hold every answer, so the report and the page rebuild
without new requests. To run again from scratch, move the caches aside and run
`python -m jevchess.pilot`, then `python -m jevchess.experiment moves grade|jev|llm`,
`puzzles jev|llm|capture|random`, `ladder`, `jev-games`, `llm-games`, `report`.

Cost of the whole study at list prices: Jev USD 0.46 (11,277 requests); Sonnet 5 USD 19.26 (1,131 calls).

## License

MIT. Lichess data is released under CC0.
