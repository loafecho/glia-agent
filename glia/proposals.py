"""Proposal store: what the agent side records instead of writing. JSONL under <state>/proposals.jsonl,
grouped by run; finish() snapshots a run to <state>/runs/<run_id>.json for the digest."""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, state: Path, run_id: str | None = None):
        self.state = Path(state)
        self.state.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or f"r-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
        self.path = self.state / "proposals.jsonl"

    def record(self, action: str, target: str, args: dict, reason: str, ladder: dict, status: str = "proposed") -> dict:
        p = {"id": f"p-{secrets.token_hex(4)}", "run_id": self.run_id, "ts": now_iso(), "action": action,
             "target": target, "args": args, "reason": reason, "ladder": ladder, "status": status}
        with open(self.path, "a") as f:
            f.write(json.dumps(p) + "\n")
        return p

    def for_run(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [p for p in (json.loads(line) for line in self.path.read_text().splitlines() if line.strip()) if p["run_id"] == self.run_id]

    def finish(self, summary: str, proposer_model: str) -> Path:
        runs = self.state / "runs"
        runs.mkdir(exist_ok=True)
        out = runs / f"{self.run_id}.json"
        out.write_text(json.dumps({"run_id": self.run_id, "finished": now_iso(), "summary": summary,
                                   "proposer_model": proposer_model, "mode": "propose-only",
                                   "items": self.for_run()}, indent=1))
        return out
