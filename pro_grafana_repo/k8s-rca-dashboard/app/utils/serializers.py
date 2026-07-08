"""
Generic SQLAlchemy row-to-dict serializer.

Models that define `to_dict()` use that; everything else falls back to
inspecting the mapper's column list so routers never need to hardcode
field names for models they didn't write.
"""

from datetime import datetime
from sqlalchemy.inspection import inspect as sa_inspect


def row_to_dict(row) -> dict:
    """Convert any SQLAlchemy mapped instance to a plain Python dict."""
    if row is None:
        return {}
    if hasattr(row, "to_dict"):
        return row.to_dict()
    result = {}
    for col in sa_inspect(type(row)).columns:
        val = getattr(row, col.key)
        if isinstance(val, datetime):
            val = val.isoformat()
        result[col.key] = val
    return result


def rows_to_list(rows) -> list[dict]:
    """Convert a list of SQLAlchemy rows to a list of dicts."""
    return [row_to_dict(r) for r in rows]
