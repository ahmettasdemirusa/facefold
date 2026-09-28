"""Deleting photos - always to the recycle bin, never permanently.

Sorting a library inevitably turns up things to throw away: screenshots,
receipts, blurred shots, the same moment taken eleven times. Doing that from
inside Facefold is much faster than hunting the files down in Explorer
afterwards, because here you can see who is in them.

Two rules this module will not bend:

  * **Nothing is erased.** Files go to the system recycle bin, so a mistake is
    one Ctrl+Z away in Explorer. There is no "delete permanently" path, and
    adding one would remove the only safety net a bulk operation has.
  * **Nothing is deleted that the user did not pick.** No rule, no cleanup pass
    and no scan ever calls in here on its own.

Hardlink note: an output folder entry and the source photo are the same file on
disk under two names, so deleting only the source frees nothing. Both go.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import db

ProgressFn = Callable[[int, int, str], None]


class TrashUnavailable(RuntimeError):
    """Raised when the recycle bin cannot be used - we do not fall back."""


def _send(path: str) -> None:
    try:
        from send2trash import send2trash
    except ImportError as exc:  # pragma: no cover - depends on optional wheel
        raise TrashUnavailable(
            "send2trash paketi kurulu degil. 'python -m pip install send2trash' "
            "calistirin. Guvenlik geregi kalici silme yapilmaz."
        ) from exc
    send2trash(path)


def available() -> tuple[bool, str]:
    try:
        import send2trash  # noqa: F401
    except ImportError:
        return False, "send2trash paketi kurulu degil"
    return True, "Silinen dosyalar Geri Donusum Kutusu'na gider"


def preview(photo_ids: list[int]) -> dict:
    """What would be removed, so the confirmation can be specific."""
    if not photo_ids:
        return {"photos": 0, "bytes": 0, "links": 0, "missing": 0}
    marks = ",".join("?" * len(photo_ids))
    rows = db.q(
        "SELECT id, path, size FROM photos WHERE id IN (%s)" % marks, photo_ids
    )
    missing = sum(1 for r in rows if not Path(r["path"]).exists())
    links = db.scalar(
        "SELECT COUNT(*) FROM links WHERE photo_id IN (%s)" % marks, photo_ids
    )
    return {
        "photos": len(rows),
        "bytes": sum(r["size"] or 0 for r in rows),
        "links": links,
        "missing": missing,
    }


def delete_photos(photo_ids: list[int], progress: ProgressFn | None = None) -> dict:
    """Send the given photos to the recycle bin and forget them.

    Output-folder entries for the same photo go first: with hardlinks they are
    the same bytes under another name, so leaving them behind would free no
    space and would leave a folder pointing at something the user deleted.
    """
    stats = {"deleted": 0, "links": 0, "missing": 0, "failed": 0,
             "bytes": 0, "errors": []}
    if not photo_ids:
        return stats

    ok, message = available()
    if not ok:
        raise TrashUnavailable(message)

    marks = ",".join("?" * len(photo_ids))
    rows = db.q("SELECT id, path, size, file_name FROM photos WHERE id IN (%s)" % marks,
                photo_ids)
    total = len(rows)
    conn = db.connect()

    for i, row in enumerate(rows):
        # 1. the copies/links we made in the output folders
        for link in db.q("SELECT id, dest FROM links WHERE photo_id=?", (row["id"],)):
            dest = Path(link["dest"])
            try:
                if dest.exists():
                    _send(str(dest))
                    stats["links"] += 1
            except Exception:  # noqa: BLE001
                pass
            conn.execute("DELETE FROM links WHERE id=?", (link["id"],))

        # 2. the original
        source = Path(row["path"])
        if not source.exists():
            stats["missing"] += 1
        else:
            try:
                _send(str(source))
                stats["deleted"] += 1
                stats["bytes"] += row["size"] or 0
            except Exception as exc:  # noqa: BLE001
                stats["failed"] += 1
                if len(stats["errors"]) < 5:
                    stats["errors"].append("%s: %s" % (row["file_name"], exc))
                continue

        # 3. forget it (faces cascade)
        conn.execute("DELETE FROM faces WHERE photo_id=?", (row["id"],))
        conn.execute("UPDATE photos SET duplicate_of=NULL WHERE duplicate_of=?", (row["id"],))
        conn.execute("DELETE FROM photos WHERE id=?", (row["id"],))

        if i % 25 == 0:
            conn.commit()
            if progress:
                progress(i, total, row["file_name"] or "")

    # A person or group left with no faces no longer refers to anything.
    conn.execute(
        "DELETE FROM clusters WHERE person_id IS NULL AND id NOT IN "
        "(SELECT DISTINCT cluster_id FROM faces WHERE cluster_id IS NOT NULL)"
    )
    conn.commit()
    return stats


# ---------------------------------------------------------------------------
# Selections the UI offers as one click
# ---------------------------------------------------------------------------

def photo_ids_for_person(person_id: int) -> list[int]:
    """Both routes to a person: a recognised face, or a hand-made link."""
    return [
        r["photo_id"] for r in db.q(
            "SELECT photo_id FROM faces WHERE person_id=? "
            "UNION SELECT photo_id FROM photo_people WHERE person_id=?",
            (person_id, person_id),
        )
    ]


def photo_ids_for_duplicates(kind: str = "exact") -> list[int]:
    """Copies that are not the one being kept.

    Only byte-identical copies by default. A visual match is a judgement, and a
    one-click bulk delete should not act on a judgement - those stay on the
    duplicates screen where they can be looked at first.
    """
    if kind == "all":
        return [
            r["id"] for r in db.q("SELECT id FROM photos WHERE duplicate_of IS NOT NULL")
        ]
    return [
        r["id"] for r in db.q(
            "SELECT id FROM photos WHERE duplicate_of IS NOT NULL AND dup_kind=?",
            (kind,),
        )
    ]


def photo_ids_faceless() -> list[int]:
    return [
        r["id"] for r in db.q(
            "SELECT id FROM photos WHERE state='done' AND face_count=0"
        )
    ]
