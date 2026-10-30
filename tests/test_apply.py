"""The approval executor: the only code that writes to a vault clone. One commit per item, on a
glia/* branch, refusing in code what the ladder forbids."""
import subprocess

import pytest

from glia import frontmatter as fm
from glia import fakevault
from glia.apply import Executor
from glia.ladder import Refused


fixture = pytest.fixture
parametrize = pytest.mark.parametrize


def git(root, *a):
    return fakevault.git(root, *a)


@fixture
def ex(clone):
    return Executor(clone, "glia/test")


def test_executor_refuses_to_work_on_main(clone):
    with pytest.raises(Refused) as e:
        Executor(clone, "main")
    assert e.value.guard == "main_push"
    assert git(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


def test_executor_creates_its_glia_branch_and_never_pushes_main(clone, ex):
    assert git(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == "glia/test"
    with pytest.raises(Refused) as e:
        ex.push("main")
    assert e.value.guard == "main_push"


def test_archive_moves_the_folder_whole_stamps_status_and_commits_once(clone, ex):
    before = git(clone, "rev-list", "--count", "HEAD").strip()
    r = ex.apply_item({"id": "t1", "action": "archive", "target": "Projects/beta", "reason": "note says superseded",
                       "date": "2026-10-15"})
    assert not (clone / "Projects/beta").exists()
    note = (clone / "Projects/_archive/beta/beta.md").read_text()
    assert fm.parse(note)["status"] == "archived"
    assert str(fm.parse(note)["archived_on"]) == "2026-10-15"
    assert (clone / "Projects/_archive/beta/conversation-log.md").exists()
    assert int(git(clone, "rev-list", "--count", "HEAD")) == int(before) + 1
    assert r["revert"] == f"git revert {r['sha']}"
    assert git(clone, "status", "--porcelain") == ""


def test_archive_without_stamp_is_a_pure_rename(clone, ex):
    ex.apply_item({"id": "t1", "action": "archive", "target": "Projects/beta", "reason": "r", "stamp": False})
    assert (clone / "Projects/_archive/beta/beta.md").read_text() == fakevault.FILES["Projects/beta/beta.md"]


def test_archive_with_open_task_is_refused_and_writes_nothing(clone, ex):
    h = fakevault.tree_hash(clone)
    with pytest.raises(Refused) as e:
        ex.apply_item({"id": "t2", "action": "archive", "target": "Projects/alpha", "reason": "r"})
    assert e.value.guard == "open_task_archive"
    assert "Tasks/alpha-next.md" in str(e.value)
    assert fakevault.tree_hash(clone) == h


def test_archive_with_acked_open_task_applies(clone, ex):
    ex.apply_item({"id": "t2", "action": "archive", "target": "Projects/alpha", "reason": "the owner ticked knowing the task",
                   "ack_open_tasks": ["Tasks/alpha-next.md"]})
    assert (clone / "Projects/_archive/alpha/alpha.md").exists()


def test_archive_task_moves_a_closed_task_and_refuses_an_open_one(clone, ex):
    ex.apply_item({"id": "t3", "action": "archive_task", "target": "Tasks/old-done.md", "reason": "done"})
    assert (clone / "Tasks/_archive/old-done.md").exists()
    with pytest.raises(Refused) as e:
        ex.apply_item({"id": "t4", "action": "archive_task", "target": "Tasks/still-open.md", "reason": "r"})
    assert e.value.guard == "task_not_closed"
    assert (clone / "Tasks/still-open.md").exists()


def test_park_stamps_only_the_frontmatter(clone, ex):
    ex.apply_item({"id": "t5", "action": "park", "target": "Projects/alpha", "reason": "60 days quiet", "date": "2026-10-14"})
    text = (clone / "Projects/alpha/alpha.md").read_text()
    assert str(fm.parse(text)["parked"]) == "2026-10-14"
    assert fm.split(text)[1] == fm.split(fakevault.FILES["Projects/alpha/alpha.md"])[1]
    assert git(clone, "diff", "--name-only", "HEAD~1", "HEAD").split() == ["Projects/alpha/alpha.md"]


def test_fix_frontmatter_sets_and_unsets_keys(clone, ex):
    ex.apply_item({"id": "t6", "action": "fix_frontmatter", "target": "Projects/gamma/gamma.md",
                   "set": {"status": "live"}, "reason": "evergreen is a legacy value"})
    assert fm.parse((clone / "Projects/gamma/gamma.md").read_text())["status"] == "live"
    ex.apply_item({"id": "t7", "action": "fix_frontmatter", "target": "Projects/alpha/alpha.md",
                   "unset": ["next_step"], "reason": "r"})
    assert "next_step" not in fm.parse((clone / "Projects/alpha/alpha.md").read_text())


def test_collapse_handoff_preserves_every_byte_before_removing_the_file(clone, ex):
    original = (clone / "Projects/alpha/handoff-2026-03-01-alpha.md").read_text()
    ex.apply_item({"id": "t8", "action": "collapse_handoff", "target": "Projects/alpha/handoff-2026-03-01-alpha.md",
                   "reason": "stale, high confidence"})
    assert not (clone / "Projects/alpha/handoff-2026-03-01-alpha.md").exists()
    archive = (clone / "Projects/alpha/archive-handoffs.md").read_text()
    assert original in archive
    assert git(clone, "status", "--porcelain") == ""


def test_collapse_handoff_heading_is_the_filename_and_inbound_links_are_repointed(clone, ex):
    (clone / "Tasks/linker.md").write_text(
        "---\nstatus: open\nnext_step: \"see [[handoff-2026-03-01-alpha|old pickup]]\"\n---\n"
        "plain [[handoff-2026-03-01-alpha]]\n| a | [[handoff-2026-03-01-alpha]] |\n"
        "```\n[[handoff-2026-03-01-alpha]] fenced\n```\n")
    (clone / "Finances/ledger.md").parent.mkdir(exist_ok=True)
    (clone / "Finances/ledger.md").write_text("money [[handoff-2026-03-01-alpha]]\n")
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", "fixture: linkers")
    out = ex.apply_item({"id": "t9", "action": "collapse_handoff", "target": "Projects/alpha/handoff-2026-03-01-alpha.md",
                         "reason": "stale"})
    archive = (clone / "Projects/alpha/archive-handoffs.md").read_text()
    assert "\n## handoff-2026-03-01-alpha.md\n" in archive
    t = "Projects/alpha/archive-handoffs#handoff-2026-03-01-alpha.md"
    linker = (clone / "Tasks/linker.md").read_text()
    assert f'next_step: "see [[{t}|old pickup]]"' in linker
    assert f"plain [[{t}|handoff-2026-03-01-alpha]]" in linker
    assert f"| a | [[{t}\\|handoff-2026-03-01-alpha]] |" in linker
    assert "```\n[[handoff-2026-03-01-alpha]] fenced\n```" in linker
    # the ladder forbids Finances/: left as is
    assert (clone / "Finances/ledger.md").read_text() == "money [[handoff-2026-03-01-alpha]]\n"
    # one commit carries the collapse and the relink
    assert git(clone, "status", "--porcelain") == ""
    files = git(clone, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(files) == ["Projects/alpha/archive-handoffs.md", "Projects/alpha/handoff-2026-03-01-alpha.md", "Tasks/linker.md"]
    assert "repointed 3 inbound link(s)" in str(out)


def test_move_loose_file_to_where_the_folder_rule_says(clone, ex):
    ex.apply_item({"id": "t9", "action": "move_loose_file", "target": "Projects/loose-analysis.md",
                   "dest": "Intelligence/research/loose-analysis.md", "reason": "Projects root holds folders only"})
    assert (clone / "Intelligence/research/loose-analysis.md").exists()
    assert not (clone / "Projects/loose-analysis.md").exists()


@parametrize("item,guard", [
    ({"action": "delete", "target": "Projects/beta/beta.md"}, "delete"),
    ({"action": "fix_frontmatter", "target": "Finances/bill-ledger.md", "set": {"status": "x"}}, "finances"),
    ({"action": "fix_frontmatter", "target": "Departments/legal/nda-template.md", "set": {"status": "x"}}, "legal"),
    ({"action": "fix_frontmatter", "target": "Context/concepts/second-brain.md", "set": {"status": "x"}}, "concepts"),
    ({"action": "fix_frontmatter", "target": "user.md", "set": {"status": "x"}}, "user_md"),
    ({"action": "fix_frontmatter", "target": "Projects/CLAUDE.md", "set": {"status": "x"}}, "claude_md"),
    ({"action": "fix_frontmatter", "target": ".claude/skills/example/SKILL.md", "set": {"name": "x"}}, "skill_library"),
    ({"action": "move_loose_file", "target": "Projects/loose-analysis.md", "dest": "../outside.md"}, "outside_vault"),
    ({"action": "move_loose_file", "target": "Projects/loose-analysis.md", "dest": "Finances/x.md"}, "finances"),
    ({"action": "archive", "target": "Finances"}, "finances"),
])
def test_refused_items_name_their_guard_and_leave_the_tree_untouched(clone, ex, item, guard):
    h = fakevault.tree_hash(clone)
    with pytest.raises(Refused) as e:
        ex.apply_item({"id": "x", "reason": "r", **item})
    assert e.value.guard == guard
    assert fakevault.tree_hash(clone) == h


def test_unknown_action_is_refused_as_a_delete_risk(clone, ex):
    with pytest.raises(Refused):
        ex.apply_item({"id": "x", "action": "rewrite_note", "target": "Projects/alpha/alpha.md", "reason": "r"})


def test_apply_items_reports_per_item_and_each_revert_line_undoes_exactly_that_item(clone, ex):
    results = ex.apply_items([
        {"id": "a", "action": "archive_task", "target": "Tasks/old-done.md", "reason": "done"},
        {"id": "b", "action": "park", "target": "Projects/alpha", "reason": "quiet", "date": "2026-10-14"},
        {"id": "c", "action": "archive", "target": "Projects/alpha", "reason": "r"},   # refused: open task
    ])
    assert [r["status"] for r in results] == ["applied", "applied", "refused"]
    assert results[2]["guard"] == "open_task_archive"
    subprocess.run(["git", "-C", str(clone), "revert", "--no-edit", results[1]["sha"]], check=True, capture_output=True)
    assert "parked" not in fm.parse((clone / "Projects/alpha/alpha.md").read_text())
    assert (clone / "Tasks/_archive/old-done.md").exists()


def test_archive_refuses_a_folder_that_carries_a_never_touch_file(clone, ex):
    (clone / "Projects/beta/CLAUDE.md").write_text("---\ncreated: 2026-01-01\n---\nbeta routing index\n")
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", "fixture: nested CLAUDE.md")
    h = fakevault.tree_hash(clone)
    with pytest.raises(Refused) as e:
        ex.apply_item({"id": "x", "action": "archive", "target": "Projects/beta", "reason": "r"})
    assert e.value.guard == "claude_md"
    assert fakevault.tree_hash(clone) == h
    assert (clone / "Projects/beta/CLAUDE.md").exists()


@parametrize("action,target", [("park", "Projects/alpha"), ("archive", "Projects/beta")])
def test_a_stamp_date_that_is_not_an_iso_date_is_refused(clone, ex, action, target):
    h = fakevault.tree_hash(clone)
    with pytest.raises(ValueError):
        ex.apply_item({"id": "x", "action": action, "target": target, "reason": "r", "date": "2026-10-01\nnext_step: archive"})
    assert fakevault.tree_hash(clone) == h


def test_git_metadata_is_refused_as_a_target_or_a_destination(clone, ex):
    cfg = (clone / ".git/config").read_text()
    with pytest.raises(Refused) as e:
        ex.apply_item({"id": "x", "action": "fix_frontmatter", "target": ".git/config", "set": {"status": "x"}, "reason": "r"})
    assert e.value.guard == "git_dir"
    with pytest.raises(Refused) as e:
        ex.apply_item({"id": "y", "action": "move_loose_file", "target": "Projects/loose-analysis.md",
                       "dest": ".git/hooks/pre-commit", "reason": "r"})
    assert e.value.guard == "git_dir"
    assert (clone / ".git/config").read_text() == cfg and (clone / "Projects/loose-analysis.md").exists()


def test_executor_refuses_a_dirty_tree_so_a_rollback_cannot_discard_work(clone, ex):
    scratch = clone / "Projects/alpha/scratch-note.md"
    scratch.write_text("someone's untracked work\n")
    with pytest.raises(RuntimeError):
        ex.apply_item({"id": "x", "action": "move_loose_file", "target": "Projects/loose-analysis.md",
                       "dest": "Tasks/still-open.md", "reason": "r"})
    assert scratch.read_text() == "someone's untracked work\n"
    with pytest.raises(RuntimeError):
        Executor(clone, "glia/test")
    assert scratch.exists()


def test_relink_leaves_ignored_and_symlinked_notes_alone(clone, ex):
    (clone / ".gitignore").write_text("scratch/\n")
    (clone / "Tasks/linker.md").write_text("---\nstatus: open\n---\nsee [[handoff-2026-03-01-alpha]]\n")
    (clone / "Tasks/link-alias.md").symlink_to("linker.md")
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", "fixture: linkers")
    ignored = clone / "scratch/notes.md"
    ignored.parent.mkdir()
    ignored.write_text("private [[handoff-2026-03-01-alpha]]\n")
    ex.apply_item({"id": "t9", "action": "collapse_handoff", "target": "Projects/alpha/handoff-2026-03-01-alpha.md", "reason": "stale"})
    assert ignored.read_text() == "private [[handoff-2026-03-01-alpha]]\n"
    assert (clone / "Tasks/link-alias.md").is_symlink()
    assert "archive-handoffs#handoff-2026-03-01-alpha.md" in (clone / "Tasks/linker.md").read_text()
    files = git(clone, "show", "--name-only", "--format=", "HEAD").split()
    assert "scratch/notes.md" not in files and "Tasks/link-alias.md" not in files
