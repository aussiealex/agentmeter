"""Shared helpers for database query construction."""

from __future__ import annotations


def build_where(clauses: list[str]) -> str:
    """Join WHERE clauses safely. All clauses must be hardcoded strings."""
    if not clauses:
        return ""
    return "WHERE " + " AND ".join(clauses)
