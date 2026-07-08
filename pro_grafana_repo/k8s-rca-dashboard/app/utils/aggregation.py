"""
Generic "latest row per key" aggregation helpers shared across all routers.

SQLite doesn't support DISTINCT ON (Postgres syntax), so we use a
correlated-subquery approach: for each distinct key value, find the row
whose `id` is the maximum (i.e. most recently inserted).
"""

from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func


def latest_metric_per_pod(db: Session, Model):
    """
    Return the single most-recent row of `Model` for each distinct pod_uid.
    `Model` must have columns: id, pod_uid, timestamp.
    """
    subq = (
        db.query(func.max(Model.id).label("max_id"))
        .group_by(Model.pod_uid)
        .subquery()
    )
    return db.query(Model).join(subq, Model.id == subq.c.max_id).all()


def latest_metric_per_node(db: Session, Model):
    """
    Return the single most-recent row of `Model` for each distinct node_name.
    `Model` must have columns: id, node_name, timestamp.
    """
    subq = (
        db.query(func.max(Model.id).label("max_id"))
        .group_by(Model.node_name)
        .subquery()
    )
    return db.query(Model).join(subq, Model.id == subq.c.max_id).all()


def metric_history(
    db: Session,
    Model,
    key_col: str,
    key_val: str,
    minutes: int = 60,
    limit: int = 500,
):
    """
    Return up to `limit` rows for `key_col == key_val` within the last
    `minutes` minutes, ordered oldest-first (suitable for charting).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    col = getattr(Model, key_col)
    return (
        db.query(Model)
        .filter(col == key_val, Model.timestamp >= cutoff)
        .order_by(Model.timestamp.asc())
        .limit(limit)
        .all()
    )
