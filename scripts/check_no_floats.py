"""Reject float usage from Mira Portfolio application source code."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

APPLICATION_DIRECTORY = Path("app")


class FloatUsageVisitor(ast.NodeVisitor):
    """Collect source locations where Python float usage appears."""

    def __init__(self) -> None:
        """Initialize the visitor without recorded violations."""
        self.violations: list[tuple[int, int, str]] = []

    def visit_Constant(self, node: ast.Constant) -> None:
        """Record float literals such as 1.25."""
        if isinstance(node.value, float):
            self.violations.append((node.lineno, node.col_offset, "float literal"))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        """Record direct float references, including annotations and constructors."""
        if node.id == "float":
            self.violations.append((node.lineno, node.col_offset, "float reference"))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Record qualified float references such as builtins.float."""
        if node.attr == "float":
            self.violations.append((node.lineno, node.col_offset, "float reference"))
        self.generic_visit(node)


def find_float_usage(source_path: Path) -> list[tuple[int, int, str]]:
    """Return all forbidden float usages found in one Python source file."""
    tree = ast.parse(source_path.read_text(encoding="utf-8-sig"), filename=str(source_path))
    visitor = FloatUsageVisitor()
    visitor.visit(tree)
    return visitor.violations


def main() -> int:
    """Check application source files and return a process status code."""
    violations_found = False
    for source_path in APPLICATION_DIRECTORY.rglob("*.py"):
        for line, column, message in find_float_usage(source_path):
            print(f"{source_path}:{line}:{column}: forbidden {message}; use Decimal instead")
            violations_found = True
    return 1 if violations_found else 0


if __name__ == "__main__":
    sys.exit(main())
