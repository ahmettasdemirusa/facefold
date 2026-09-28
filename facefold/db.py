"""SQLite storage: schema, connections and small query helpers.

The database is the single source of truth. Output folders are disposable and
can always be rebuilt from it, which is what makes "change a rule, re-run"
cheap and safe.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterable, Iterator

import numpy as np

from . import config

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    id            INTEGER PRIMARY KEY,
    path          TEXT NOT NULL UNIQUE,
    source_root   TEXT,
    archive       TEXT,
    member        TEXT,
    file_name     TEXT,
    ext           TEXT,
    size          INTEGER,
    mtime         REAL,
    sha1          TEXT,
    phash         TEXT,
    width         INTEGER,
    height        INTEGER,
    sig           BLOB,
    orientation   INTEGER DEFAULT 1,
    taken_at      TEXT,
    taken_source  TEXT,
    camera        TEXT,
    kind          TEXT,
    gps_lat       REAL,
    gps_lon       REAL,
    face_count    INTEGER DEFAULT -1,
    duplicate_of  INTEGER REFERENCES photos(id) ON DELETE SET NULL,
    dup_kind      TEXT,
    state         TEXT NOT NULL DEFAULT 'new',
    error         TEXT,
    thumb         TEXT,
    added_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_photos_state ON photos(state);
CREATE INDEX IF NOT EXISTS ix_photos_sha1  ON photos(sha1);
CREATE INDEX IF NOT EXISTS ix_photos_phash ON photos(phash);
CREATE INDEX IF NOT EXISTS ix_photos_dup   ON photos(duplicate_of);
CREATE INDEX IF NOT EXISTS ix_photos_taken ON photos(taken_at);

CREATE TABLE IF NOT EXISTS persons (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    cover_face_id INTEGER,
    notes         TEXT,
    hidden        INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS clusters (
    id            INTEGER PRIMARY KEY,
    person_id     INTEGER REFERENCES persons(id) ON DELETE SET NULL,
    label         TEXT,
    size          INTEGER DEFAULT 0,
    centroid      BLOB,
    ignored       INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_clusters_person ON clusters(person_id);

CREATE TABLE IF NOT EXISTS faces (
    id            INTEGER PRIMARY KEY,
    photo_id      INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    bbox          TEXT,
    det_score     REAL,
    face_px       INTEGER,
    blur          REAL,
    yaw           REAL,
    age           INTEGER,
    gender        INTEGER,
    embedding     BLOB,
    cluster_id    INTEGER REFERENCES clusters(id) ON DELETE SET NULL,
    person_id     INTEGER REFERENCES persons(id) ON DELETE SET NULL,
    assign_source TEXT,
    confidence    REAL,
    thumb         TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_faces_photo   ON faces(photo_id);
CREATE INDEX IF NOT EXISTS ix_faces_person  ON faces(person_id);
CREATE INDEX IF NOT EXISTS ix_faces_cluster ON faces(cluster_id);

-- People linked to a photo by hand rather than by a detected face.
-- Needed because the detector misses faces: someone turned away, too far
-- back, half out of frame, or a photo that is of them without showing a
-- recognisable face at all. Without this the user can see the person is in
-- the photo but has no way to say so.
CREATE TABLE IF NOT EXISTS photo_people (
    photo_id      INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    person_id     INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    created_at    TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (photo_id, person_id)
);
CREATE INDEX IF NOT EXISTS ix_photo_people_person ON photo_people(person_id);

CREATE TABLE IF NOT EXISTS categories (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    rule_type     TEXT NOT NULL,
    people        TEXT NOT NULL DEFAULT '[]',
    options       TEXT NOT NULL DEFAULT '{}',
    folder        TEXT,
    enabled       INTEGER DEFAULT 1,
    sort_order    INTEGER DEFAULT 0,
    origin        TEXT NOT NULL DEFAULT 'manual',
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS links (
    id            INTEGER PRIMARY KEY,
    category_id   INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    photo_id      INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    dest          TEXT NOT NULL,
    mode          TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(category_id, photo_id)
);
CREATE INDEX IF NOT EXISTS ix_links_cat ON links(category_id);

CREATE TABLE IF NOT EXISTS kv (
    key           TEXT PRIMARY KEY,
    value         TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'running',
    total         INTEGER DEFAULT 0,
    done          INTEGER DEFAULT 0,
    message       TEXT,
    detail        TEXT,
    cancel        INTEGER DEFAULT 0,
    started_at    TEXT DEFAULT (datetime('now')),
    ended_at      TEXT
);
"""

_local = threading.local()


def connect() -> sqlite3.Connection:
    """Per-thread connection. Flask handlers and workers each get their own."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        config.ensure_dirs()
        conn = sqlite3.connect(config.DB_PATH, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def close() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# Indexes on columns that arrived after the first release. They cannot live in
# SCHEMA: on an existing database executescript runs before the migration has
# added the column, and CREATE INDEX on a missing column fails.
LATE_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_photos_kind ON photos(kind);
"""


def init_db() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.executescript(LATE_INDEXES)
    conn.execute("PRAGMA user_version=%d" % SCHEMA_VERSION)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a user's database was first created.

    CREATE TABLE IF NOT EXISTS leaves an existing table untouched, so new
    columns have to be added explicitly or upgrading would lose data.
    """
    wanted = {
        "photos": {"sig": "BLOB", "archive": "TEXT", "member": "TEXT",
                   "kind": "TEXT"},
        "categories": {"origin": "TEXT NOT NULL DEFAULT 'manual'"},
    }
    for table, columns in wanted.items():
        have = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)}
        for name, decl in columns.items():
            if name not in have:
                conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, decl))


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def q(sql: str, params: Iterable[Any] = ()) -> list:
    return connect().execute(sql, tuple(params)).fetchall()


def q1(sql: str, params: Iterable[Any] = ()):
    return connect().execute(sql, tuple(params)).fetchone()


def scalar(sql: str, params: Iterable[Any] = (), default: Any = 0) -> Any:
    row = q1(sql, params)
    if row is None or row[0] is None:
        return default
    return row[0]


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    conn = connect()
    cur = conn.execute(sql, tuple(params))
    conn.commit()
    return cur


# ---------------------------------------------------------------------------
# Embeddings are stored as raw float32: 512 floats cost 2 KB per face.
# ---------------------------------------------------------------------------

def pack_vec(vec) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def unpack_vec(blob):
    if not blob:
        return None
    return np.frombuffer(blob, dtype=np.float32)


# ---------------------------------------------------------------------------
# Settings and key/value store
# ---------------------------------------------------------------------------

def get_settings() -> config.Settings:
    row = q1("SELECT value FROM kv WHERE key='settings'")
    return config.Settings.from_json(row["value"] if row else "")


def save_settings(s: config.Settings) -> None:
    execute(
        "INSERT INTO kv(key, value) VALUES('settings', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (s.to_json(),),
    )


def get_kv(key: str, default: Any = None) -> Any:
    row = q1("SELECT value FROM kv WHERE key=?", (key,))
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return row["value"]


def set_kv(key: str, value: Any) -> None:
    execute(
        "INSERT INTO kv(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )


# ---------------------------------------------------------------------------
# Background jobs: workers write progress here, the web UI polls it.
# ---------------------------------------------------------------------------

def job_start(kind: str, total: int = 0, message: str = "") -> int:
    cur = execute(
        "INSERT INTO jobs(kind, state, total, done, message) VALUES(?,'running',?,0,?)",
        (kind, total, message),
    )
    return int(cur.lastrowid)


def job_update(job_id: int, *, done=None, total=None, message=None, detail=None) -> None:
    sets: list[str] = []
    params: list[Any] = []
    if done is not None:
        sets.append("done=?")
        params.append(done)
    if total is not None:
        sets.append("total=?")
        params.append(total)
    if message is not None:
        sets.append("message=?")
        params.append(message)
    if detail is not None:
        sets.append("detail=?")
        params.append(detail)
    if not sets:
        return
    params.append(job_id)
    execute("UPDATE jobs SET " + ", ".join(sets) + " WHERE id=?", params)


def job_finish(job_id: int, state: str = "done", message: str = "") -> None:
    execute(
        "UPDATE jobs SET state=?, message=COALESCE(NULLIF(?,''), message), "
        "ended_at=datetime('now') WHERE id=?",
        (state, message, job_id),
    )


def job_cancelled(job_id: int) -> bool:
    return bool(scalar("SELECT cancel FROM jobs WHERE id=?", (job_id,), 0))


def job_cancel(job_id: int) -> None:
    execute("UPDATE jobs SET cancel=1 WHERE id=?", (job_id,))


def active_job():
    return q1("SELECT * FROM jobs WHERE state='running' ORDER BY id DESC LIMIT 1")
