"""Prepare the caches for publication: fold WAL files in, drop CLI session identifiers.

The sqlite caches let anyone re-run every analysis without new Jev, LLM or
Stockfish requests. Claude Code CLI outputs carry per-call session and request ids;
those are removed.
"""

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "results/cache"
DROP = ("session_id", "uuid")


def main():
    llm = sqlite3.connect(CACHE / "llm.sqlite")
    rows = llm.execute("select key, response from llm").fetchall()
    for key, resp in rows:
        d = json.loads(resp)
        if any(k in d for k in DROP):
            for k in DROP:
                d.pop(k, None)
            llm.execute("update llm set response=? where key=?", (json.dumps(d), key))
    llm.commit()
    llm.close()
    for name in ("jev", "llm", "grades"):
        db = sqlite3.connect(CACHE / f"{name}.sqlite")
        db.execute("pragma wal_checkpoint(TRUNCATE)")
        db.execute("vacuum")
        db.close()
        print(name, (CACHE / f"{name}.sqlite").stat().st_size)


if __name__ == "__main__":
    main()
