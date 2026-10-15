"""A small Obsidian-style vault for tests, --selftest and --fence-test.

build(dir) writes the files, makes a git repo on `main`, a bare origin.git next to it and a clone of
that origin (the shape the executor expects: a dedicated clone tracking an origin). Returns
(origin_bare, clone). Nothing here touches the real vault.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

FILES = {
    "CLAUDE.md": "---\ncreated: 2026-01-01\n---\nroot routing index (never touched by glia)\n",
    "user.md": "---\ncreated: 2026-01-01\ndomain: personal\n---\nowner profile (never touched by glia)\n",
    "vault-layout.yaml": "projects: Projects\ntasks: Tasks\nprojects_archive: Projects/_archive\ntasks_archive: Tasks/_archive\n",
    "Finances/bill-ledger.md": "---\ncreated: 2026-01-01\ndomain: both\nbucket: business\n---\n| bill | amount |\n",
    "Departments/legal/nda-template.md": "---\ncreated: 2026-01-01\n---\nNDA\n",
    "Context/concepts/second-brain.md": "---\ncreated: 2026-01-01\n---\nconcept\n",
    ".claude/skills/example/SKILL.md": "---\nname: example\n---\nskill text\n",
    "Projects/CLAUDE.md": "---\ncreated: 2026-01-01\n---\nProjects routing index\n",
    "Projects/alpha/alpha.md": "---\ncreated: 2026-01-01\ntags: [project]\nstatus: growing\ndomain: business\nnext_step: ship it\n---\nAlpha is being built.\n",
    "Projects/alpha/conversation-log.md": "---\ncreated: 2026-01-01\nstatus: growing\n---\n## 2026-01-01\n- started\n",
    "Projects/alpha/handoff-2026-03-01-alpha.md": "---\ncreated: 2026-03-01\nstatus: stale\n---\nOld handoff body. Line two.\n",
    "Projects/beta/beta.md": "---\ncreated: 2026-01-01\ntags: [project]\nstatus: growing\ndomain: business\nnext_step: closed, superseded by alpha\n---\nBeta is done.\n",
    "Projects/beta/conversation-log.md": "---\ncreated: 2026-01-01\nstatus: growing\n---\n## 2026-02-01\n- superseded\n",
    "Projects/gamma/gamma.md": "---\ncreated: 2026-01-01\ntags: [project]\nstatus: evergreen\ndomain: both\n---\nGamma runs forever.\n",
    "Projects/gamma/conversation-log.md": "---\ncreated: 2026-01-01\n---\n## 2026-01-01\n- live\n",
    "Projects/Seeds/idea.md": "---\ncreated: 2026-05-01\nstatus: seed\n---\nAn idea.\n",
    "Projects/loose-analysis.md": "---\ncreated: 2026-06-01\ntags: [research]\n---\nA loose research note in the Projects root.\n",
    "Tasks/CLAUDE.md": "---\ncreated: 2026-01-01\nstatus: evergreen\n---\nTasks index. Mentions [[beta]] in prose.\n",
    "Tasks/alpha-next.md": "---\ncreated: 2026-02-01\nstatus: growing\n---\nNext step for [[alpha]].\n",
    "Tasks/old-done.md": "---\ncreated: 2026-02-01\nstatus: done\n---\nFinished. Was for [[beta]].\n",
    "Tasks/still-open.md": "---\ncreated: 2026-02-01\nstatus: open\n---\nUnrelated open task.\n",
    "Tasks/_archive/already-archived.md": "---\ncreated: 2026-01-01\nstatus: archived\n---\nold\n",
    "Intelligence/research/README.md": "research notes land here\n",
}


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def build(base: Path) -> tuple[Path, Path]:
    base = Path(base)
    src = base / "src"
    for rel, text in FILES.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git(src, "init", "-q", "-b", "main")
    git(src, "-c", "user.name=fake", "-c", "user.email=fake@example.com", "add", "-A")
    git(src, "-c", "user.name=fake", "-c", "user.email=fake@example.com", "commit", "-q", "-m", "fake vault")
    origin = base / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(origin)], check=True)
    clone = base / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    git(clone, "config", "user.name", "glia-test")
    git(clone, "config", "user.email", "glia-test@example.com")
    return origin, clone


def tree_hash(root: Path) -> str:
    """Hash of HEAD's tree plus the working-tree state: any write changes it."""
    head = git(root, "rev-parse", "HEAD").strip()
    status = git(root, "status", "--porcelain", "--untracked-files=all")
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    return f"{head} {branch} {status}"
