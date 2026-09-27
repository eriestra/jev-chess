# Jev chess benchmark: specification

## Question

How strong is Jev at chess when, for each move, it sees only the position and the list of legal moves, and is asked for the best move?

## What Jev receives

One TypeSafe `choice` request per move, to `jev-latest` (Jev 1.13.0 at the time of the run):

- `state`: the position. Piece placement, side to move, castling rights and en passant square. The board format is chosen in the pilot (FEN string, 8×8 text grid, or piece lists).
- `instructions`: `Choose the best move for White in this chess position.` (or Black).
- `criteria`: every legal move of the position, keyed by its SAN (for example `Nf3`, `exd5`, `O-O`, `e8=Q+`). Descriptions are either empty or a plain description of the move (chosen in the pilot). The legal-move list is complete: python-chess generates it, and a Choice accepts up to 255 options ([TypeSafe API](https://docs.typesafe.ai/api.md)), more than the 218 legal moves a chess position can have.

Nothing else: no move history, no evaluation, no search, no engine hints. The player plays Jev's highest-probability option (`choice`). Because every option is legal, Jev cannot make an illegal move; the test measures move selection only.

## Measures

1. **Game rating (Elo).** Games against players of known strength, fitted by maximum likelihood.
   - Anchor: Stockfish 19 with `UCI_LimitStrength` and `UCI_Elo = 1320`, its lowest setting. `UCI_Elo` is mapped to CCRL Blitz ratings, calibrated with the `8moves_v3.pgn` book at 120 s + 1 s ([Stockfish PR #4341](https://github.com/official-stockfish/Stockfish/pull/4341), [source](https://github.com/official-stockfish/Stockfish/blob/master/src/search.h)).
   - Reference players below 1320: Stockfish 1320 that plays a uniformly random legal move with probability 10%, 25%, 50% or 75%, the capture-first rule, and a fully random player. Each is rated from games against its neighbours, so the scale extends below the anchor.
   - Conditions: openings from `8moves_v3.pgn` ([official-stockfish/books](https://github.com/official-stockfish/books)), each played twice with colours reversed. The anchor runs on a 120 s + 1 s clock; the reference players on 10 s + 0.1 s, with a 40-game match between the two clocks. 1 thread, 16 MB hash. Jev and the LLM have no clock. Games end by the rules (mate, stalemate, insufficient material, threefold repetition, 50-move rule) or at 400 plies as a draw.
   - Fit: `P(A beats B) = 1 / (1 + 10^((R_B − R_A)/400))`, draws as half points, 2 virtual draws per pair (as in BayesElo), anchor fixed at 1320. 95% intervals from 500 bootstrap resamples of each pair's games.
2. **Move quality.** Positions sampled from rated Lichess games of January 2013 ([database.lichess.org](https://database.lichess.org/), CC0), one per game at a random ply between 8 and 120. Stockfish 19 at full strength scores every legal move at a fixed depth (MultiPV over all moves). For each chosen move: centipawn loss (evaluations clipped to ±1000), rank among legal moves, top-1 agreement, and Lichess accuracy and blunder labels: win% `= 50 + 50·(2/(1+e^(−0.00368208·cp)) − 1)`, accuracy `= 103.1668·e^(−0.04354·Δwin%) − 3.1669`, blunder at a drop of 0.3 in winning chances ([AccuracyPercent.scala](https://github.com/lichess-org/lila/blob/master/modules/analyse/src/main/AccuracyPercent.scala), [Advice.scala](https://github.com/lichess-org/lila/blob/master/modules/tree/src/main/Advice.scala)). The same grades are computed for the move the human played in the source game and for a uniformly random legal move.
3. **Puzzle rating.** 400 puzzles from the Lichess puzzle database ([database.lichess.org](https://database.lichess.org/#puzzles), CC0), 40 in each 200-point band from 600 to 2600, restricted to puzzles with rating deviation ≤ 80, ≥ 1000 plays and popularity ≥ 80. A puzzle is solved only if every solver move matches; a mating move is always accepted. Rating by maximum likelihood over puzzle ratings with the same logistic curve.

## Comparison players

- **Uniformly random legal move**: the floor.
- **Capture-first rule**: a mating move if one exists, else the most valuable capture, else a check, else a random move.
- **Human moves** from the source games, with the players' Lichess ratings of January 2013.
- **Claude Sonnet 5** through the Claude Code CLI with the same state, instruction and move list as text, default effort (extended thinking on), asked to reply with one move from the list. Cost is the CLI's `total_cost_usd`, computed at API list price.

## Cost

- Jev 1.13: USD 0.042 per million input tokens, output tokens free ([TypeSafe models](https://docs.typesafe.ai/models.md)). All requests and token counts are logged.
- Sonnet 5: list price as reported per call by the CLI.

## Reproducibility

Every Jev request and answer, every LLM reply, and every Stockfish analysis is cached in `results/cache/`. Games are stored as JSON lines with full PGN and per-move records (Jev confidence, top three options, tokens, latency). Seeds fix the position, puzzle and opening samples.
