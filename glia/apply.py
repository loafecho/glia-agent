"""The approval executor: the only code in glia that writes to a vault clone.

It applies items the owner ticked (one commit each, so one `git revert <sha>` undoes exactly one item) on a
glia/* branch of a dedicated clone, and refuses in code what the ladder forbids — even for a ticked
item. It never pushes main and never deletes.

    python3 -m glia.apply --clone ~/glia-vault --branch glia/2026-10-15 --items ticked.json
    python3 -m glia.apply --replay fixtures/replay-demo.json --scratch

--replay --scratch works in a /tmp scratch dir. The fixture names a source: a real vault repo plus a
base and an expected commit (a restructure someone did by hand), or "fakevault", in which case the
fixture vault is built and its `expected_changes` are made by hand with plain git to produce the
expected commit. Every item is applied as approved, names + frontmatter are compared against the
expected commit over the fixture's scope, the resulting branch is kept in a bare repo under .scratch/
for the merge gate, and the scratch dir is deleted.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from glia import frontmatter, handoff_relink, ladder
from glia.ladder import Refused

REPO_DIR = Path(__file__).resolve().parent.parent
SCRATCH_RESULT = REPO_DIR / ".scratch" / "replay.git"


def git(root: Path, *args: str, check: bool = True) -> str:
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {root}: {p.stderr.strip()}")
    return p.stdout


def today() -> str:
    return dt.date.today().isoformat()


class Executor:
    def __init__(self, clone: Path, branch: str):
        self.root = Path(clone)
        if not (self.root / ".git").exists():
            raise RuntimeError(f"{self.root} is not a git clone")
        ladder.guard_main_push(branch)
        self.branch = branch
        current = git(self.root, "rev-parse", "--abbrev-ref", "HEAD").strip()
        if current != branch:
            if git(self.root, "status", "--porcelain").strip():
                raise RuntimeError(f"{self.root} has uncommitted changes; refusing to switch branches")
            exists = git(self.root, "rev-parse", "--verify", "-q", f"refs/heads/{branch}", check=False).strip()
            git(self.root, "checkout", "-q", *(() if exists else ("-b",)), branch)

    # ------------------------------------------------------------------ helpers
    def _assert_on_branch(self) -> None:
        current = git(self.root, "rev-parse", "--abbrev-ref", "HEAD").strip()
        ladder.guard_main_push(current)
        if current != self.branch:
            raise RuntimeError(f"clone is on {current}, expected {self.branch}")

    def _path(self, rel: str) -> Path:
        return self.root / ladder.check_path(self.root, rel)

    def _commit(self, item: dict, summary: str) -> str:
        msg = f"glia: {summary} [{item.get('id', '?')}]\n\nreason: {item.get('reason', '')}\naction: {item['action']}\ntarget: {item['target']}"
        git(self.root, "commit", "-q", "-m", msg)
        return git(self.root, "rev-parse", "HEAD").strip()

    def _write(self, p: Path, text: str) -> None:
        p.write_text(text)
        git(self.root, "add", "--", str(p.relative_to(self.root)))

    def _mv(self, src: Path, dst: Path) -> None:
        if not src.exists():
            raise RuntimeError(f"{src.relative_to(self.root)} does not exist")
        if dst.exists():
            raise RuntimeError(f"{dst.relative_to(self.root)} already exists")
        dst.parent.mkdir(parents=True, exist_ok=True)
        git(self.root, "mv", "--", str(src.relative_to(self.root)), str(dst.relative_to(self.root)))

    # ------------------------------------------------------------------ actions
    def _archive(self, item: dict) -> str:
        target = ladder.check_path(self.root, item["target"])
        if not target.startswith("Projects/") or target.count("/") != 1 or target.startswith("Projects/_archive"):
            raise RuntimeError(f"archive takes a Projects/<slug> folder, got {target}")
        slug = target.split("/")[1]
        src = self._path(target)
        dst = self._path(f"Projects/_archive/{slug}")
        acked = ladder.guard_open_task_archive(self.root, target, item.get("ack_open_tasks") or [])
        self._mv(src, dst)
        if item.get("stamp", True):
            note = dst / f"{slug}.md"
            if note.exists():
                self._write(note, frontmatter.set_keys(note.read_text(), {"status": "archived", "archived_on": item.get("date") or today()}))
        extra = f" (open tasks acked: {', '.join(acked)})" if acked else ""
        return f"archive {target} -> Projects/_archive/{slug}{extra}"

    def _archive_task(self, item: dict) -> str:
        target = ladder.check_path(self.root, item["target"])
        if not target.startswith("Tasks/") or target.count("/") != 1:
            raise RuntimeError(f"archive_task takes a Tasks/<name>.md file, got {target}")
        src = self._path(target)
        ladder.guard_task_not_closed(src.read_text())
        dst = self._path(f"Tasks/_archive/{src.name}")
        self._mv(src, dst)
        return f"archive_task {target} -> Tasks/_archive/{src.name}"

    def _park(self, item: dict) -> str:
        target = ladder.norm(item["target"])
        folder = self._path(target)
        note = folder / f"{folder.name}.md" if folder.is_dir() else folder
        ladder.check_path(self.root, str(note.relative_to(self.root)))
        if not note.exists():
            raise RuntimeError(f"no folder note at {note.relative_to(self.root)}")
        self._write(note, frontmatter.set_keys(note.read_text(), {"parked": item.get("date") or today()}))
        return f"park {target}"

    def _fix_frontmatter(self, item: dict) -> str:
        p = self._path(item["target"])
        if not p.is_file():
            raise RuntimeError(f"{item['target']} is not a file")
        updates = dict(item.get("set") or {})
        unset = list(item.get("unset") or [])
        if not updates and not unset:
            raise RuntimeError("fix_frontmatter needs set and/or unset")
        self._write(p, frontmatter.set_keys(p.read_text(), updates, unset=unset))
        what = ", ".join(f"{k}={v}" for k, v in updates.items()) + ("" if not unset else " unset " + ",".join(unset))
        return f"fix_frontmatter {ladder.norm(item['target'])}: {what}"

    def _collapse_handoff(self, item: dict) -> str:
        src = self._path(item["target"])
        if not src.is_file() or not src.name.startswith("handoff-"):
            raise RuntimeError(f"collapse_handoff takes a handoff-*.md file, got {item['target']}")
        archive = self._path(str(src.parent.relative_to(self.root) / "archive-handoffs.md"))
        content = src.read_text()
        head = archive.read_text() if archive.exists() else f"---\ncreated: {today()}\ntags: [handoff, archive]\nstatus: archived\n---\n\nCollapsed handoffs, verbatim, most recent first.\n"
        # the heading is exactly the filename so [[...archive-handoffs#handoff-x.md|...]] resolves
        section = f"\n\n## {src.name}\n\n> collapsed {today()} by glia, {item.get('id', '?')}\n\n{content.rstrip()}\n"
        self._write(archive, head.rstrip("\n") + "\n" + section)
        ladder.guard_delete(item["action"], removes=True, preserved_in=archive, content=content)
        git(self.root, "rm", "-q", "--", str(src.relative_to(self.root)))
        arel = str(archive.relative_to(self.root))
        relinked = self._relink_inbound(src.name, arel)
        extra = f", repointed {relinked} inbound link(s)" if relinked else ""
        return f"collapse_handoff {ladder.norm(item['target'])} -> {arel}{extra}"

    def _relink_inbound(self, name: str, arel: str) -> int:
        """Repoint dangling [[handoff-x]] links at the new archive heading (glia.handoff_relink).
        Files the ladder forbids are left alone; changed files join this item's commit."""
        def skip(rel: str) -> bool:
            try:
                ladder.check_path(self.root, rel)
                return False
            except Refused:
                return True
        res = handoff_relink.relink(str(self.root), name, arel, skip=skip)
        for c in res["changed"]:
            git(self.root, "add", "--", c["path"])
        return sum(len(c["links"]) for c in res["changed"])

    def _move_loose_file(self, item: dict) -> str:
        src = self._path(item["target"])
        if not src.is_file():
            raise RuntimeError(f"move_loose_file takes a file, got {item['target']}")
        dst = self._path(item["dest"])
        self._mv(src, dst)
        return f"move_loose_file {ladder.norm(item['target'])} -> {ladder.norm(item['dest'])}"

    ACTIONS = {"archive": _archive, "archive_task": _archive_task, "park": _park, "fix_frontmatter": _fix_frontmatter,
               "collapse_handoff": _collapse_handoff, "move_loose_file": _move_loose_file}

    # ------------------------------------------------------------------ public
    def apply_item(self, item: dict) -> dict:
        """Apply one approved item as one commit. Raises Refused (tree untouched) when the ladder says no."""
        self._assert_on_branch()
        action = str(item.get("action", ""))
        ladder.guard_delete(action)
        if action not in self.ACTIONS:
            raise Refused("delete", f"{action!r} is not a glia action; only {sorted(self.ACTIONS)} exist")
        before = git(self.root, "rev-parse", "HEAD").strip()
        try:
            summary = self.ACTIONS[action](self, item)
        except Exception:
            git(self.root, "reset", "-q", "--hard", before)
            git(self.root, "clean", "-qfd")
            raise
        sha = self._commit(item, summary)
        return {"id": item.get("id"), "action": action, "target": ladder.norm(item["target"]), "status": "applied",
                "sha": sha, "revert": f"git revert {sha}", "summary": summary}

    def apply_items(self, items: list[dict]) -> list[dict]:
        out = []
        for item in items:
            try:
                out.append(self.apply_item(item))
            except Refused as e:
                out.append({"id": item.get("id"), "action": item.get("action"), "target": item.get("target"),
                            "status": "refused", "guard": e.guard, "reason": e.reason})
        return out

    def push(self, ref: str | None = None, remote: str = "origin") -> str:
        ref = ref or self.branch
        ladder.guard_main_push(self.branch, push_ref=ref)
        return git(self.root, "push", "-u", remote, f"{self.branch}:{ref}")


# ---------------------------------------------------------------------- replay / compare

def _tree_files(root: Path, rev: str, scope: list[str]) -> dict[str, dict]:
    names = git(root, "ls-tree", "-r", "--name-only", rev, "--", *[s.rstrip("/") for s in scope]).split("\n")
    out = {}
    for n in names:
        if not n or not n.endswith(".md"):
            continue
        text = git(root, "show", f"{rev}:{n}")
        out[n] = frontmatter.parse(text)
    return out


def compare_names_and_frontmatter(root: Path, branch: str, expected_rev: str, scope: list[str], out_of_scope: list[str]) -> list[dict]:
    got = _tree_files(root, branch, scope)
    want = _tree_files(root, expected_rev, scope)
    skip = set(ladder.norm(p) for p in out_of_scope)
    diffs = []
    for p in sorted(set(got) | set(want)):
        if p in skip:
            continue
        if p not in want:
            diffs.append({"path": p, "kind": "only-in-replay"})
        elif p not in got:
            diffs.append({"path": p, "kind": "only-in-expected"})
        elif got[p] != want[p]:
            diffs.append({"path": p, "kind": "frontmatter", "replay": got[p], "expected": want[p]})
    return diffs


def build_expected(clone: Path, changes: list[dict]) -> tuple[str, str]:
    """Make the expected commit by hand, with plain git and text edits (no executor, no frontmatter
    helper), on a side branch. Returns (base, expect) and leaves HEAD detached at base."""
    base = git(clone, "rev-parse", "HEAD").strip()
    git(clone, "checkout", "-q", "-b", "expected")
    for ch in changes:
        if "mv" in ch:
            src, dst = ch["mv"]
            (clone / dst).parent.mkdir(parents=True, exist_ok=True)
            git(clone, "mv", "--", src, dst)
        elif "replace" in ch:
            p = clone / ch["replace"]
            text = p.read_text()
            if ch["old"] not in text:
                raise ValueError(f"{ch['replace']}: {ch['old']!r} not found")
            p.write_text(text.replace(ch["old"], ch["new"], 1))
            git(clone, "add", "--", ch["replace"])
        elif "write" in ch:
            p = clone / ch["write"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(ch["text"])
            git(clone, "add", "--", ch["write"])
        else:
            raise ValueError(f"unknown expected change {ch!r}")
    git(clone, "commit", "-q", "-m", "hand-made restructure (expected result)")
    expect = git(clone, "rev-parse", "HEAD").strip()
    git(clone, "checkout", "-q", "--detach", base)
    return base, expect


def replay(fixture_path: Path, scratch: bool, out_dir: Path | None = None) -> int:
    fx = json.loads(Path(fixture_path).read_text())
    if not scratch:
        print("replay only runs on a /tmp scratch clone (--scratch); the live vault and the dedicated clone are never replay targets", file=sys.stderr)
        return 2
    source = fx["source_repo"]
    out_dir = Path(out_dir) if out_dir else REPO_DIR / ".scratch"
    result_repo = out_dir / "replay.git"
    tmp = Path(tempfile.mkdtemp(prefix="glia-scratch-", dir="/tmp"))
    try:
        if source == "fakevault":
            from glia import fakevault
            _, clone = fakevault.build(tmp / "fake")
            base, expect = build_expected(clone, fx["expected_changes"])
        else:
            clone = tmp / "clone"
            subprocess.run(["git", "clone", "-q", "--no-checkout", source, str(clone)], check=True)
            base, expect = fx["base"], fx["expect"]
        git(clone, "remote", "set-url", "--push", "origin", "glia-scratch-never-pushes")
        git(clone, "config", "user.name", "glia")
        git(clone, "config", "user.email", "glia@example.com")
        git(clone, "checkout", "-q", "-b", fx["branch"], base)
        ex = Executor(clone, fx["branch"])
        results = ex.apply_items(fx["items"])
        applied = [r for r in results if r["status"] == "applied"]
        for r in results:
            if r["status"] != "applied":
                print(f"REFUSED id={r['id']} action={r['action']} target={r['target']} guard={r['guard']}: {r['reason']}")
        diffs = compare_names_and_frontmatter(clone, fx["branch"], expect, fx["scope"], [o["path"] for o in fx["out_of_scope"]])
        for d in diffs:
            keys = sorted(k for k in set(d.get("replay", {})) | set(d.get("expected", {})) if d.get("replay", {}).get(k) != d.get("expected", {}).get(k)) if d["kind"] == "frontmatter" else []
            print(f"DIFF path={d['path']} kind={d['kind']}" + "".join(f" {k}: replay={d['replay'].get(k)!r} expected={d['expected'].get(k)!r}" for k in keys))
        reverts = [r["revert"] for r in applied]
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "replay-reverts.txt").write_text("\n".join(reverts) + "\n")
        (out_dir / "replay-results.json").write_text(json.dumps(results, indent=1))
        if result_repo.exists():
            shutil.rmtree(result_repo)
        subprocess.run(["git", "clone", "-q", "--bare", str(clone), str(result_repo)], check=True)
        by_action = {}
        for it in fx["items"]:
            by_action[it["action"]] = by_action.get(it["action"], 0) + 1
        print(f"REPLAY base={base[:8]} expect={expect[:8]} scope={','.join(fx['scope'])} actions=" + ",".join(f"{k}:{v}" for k, v in sorted(by_action.items()))
              + f" out_of_scope={len(fx['out_of_scope'])} result_repo={result_repo} scratch_deleted=yes")
        ok = len(applied) == len(fx["items"]) and not diffs
        print(f"{'REPLAY-OK' if ok else 'REPLAY-FAIL'} items={len(fx['items'])} applied={len(applied)} diff_vs_{expect[:8]}={len(diffs)} branch={fx['branch']} revert_lines={len(reverts)}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replay", metavar="FIXTURE", help="replay an approved-items fixture on a scratch clone")
    ap.add_argument("--scratch", action="store_true", help="required with --replay: work on a /tmp scratch clone")
    ap.add_argument("--clone", help="dedicated vault clone (ticked-items mode)")
    ap.add_argument("--branch", help="glia/* branch to write on (ticked-items mode)")
    ap.add_argument("--items", help="JSON file of ticked items (ticked-items mode)")
    a = ap.parse_args(argv)
    if a.replay:
        return replay(Path(a.replay), a.scratch)
    if a.clone and a.branch and a.items:
        ex = Executor(Path(a.clone), a.branch)
        results = ex.apply_items(json.loads(Path(a.items).read_text()))
        print(json.dumps(results, indent=1))
        return 0 if all(r["status"] == "applied" for r in results) else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
