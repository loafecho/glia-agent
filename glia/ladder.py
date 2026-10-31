"""The authority ladder as code: what the agent may propose, what needs the owner, and what is never allowed.

Every refusal is a guard_<name>() function that raises Refused(name). GUARDS is the registry
scripts/prove-red.sh walks: it disables one guard at a time in a scratch copy and expects the test
suite to go red. Nothing in this module has a runtime off-switch; PROPOSE_ONLY is a constant.

Tiers (what the ladder says):
  propose-only  the agent side never writes; every mutating tool records a proposal (guard_agent_write)
  ask           an archive whose project an open Task still points at needs the owner's explicit ack
  never         deleting anything; Finances/, Departments/legal/, Context/concepts/, user.md,
                CLAUDE.md, the skill library, git metadata (.git/); pushing to main; anything outside the clone

Path guards compare case-insensitively and run on both the path as given and the path after resolving
symlinks inside the clone, so a link (or a case-folding filesystem) cannot reach a never-touch file
through an allowed-looking name.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

PROPOSE_ONLY = True           # suggest-only until a track record of approved proposals justifies acting

READ_TOOLS = ("list_projects", "read_note")
META_TOOLS = ("propose", "finish")
MUTATING_TOOLS = ("archive", "park", "fix_frontmatter", "collapse_handoff", "archive_task", "move_loose_file")
TOOL_ORDER = [*READ_TOOLS, *META_TOOLS, *MUTATING_TOOLS]

DELETE_VERBS = {"delete", "rm", "remove", "unlink", "purge", "wipe", "truncate"}
CLOSED_TASK_STATUSES = {"done", "cancelled", "archived"}
NEVER_PREFIXES = {
    "finances": ("Finances",),
    "legal": ("Departments/legal",),
    "concepts": ("Context/concepts",),
}
NEVER_BASENAMES = {"user_md": "user.md", "claude_md": "CLAUDE.md"}
SKILL_LIBRARY_PREFIXES = (".claude", "Plugins & Skills")
SKILL_LIBRARY_MARKER = "workspace/skill-library"   # the repo itself; a vault note *about* the skill library is not the library
READ_ALLOW_PREFIXES = ("Projects", "Tasks", "Inbox", "Daily", "Intelligence", "Team Schedules", "Team",
                       "Onboarding", "Dashboard", "Infrastructure", "Resources/templates", "vault-layout.yaml")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")


class Refused(Exception):
    """A write the ladder forbids. .guard names the rule; str() is the reason the owner/the model sees."""

    def __init__(self, guard: str, reason: str):
        super().__init__(f"{guard}: {reason}")
        self.guard = guard
        self.reason = reason


def norm(rel: str) -> str:
    """Treat a backslash as a separator *before* normalising, so "a\\..\\..\\x" collapses to "../x"
    (replacing after normpath left its ".." uncollapsed and let it past guard_outside_vault)."""
    return os.path.normpath(str(rel).replace("\\", "/"))


def _under(rel: str, prefix: str) -> bool:
    rel = norm(rel)
    return rel == prefix or rel.startswith(prefix + "/")


def _under_ci(rel: str, prefix: str) -> bool:
    """_under, case-insensitive: the never-touch set must hold on case-folding filesystems too."""
    return _under(norm(rel).casefold(), prefix.casefold())


def resolved_rel(root: Path, rel: str) -> str | None:
    """The clone-relative path after resolving symlinks, or None when it resolves outside the clone."""
    root_r = Path(root).resolve()
    try:
        return (root_r / norm(rel)).resolve().relative_to(root_r).as_posix()
    except ValueError:
        return None


# ------------------------------------------------------------------------------------ path guards

def guard_outside_vault(root: Path, rel: str) -> None:
    """The target must stay inside the clone, lexically and after resolving symlinks."""
    rel_s = str(rel)
    n = norm(rel_s)
    if rel_s.startswith("/") or n.startswith("/") or n == ".." or n.startswith("../"):
        raise Refused("outside_vault", f"{rel_s!r} is not a path inside the vault clone")
    root_r = Path(root).resolve()
    target = (root_r / norm(rel_s))
    probe = target
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if probe.resolve() != root_r and root_r not in probe.resolve().parents:
        raise Refused("outside_vault", f"{rel_s!r} resolves outside the vault clone")


def guard_finances(rel: str) -> None:
    for p in NEVER_PREFIXES["finances"]:
        if _under_ci(rel, p):
            raise Refused("finances", f"{norm(rel)} is under Finances/ (never touched by glia)")


def guard_legal(rel: str) -> None:
    for p in NEVER_PREFIXES["legal"]:
        if _under_ci(rel, p):
            raise Refused("legal", f"{norm(rel)} is under Departments/legal/ (never touched by glia)")


def guard_concepts(rel: str) -> None:
    for p in NEVER_PREFIXES["concepts"]:
        if _under_ci(rel, p):
            raise Refused("concepts", f"{norm(rel)} is a concept page (never touched by glia)")


def guard_user_md(rel: str) -> None:
    if os.path.basename(norm(rel)).casefold() == NEVER_BASENAMES["user_md"].casefold():
        raise Refused("user_md", f"{norm(rel)} is user.md (never touched by glia)")


def guard_claude_md(rel: str) -> None:
    if os.path.basename(norm(rel)).casefold() == NEVER_BASENAMES["claude_md"].casefold():
        raise Refused("claude_md", f"{norm(rel)} is a CLAUDE.md routing index (never touched by glia)")


def guard_skill_library(rel: str) -> None:
    r = norm(rel)
    marker = SKILL_LIBRARY_MARKER.casefold()
    if any(_under_ci(r, p) for p in SKILL_LIBRARY_PREFIXES) or marker in r.casefold() or marker in str(rel).casefold():
        raise Refused("skill_library", f"{r} belongs to the agent tooling / skill library (never touched by glia)")


def guard_git_dir(rel: str) -> None:
    """git's own metadata is not a note: no path component may be .git (any case)."""
    if any(part.casefold() == ".git" for part in norm(rel).split("/")):
        raise Refused("git_dir", f"{norm(rel)} is inside git metadata (never touched by glia)")


PATH_GUARDS = (guard_finances, guard_legal, guard_concepts, guard_user_md, guard_claude_md, guard_skill_library, guard_git_dir)


def check_path(root: Path, rel: str) -> str:
    """Run every path guard on the path as given and on its symlink-resolved form; return the
    normalised relative path when all pass."""
    guard_outside_vault(root, rel)
    real = resolved_rel(root, rel)
    if real is None:   # e.g. a dangling symlink to outside: the existence probe stopped at an inside parent
        raise Refused("outside_vault", f"{str(rel)!r} resolves outside the vault clone")
    for r in dict.fromkeys(x for x in (str(rel), real) if x is not None):
        guard_skill_library(r)
        for g in PATH_GUARDS:
            g(r)
    return norm(rel)


def read_allowed(root: Path, rel: str) -> str:
    """read_note allowlist: inside the clone, never-set excluded, and under a listed folder
    (both as named and after resolving symlinks)."""
    r = check_path(root, rel)
    real = resolved_rel(root, rel)   # never None: check_path refused that
    for x in (r, real):
        if not any(_under(x, p) for p in READ_ALLOW_PREFIXES):
            raise Refused("read_allowlist", f"{r} is outside the read allowlist")
    return r


# ---------------------------------------------------------------------------------- action guards

def guard_delete(action: str, removes: bool = False, preserved_in: Path | None = None, content: str | None = None) -> None:
    """Nothing is ever deleted. A file may only vanish if its full content landed in another note first."""
    if str(action).lower() in DELETE_VERBS:
        raise Refused("delete", f"{action!r}: glia never deletes anything")
    if removes:
        if preserved_in is None or not Path(preserved_in).exists():
            raise Refused("delete", f"{action}: would remove a file without preserving its content")
        if content is not None and content not in Path(preserved_in).read_text():
            raise Refused("delete", f"{action}: {preserved_in} does not contain the removed file's content")


def task_status(text: str) -> str | None:
    from glia import frontmatter
    st = frontmatter.parse(text).get("status")
    return str(st) if st is not None else None


def guard_task_not_closed(text: str) -> None:
    """Only done/cancelled/archived tasks move to Tasks/_archive/; closing a task is the owner's call."""
    st = task_status(text)
    if st not in CLOSED_TASK_STATUSES:
        raise Refused("task_not_closed", f"task status is {st!r}, not one of {sorted(CLOSED_TASK_STATUSES)}")


def open_tasks_linking(root: Path, folder_rel: str, reader=None) -> list[str]:
    """Open tasks (Tasks/*.md, not _archive, not the index) that wikilink any note in the folder."""
    folder = norm(folder_rel)
    if reader is None:
        root = Path(root)
        def list_tasks():
            d = root / "Tasks"
            return sorted(f"Tasks/{p.name}" for p in d.glob("*.md")) if d.is_dir() else []

        def read(rel):
            return (root / rel).read_text(errors="replace")

        def folder_notes():
            d = root / folder
            if d.is_file():
                return [d.stem]
            return [p.stem for p in d.rglob("*.md")] if d.is_dir() else []
    else:
        list_tasks, read, folder_notes = reader.list_tasks, reader.read, (lambda: reader.folder_notes(folder))
    names = set(folder_notes()) | {os.path.basename(folder)}
    hits = []
    for t in list_tasks():
        if os.path.basename(t) == "CLAUDE.md":
            continue
        text = read(t)
        if task_status(text) in CLOSED_TASK_STATUSES:
            continue
        links = {m.strip() for m in WIKILINK_RE.findall(text)}
        if links & names:
            hits.append(t)
    return sorted(hits)


def guard_open_task_archive(root: Path, folder_rel: str, acked: list[str], reader=None) -> list[str]:
    """Archiving a project an open Task points at needs the owner's explicit ack of each such task."""
    open_tasks = open_tasks_linking(root, folder_rel, reader)
    missing = [t for t in open_tasks if t not in set(acked or [])]
    if missing:
        raise Refused("open_task_archive", f"open tasks still point at {norm(folder_rel)}: {', '.join(missing)} (needs the owner's ack)")
    return open_tasks


def guard_main_push(branch: str, push_ref: str | None = None) -> None:
    """Writes happen on glia/* branches only; main is never pushed by glia."""
    b = (branch or "").strip()
    if not b.startswith("glia/") or b == "glia/":
        raise Refused("main_push", f"branch {b!r} is not a glia/* branch; glia never writes on main")
    if push_ref is not None:
        ref = push_ref.split("/")[-1] if push_ref.startswith("refs/") else push_ref
        if ref in ("main", "master") or not ref.startswith("glia/"):
            raise Refused("main_push", f"refusing to push {push_ref!r}: glia never pushes main")


def guard_agent_write(tool: str) -> None:
    """Propose-only: the agent side records a proposal and never writes."""
    if PROPOSE_ONLY:
        raise Refused("agent_write", f"{tool}: propose-only — recorded as a proposal for the owner's ✅, nothing written")


GUARDS = {
    name[len("guard_"):]: fn
    for name, fn in sorted(globals().items())
    if name.startswith("guard_") and callable(fn)
}
