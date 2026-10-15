"""Discord-shaped weekly digest from a run snapshot: per item an action, a target, a one-line reason,
a `git revert <sha>` placeholder (filled in once the executor applies it) and a ✅ slot.

    python3 -m glia.digest --from fixtures/sample-run.json --dry

--dry prints and never posts. Posting is glia.relay's job (one message per item, see that module); there
is no posting code here on purpose.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DISCORD_LIMIT = 2000


def render(run: dict) -> tuple[str, dict]:
    items = run.get("items", [])
    date = run.get("date") or run.get("finished", "")[:10]
    model = run.get("proposer_model", "?")
    tickable = [i for i in items if i.get("ladder", {}).get("tier") != "never"]
    blocked = [i for i in items if i.get("ladder", {}).get("tier") == "never"]
    lines = [f"**Glia weekly digest — {date}** · run `{run.get('run_id', '?')}` · proposer `{model}` · mode propose-only",
             f"{len(tickable)} suggestion(s). React ✅ on a line to approve it; untouched lines are skipped. "
             "Each approved item becomes one commit on a glia/* branch, so one `git revert` undoes exactly one item.", ""]
    for n, it in enumerate(tickable, 1):
        sha = it.get("sha")
        revert = f"`git revert {sha}`" if sha else f"`git revert <sha-{it.get('id', n)}>`"
        extra = ""
        if it.get("ladder", {}).get("open_tasks"):
            extra = "  ⚠️ open tasks: " + ", ".join(f"[[{Path(t).stem}]]" for t in it["ladder"]["open_tasks"]) + " (✅ acks them)"
        args = it.get("args") or {}
        detail = ""
        if it["action"] == "fix_frontmatter" and (args.get("set") or args.get("unset")):
            detail = " → " + ", ".join(f"{k}: {v}" for k, v in (args.get("set") or {}).items()) + ("" if not args.get("unset") else " unset " + ",".join(args["unset"]))
        if it["action"] == "propose" and args.get("what"):
            detail = f" → {args['what']}"
        if it["action"] == "move_loose_file" and args.get("dest"):
            detail = f" → `{args['dest']}`"
        lines.append(f"{n}. ✅ **{it['action']}** `{it['target']}`{detail}")
        lines.append(f"   {it.get('reason', '')}{extra}")
        lines.append(f"   undo: {revert}")
    if blocked:
        lines += ["", f"⛔ {len(blocked)} not tick-able (the ladder never allows these; shown so you can see what Glia reached for):"]
        for it in blocked:
            lines.append(f"- {it['action']} `{it['target']}` — {', '.join(it.get('ladder', {}).get('flags', []))}: {it.get('reason', '')}")
    if run.get("summary"):
        lines += ["", f"_Glia's summary:_ {run['summary']}"]
    text = "\n".join(lines) + "\n"
    chunks, cur = [], ""
    for line in text.split("\n"):
        candidate = (cur + "\n" if cur else "") + line
        if len(candidate) > DISCORD_LIMIT - 20:
            chunks.append(cur + "\n")
            cur = line
        else:
            cur = candidate
    if cur:
        chunks.append(cur)
    return text, {"items": len(items), "tickable": len(tickable), "blocked": len(blocked), "posted": False, "chunks": chunks}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", required=True, help="run snapshot JSON (gateway finish() output or a fixture)")
    ap.add_argument("--dry", action="store_true", help="print, never post")
    ap.add_argument("--post", action="store_true", help="not implemented here: glia.relay posts")
    a = ap.parse_args(argv)
    if a.post:
        print("this module never posts; glia.relay does", file=sys.stderr)
        return 2
    if not a.dry:
        ap.error("--dry is the only mode")
    run = json.loads(Path(a.src).read_text())
    text, meta = render(run)
    for i, c in enumerate(meta["chunks"], 1):
        print(f"--- message {i}/{len(meta['chunks'])} ({len(c)} chars) ---")
        print(c, end="" if c.endswith("\n") else "\n")
    print(f"DIGEST-OK items={meta['items']} posted=no")
    return 0


if __name__ == "__main__":
    sys.exit(main())
