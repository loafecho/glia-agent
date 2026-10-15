#!/usr/bin/env python3
"""Disable one ladder guard in a COPY of glia/ladder.py (used only by scripts/prove-red.sh).

Rewrites `def guard_<name>(...)` so its body is a bare `return None` — the guard stops refusing — and
prints the guard names when asked. Never run against the real tree; prove-red.sh works in a mktemp copy.
"""
import ast
import sys
from pathlib import Path


def guard_names(src: str) -> list[str]:
    tree = ast.parse(src)
    return sorted(n.name[len("guard_"):] for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("guard_"))


def disable(src: str, guard: str) -> str:
    tree = ast.parse(src)
    hit = False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == f"guard_{guard}":
            keep = [node.body[0]] if node.body and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], "value", None), ast.Constant) else []
            node.body = keep + [ast.Return(value=ast.Constant(value=None))]
            hit = True
    if not hit:
        raise SystemExit(f"no guard_{guard} in ladder.py")
    return ast.unparse(tree) + "\n"


def main() -> int:
    path = Path(sys.argv[1])
    src = path.read_text()
    if sys.argv[2] == "--list":
        print("\n".join(guard_names(src)))
        return 0
    out = disable(src, sys.argv[2])
    if out == src:
        raise SystemExit("mutation produced identical source")
    path.write_text(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
