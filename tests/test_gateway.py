"""The agent side: a propose-only MCP door. Mutating tools record a proposal and write nothing."""
import hashlib
import ipaddress
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from glia import fakevault
from glia.gateway import TOOL_ORDER, Gateway, ProposeOnlyBackend, bind_allowed, make_handler, parse_nets

fixture = pytest.fixture


TOKEN = "test-token-not-a-secret"


@fixture
def gw(clone, tmp_path):
    state = tmp_path / "state"
    be = ProposeOnlyBackend(clone, state)
    return Gateway(be, hashlib.sha256(TOKEN.encode()).hexdigest(), {"127.0.0.1"}, state / "calls.jsonl")


def rpc(gw, method, params=None, mid=1):
    return gw.handle_rpc({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})


def test_tool_list_is_exactly_the_ladder_tools_in_order(gw):
    names = [t["name"] for t in rpc(gw, "tools/list")["result"]["tools"]]
    assert names == TOOL_ORDER == ["list_projects", "read_note", "propose", "finish", "archive", "park",
                                   "fix_frontmatter", "collapse_handoff", "archive_task", "move_loose_file"]
    assert "delete" not in names and "unpark" not in names


def test_every_mutating_tool_records_a_proposal_and_writes_nothing(gw, clone):
    h = fakevault.tree_hash(clone)
    calls = {
        "archive": {"target": "Projects/beta", "reason": "superseded"},
        "park": {"target": "Projects/alpha", "reason": "quiet 60 days"},
        "fix_frontmatter": {"target": "Projects/gamma/gamma.md", "set": {"status": "live"}, "reason": "legacy value"},
        "collapse_handoff": {"target": "Projects/alpha/handoff-2026-03-01-alpha.md", "reason": "stale"},
        "archive_task": {"target": "Tasks/old-done.md", "reason": "done"},
        "move_loose_file": {"target": "Projects/loose-analysis.md", "dest": "Intelligence/research/loose-analysis.md", "reason": "loose"},
    }
    for tool, args in calls.items():
        r = rpc(gw, "tools/call", {"name": tool, "arguments": args})["result"]
        assert r["isError"] is False, r
        assert "proposed p-" in r["content"][0]["text"]
    assert fakevault.tree_hash(clone) == h
    assert gw.backend.writes_blocked == 6
    assert len(gw.backend.proposals()) == 6
    assert {p["action"] for p in gw.backend.proposals()} == set(calls)


def test_archive_proposal_carries_the_open_tasks_so_the_digest_can_show_them(gw):
    rpc(gw, "tools/call", {"name": "archive", "arguments": {"target": "Projects/alpha", "reason": "r"}})
    (p,) = gw.backend.proposals()
    assert p["ladder"]["tier"] == "ask"
    assert p["ladder"]["open_tasks"] == ["Tasks/alpha-next.md"]


def test_a_proposal_against_a_never_path_is_recorded_as_refused_and_flagged(gw):
    r = rpc(gw, "tools/call", {"name": "fix_frontmatter",
                               "arguments": {"target": "Finances/bill-ledger.md", "set": {"status": "x"}, "reason": "r"}})["result"]
    assert r["isError"] is True and "finances" in r["content"][0]["text"]
    (p,) = gw.backend.proposals()
    assert p["ladder"]["tier"] == "never" and p["status"] == "refused"


def test_read_note_is_allowlisted_and_never_reads_the_never_set(gw):
    ok = rpc(gw, "tools/call", {"name": "read_note", "arguments": {"path": "Projects/alpha/alpha.md"}})["result"]
    assert ok["isError"] is False and "Alpha is being built" in ok["content"][0]["text"]
    for path in ("Finances/bill-ledger.md", "user.md", "CLAUDE.md", "../x.md", "Departments/legal/nda-template.md"):
        r = rpc(gw, "tools/call", {"name": "read_note", "arguments": {"path": path}})["result"]
        assert r["isError"] is True, path


def test_list_projects_reads_slugs_status_and_parked(gw):
    r = rpc(gw, "tools/call", {"name": "list_projects", "arguments": {}})["result"]
    rows = json.loads(r["content"][0]["text"])
    assert {x["slug"] for x in rows} == {"alpha", "beta", "gamma"}
    assert all("status" in x for x in rows)


def test_propose_and_finish_close_a_run_without_merging(gw, clone, tmp_path):
    h = fakevault.tree_hash(clone)
    rpc(gw, "tools/call", {"name": "propose", "arguments": {"action": "merge_notes", "target": "Projects/alpha", "reason": "two notes overlap"}})
    r = rpc(gw, "tools/call", {"name": "finish", "arguments": {"summary": "one suggestion"}})["result"]
    assert r["isError"] is False
    text = r["content"][0]["text"]
    assert "merge_gate=not-run" in text and "posted=no" in text
    runs = list((tmp_path / "state" / "runs").glob("*.json"))
    assert len(runs) == 1
    assert fakevault.tree_hash(clone) == h


def test_unknown_tool_is_an_error_result_not_a_write(gw, clone):
    h = fakevault.tree_hash(clone)
    r = rpc(gw, "tools/call", {"name": "delete", "arguments": {"target": "Projects/alpha"}})["result"]
    assert r["isError"] is True
    assert fakevault.tree_hash(clone) == h


def test_bind_is_limited_to_the_configured_private_network():
    nets = [ipaddress.ip_network("198.51.100.0/24")]
    assert bind_allowed("198.51.100.10", nets)
    assert not bind_allowed("0.0.0.0", nets)
    assert not bind_allowed("203.0.113.10", nets)
    assert not bind_allowed("127.0.0.1", nets)
    assert not bind_allowed("not-an-address", nets)


def test_a_catch_all_bind_network_is_refused():
    with pytest.raises(ValueError):
        parse_nets("0.0.0.0/0")
    assert parse_nets("198.51.100.0/24, 203.0.113.0/28") == [ipaddress.ip_network("198.51.100.0/24"), ipaddress.ip_network("203.0.113.0/28")]


def test_http_door_checks_source_token_sha256_and_path(gw):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(gw))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/mcp"
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()

        def post(path=url, token=TOKEN):
            req = urllib.request.Request(path, data=body, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            return urllib.request.urlopen(req, timeout=5)

        assert json.loads(post().read())["result"]["tools"]
        with pytest.raises(urllib.error.HTTPError) as e:
            post(token="wrong")
        assert e.value.code == 401
        with pytest.raises(urllib.error.HTTPError) as e:
            post(path=url.replace("/mcp", "/other"))
        assert e.value.code == 404
    finally:
        srv.shutdown()


def test_finish_rotates_the_run_id_so_each_weekly_pass_is_its_own_snapshot(tmp_path):
    import hashlib
    from glia import fakevault
    from glia.gateway import Gateway, ProposeOnlyBackend
    _, clone = fakevault.build(tmp_path)
    state = tmp_path / "state"
    gw = Gateway(ProposeOnlyBackend(clone, state), hashlib.sha256(b"t").hexdigest(), {"127.0.0.1"}, state / "calls.jsonl")

    def call(name, args):
        return gw.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})["result"]

    first = gw.backend.store.run_id
    call("park", {"target": "Projects/gamma", "reason": "quiet"})
    call("finish", {"summary": "pass one"})
    second = gw.backend.store.run_id
    assert second != first
    call("archive", {"target": "Projects/beta", "reason": "superseded"})
    call("finish", {"summary": "pass two"})
    runs = {p.stem: json.loads(p.read_text()) for p in (state / "runs").glob("*.json")}
    assert set(runs) == {first, second}
    assert [i["action"] for i in runs[first]["items"]] == ["park"]
    assert [i["action"] for i in runs[second]["items"]] == ["archive"]


def test_list_projects_is_compact_enough_for_one_tool_result(gw):
    """First live pass: 44 projects came back as 76 KB (55 KB of next_step text + every file name),
    which the client could not use. Clip next_step, drop the file list, keep only the handoff names."""
    note = gw.backend.root / "Projects/alpha/alpha.md"
    from glia import frontmatter
    note.write_text(frontmatter.set_keys(note.read_text(), {"next_step": "x" * 3000}))
    (gw.backend.root / "Projects/alpha/handoff-2026-03-02-extra.md").write_text("---\ncreated: 2026-03-02\n---\nh\n")
    r = rpc(gw, "tools/call", {"name": "list_projects", "arguments": {}})["result"]
    rows = {x["slug"]: x for x in json.loads(r["content"][0]["text"])}
    a = rows["alpha"]
    assert "files" not in a
    assert len(a["next_step"]) <= 241 and a["next_step"].endswith("…")
    assert sorted(a["handoffs"]) == ["handoff-2026-03-01-alpha.md", "handoff-2026-03-02-extra.md"]
    assert len(r["content"][0]["text"]) < 400 * len(rows)


def test_read_note_lists_an_allowlisted_directory_and_refuses_a_fenced_one(gw):
    r = rpc(gw, "tools/call", {"name": "read_note", "arguments": {"path": "Tasks/"}})["result"]
    assert r["isError"] is False
    listing = json.loads(r["content"][0]["text"])
    assert "old-done.md" in listing["files"] and "_archive/" in listing["dirs"]
    r = rpc(gw, "tools/call", {"name": "read_note", "arguments": {"path": "Projects/alpha"}})["result"]
    assert r["isError"] is False and "alpha.md" in json.loads(r["content"][0]["text"])["files"]
    for path in ("Finances/", "Departments/legal", "Context/concepts/", "."):
        r = rpc(gw, "tools/call", {"name": "read_note", "arguments": {"path": path}})["result"]
        assert r["isError"] is True, path


def test_free_form_propose_is_recorded_as_action_propose_with_the_text_in_args(gw):
    text = "Quote (or trim) the very long unquoted next_step in frontmatter so the YAML parses again and the Dashboard sees it"
    r = rpc(gw, "tools/call", {"name": "propose", "arguments": {"action": text, "target": "Projects/alpha/alpha.md", "reason": "yaml"}})["result"]
    assert r["isError"] is False
    (p,) = gw.backend.proposals()
    assert p["action"] == "propose" and p["args"]["what"] == text and "free-form" in p["ladder"]["flags"]


def test_list_projects_last_log_reads_bullet_dates_too(gw):
    """Second live pass: an older log uses '- **2026-10-08** — …' bullets under one heading, so last_log was null."""
    log = gw.backend.root / "Projects/alpha/conversation-log.md"
    log.write_text("---\ncreated: 2026-01-01\n---\n\n# alpha — Conversation Log\n\n- **2026-10-08** — did a thing\n- **2026-09-29** — earlier\n")
    rows = {x["slug"]: x for x in json.loads(rpc(gw, "tools/call", {"name": "list_projects", "arguments": {}})["result"]["content"][0]["text"])}
    assert rows["alpha"]["last_log"] == "2026-10-08"
