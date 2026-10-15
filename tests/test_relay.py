"""Discord approvals: a run snapshot becomes one Discord message per tick-able item; the owner's ✅
becomes an executor-shaped approval on disk. Nothing in this module may execute, merge or post on its own.
Discord is faked: `api` records calls and serves the reactions the test plants."""
import json
import shutil
from pathlib import Path

import pytest

from glia.relay import Digests

fixture = pytest.fixture


OWNER = "100000000000000001"
SAMPLE = Path("fixtures/sample-run.json")


class FakeDiscord:
    def __init__(self):
        self.calls = []
        self.posts = []
        self.reactions = {}          # msg id -> {emoji: [user ids]}
        self.n = 0

    def post(self, ch, text, ping=False):
        self.n += 1
        mid = f"m{self.n}"
        self.posts.append({"id": mid, "ch": ch, "text": text, "ping": ping})
        return {"id": mid}

    def api(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET" and "/reactions/" in path:
            mid = path.split("/messages/")[1].split("/")[0]
            emoji = path.split("/reactions/")[1].split("?")[0]
            from urllib.parse import unquote
            return [{"id": u} for u in self.reactions.get(mid, {}).get(unquote(emoji), [])]
        return {}

    def seeded(self):
        return [p.split("/messages/")[1].split("/")[0] for m, p, _ in self.calls if m == "PUT" and p.endswith("/@me")]


@fixture
def state(tmp_path):
    (tmp_path / "runs").mkdir()
    shutil.copy(SAMPLE, tmp_path / "runs" / "r-sample-2026-10-15.json")
    return tmp_path


def sample():
    return json.loads(SAMPLE.read_text())


def test_new_snapshot_posts_header_then_one_message_per_tickable_item_with_tick_seeded(state):
    d, dc = Digests(state), FakeDiscord()
    run = sample()
    tickable = [i for i in run["items"] if i["ladder"]["tier"] != "never"]
    out = d.poll(dc.api, dc.post, OWNER, channel="C1")
    assert out["posted_runs"] == ["r-sample-2026-10-15"]
    assert dc.posts[0]["text"].startswith("**Glia weekly digest")
    item_msgs = dc.posts[1:1 + len(tickable)]
    for it, m in zip(tickable, item_msgs):
        assert it["action"] in m["text"] and it["target"] in m["text"] and it["reason"] in m["text"]
        assert "✅" in m["text"] and "git revert" in m["text"]
        assert m["id"] in dc.seeded(), "the bot pre-reacts ✅ on every tick-able line"
    blocked = [p for p in dc.posts if "⛔" in p["text"]]
    assert len(blocked) == 1 and "Finances/bill-ledger.md" in blocked[0]["text"]
    assert blocked[0]["id"] not in dc.seeded(), "never-tier lines get no ✅"
    assert dc.posts[1]["ping"] is False and dc.posts[-1]["ping"] is True, "one owner ping, on the last message"
    n = len(dc.posts)
    d.poll(dc.api, dc.post, OWNER, channel="C1")
    assert len(dc.posts) == n, "a snapshot is posted once"


def test_open_tasks_are_on_the_line(state):
    d, dc = Digests(state), FakeDiscord()
    d.poll(dc.api, dc.post, OWNER, channel="C1")
    line = next(p for p in dc.posts if "Projects/token-broker" in p["text"])
    assert "token-broker-kickoff" in line["text"] and "secrets-cutover" in line["text"]


def test_owner_tick_writes_an_executor_shaped_approval_and_runs_nothing(state):
    d, dc = Digests(state), FakeDiscord()
    d.poll(dc.api, dc.post, OWNER, channel="C1")
    msg = next(p for p in dc.posts if "Projects/token-broker" in p["text"])
    dc.reactions[msg["id"]] = {"✅": ["bot", OWNER]}
    out = d.poll(dc.api, dc.post, OWNER, channel="C1")
    assert out["approved"] == 1 and out["executed"] == 0
    appr = json.loads((state / "approvals" / "r-sample-2026-10-15.json").read_text())
    assert len(appr) == 1
    it = appr[0]
    assert it["action"] == "archive" and it["target"] == "Projects/token-broker" and it["id"] == "p-2b3c4d5e"
    assert it["ack_open_tasks"] == ["Tasks/token-broker-kickoff.md", "Tasks/secrets-cutover.md"]
    assert it["reason"] and it["approved_by"] == "owner-discord" and it["approved_at"]
    assert "args" not in it and "ladder" not in it, "flattened like Executor.apply_item wants"
    # idempotent: the same ✅ is not recorded twice
    d.poll(dc.api, dc.post, OWNER, channel="C1")
    assert len(json.loads((state / "approvals" / "r-sample-2026-10-15.json").read_text())) == 1


def test_fix_frontmatter_approval_carries_set_and_unset_flat(state):
    d, dc = Digests(state), FakeDiscord()
    d.poll(dc.api, dc.post, OWNER, channel="C1")
    msg = next(p for p in dc.posts if "Projects/push-alerts/push-alerts.md" in p["text"])
    dc.reactions[msg["id"]] = {"✅": [OWNER]}
    d.poll(dc.api, dc.post, OWNER, channel="C1")
    appr = json.loads((state / "approvals" / "r-sample-2026-10-15.json").read_text())
    assert appr[0]["set"] == {"status": "live"}


def test_someone_elses_tick_is_ignored(state):
    d, dc = Digests(state), FakeDiscord()
    d.poll(dc.api, dc.post, OWNER, channel="C1")
    msg = next(p for p in dc.posts if "Projects/legacy-tracker" in p["text"])
    dc.reactions[msg["id"]] = {"✅": ["bot", "999"]}
    out = d.poll(dc.api, dc.post, OWNER, channel="C1")
    assert out["approved"] == 0 and not (state / "approvals").exists()


def test_never_tier_item_cannot_be_approved_even_with_a_tick(state):
    d, dc = Digests(state), FakeDiscord()
    d.poll(dc.api, dc.post, OWNER, channel="C1")
    blocked = next(p for p in dc.posts if "⛔" in p["text"])
    dc.reactions[blocked["id"]] = {"✅": [OWNER]}
    out = d.poll(dc.api, dc.post, OWNER, channel="C1")
    assert out["approved"] == 0 and not (state / "approvals").exists()


def test_relay_module_never_imports_the_executor():
    import glia.relay as r
    src = Path(r.__file__).read_text()
    assert "glia.apply" not in src and "Executor" not in src and "subprocess" not in src
