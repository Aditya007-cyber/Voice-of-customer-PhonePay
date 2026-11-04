"""SQLite storage + every number the product shows. LLM code never computes counts: stats.py does, with SQL."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA = """
DROP TABLE IF EXISTS records; DROP TABLE IF EXISTS pain_points; DROP TABLE IF EXISTS clusters;
DROP TABLE IF EXISTS churn;
CREATE TABLE records (
  id TEXT PRIMARY KEY, source TEXT, timestamp TEXT, raw_text TEXT, redacted_text TEXT,
  rating INTEGER, app_version TEXT, user_segment TEXT, is_synthetic INTEGER, pii_found INTEGER
);
CREATE TABLE pain_points (
  pp_id INTEGER PRIMARY KEY AUTOINCREMENT, record_id TEXT, category TEXT, severity INTEGER,
  sentiment TEXT, issue TEXT, evidence_quote TEXT, cluster_id INTEGER
);
CREATE TABLE clusters (cluster_id INTEGER PRIMARY KEY, name TEXT, category TEXT, purity REAL, top_terms TEXT);
CREATE TABLE churn (record_id TEXT PRIMARY KEY, flag INTEGER, trigger_phrase TEXT, competitor TEXT);
CREATE INDEX idx_pp_cluster ON pain_points(cluster_id);
CREATE INDEX idx_rec_ts ON records(timestamp);
"""


def connect(path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def as_of(conn) -> datetime:
    ts = conn.execute("SELECT MAX(timestamp) FROM records").fetchone()[0]
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")


def windows(conn, days: int = 14):
    end = as_of(conn)
    recent_start = end - timedelta(days=days)
    prior_start = end - timedelta(days=2 * days)
    f = "%Y-%m-%d %H:%M:%S"
    return {"end": end.strftime(f), "recent_start": recent_start.strftime(f), "prior_start": prior_start.strftime(f)}


def _count_records(conn, start=None, end=None, source=None):
    q, a = "SELECT COUNT(*) FROM records WHERE 1=1", []
    if start:
        q += " AND timestamp > ?"; a.append(start)
    if end:
        q += " AND timestamp <= ?"; a.append(end)
    if source:
        q += " AND source = ?"; a.append(source)
    return conn.execute(q, a).fetchone()[0]


def _ids(cluster_ids):
    ids = [cluster_ids] if isinstance(cluster_ids, int) else list(cluster_ids)
    return ids, ",".join("?" * len(ids))


def _count_cluster_records(conn, cluster_ids, start=None, end=None, source=None):
    ids, ph = _ids(cluster_ids)
    q = ("SELECT COUNT(DISTINCT p.record_id) FROM pain_points p JOIN records r ON r.id = p.record_id "
         f"WHERE p.cluster_id IN ({ph})")
    a = list(ids)
    if start:
        q += " AND r.timestamp > ?"; a.append(start)
    if end:
        q += " AND r.timestamp <= ?"; a.append(end)
    if source:
        q += " AND r.source = ?"; a.append(source)
    return conn.execute(q, a).fetchone()[0]


def cluster_stats(conn, cluster_id, days: int = 14) -> dict:
    """The single source of truth for any figure shown about a cluster (int) or a theme (list of cluster ids)."""
    ids, ph = _ids(cluster_id)
    w = windows(conn, days)
    total_all = _count_records(conn)
    total_recent = _count_records(conn, w["recent_start"], w["end"])
    total_prior = _count_records(conn, w["prior_start"], w["recent_start"])
    n_all = _count_cluster_records(conn, cluster_id)
    n_recent = _count_cluster_records(conn, cluster_id, w["recent_start"], w["end"])
    n_prior = _count_cluster_records(conn, cluster_id, w["prior_start"], w["recent_start"])
    avg_sev = conn.execute(f"SELECT AVG(severity) FROM pain_points WHERE cluster_id IN ({ph})", ids).fetchone()[0] or 0
    churn_n = conn.execute(
        "SELECT COUNT(DISTINCT p.record_id) FROM pain_points p JOIN churn c ON c.record_id = p.record_id "
        f"WHERE p.cluster_id IN ({ph}) AND c.flag = 1", ids).fetchone()[0]
    by_source = {r[0]: r[1] for r in conn.execute(
        "SELECT r.source, COUNT(DISTINCT r.id) FROM pain_points p JOIN records r ON r.id = p.record_id "
        f"WHERE p.cluster_id IN ({ph}) GROUP BY r.source ORDER BY 2 DESC", ids)}
    source_totals = {r[0]: r[1] for r in conn.execute("SELECT source, COUNT(*) FROM records GROUP BY source")}
    by_version = {r[0] or "n/a": r[1] for r in conn.execute(
        "SELECT r.app_version, COUNT(DISTINCT r.id) FROM pain_points p JOIN records r ON r.id = p.record_id "
        f"WHERE p.cluster_id IN ({ph}) GROUP BY r.app_version ORDER BY 1", ids)}
    return {
        "window_days": days, "n_all": n_all, "total_all": total_all,
        "n_recent": n_recent, "total_recent": total_recent,
        "n_prior": n_prior, "total_prior": total_prior,
        "share_all": n_all / total_all if total_all else 0,
        "share_recent": n_recent / total_recent if total_recent else 0,
        "share_prior": n_prior / total_prior if total_prior else 0,
        "avg_severity": round(avg_sev, 2), "churn_records": churn_n,
        "by_source": by_source, "source_totals": source_totals, "by_version": by_version,
    }


def weekly_series(conn, cluster_id=None):
    """Records per ISO-week-start (Monday) bucket; if cluster_id given, only that cluster's records."""
    if cluster_id is None:
        rows = conn.execute("SELECT date(timestamp, 'weekday 0', '-6 days') AS wk, COUNT(*) FROM records GROUP BY wk ORDER BY wk")
    else:
        ids, ph = _ids(cluster_id)
        rows = conn.execute(
            "SELECT date(r.timestamp, 'weekday 0', '-6 days') AS wk, COUNT(DISTINCT r.id) FROM pain_points p "
            f"JOIN records r ON r.id = p.record_id WHERE p.cluster_id IN ({ph}) GROUP BY wk ORDER BY wk", ids)
    return [(r[0], r[1]) for r in rows]
