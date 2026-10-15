"""The merge gate: classify a glia/* branch against main and pick ff-merge vs PR.

    python3 -m glia.mergegate --check glia/replay [--repo PATH] [--plus-body-edit]

Verdicts: `ff-merge` when every change is a rename inside Projects/ or Tasks/ or a frontmatter-only edit
there; `pr` when anything else changed (a body edit, an added or removed file, a move out of those two
folders); `refused` when the branch touches a path the ladder never allows. This module only classifies —
no merge, no push: in propose-only mode the merge itself is still the owner's hand.

Repo resolution: --repo, then $GLIA_CLONE, then ~/glia-vault if it has the branch, then the
replay result repo under .scratch/ (written by `glia.apply --replay --scratch`).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from glia import frontmatter, ladder
from glia.apply import SCRATCH_RESULT, git
from glia.ladder import Refused

SCOPE = ("Projects", "Tasks")
DEDICATED_CLONE = Path.home() / "glia-vault"


def _in_scope(path: str) -> bool:
    return any(path == s or path.startswith(s + "/") for s in SCOPE)


def _has_branch(repo: Path, branch: str) -> bool:
    return bool(git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{branch}", check=False).strip())


def resolve_repo(branch: str, repo: str | None) -> Path:
    for cand in (repo, os.environ.get("GLIA_CLONE")):
        if cand:
            return Path(cand)
    if DEDICATED_CLONE.exists() and _has_branch(DEDICATED_CLONE, branch):
        return DEDICATED_CLONE
    if SCRATCH_RESULT.exists() and _has_branch(SCRATCH_RESULT, branch):
        return SCRATCH_RESULT
    raise SystemExit(f"no repo holds branch {branch}: pass --repo (looked at $GLIA_CLONE, {DEDICATED_CLONE}, {SCRATCH_RESULT})")


def _main_ref(repo: Path) -> str:
    for ref in ("main", "origin/main", "master"):
        if git(repo, "rev-parse", "--verify", "-q", ref, check=False).strip():
            return ref
    raise SystemExit("no main branch in repo")


def _frontmatter_only(repo: Path, base: str, head: str, old: str, new: str) -> bool:
    a = git(repo, "show", f"{base}:{old}")
    b = git(repo, "show", f"{head}:{new}")
    return frontmatter.split(a)[1] == frontmatter.split(b)[1] and frontmatter.parse(a) != frontmatter.parse(b)


def check(repo: Path, branch: str) -> dict:
    repo = Path(repo)
    main = _main_ref(repo)
    base = git(repo, "merge-base", branch, main).strip()
    raw = git(repo, "diff", "--name-status", "-M", "-z", base, branch)
    fields = raw.split("\0")
    renames = frontmatter_only = other = 0
    other_paths: list[str] = []
    refused: list[str] = []
    touched: list[str] = []
    i = 0
    while i < len(fields) and fields[i]:
        status = fields[i]
        if status[0] in "RC":
            old, new = fields[i + 1], fields[i + 2]
            i += 3
            touched += [old, new]
            if not (_in_scope(old) and _in_scope(new)):
                other += 1
                other_paths.append(new)
                continue
            renames += 1
            if status != "R100":
                if _frontmatter_only(repo, base, branch, old, new):
                    frontmatter_only += 1
                else:
                    other += 1
                    other_paths.append(new)
        else:
            path = fields[i + 1]
            i += 2
            touched.append(path)
            if status == "M" and _in_scope(path) and _frontmatter_only(repo, base, branch, path, path):
                frontmatter_only += 1
            else:
                other += 1
                other_paths.append(path)
    for p in touched:
        try:
            ladder.check_path(repo, p) if (repo / ".git").is_dir() else _check_path_lexical(p)
        except Refused as e:
            if e.guard not in refused:
                refused.append(e.guard)
    verdict = "refused" if refused else ("ff-merge" if other == 0 else "pr")
    return {"verdict": verdict, "renames": renames, "frontmatter_only": frontmatter_only, "other": other,
            "other_paths": sorted(other_paths), "refused": refused, "base": base[:8], "main": main, "branch": branch}


def _check_path_lexical(p: str) -> None:
    """Bare repos have no working tree, so run the path guards without the symlink check."""
    ladder.guard_skill_library(p)
    for g in ladder.PATH_GUARDS:
        g(p)


def with_body_edit(repo: Path, branch: str) -> dict:
    """Clone the repo to a temp dir, add one body edit on a scratch branch, classify, delete the temp."""
    tmp = Path(tempfile.mkdtemp(prefix="glia-gate-", dir="/tmp"))
    try:
        clone = tmp / "clone"
        subprocess.run(["git", "clone", "-q", "--no-checkout", str(repo), str(clone)], check=True)
        git(clone, "config", "user.name", "glia")
        git(clone, "config", "user.email", "glia@example.com")
        main = _main_ref(clone)
        git(clone, "branch", "-q", "main", main, check=False)
        git(clone, "checkout", "-q", "-b", f"{branch}-bodyedit", f"origin/{branch}" if not _has_branch(clone, branch) else branch)
        note = next(p for p in sorted((clone / "Projects").rglob("conversation-log.md")))
        note.write_text(note.read_text() + "\n- (scratch body edit for the merge-gate check)\n")
        git(clone, "commit", "-qam", "scratch: body edit")
        return check(clone, f"{branch}-bodyedit")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", metavar="BRANCH", required=True)
    ap.add_argument("--repo")
    ap.add_argument("--plus-body-edit", action="store_true", help="also classify a scratch copy of the branch with one note body edited")
    a = ap.parse_args(argv)
    repo = resolve_repo(a.check, a.repo)
    v = with_body_edit(repo, a.check) if a.plus_body_edit else check(repo, a.check)
    print(f"GATE repo={repo} base={v['base']} main={v['main']} branch={v['branch']}")
    for p in v["other_paths"]:
        print(f"OTHER {p}")
    for g in v["refused"]:
        print(f"REFUSED guard={g}")
    print(f"GATE verdict={v['verdict']} renames={v['renames']} frontmatter_only={v['frontmatter_only']} other={v['other']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
