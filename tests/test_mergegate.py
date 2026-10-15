from glia import fakevault
from glia.apply import Executor
from glia.mergegate import check


def test_renames_and_frontmatter_only_edits_in_projects_and_tasks_are_ff_merge(clone):
    ex = Executor(clone, "glia/gate")
    ex.apply_items([
        {"id": "a", "action": "archive_task", "target": "Tasks/old-done.md", "reason": "done"},
        {"id": "b", "action": "archive", "target": "Projects/beta", "reason": "superseded", "date": "2026-10-15"},
        {"id": "c", "action": "park", "target": "Projects/alpha", "reason": "quiet", "date": "2026-10-14"},
    ])
    v = check(clone, "glia/gate")
    assert v["verdict"] == "ff-merge"
    assert v["renames"] == 3          # the task + two files in beta/ (beta.md is a rename with a frontmatter-only edit)
    assert v["frontmatter_only"] == 2  # beta.md stamp + alpha.md park
    assert v["other"] == 0


def test_a_body_edit_makes_it_a_pr(clone):
    ex = Executor(clone, "glia/gate2")
    ex.apply_items([{"id": "a", "action": "park", "target": "Projects/alpha", "reason": "quiet", "date": "2026-10-14"}])
    p = clone / "Projects/alpha/conversation-log.md"
    p.write_text(p.read_text() + "\n## 2026-10-14\n- parked\n")
    fakevault.git(clone, "commit", "-qam", "body edit")
    v = check(clone, "glia/gate2")
    assert v["verdict"] == "pr"
    assert v["other"] == 1
    assert v["other_paths"] == ["Projects/alpha/conversation-log.md"]


def test_collapse_handoff_is_a_pr_because_it_adds_and_removes(clone):
    ex = Executor(clone, "glia/gate3")
    ex.apply_items([{"id": "a", "action": "collapse_handoff", "target": "Projects/alpha/handoff-2026-03-01-alpha.md", "reason": "stale"}])
    v = check(clone, "glia/gate3")
    assert v["verdict"] == "pr" and v["other"] >= 1


def test_a_branch_touching_a_never_path_is_refused_not_merged(clone):
    fakevault.git(clone, "checkout", "-qb", "glia/bad")
    p = clone / "Finances/bill-ledger.md"
    p.write_text(p.read_text().replace("bucket: business", "bucket: personal"))
    fakevault.git(clone, "commit", "-qam", "touch finances")
    v = check(clone, "glia/bad")
    assert v["verdict"] == "refused"
    assert v["refused"] == ["finances"]


def test_moves_outside_projects_and_tasks_are_other(clone):
    ex = Executor(clone, "glia/gate4")
    ex.apply_items([{"id": "a", "action": "move_loose_file", "target": "Projects/loose-analysis.md",
                     "dest": "Intelligence/research/loose-analysis.md", "reason": "r"}])
    v = check(clone, "glia/gate4")
    assert v["verdict"] == "pr" and v["other"] == 1
