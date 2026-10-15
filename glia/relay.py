"""Approvals over Discord: post each run snapshot once to a channel (one message per tick-able item,
✅ pre-seeded), then turn the owner's ✅ into executor-shaped approvals on disk.

This module posts and records. It never executes, merges, pushes or imports the executor: the approvals
file (<state>/approvals/<run_id>.json) is the hand-off to the approval executor (`--items` mode of the apply module), which a
separate step runs. Until then a ✅ changes a JSON file and nothing else.

A bot loop calls `Digests(state).poll(api, post, owner_id, channel=...)` on every tick; `api`/`post` are the
bot's own Discord helpers, so one bot, one token path, and this file has no network code of its own.
"""
from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

TICK = "✅"
DECISION_DAYS = 14          # a line not ticked in two weeks is skipped for good


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


GROUPS = [("archive", "Archive"), ("park", "Park"), ("fix_frontmatter", "Frontmatter"), ("collapse_handoff", "Handoffs"),
          ("archive_task", "Tasks"), ("move_loose_file", "Loose files"), ("propose", "Other suggestions")]
LINE_MAX = 160


def short_name(path: str) -> str:
    """Projects/<slug>/… → slug (plus the file when it is not the folder note); Tasks/x.md → x."""
    parts = path.strip("/").split("/")
    if parts[0] == "Projects" and len(parts) >= 2:
        if len(parts) == 2 or parts[-1] == f"{parts[1]}.md":
            return parts[1]
        return f"{parts[1]}/{Path(parts[-1]).stem}"
    return Path(parts[-1]).stem


def clip(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:(") + "…"


KNOWN_ACTIONS = {k for k, _ in GROUPS}


def normalise(it: dict) -> dict:
    """An action that is not one of the tools is a free-form proposal whose text landed in `action`
    (an older record shape). Render it as propose + what."""
    if it.get("action") in KNOWN_ACTIONS:
        return it
    return {**it, "action": "propose", "args": {**(it.get("args") or {}), "what": it.get("action", "")}}


def item_line(it: dict) -> str:
    """One channel line: ✅ action **name** → change — reason (clipped). Fits a phone screen."""
    it = normalise(it)
    args = it.get("args") or {}
    change = ""
    if it["action"] == "fix_frontmatter" and (args.get("set") or args.get("unset")):
        change = " → " + ", ".join(f"{k}: {v}" for k, v in (args.get("set") or {}).items())
        if args.get("unset"):
            change += " unset " + ",".join(args["unset"])
    elif it["action"] == "move_loose_file" and args.get("dest"):
        change = f" → {short_name(args['dest'])}"
    elif it["action"] == "propose" and args.get("what"):
        change = f" — {clip(args['what'], 70)}"
    tasks = it.get("ladder", {}).get("open_tasks") or []
    tail = f" ⚠️ {len(tasks)} open task{'s' if len(tasks) != 1 else ''}" if tasks else ""
    head = f"✅ {it['action']} **{short_name(it['target'])}**{change}"
    room = LINE_MAX - len(head) - len(tail) - 3
    reason = f" — {clip(it.get('reason', ''), room)}" if room > 20 and it.get("reason") else ""
    return head + reason + tail


def detail_lines(n: int, it: dict) -> str:
    it = normalise(it)
    args = it.get("args") or {}
    extra = ""
    if it.get("ladder", {}).get("open_tasks"):
        extra = "\n   ⚠️ open tasks point at it: " + ", ".join(f"[[{Path(t).stem}]]" for t in it["ladder"]["open_tasks"]) + " (your ✅ acks them)"
    what = f"\n   {args['what']}" if it["action"] == "propose" and args.get("what") else ""
    sha = it.get("sha")
    revert = f"`git revert {sha}`" if sha else f"`git revert <sha-{it.get('id', n)}>`"
    return f"{n}. **{it['action']}** `{it['target']}`{what}\n   {it.get('reason', '')}{extra}\n   undo: {revert}"


def approval_from(it: dict) -> dict:
    """Flatten a proposal into what the approval executor's apply_item takes: id, action, target, reason, args at top level,
    plus the open tasks the owner's ✅ acknowledged."""
    out = {"id": it["id"], "action": it["action"], "target": it["target"], "reason": it.get("reason", "")}
    out.update(it.get("args") or {})
    out["ack_open_tasks"] = list(it.get("ladder", {}).get("open_tasks") or [])
    return out


class Digests:
    def __init__(self, state: Path):
        self.state = Path(state)
        self.runs = self.state / "runs"
        self.approvals = self.state / "approvals"
        self.index_path = self.state / "discord.json"
        self.index = json.loads(self.index_path.read_text()) if self.index_path.exists() else {"runs": {}}

    def _save(self) -> None:
        self.index_path.write_text(json.dumps(self.index, indent=1))

    # ------------------------------------------------------------------ posting
    def _post_run(self, run: dict, api, post, channel: str) -> dict:
        items = run.get("items", [])
        tickable = [i for i in items if i.get("ladder", {}).get("tier") != "never"]
        blocked = [i for i in items if i.get("ladder", {}).get("tier") == "never"]
        date = run.get("date") or run.get("finished", "")[:10]
        n = len(tickable)
        head = (f"**Glia · {date} · {n} suggestion{'s' if n != 1 else ''}** · tick ✅ on a line to approve it · "
                f"full reasons in the thread · untouched lines expire in {DECISION_DAYS} days · a ✅ records an approval, nothing runs")
        rec = {"posted_at": _now(), "channel": channel, "messages": {}, "decided": {}}
        hm = post(channel, head, ping=True) or {}
        thread = None
        if hm.get("id"):
            t = api("POST", f"/channels/{channel}/messages/{hm['id']}/threads",
                    {"name": f"Glia {date} · details", "auto_archive_duration": 10080}) or {}
            thread = t.get("id")
        numbered = list(enumerate(tickable, 1))
        for key, title in GROUPS:
            group = [(k, it) for k, it in numbered if normalise(it)["action"] == key]
            if not group:
                continue
            post(channel, f"**{title}** ({len(group)})")
            for _, it in group:
                m = post(channel, item_line(it)) or {}
                if m.get("id"):
                    api("PUT", f"/channels/{channel}/messages/{m['id']}/reactions/{urllib.parse.quote(TICK)}/@me")
                    rec["messages"][m["id"]] = it["id"]
        if thread:
            details = [detail_lines(k, it) for k, it in numbered]
            if blocked:
                details.append(f"⛔ {len(blocked)} not tick-able (the ladder never allows these; shown so you can see what Glia reached for):\n"
                               + "\n".join(f"- {it['action']} `{it['target']}` — {', '.join(it.get('ladder', {}).get('flags', []))}: {it.get('reason', '')}" for it in blocked))
            if run.get("summary"):
                details.append(f"_Glia's summary:_ {run['summary']}")
            details.append(f"run `{run.get('run_id', '?')}` · proposer `{run.get('proposer_model', '?')}` · mode propose-only")
            for block in details:
                post(thread, block)
        rec["items"] = {it["id"]: it for it in tickable}
        return rec

    # ------------------------------------------------------------------ ticks
    def _record(self, run_id: str, it: dict, by: str) -> None:
        self.approvals.mkdir(parents=True, exist_ok=True)
        p = self.approvals / f"{run_id}.json"
        cur = json.loads(p.read_text()) if p.exists() else []
        if any(a["id"] == it["id"] for a in cur):
            return
        a = approval_from(it)
        a["approved_by"], a["approved_at"] = by, _now()
        cur.append(a)
        p.write_text(json.dumps(cur, indent=1))

    def poll(self, api, post, owner: str, channel: str) -> dict:
        out = {"posted_runs": [], "approved": 0, "executed": 0}
        self.runs.mkdir(parents=True, exist_ok=True)
        for p in sorted(self.runs.glob("*.json")):
            run = json.loads(p.read_text())
            rid = run.get("run_id") or p.stem
            if rid in self.index["runs"]:
                continue
            self.index["runs"][rid] = self._post_run(run, api, post, channel)
            out["posted_runs"].append(rid)
            self._save()
        for rid, rec in self.index["runs"].items():
            posted = datetime.strptime(rec["posted_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - posted).days > DECISION_DAYS:
                continue
            for mid, pid in rec["messages"].items():
                if pid in rec["decided"]:
                    continue
                users = api("GET", f"/channels/{rec['channel']}/messages/{mid}/reactions/{urllib.parse.quote(TICK)}?limit=25") or []
                if not any(u.get("id") == owner for u in users):
                    continue
                it = rec["items"].get(pid)
                if not it or it.get("ladder", {}).get("tier") == "never":
                    continue
                self._record(rid, it, "owner-discord")
                rec["decided"][pid] = _now()
                out["approved"] += 1
                self._save()
        return out
