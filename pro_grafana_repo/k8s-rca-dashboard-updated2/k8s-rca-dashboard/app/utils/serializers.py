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


def paginate_query(query, page: int | None = None, page_size: int | None = None, fallback_limit: int | None = None):
    """
    Paginate a query. If page is None, it returns a simple list of rows limited by fallback_limit.
    If page is not None, it returns a dict with pagination details.
    """
    if page is None:
        if fallback_limit is not None:
            query = query.limit(fallback_limit)
        return rows_to_list(query.all())
    
    page = max(1, page)
    page_size = max(1, page_size or 20)
    
    total = query.count()
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    pages = (total + page_size - 1) // page_size if total > 0 else 0
    
    return {
        "items": rows_to_list(rows),
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages
    }


def paginate_list(items: list, page: int | None = None, page_size: int | None = None, fallback_limit: int | None = None):
    """
    Paginate a Python list. If page is None, return standard list limited by fallback_limit.
    If page is not None, return paginated dict envelope.
    """
    if page is None:
        if fallback_limit is not None:
            return items[:fallback_limit]
        return items
    
    page = max(1, page)
    page_size = max(1, page_size or 20)
    
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    sliced = items[start:end]
    pages = (total + page_size - 1) // page_size if total > 0 else 0
    
    return {
        "items": sliced,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages
    }

