"""Stable output envelope.

All domain content is produced by the engine; this module only wraps it and
sorts collections so that byte-for-byte output is independent of input order.
"""
from __future__ import annotations

from typing import Any


def _sort_in_place(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sort_in_place(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_sort_in_place(item) for item in value]
    return value


def build_result(project: str, data: dict) -> dict:
    envelope = {
        "project": project,
        "status": "ok",
        **data,
    }
    return _sort_in_place(envelope)
