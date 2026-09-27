"""Publish the results page to Almond by JSON-RPC. Credentials stay in ~/.almond-private.

  python -m jevchess.almond create     # new site owned by the saved account grant
  python -m jevchess.almond preview    # store results/page/index.html as a private preview
  python -m jevchess.almond activate   # promote the recorded preview
  python -m jevchess.almond context    # page slugs and live revisions
"""

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = Path.home() / ".almond-private"
SITE_FILE = PRIVATE / "jev-chess-site.json"
ENDPOINT = "https://almond.build/mcp"
INTENT = "Publish the results of the Jev chess benchmark (Elo, puzzles, move quality vs Stockfish) as a page"


def call(name: str, args: dict):
    r = httpx.post(ENDPOINT, timeout=120, headers={"accept": "application/json, text/event-stream"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    text = r.text
    if "text/event-stream" in r.headers.get("content-type", ""):
        text = "".join(l[5:] for l in text.splitlines() if l.startswith("data:"))
    payload = json.loads(text)
    if "error" in payload:
        raise RuntimeError(f"{name}: {payload['error']}")
    content = payload["result"].get("content", [{}])[0].get("text")
    try:
        parsed = json.loads(content) if content else payload["result"]
    except json.JSONDecodeError:
        parsed = content
    if payload["result"].get("isError"):
        raise RuntimeError(f"{name}: {parsed}")
    return parsed


def redact(d):
    if isinstance(d, dict):
        return {k: ("<redacted>" if "token" in k.lower() or k.lower() in ("writetoken", "secret") else redact(v)) for k, v in d.items()}
    if isinstance(d, list):
        return [redact(x) for x in d]
    return d


def main(cmd: str):
    html = (ROOT / "results/page/index.html").read_text()
    if cmd == "create":
        if SITE_FILE.exists():
            raise SystemExit(f"{SITE_FILE} exists; use preview/activate")
        authority = json.loads((PRIVATE / "corridor-account-authority.json").read_text())
        out = call("app_create", {
            "accountToken": authority["accountToken"], "name": "Jev at chess", "slug": "jev-chess", "html": html,
            "collection": {"key": "notes", "label": "Notes", "acceptsForms": False,
                           "fields": [{"key": "text", "label": "Text", "type": "longtext", "required": False}]},
        })
        SITE_FILE.write_text(json.dumps({**out, "endpoint": ENDPOINT, "accountAuthority": str(PRIVATE / "corridor-account-authority.json"),
                                         "source": str(ROOT / "results/page/index.html")}, indent=1))
        SITE_FILE.chmod(0o600)
        print(json.dumps(redact(out), indent=1))
        return
    site = json.loads(SITE_FILE.read_text())
    args = {"siteId": site["siteId"], "writeToken": site["writeToken"]}
    if cmd == "context":
        print(json.dumps(redact(call("site_context", args)), indent=1)[:4000])
    elif cmd == "preview":
        out = call("page_publish", {**args, "slug": "index", "html": html, "activate": False, "intent": INTENT})
        (PRIVATE / "jev-chess-preview.json").write_text(json.dumps(out, indent=1))
        print(json.dumps(redact(out), indent=1))
    elif cmd == "activate":
        prev = json.loads((PRIVATE / "jev-chess-preview.json").read_text())
        a = {**args, "slug": "index", "revision": prev.get("revision"), "intent": INTENT}
        if prev.get("baseRevision"):
            a["baseRevision"] = prev["baseRevision"]
        print(json.dumps(redact(call("page_activate", a)), indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
