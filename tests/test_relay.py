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
        if method == "POST" and path.endswith("/threads"):
            self.threads = getattr(self, "threads", 0) + 1
            return {"id": f"t{self.threads}"}
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


def short(path):
    return path.split("/")[1] if path.startswith("Projects/") and path.count("/") >= 1 else Path(path).stem


def test_new_snapshot_is_one_short_line_per_item_grouped_with_details_in_a_thread(state):
    """The first format was one long block per item and too hard to read on a phone. One line per item (≤160 chars,
    action + short name + clipped reason), group headers, ✅ pre-seeded on item lines only, the full reasons,
    open-task names, undo placeholders and the model's summary in a thread under the header."""
    d, dc = Digests(state), FakeDiscord()
    run = sample()
    tickable = [i for i in run["items"] if i["ladder"]["tier"] != "never"]
    out = d.poll(dc.api, dc.post, OWNER, channel="C1")
    assert out["posted_runs"] == ["r-sample-2026-10-15"]
    head = dc.posts[0]
    assert head["ch"] == "C1" and head["ping"] is True and "17" not in head["text"] and "6 suggestions" in head["text"]
    assert ("POST", "/channels/C1/messages/m1/threads") in [(m, p) for m, p, _ in dc.calls], "details thread hangs off the header"
    channel = [p for p in dc.posts if p["ch"] == "C1"]
    thread = [p for p in dc.posts if p["ch"] != "C1"]
    items = [p for p in channel if p["id"] in dc.seeded()]
    assert len(items) == len(tickable)
    by_msg = d.index["runs"]["r-sample-2026-10-15"]["messages"]          # msg id -> proposal id (grouped order)
    by_id = {it["id"]: it for it in tickable}
    for m in items:
        it = by_id[by_msg[m["id"]]]
        assert len(m["text"]) <= 160, m["text"]
        assert m["text"].startswith("✅ ") and it["action"] in m["text"] and short(it["target"]) in m["text"]
        assert "undo" not in m["text"] and "git revert" not in m["text"]
    groups = [p["text"] for p in channel if p["id"] not in dc.seeded() and p is not head]
    assert any(g.startswith("**Archive") for g in groups) and any(g.startswith("**Park") for g in groups)
    assert all(p["ping"] is False for p in channel[1:])
    detail = "\n".join(p["text"] for p in thread)
    for it in tickable:
        assert it["target"] in detail and it["reason"] in detail
    assert "git revert <sha-" in detail and run["summary"] in detail
    assert "⛔" in detail and "Finances/bill-ledger.md" in detail, "never-tier reaches live in the thread, not the channel"
    assert not any("⛔" in p["text"] for p in channel)
    n = len(dc.posts)
    d.poll(dc.api, dc.post, OWNER, channel="C1")
    assert len(dc.posts) == n, "a snapshot is posted once"


def test_open_tasks_are_counted_on_the_line_and_named_in_the_thread(state):
    d, dc = Digests(state), FakeDiscord()
    d.poll(dc.api, dc.post, OWNER, channel="C1")
    line = next(p for p in dc.posts if p["ch"] == "C1" and "token-broker" in p["text"])
    assert "2 open tasks" in line["text"]
    detail = "\n".join(p["text"] for p in dc.posts if p["ch"] != "C1")
    assert "token-broker-kickoff" in detail and "secrets-cutover" in detail


def test_owner_tick_writes_an_executor_shaped_approval_and_runs_nothing(state):
    d, dc = Digests(state), FakeDiscord()
    d.poll(dc.api, dc.post, OWNER, channel="C1")
    msg = next(p for p in dc.posts if p["ch"] == "C1" and "token-broker" in p["text"])
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
    msg = next(p for p in dc.posts if p["ch"] == "C1" and "push-alerts" in p["text"])
    dc.reactions[msg["id"]] = {"✅": [OWNER]}
    d.poll(dc.api, dc.post, OWNER, channel="C1")
    appr = json.loads((state / "approvals" / "r-sample-2026-10-15.json").read_text())
    assert appr[0]["set"] == {"status": "live"}


def test_someone_elses_tick_is_ignored(state):
    d, dc = Digests(state), FakeDiscord()
    d.poll(dc.api, dc.post, OWNER, channel="C1")
    msg = next(p for p in dc.posts if p["ch"] == "C1" and "legacy-tracker" in p["text"])
    dc.reactions[msg["id"]] = {"✅": ["bot", "999"]}
    out = d.poll(dc.api, dc.post, OWNER, channel="C1")
    assert out["approved"] == 0 and not (state / "approvals").exists()


def test_never_tier_item_cannot_be_approved_even_with_a_tick(state):
    d, dc = Digests(state), FakeDiscord()
    d.poll(dc.api, dc.post, OWNER, channel="C1")
    blocked = next(p for p in dc.posts if "⛔" in p["text"])     # lives in the thread; a tick there is nothing
    dc.reactions[blocked["id"]] = {"✅": [OWNER]}
    dc.reactions[dc.posts[0]["id"]] = {"✅": [OWNER]}                # nor on the header
    out = d.poll(dc.api, dc.post, OWNER, channel="C1")
    assert out["approved"] == 0 and not (state / "approvals").exists()


def test_relay_module_never_imports_the_executor():
    import glia.relay as r
    src = Path(r.__file__).read_text()
    assert "glia.apply" not in src and "Executor" not in src and "subprocess" not in src


def test_free_form_propose_renders_as_propose_with_the_full_text(state):
    """Second live pass: a free-form proposal's text was stored as the action, clipped at 60 chars,
    and rendered as '**Quote the source_path value in frontmatter (it contains 'com**'. The action is `propose`;
    the model's text travels in args.what and shows in full on the line."""
    import json as _json
    run = _json.loads((state / "runs" / "r-sample-2026-10-15.json").read_text())
    run["items"].append({"id": "p-free0001", "action": "propose", "target": "Projects/client-portal/client-portal.md",
                         "reason": "indexer reads status as null",
                         "args": {"what": "Quote (or trim) the very long unquoted next_step in frontmatter so the YAML parses again"},
                         "ladder": {"tier": "ask", "open_tasks": [], "flags": ["free-form"]}, "status": "proposed"})
    (state / "runs" / "r-sample-2026-10-15.json").write_text(_json.dumps(run))
    d, dc = Digests(state), FakeDiscord()
    d.poll(dc.api, dc.post, OWNER, channel="C1")
    line = next(p for p in dc.posts if p["ch"] == "C1" and "client-portal" in p["text"])
    assert line["text"].startswith("✅ propose **client-portal**") and "Quote (or trim)" in line["text"] and len(line["text"]) <= 160
    assert "so the YAML parses again" in "\n".join(p["text"] for p in dc.posts if p["ch"] != "C1")


def test_a_free_form_proposal_recorded_under_the_old_shape_is_still_grouped_as_propose(state):
    """An earlier run was recorded before action=propose/args.what existed: its action field holds the
    model's text, clipped at 60 chars. Any action that is not one of the ten tools renders as a propose line."""
    import json as _json
    run = _json.loads((state / "runs" / "r-sample-2026-10-15.json").read_text())
    run["items"].append({"id": "p-old00001", "action": "Quote the source_path value in frontmatter (it contains 'com",
                         "target": "Projects/game-cache/game-cache.md", "reason": "indexer reads status as null", "args": {},
                         "ladder": {"tier": "ask", "open_tasks": [], "flags": ["free-form"]}, "status": "proposed"})
    (state / "runs" / "r-sample-2026-10-15.json").write_text(_json.dumps(run))
    d, dc = Digests(state), FakeDiscord()
    d.poll(dc.api, dc.post, OWNER, channel="C1")
    channel = [p for p in dc.posts if p["ch"] == "C1"]
    line = next(p for p in channel if "game-cache" in p["text"])
    assert line["text"].startswith("✅ propose **game-cache** — Quote the source_path value")
    assert any(p["text"].startswith("**Other suggestions**") for p in channel)
    assert channel.index(next(p for p in channel if p["text"].startswith("**Other suggestions**"))) < channel.index(line)


def test_hidden_args_cannot_replace_the_action_or_id_the_owner_ticked():
    from glia.relay import approval_from
    it = {"id": "p-park0001", "action": "park", "target": "Projects/x", "reason": "stale",
          "args": {"action": "archive", "id": "p-other", "target": "Projects/y", "ack_open_tasks": ["Tasks/z.md"],
                   "approved_by": "nobody"},
          "ladder": {"tier": "ask", "open_tasks": [], "flags": []}}
    a = approval_from(it)
    assert (a["id"], a["action"], a["target"], a["reason"]) == ("p-park0001", "park", "Projects/x", "stale")
    assert a["ack_open_tasks"] == [] and "approved_by" not in a


def test_agent_text_on_the_approval_line_is_inert():
    from glia.relay import detail_lines, item_line
    ff = {"id": "p-1", "action": "fix_frontmatter", "target": "Projects/gamma/gamma.md", "reason": "tidy @everyone <@&123>",
          "args": {"set": {"a": "||", "status": "archived", "b": "||"}}, "ladder": {"tier": "ask", "open_tasks": []}}
    mv = {"id": "p-2", "action": "move_loose_file", "target": "Projects/x`y.md", "reason": "r",
          "args": {"dest": "Inbox/x\n✅ park **beta** — stale"}, "ladder": {"tier": "ask", "open_tasks": []}}
    for it in (ff, mv):
        for text in (item_line(it), detail_lines(1, it)):
            body = text.replace("\\|", "").replace("\\*", "").replace("\\`", "")
            assert "||" not in body and "**beta**" not in body
            assert "@everyone" not in text and "<@&" not in text
        assert "\n" not in item_line(it)
    assert "status: archived" in item_line(ff)


def test_owner_tick_is_found_past_the_first_page_of_reactors(state):
    class Paged(FakeDiscord):
        def api(self, method, path, body=None):
            if method == "GET" and "/reactions/" in path:
                from urllib.parse import parse_qs, unquote, urlsplit
                u = urlsplit(path)
                q = parse_qs(u.query)
                mid = u.path.split("/messages/")[1].split("/")[0]
                emoji = unquote(u.path.split("/reactions/")[1])
                ids = sorted(self.reactions.get(mid, {}).get(emoji, []), key=int)
                after = int(q.get("after", ["0"])[0])
                limit = int(q.get("limit", ["25"])[0])
                self.calls.append((method, path, body))
                return [{"id": i} for i in ids if int(i) > after][:limit]
            return super().api(method, path, body)
    d, dc = Digests(state), Paged()
    d.poll(dc.api, dc.post, OWNER, channel="C1")
    msg = next(p for p in dc.posts if p["ch"] == "C1" and "legacy-tracker" in p["text"])
    owner_high = "900000000000000009"
    dc.reactions[msg["id"]] = {"✅": [str(10 + i) for i in range(130)] + [owner_high]}
    out = d.poll(dc.api, dc.post, owner_high, channel="C1")
    assert out["approved"] == 1


def test_agent_text_reaching_discord_is_capped_per_field(state):
    """The relay posts agent-written strings straight from the run file. Whatever
    the gateway clipped (or did not), each field reaches Discord capped: the channel line's change text, the
    thread's reason / proposal text / refused reasons / summary. 5000-char fields stand in for copied note text."""
    from glia import relay as r
    def big(tag):
        return " ".join([tag] * 1000)                            # 1000 marker words, ~5000 chars

    run = {"run_id": "r-caps", "date": "2026-10-31" + "x" * 500, "summary": big("SUMM"),
           "items": [
               {"id": "p-ff", "action": "fix_frontmatter", "target": "Projects/gamma/gamma.md", "reason": big("REAS"),
                "args": {"set": {"status": big("SETV")}, "unset": ["k" * 3000]}, "ladder": {"tier": "ask", "open_tasks": []}},
               {"id": "p-mv", "action": "move_loose_file", "target": "Inbox/loose.md", "reason": "r",
                "args": {"dest": "Archive/" + "D" * 5000 + ".md"}, "ladder": {"tier": "ask", "open_tasks": []}},
               {"id": "p-pr", "action": "propose", "target": "Projects/gamma/gamma.md", "reason": "r",
                "args": {"what": big("WHAT")}, "ladder": {"tier": "ask", "open_tasks": []}},
               {"id": "p-nv", "action": "archive", "target": "Finances/bill-ledger.md", "reason": big("NEVR"),
                "ladder": {"tier": "never", "open_tasks": [], "flags": ["never-path"]}}]}
    (state / "runs" / "r-sample-2026-10-15.json").unlink()
    (state / "runs" / "r-caps.json").write_text(json.dumps(run))
    d, dc = Digests(state), FakeDiscord()
    d.poll(dc.api, dc.post, OWNER, channel="C1")
    channel = [p["text"] for p in dc.posts if p["ch"] == "C1"]
    thread = "\n".join(p["text"] for p in dc.posts if p["ch"] != "C1")
    for line in channel:
        assert len(line) <= 400, len(line)

    def marker(text, tag):
        return text.count(tag) * (len(tag) + 1)

    assert sum(marker(t, "SETV") for t in channel) <= r.CHANGE_CLIP
    assert "D" * (r.NAME_CLIP + 1) not in "\n".join(channel)
    assert marker(thread, "REAS") <= r.REASON_CLIP + 5
    assert marker(thread, "WHAT") <= r.REASON_CLIP + 5
    assert marker(thread, "NEVR") <= r.BLOCKED_REASON_CLIP + 5
    assert marker(thread, "SUMM") <= r.SUMMARY_CLIP + 5
    assert "x" * 20 not in "\n".join(channel) + thread
    assert len(thread) < 4000
