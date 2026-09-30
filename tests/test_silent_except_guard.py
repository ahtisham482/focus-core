"""Roadmap 1.3 guard: silent ``except ...: pass`` handlers must not return.

Walks the trees the 1.3 sweep covered (``focuscore/`` and
``dashboard/``, which include the launcher/desktop/tray/shield entry
modules) with the AST and fails on any ``except`` handler whose whole
body is ``pass``.

There is deliberately NO allowlist. Every former silent site now logs:
WARNING+ where a real failure degrades something the user relies on,
DEBUG where the exception is a control-flow signal (queue.Empty /
queue.Full), an expected condition (ActivityWatch stopped, a
pre-migration database), or a recurring cosmetic UI operation. A new
pass-only handler therefore means a new silent-degradation site, and
this test is the bill that stops it recurring.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TREES = (REPO_ROOT / "focuscore", REPO_ROOT / "dashboard")


def _pass_only_handlers():
    """(path, line, handler description) for every pass-only except."""
    found = []
    for tree in TREES:
        for path in sorted(tree.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            module = ast.parse(path.read_text(encoding="utf-8"),
                               filename=str(path))
            parents = {}
            for node in ast.walk(module):
                for child in ast.iter_child_nodes(node):
                    parents[child] = node

            def enclosing(node):
                cur = node
                while cur in parents:
                    cur = parents[cur]
                    if isinstance(cur, (ast.FunctionDef,
                                        ast.AsyncFunctionDef)):
                        return cur.name
                return "<module>"

            for node in ast.walk(module):
                if isinstance(node, ast.ExceptHandler) and \
                        len(node.body) == 1 and \
                        isinstance(node.body[0], ast.Pass):
                    found.append((
                        str(path.relative_to(REPO_ROOT)),
                        node.lineno,
                        "except %s in %s()" % (
                            ast.unparse(node.type) if node.type else "bare",
                            enclosing(node)),
                    ))
    return found


def test_no_silent_except_pass_handlers():
    offenders = _pass_only_handlers()
    assert offenders == [], (
        "pass-only except handlers swallow failures silently; log at "
        "WARNING+ (or DEBUG for control-flow/expected/cosmetic sites) "
        "instead:\n" + "\n".join(
            "%s:%d  %s" % (p, line, desc)
            for p, line, desc in offenders))


def test_guard_actually_walks_the_trees():
    # Meta-check: the guard must be looking at real files, otherwise the
    # test above is vacuous.
    py_files = [p for tree in TREES for p in tree.rglob("*.py")
                if "__pycache__" not in p.parts]
    assert len(py_files) > 30
