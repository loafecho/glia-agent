"""Replay machinery on the fake vault: approved items applied on a scratch branch must reproduce an
expected commit's names + frontmatter, with one revert line per item."""
from pathlib import Path

from glia import fakevault
from glia.apply import Executor, compare_names_and_frontmatter


def test_replay_compare_is_zero_when_items_reproduce_the_expected_tree(clone):
    # Build the "expected" state by hand on a branch, then replay the same moves as items elsewhere.
    fakevault.git(clone, "checkout", "-qb", "expected")
    fakevault.git(clone, "mv", "Tasks/old-done.md", "Tasks/_archive/old-done.md")
    (clone / "Projects/_archive").mkdir(exist_ok=True)
    fakevault.git(clone, "mv", "Projects/beta", "Projects/_archive/beta")
    p = clone / "Projects/alpha/alpha.md"
    p.write_text(p.read_text().replace("status: growing\n", "status: growing\nparked: 2026-10-14\n"))
    fakevault.git(clone, "commit", "-qam", "expected")
    expected = fakevault.git(clone, "rev-parse", "HEAD").strip()
    fakevault.git(clone, "checkout", "-q", "main")

    ex = Executor(clone, "glia/replay")
    results = ex.apply_items([
        {"id": "1", "action": "archive_task", "target": "Tasks/old-done.md", "reason": "done"},
        {"id": "2", "action": "archive", "target": "Projects/beta", "reason": "superseded", "stamp": False},
        {"id": "3", "action": "park", "target": "Projects/alpha", "reason": "quiet", "date": "2026-10-14"},
    ])
    assert all(r["status"] == "applied" for r in results)
    diffs = compare_names_and_frontmatter(clone, "glia/replay", expected, ["Projects/", "Tasks/"], out_of_scope=[])
    assert diffs == []
    assert len({r["sha"] for r in results}) == 3
    assert all(r["revert"].startswith("git revert ") for r in results)


def test_replay_compare_reports_a_missing_file_and_a_frontmatter_difference(clone):
    fakevault.git(clone, "checkout", "-qb", "expected")
    fakevault.git(clone, "mv", "Tasks/old-done.md", "Tasks/_archive/old-done.md")
    fakevault.git(clone, "commit", "-qam", "expected")
    expected = fakevault.git(clone, "rev-parse", "HEAD").strip()
    fakevault.git(clone, "checkout", "-q", "main")
    ex = Executor(clone, "glia/replay")
    ex.apply_items([{"id": "3", "action": "park", "target": "Projects/alpha", "reason": "quiet", "date": "2026-10-14"}])
    diffs = compare_names_and_frontmatter(clone, "glia/replay", expected, ["Projects/", "Tasks/"], out_of_scope=[])
    kinds = {(d["path"], d["kind"]) for d in diffs}
    assert ("Tasks/old-done.md", "only-in-replay") in kinds
    assert ("Tasks/_archive/old-done.md", "only-in-expected") in kinds
    assert ("Projects/alpha/alpha.md", "frontmatter") in kinds


def test_replay_demo_fixture_reproduces_the_hand_made_restructure(tmp_path, capsys):
    from glia.apply import replay
    from glia.mergegate import check

    assert replay(Path("fixtures/replay-demo.json"), scratch=True, out_dir=tmp_path) == 0
    out = capsys.readouterr().out
    assert "REPLAY-OK items=4 applied=4" in out and "revert_lines=4" in out
    assert len((tmp_path / "replay-reverts.txt").read_text().split("\n")) == 5
    v = check(tmp_path / "replay.git", "glia/replay")
    assert v["verdict"] == "ff-merge" and v["other"] == 0


def test_replay_refuses_without_scratch(tmp_path):
    from glia.apply import replay

    assert replay(Path("fixtures/replay-demo.json"), scratch=False, out_dir=tmp_path) == 2
    assert not (tmp_path / "replay.git").exists()
