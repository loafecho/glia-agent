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


def item_line(n: int, it: dict) -> str:
    args = it.get("args") or {}
    detail = ""
    if it["action"] == "fix_frontmatter" and (args.get("set") or args.get("unset")):
        detail = " → " + ", ".join(f"{k}: {v}" for k, v in (args.get("set") or {}).items())
        if args.get("unset"):
            detail += " unset " + ",".join(args["unset"])
    if it["action"] == "move_loose_file" and args.get("dest"):
        detail = f" → `{args['dest']}`"
    extra = ""
    if it.get("ladder", {}).get("open_tasks"):
        extra = "\n⚠️ open tasks point at it: " + ", ".join(f"[[{Path(t).stem}]]" for t in it["ladder"]["open_tasks"]) + " (your ✅ acks them)"
    sha = it.get("sha")
    revert = f"`git revert {sha}`" if sha else f"`git revert <sha-{it.get('id', n)}>`"
    return f"{n}. {TICK} **{it['action']}** `{it['target']}`{detail}\n{it.get('reason', '')}{extra}\nundo: {revert}"


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
        head = (f"**Glia weekly digest — {date}** · run `{run.get('run_id', '?')}` · proposer `{run.get('proposer_model', '?')}` · mode propose-only\n"
                f"{len(tickable)} suggestion(s). Tap {TICK} on a line to approve it; untouched lines are skipped after {DECISION_DAYS} days. "
                "Each approved item becomes one commit on a glia/* branch, so one `git revert` undoes exactly one item. "
                "A ✅ records an approval; nothing runs from it.")
        rec = {"posted_at": _now(), "channel": channel, "messages": {}, "decided": {}}
        post(channel, head)
        last_is_ping = not blocked and not run.get("summary")
        for n, it in enumerate(tickable, 1):
            m = post(channel, item_line(n, it), ping=(last_is_ping and n == len(tickable))) or {}
            if m.get("id"):
                api("PUT", f"/channels/{channel}/messages/{m['id']}/reactions/{urllib.parse.quote(TICK)}/@me")
                rec["messages"][m["id"]] = it["id"]
        tail = []
        if blocked:
            tail.append(f"⛔ {len(blocked)} not tick-able (the ladder never allows these; shown so you can see what Glia reached for):\n"
                        + "\n".join(f"- {it['action']} `{it['target']}` — {', '.join(it.get('ladder', {}).get('flags', []))}: {it.get('reason', '')}" for it in blocked))
        if run.get("summary"):
            tail.append(f"_Glia's summary:_ {run['summary']}")
        if tail:
            post(channel, "\n\n".join(tail), ping=True)
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
