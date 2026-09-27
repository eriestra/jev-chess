#!/bin/sh
# Downloads the public data sets this benchmark reads (Lichess data is CC0).
set -e
cd "$(dirname "$0")/../data"
curl -sO https://database.lichess.org/standard/lichess_db_standard_rated_2013-01.pgn.zst
curl -sO https://database.lichess.org/lichess_db_puzzle.csv.zst
curl -sLO https://github.com/official-stockfish/books/raw/master/8moves_v3.pgn.zip
unzip -oq 8moves_v3.pgn.zip
