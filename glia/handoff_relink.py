#!/usr/bin/env python3
"""handoff_relink — repoint wikilinks at an archived handoff to its archive heading.

When a handoff note `handoff-<x>.md` is archived, its text moves into some
`archive-handoffs.md` under a heading `## handoff-<x>.md` and the file goes away.
Every `[[handoff-<x>]]` left in the vault then dangles. This rewrites each one to

    [[<archive path without .md>#handoff-<x>.md|<alias or handoff-x>]]

The full archive path is used because many project folders each own an archive-handoffs.md.

Rules
  * only dangling links change: a link whose target basename still exists as a .md
    file anywhere in the vault is left alone;
  * an existing alias is kept; with no alias the alias is the bare handoff name;
  * inside a markdown table row the alias pipe is written `\\|` (and an already
    escaped `\\|` stays escaped); frontmatter never gets the escape (YAML);
  * fenced code blocks and inline code spans are never touched;
  * `Resources/sources/` (immutable captures), `.git`, `.claude`, `.obsidian`,
    `.trash` are never touched;
  * a `skip(rel)` predicate (or --skip-dirty: any file git reports as modified or
    untracked) leaves a file alone and reports it as skipped.

Stdlib only.

CLI
  handoff_relink.py --vault V --handoff handoff-x[.md] --archive Projects/p/archive-handoffs.md [--dry-run] [--skip-dirty] [--json]
  handoff_relink.py --vault V --repair-all [--dry-run] [--skip-dirty] [--json]
Exit 0 on success, 2 on usage error or a missing archive heading.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

SKIP_DIRS = {".git", ".claude", ".obsidian", ".trash", "node_modules"}
SKIP_PREFIXES = ("Resources/sources/",)

# [[ target (#sub)? (| or \| alias)? ]] — target may carry a folder path and/or .md
LINK_RE = re.compile(
    r"\[\[(?P<target>[^\]\|#\n]+?)(?P<sub>#[^\]\|\n]*)?(?:(?P<pipe>\\?\|)(?P<alias>[^\]\n]*))?\]\]")
HEADING_RE = re.compile(r"^#{1,6}[ \t]+(?P<h>handoff-[^\s#\[\]\|]+?)[ \t]*$")
FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
CODE_SPAN_RE = re.compile(r"(`+)(?:.+?)\1")


class RelinkError(Exception):
    pass


def handoff_key(name: str) -> str:
    """'handoff-x', 'handoff-x.md', 'Projects/p/handoff-x.md' -> 'handoff-x' (lower-cased)."""
    base = name.strip().rsplit("/", 1)[-1]
    if base.lower().endswith(".md"):
        base = base[:-3]
    return base.lower()


def iter_md(vault: str):
    """Yield vault-relative paths of every .md file that may be edited."""
    for root, dirs, files in os.walk(vault):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for f in sorted(files):
            if not f.endswith(".md"):
                continue
            rel = os.path.relpath(os.path.join(root, f), vault).replace(os.sep, "/")
            if rel.startswith(SKIP_PREFIXES):
                continue
            yield rel


def all_basenames(vault: str) -> set:
    """Lower-cased basenames (no .md) of every .md in the vault, skipped dirs excluded
    (Resources/sources counts: a link to a capture there is not dangling)."""
    out = set()
    for root, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".md"):
                out.add(f[:-3].lower())
    return out


def archive_headings(text: str) -> dict:
    """{handoff-key: heading text} for handoff headings outside code fences."""
    out, fence = {}, None
    for line in text.splitlines():
        m = FENCE_RE.match(line)
        if m:
            tok = m.group(1)[0]
            if fence is None:
                fence = tok
            elif fence == tok:
                fence = None
            continue
        if fence:
            continue
        h = HEADING_RE.match(line)
        if h:
            out.setdefault(handoff_key(h.group("h")), h.group("h"))
    return out


def heading_index(vault: str) -> dict:
    """{handoff-key: [(archive rel path, heading text), ...]} across every archive-handoffs.md."""
    idx = {}
    for rel in iter_md(vault):
        if rel.rsplit("/", 1)[-1] != "archive-handoffs.md":
            continue
        with open(os.path.join(vault, rel), encoding="utf-8", errors="replace") as fh:
            for k, h in archive_headings(fh.read()).items():
                idx.setdefault(k, []).append((rel, h))
    return idx


def _code_spans(line: str):
    return [m.span() for m in CODE_SPAN_RE.finditer(line)]


def _is_table_row(line: str) -> bool:
    return line.lstrip().startswith("|")


def rewrite_text(text: str, resolve) -> tuple:
    """Rewrite dangling handoff links in `text`.

    resolve(key) -> (archive_rel, heading) to rewrite, or None to leave the link.
    Returns (new_text, [(key, old_link, new_link), ...])."""
    lines = text.splitlines(keepends=True)
    changes = []
    fence = None
    in_fm = bool(lines) and lines[0].rstrip("\r\n") == "---"
    for i, line in enumerate(lines):
        if in_fm and i > 0 and line.rstrip("\r\n") in ("---", "..."):
            in_fm = False
            continue
        if not in_fm:
            m = FENCE_RE.match(line)
            if m:
                tok = m.group(1)[0]
                if fence is None:
                    fence = tok
                elif fence == tok:
                    fence = None
                continue
            if fence:
                continue
        if "[[" not in line:
            continue
        spans = _code_spans(line)
        table = (not in_fm) and _is_table_row(line)

        def sub(m):
            if any(a <= m.start() < b for a, b in spans):
                return m.group(0)
            key = handoff_key(m.group("target"))
            if not key.startswith("handoff-"):
                return m.group(0)
            hit = resolve(key)
            if not hit:
                return m.group(0)
            arel, heading = hit
            alias = m.group("alias")
            if alias is None or not alias.strip():
                alias = m.group("target").strip().rsplit("/", 1)[-1]
                if alias.lower().endswith(".md"):
                    alias = alias[:-3]
            pipe = m.group("pipe") or ""
            if in_fm:
                pipe = "|"
            elif pipe == "\\|" or table:
                pipe = "\\|"
            else:
                pipe = "|"
            new = f"[[{arel[:-3] if arel.endswith('.md') else arel}#{heading}{pipe}{alias}]]"
            changes.append((key, m.group(0), new))
            return new

        lines[i] = LINK_RE.sub(sub, line)
    return "".join(lines), changes


def _atomic_write(path: str, text: str) -> None:
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".relink-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        st = os.stat(path)
        os.chmod(tmp, st.st_mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def dirty_paths(vault: str) -> set:
    """Vault-relative paths git reports as modified, staged, or untracked."""
    p = subprocess.run(["git", "-C", vault, "status", "--porcelain", "-z", "--untracked-files=all"],
                       capture_output=True, text=True, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    if p.returncode != 0:
        raise RelinkError(f"git status failed in {vault}: {p.stderr.strip()}")
    out, parts, i = set(), p.stdout.split("\0"), 0
    while i < len(parts):
        e = parts[i]
        i += 1
        if len(e) < 4:
            continue
        out.add(e[3:])
        if e[0] in "RC":  # rename/copy: next entry is the source path
            if i < len(parts) and parts[i]:
                out.add(parts[i])
            i += 1
    return out


def _run(vault, resolve, dry, skip):
    """Apply `resolve` over every editable note. Returns (changed, skipped) where
    changed = [{"path", "links": [{"handoff", "old", "new"}]}] and skipped = same shape."""
    changed, skipped = [], []
    for rel in iter_md(vault):
        path = os.path.join(vault, rel)
        try:
            with open(path, encoding="utf-8", newline="") as fh:
                text = fh.read()
        except (UnicodeDecodeError, OSError):
            continue
        if "[[" not in text or "handoff-" not in text.lower():
            continue
        new, ch = rewrite_text(text, resolve)
        if not ch:
            continue
        rec = {"path": rel, "links": [{"handoff": k, "old": o, "new": n} for k, o, n in ch]}
        if skip and skip(rel):
            skipped.append(rec)
            continue
        if not dry:
            _atomic_write(path, new)
        changed.append(rec)
    return changed, skipped


def relink(vault: str, handoff: str, archive_rel: str, *, dry: bool = False, skip=None) -> dict:
    """Rewrite every dangling link to `handoff` so it points at its heading in `archive_rel`.

    Call it after the handoff file is gone. Raises RelinkError if the archive lacks the heading."""
    vault = os.path.abspath(vault)
    key = handoff_key(handoff)
    archive_rel = archive_rel.replace(os.sep, "/").lstrip("/")
    apath = os.path.join(vault, archive_rel)
    if not os.path.isfile(apath):
        raise RelinkError(f"archive not found: {archive_rel}")
    with open(apath, encoding="utf-8", errors="replace") as fh:
        heading = archive_headings(fh.read()).get(key)
    if not heading:
        raise RelinkError(f"{archive_rel} has no '## {key}.md' heading")
    names = all_basenames(vault)
    if key in names:  # the handoff (or a same-named note) still exists: its links still resolve
        return {"handoff": key, "archive": archive_rel, "changed": [], "skipped": [], "note": "target still exists"}

    def resolve(k):
        return (archive_rel, heading) if k == key else None

    changed, skipped = _run(vault, resolve, dry, skip)
    return {"handoff": key, "archive": archive_rel, "changed": changed, "skipped": skipped}


def repair_all(vault: str, *, dry: bool = False, skip=None) -> dict:
    """Find every dangling [[handoff-*]] link and rewrite it when exactly one
    archive-handoffs.md carries its heading. Links with 0 or >1 candidate archives
    are reported in `unresolved` and left alone."""
    vault = os.path.abspath(vault)
    names = all_basenames(vault)
    idx = heading_index(vault)
    unresolved = {}

    def resolve(k):
        if k in names:
            return None
        hits = idx.get(k, [])
        if len(hits) == 1:
            return hits[0]
        return None

    # collect unresolved dangling links (read-only pass over the same files)
    def note_unresolved(k):
        if k in names:
            return None
        hits = idx.get(k, [])
        if len(hits) != 1:
            unresolved.setdefault(k, {"handoff": k, "archives": [h[0] for h in hits], "links": 0, "files": set()})
            unresolved[k]["links"] += 1
        return None

    for rel in iter_md(vault):
        try:
            with open(os.path.join(vault, rel), encoding="utf-8", newline="") as fh:
                text = fh.read()
        except (UnicodeDecodeError, OSError):
            continue
        if "handoff-" not in text.lower():
            continue
        before = {k: v["links"] for k, v in unresolved.items()}
        rewrite_text(text, note_unresolved)
        for k, v in unresolved.items():
            if v["links"] != before.get(k, 0):
                v["files"].add(rel)

    changed, skipped = _run(vault, resolve, dry, skip)
    unres = []
    for k in sorted(unresolved):
        v = unresolved[k]
        unres.append({"handoff": k, "reason": "no archive heading" if not v["archives"] else "heading in >1 archive",
                      "archives": v["archives"], "links": v["links"], "files": sorted(v["files"])})
    return {"changed": changed, "skipped": skipped, "unresolved": unres}


def _summary(res: dict, dry: bool) -> str:
    n_links = sum(len(c["links"]) for c in res["changed"])
    out = [f"{'would rewrite' if dry else 'rewrote'} {n_links} link(s) in {len(res['changed'])} file(s)"]
    for c in res["changed"]:
        out.append(f"  {c['path']}: {len(c['links'])}")
    if res.get("skipped"):
        out.append(f"skipped {len(res['skipped'])} file(s) (uncommitted or owned elsewhere):")
        for c in res["skipped"]:
            out.append(f"  {c['path']}: {len(c['links'])} link(s) left")
    if "unresolved" in res:
        out.append(f"unresolved {len(res['unresolved'])} handoff target(s), "
                   f"{sum(u['links'] for u in res['unresolved'])} link(s), left as they are:")
        for u in res["unresolved"]:
            extra = f" [{', '.join(u['archives'])}]" if u["archives"] else ""
            out.append(f"  {u['handoff']}: {u['reason']}{extra}, {u['links']} link(s) in {', '.join(u['files'])}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--vault", default=os.environ.get("OBSIDIAN_VAULT_PATH", "."))
    ap.add_argument("--handoff", help="archived handoff name, e.g. handoff-2026-09-02-x(.md)")
    ap.add_argument("--archive", help="vault-relative archive-handoffs.md holding its heading")
    ap.add_argument("--repair-all", action="store_true", help="fix every dangling [[handoff-*]] link with one archive match")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-dirty", action="store_true", help="leave files with uncommitted changes alone")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    skip = None
    if a.skip_dirty:
        dirty = dirty_paths(a.vault)
        skip = dirty.__contains__
    try:
        if a.repair_all:
            if a.handoff or a.archive:
                ap.error("--repair-all takes no --handoff/--archive")
            res = repair_all(a.vault, dry=a.dry_run, skip=skip)
        else:
            if not (a.handoff and a.archive):
                ap.error("need --handoff and --archive, or --repair-all")
            res = relink(a.vault, a.handoff, a.archive, dry=a.dry_run, skip=skip)
    except RelinkError as e:
        print(f"handoff_relink: {e}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=1) if a.json else _summary(res, a.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
