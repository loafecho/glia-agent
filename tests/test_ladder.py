"""One red test per guard. scripts/prove-red.sh disables each guard in a scratch copy and expects
at least one test in this suite (or the executor/gateway suites) to fail."""
import pytest

from glia import ladder
from glia.ladder import Refused


parametrize = pytest.mark.parametrize


def refused_by(fn, *a, **kw):
    with pytest.raises(Refused) as e:
        fn(*a, **kw)
    return e.value.guard


def test_guard_registry_names_every_guard():
    names = set(ladder.GUARDS)
    assert {"outside_vault", "finances", "legal", "concepts", "user_md", "claude_md", "skill_library",
            "delete", "open_task_archive", "main_push", "agent_write", "task_not_closed"} <= names
    assert len(names) >= 9


@parametrize("rel,guard", [
    ("Finances/bill-ledger.md", "finances"),
    ("Finances/briefs/2026-10.md", "finances"),
    ("Departments/legal/nda-template.md", "legal"),
    ("Context/concepts/second-brain.md", "concepts"),
    ("user.md", "user_md"),
    ("CLAUDE.md", "claude_md"),
    ("Projects/CLAUDE.md", "claude_md"),
    (".claude/skills/example/SKILL.md", "skill_library"),
    ("Plugins & Skills/skill-library.md", "skill_library"),
])
def test_never_paths_are_refused_by_the_named_guard(clone, rel, guard):
    assert refused_by(ladder.check_path, clone, rel) == guard


@parametrize("rel", ["../escape.md", "/etc/hostname", "Projects/../../x.md", "Projects/./../../y.md"])
def test_paths_outside_the_clone_are_refused(clone, rel):
    assert refused_by(ladder.check_path, clone, rel) == "outside_vault"


def test_symlink_pointing_outside_is_refused(clone, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (clone / "Projects" / "link").symlink_to(outside)
    assert refused_by(ladder.check_path, clone, "Projects/link/note.md") == "outside_vault"


def test_ordinary_project_and_task_paths_pass(clone):
    ladder.check_path(clone, "Projects/alpha/alpha.md")
    ladder.check_path(clone, "Tasks/alpha-next.md")
    ladder.check_path(clone, "Projects/_archive/alpha/alpha.md")


@parametrize("action", ["delete", "rm", "remove", "unlink", "purge"])
def test_delete_verbs_are_refused(action):
    assert refused_by(ladder.guard_delete, action) == "delete"


def test_removing_a_file_without_preserving_its_content_is_refused(clone):
    assert refused_by(ladder.guard_delete, "collapse_handoff", removes=True, preserved_in=None) == "delete"


def test_removing_a_file_whose_content_landed_elsewhere_passes(clone):
    archive = clone / "Projects/alpha/archive-handoffs.md"
    original = clone / "Projects/alpha/handoff-2026-03-01-alpha.md"
    archive.write_text("---\ncreated: 2026-10-15\n---\n" + original.read_text())
    ladder.guard_delete("collapse_handoff", removes=True, preserved_in=archive, content=original.read_text())


def test_open_tasks_linking_a_folder_are_found(clone):
    assert ladder.open_tasks_linking(clone, "Projects/alpha") == ["Tasks/alpha-next.md"]
    assert ladder.open_tasks_linking(clone, "Projects/beta") == []   # only a done task and the index link it


def test_archive_with_an_open_task_is_refused_unless_each_task_is_acked(clone):
    assert refused_by(ladder.guard_open_task_archive, clone, "Projects/alpha", []) == "open_task_archive"
    assert refused_by(ladder.guard_open_task_archive, clone, "Projects/alpha", ["Tasks/other.md"]) == "open_task_archive"
    ladder.guard_open_task_archive(clone, "Projects/alpha", ["Tasks/alpha-next.md"])
    ladder.guard_open_task_archive(clone, "Projects/beta", [])


@parametrize("branch", ["main", "master", "feature/x", "glia", ""])
def test_writes_on_anything_but_a_glia_branch_are_refused(branch):
    assert refused_by(ladder.guard_main_push, branch) == "main_push"


def test_push_to_main_is_refused_even_from_a_glia_branch():
    assert refused_by(ladder.guard_main_push, "glia/2026-10-15", push_ref="main") == "main_push"
    assert refused_by(ladder.guard_main_push, "glia/2026-10-15", push_ref="refs/heads/main") == "main_push"
    ladder.guard_main_push("glia/2026-10-15", push_ref="glia/2026-10-15")


def test_task_that_is_not_closed_cannot_be_archived():
    assert refused_by(ladder.guard_task_not_closed, "---\nstatus: growing\n---\nx\n") == "task_not_closed"
    assert refused_by(ladder.guard_task_not_closed, "no frontmatter\n") == "task_not_closed"
    for st in ("done", "cancelled", "archived"):
        ladder.guard_task_not_closed(f"---\nstatus: {st}\n---\nx\n")


def test_agent_side_writes_are_always_refused_in_propose_only_mode():
    assert ladder.PROPOSE_ONLY is True
    for tool in ladder.MUTATING_TOOLS:
        assert refused_by(ladder.guard_agent_write, tool) == "agent_write"


@parametrize("rel", [".git/config", ".git/HEAD", "Projects/../.git/hooks/pre-commit", ".GIT/config", "Projects/x/.git/config"])
def test_git_metadata_is_never_a_target(clone, rel):
    assert refused_by(ladder.check_path, clone, rel) == "git_dir"


def test_a_symlink_inside_the_clone_cannot_reach_a_never_path(clone):
    (clone / "Projects" / "leak").symlink_to("../Finances")
    (clone / "Projects/alpha/l.md").symlink_to("../../Finances/bill-ledger.md")
    assert refused_by(ladder.check_path, clone, "Projects/leak/bill-ledger.md") == "finances"
    assert refused_by(ladder.check_path, clone, "Projects/alpha/l.md") == "finances"
    assert refused_by(ladder.read_allowed, clone, "Projects/leak/bill-ledger.md") == "finances"


@parametrize("rel,guard", [("finances/bill-ledger.md", "finances"), ("Projects/alpha/claude.md", "claude_md"),
                           ("USER.md", "user_md"), ("departments/Legal/x.md", "legal")])
def test_never_guards_ignore_case(clone, rel, guard):
    assert refused_by(ladder.check_path, clone, rel) == guard
