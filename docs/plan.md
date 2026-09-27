# Jev chess benchmark: plan

## Stages

| # | Stage | Output | Status |
|---|---|---|---|
| 1 | Pilot: 100 Lichess positions × 6 input variants (FEN / grid / piece lists × bare SAN / described moves), graded at depth 16; 20 repeated requests; one test game | `results/pilot/report.json` | done |
| 2 | Choose the input variant (lowest average centipawn loss) | `experiment.json` | done: grid + described moves |
| 3 | Move quality on 559 held-out positions, graded at depth 12 | `results/moves_jev.json` | done |
| 4 | Puzzle rating on 400 stratified Lichess puzzles; controls: capture-first rule, exact random expectation | `results/puzzles_*.json` | done |
| 5 | Sonnet 5 contrast: 100 positions, 150 puzzles, 20 games against the Stockfish 1320 anchor at 120+1 | `results/*_llm.*` | done |
| 6 | Rating ladder below the 1320 anchor: 40 games between the anchor (120+1) and Stockfish 1320 at 10+0.1, then 10 pairs × 40 games at 10+0.1 | `results/games/ladder_*.jsonl` | done |
| 7 | Jev games: 60 games each against Random, Capture-first and Stockfish 1320 at 75% and 50% random moves | `results/games/jev_vs_*.jsonl` | done |
| 8 | Fit ratings with bootstrap intervals; build the results page | `results/summary.json`, page | done |
| 9 | Publish the page on Almond; publish the source on GitHub | [sites.almond.build/jev-chess](https://sites.almond.build/jev-chess/), [github.com/eriestra/jev-chess](https://github.com/eriestra/jev-chess) | done |

## Decisions taken from the pilot

- **Input variant:** grid board with described moves. The six variants did not differ at 95% confidence (paired bootstrap on centipawn loss), so the choice follows the best point estimate.
- **Grading depth 12 for the held-out set.** Depth 16 over every legal move took about 9 s per position per core; depth 12 keeps the full run within the machine's budget. The pilot positions are graded at both depths to report agreement.
- **Ladder below 1320.** A test game and the pilot showed Jev far below Stockfish's lowest setting. Rungs made of Stockfish 1320 with 25/50/75% random moves, plus Random and the capture-first rule, carry the scale down from the anchor.
- **Fast clock below the anchor.** At `UCI_Elo` 1320 Stockfish picks its move from a depth-1 search, so the reference players run on 10 + 0.1 to keep games short. A 40-game match against the anchor on 120 + 1 measures the difference (the fast-clock player rates about 100 points lower).
- **A 10% rung.** Stockfish 1320 beat its 25%-random version 39–1, too lopsided to rate well; a 10%-random player between them tightens that link.
- **Capture-first control.** Jev picked a capture in 61–70% of pilot positions (7% of legal moves are captures), so a rule that only prefers mates, big captures and checks is measured alongside it.

## Compute and cost budget

- Jev: about 1,000 input tokens per move at USD 0.042 per million, so every Jev request in the project costs well under USD 1 in total.
- Sonnet 5: about USD 0.019 per move at list price; the 100 positions, 150 puzzles and 20 games cost USD 19.26 in total.
- Stockfish: 1 thread per engine; the anchor on 120 + 1, reference players on 10 + 0.1; 4–6 games in parallel on an 8-core machine.
