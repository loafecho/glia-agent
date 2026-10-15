"""Frontmatter helpers that never touch the body.

split()/parse() read; set_keys() edits the YAML block line-by-line so an edit to one key leaves every
other byte of the note where it was (the merge gate relies on "frontmatter-only" being literally true).
"""
from __future__ import annotations

import yaml

FENCE = "---"


def split(text: str) -> tuple[str | None, str]:
    """Return (frontmatter_text_without_fences, body). frontmatter is None when the note has none."""
    if not text.startswith(FENCE + "\n") and text != FENCE:
        return None, text
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].rstrip() == FENCE:
            head = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1:])
            return head, body
    return None, text


def parse(text: str) -> dict:
    head, _ = split(text)
    if head is None:
        return {}
    try:
        data = yaml.safe_load(head)
    except yaml.YAMLError:
        return {"_unparseable": head}
    return data if isinstance(data, dict) else {}


def _render(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_render(v) for v in value) + "]"
    if value is None:
        return ""
    s = str(value)
    if s == "" or s[0] in "&*!|>'\"%@`#" or ": " in s or s.strip() != s:
        return yaml.safe_dump(s, default_flow_style=True).strip()
    return s


def set_keys(text: str, updates: dict, unset: list[str] | None = None) -> str:
    """Set/replace top-level keys and drop `unset` ones, editing only the frontmatter block."""
    unset = list(unset or [])
    head, body = split(text)
    lines = [] if head is None else head.split("\n")
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        key = line.split(":", 1)[0].strip() if ":" in line and not line.startswith((" ", "\t", "-", "#")) else None
        if key in unset:
            continue
        if key in updates and key not in seen:
            out.append(f"{key}: {_render(updates[key])}")
            seen.add(key)
            continue
        out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}: {_render(value)}")
    if head is None and not out:
        return text
    while out and out[-1] == "":
        out.pop()
    if head is None:
        return FENCE + "\n" + "\n".join(out) + "\n" + FENCE + "\n" + body
    return FENCE + "\n" + "\n".join(out) + "\n" + FENCE + "\n" + body
