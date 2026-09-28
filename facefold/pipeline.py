"""The expensive per-photo stage: decode, hash, thumbnail, detect faces.

Decoding a 12 MP HEIC and running the neural net cost roughly the same, and
they use different resources, so the two are overlapped: a small pool of
threads decodes ahead while the model works on the previous photo. Memory is
bounded by processing in chunks the size of the pool.
"""

from __future__ import annotations

import json
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from PIL import Image

from . import archives, config, db, faces as face_mod, imaging, kinds

ProgressFn = Callable[[int, int, str], None]


@dataclass
class LoadedPhoto:
    """Everything the decode step produced for one photo."""

    photo_id: int
    path: str
    img: Image.Image | None = None
    sha1: str | None = None
    phash: str | None = None
    sig: bytes | None = None
    meta: dict | None = None
    thumb: str | None = None
    error: str | None = None


def _thumb_path(photo_id: int) -> Path:
    return config.THUMB_DIR / str(photo_id // 1000) / ("%d.jpg" % photo_id)


def _face_thumb_path(photo_id: int, index: int) -> Path:
    return config.FACE_DIR / str(photo_id // 1000) / ("%d_%d.jpg" % (photo_id, index))


def _load_one(photo_id: int, path: str, mtime: float | None = None) -> LoadedPhoto:
    """Decode + metadata + hashes + thumbnail. Runs in a worker thread."""
    item = LoadedPhoto(photo_id=photo_id, path=path)
    try:
        # A photo inside a zip is read once, here, and the bytes are reused for
        # all three passes below. Letting each pass open the archive on its own
        # meant re-parsing the zip index three times per photo, and a Takeout
        # archive can hold tens of thousands of entries.
        data = imaging.read_source(path) if archives.is_inside_archive(path) else None
        img = imaging.load_image(path, data)
        item.img = img
        item.meta = imaging.read_meta(path, img=None, mtime=mtime, data=data)
        item.sha1 = imaging.sha1_file(path, data=data)
        item.phash = imaging.phash(img)
        item.sig = imaging.signature(img)
        item.thumb = imaging.save_thumb(img, _thumb_path(photo_id))
    except Exception as exc:  # noqa: BLE001
        item.error = "%s: %s" % (type(exc).__name__, exc)
        if item.img is not None:
            item.img.close()
            item.img = None
    return item


def process_new(
    job_id: int | None = None,
    limit: int | None = None,
    progress: ProgressFn | None = None,
    detect_faces: bool = True,
) -> dict:
    """Process every photo still in state 'new'.

    Safe to interrupt: each photo is committed on its own, so re-running picks
    up where it stopped.
    """
    settings = db.get_settings()
    workers = max(1, int(settings.workers or 2))

    sql = "SELECT id, path, mtime FROM photos WHERE state='new' ORDER BY id"
    if limit:
        sql += " LIMIT %d" % int(limit)
    todo = db.q(sql)
    total = len(todo)

    stats = {"total": total, "done": 0, "errors": 0, "faces": 0, "no_face": 0}
    if total == 0:
        return stats

    engine = face_mod.get_engine() if detect_faces else None
    conn = db.connect()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, total, workers):
            if job_id is not None and db.job_cancelled(job_id):
                stats["cancelled"] = True
                break

            chunk = todo[start:start + workers]
            loaded = list(pool.map(lambda r: _load_one(r["id"], r["path"], r["mtime"]), chunk))

            for item in loaded:
                try:
                    if item.error:
                        conn.execute(
                            "UPDATE photos SET state='error', error=? WHERE id=?",
                            (item.error[:500], item.photo_id),
                        )
                        stats["errors"] += 1
                        continue

                    meta = item.meta or {}
                    detections = []
                    if engine is not None:
                        detections = engine.analyze(
                            item.img,
                            max_side=settings.detect_max_side,
                            min_det_score=settings.min_det_score,
                            min_face_px=settings.min_face_px,
                        )

                    conn.execute("DELETE FROM faces WHERE photo_id=?", (item.photo_id,))
                    for idx, det in enumerate(detections):
                        thumb = ""
                        try:
                            thumb = imaging.save_face_crop(
                                item.img, det.bbox, _face_thumb_path(item.photo_id, idx)
                            )
                        except Exception:  # noqa: BLE001
                            pass
                        conn.execute(
                            "INSERT INTO faces(photo_id, bbox, det_score, face_px, blur, "
                            "yaw, age, gender, embedding, thumb) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (
                                item.photo_id, det.bbox_json(), det.det_score,
                                det.face_px, det.blur, det.yaw, det.age, det.gender,
                                db.pack_vec(det.embedding), thumb,
                            ),
                        )

                    conn.execute(
                        "UPDATE photos SET state='done', error=NULL, sha1=?, phash=?, "
                        "sig=?, width=?, height=?, orientation=?, taken_at=?, "
                        "taken_source=?, camera=?, gps_lat=?, gps_lon=?, face_count=?, "
                        "kind=?, thumb=? WHERE id=?",
                        (
                            item.sha1, item.phash, item.sig,
                            meta.get("width"), meta.get("height"),
                            meta.get("orientation", 1), meta.get("taken_at"),
                            meta.get("taken_source"), meta.get("camera"),
                            meta.get("gps_lat"), meta.get("gps_lon"),
                            len(detections),
                            kinds.classify(
                                PurePosixPath(item.path.replace("\\", "/")).suffix.lower(),
                                meta.get("camera"), meta.get("width"),
                                meta.get("height"),
                                PurePosixPath(item.path.replace("\\", "/")).name,
                            ),
                            item.thumb, item.photo_id,
                        ),
                    )
                    stats["faces"] += len(detections)
                    if not detections:
                        stats["no_face"] += 1
                except Exception:  # noqa: BLE001
                    stats["errors"] += 1
                    conn.execute(
                        "UPDATE photos SET state='error', error=? WHERE id=?",
                        (traceback.format_exc()[-500:], item.photo_id),
                    )
                finally:
                    if item.img is not None:
                        item.img.close()
                        item.img = None
                    stats["done"] += 1

            conn.commit()
            if job_id is not None:
                db.job_update(
                    job_id, done=stats["done"], total=total,
                    detail="%d yuz bulundu" % stats["faces"],
                )
            if progress:
                progress(stats["done"], total, "%d yuz" % stats["faces"])

    return stats


def repair_dates() -> dict:
    """Throw away capture dates that cannot be real, and try again.

    Photos read out of a zip inherit the archive entry's timestamp, and a zip
    entry with no date reads as 1980-01-01 - the oldest value the format can
    hold. In one library that put a third of the photos in a "1980" folder.

    A date that is clearly a placeholder is worse than no date: it sorts wrong,
    it groups wrong, and it looks like fact. So implausible ones are cleared
    and re-derived from the filename where possible; anything left stays empty
    and the interface says the date is unknown.
    """
    stats = {"cleared": 0, "from_filename": 0, "still_unknown": 0}
    bad = db.q(
        "SELECT id, file_name FROM photos "
        "WHERE taken_at IS NOT NULL AND ("
        "  CAST(substr(taken_at,1,4) AS INTEGER) < %d OR "
        "  CAST(substr(taken_at,1,4) AS INTEGER) > %d)"
        % (imaging.MIN_PLAUSIBLE_YEAR, imaging.MAX_PLAUSIBLE_YEAR)
    )
    conn = db.connect()
    for row in bad:
        stats["cleared"] += 1
        guess = imaging._date_from_filename(row["file_name"] or "")
        if imaging.plausible(guess):
            conn.execute(
                "UPDATE photos SET taken_at=?, taken_source='filename' WHERE id=?",
                (guess.strftime("%Y-%m-%d %H:%M:%S"), row["id"]),
            )
            stats["from_filename"] += 1
        else:
            conn.execute(
                "UPDATE photos SET taken_at=NULL, taken_source=NULL WHERE id=?",
                (row["id"],),
            )
            stats["still_unknown"] += 1
    conn.commit()
    return stats


def reset_faces() -> None:
    """Forget every detection and re-run analysis (after a settings change)."""
    db.execute("DELETE FROM faces")
    db.execute("DELETE FROM clusters")
    db.execute("UPDATE photos SET state='new', face_count=-1")


def counts() -> dict:
    """Dashboard numbers."""
    return {
        "photos": db.scalar("SELECT COUNT(*) FROM photos"),
        "done": db.scalar("SELECT COUNT(*) FROM photos WHERE state='done'"),
        "pending": db.scalar("SELECT COUNT(*) FROM photos WHERE state='new'"),
        "errors": db.scalar("SELECT COUNT(*) FROM photos WHERE state='error'"),
        "duplicates": db.scalar("SELECT COUNT(*) FROM photos WHERE duplicate_of IS NOT NULL"),
        "faces": db.scalar("SELECT COUNT(*) FROM faces"),
        "named_faces": db.scalar("SELECT COUNT(*) FROM faces WHERE person_id IS NOT NULL"),
        "persons": db.scalar("SELECT COUNT(*) FROM persons"),
        "clusters": db.scalar(
            "SELECT COUNT(*) FROM clusters WHERE person_id IS NULL AND ignored=0"
        ),
        "categories": db.scalar("SELECT COUNT(*) FROM categories WHERE enabled=1"),
        "no_face": db.scalar(
            "SELECT COUNT(*) FROM photos WHERE state='done' AND face_count=0"
        ),
        "loose": db.scalar(
            "SELECT COUNT(*) FROM faces WHERE person_id IS NULL AND cluster_id IS NULL"
        ),
        "in_archives": db.scalar(
            "SELECT COUNT(*) FROM photos WHERE archive IS NOT NULL"
        ),
    }
