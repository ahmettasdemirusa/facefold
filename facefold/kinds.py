"""Telling apart the three things that share a phone's photo roll.

A camera roll is not a photo album. Mixed in with the photos someone actually
took are screenshots of receipts, chat threads, memes, adverts and web pages -
and those contain faces. Plenty of them: in one real library, **a third of all
unnamed faces came from screenshots**. Strangers from an Instagram ad, a
concert poster, a TV still.

Asking someone to name those is not organising their life, it is unpaid data
labelling. So photos get sorted into three kinds first, and only the ones that
could plausibly show people the user knows take part in face grouping.

    camera      EXIF says a camera took it. This is their life.
    screenshot  A picture of a screen. Never enters face grouping.
    received    No camera info, but not a screenshot either - WhatsApp,
                downloads, things people sent. Often real family photos, so
                these do take part.

The rules are deliberately simple and checkable rather than clever: a wrong
guess here quietly hides someone's photos, which is much worse than letting a
few screenshots through.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import db

CAMERA = "camera"
SCREENSHOT = "screenshot"
RECEIVED = "received"

LABELS = {
    CAMERA: "Kamerayla cekilmis",
    SCREENSHOT: "Ekran goruntusu",
    RECEIVED: "Gelen / indirilen",
}

# Kinds that take part in face grouping by default.
DEFAULT_FACE_KINDS = [CAMERA, RECEIVED]

# "Screenshot_2024-01-01", "Ekran Goruntusu", "Screen Shot 2024-...", "Snapchat-..."
_SCREENSHOT_NAME = re.compile(
    r"(screen[\s_-]?shot|screenshot|ekran[\s_-]?g[oö]r[uü]nt[uü]s[uü]|"
    r"captur[ae]|snip|scrn)", re.IGNORECASE
)


def classify(ext: str | None, camera: str | None, width: int | None,
             height: int | None, file_name: str | None) -> str:
    """Which of the three kinds a photo is.

    Order matters: a real camera tag beats everything, because some phones do
    write PNG and some people photograph screens with a real camera.
    """
    if camera and camera.strip():
        return CAMERA

    name = file_name or ""
    if _SCREENSHOT_NAME.search(name):
        return SCREENSHOT

    # No camera tag and a PNG: on a phone roll this is a screenshot almost
    # without exception. Cameras write JPEG or HEIC, never PNG.
    if (ext or "").lower() == ".png":
        return SCREENSHOT

    return RECEIVED


def classify_all(only_missing: bool = True) -> dict:
    """Label every photo already in the database.

    Cheap: it only reads columns that scanning already stored, so re-labelling
    a 50k library takes a second and never re-opens a file.
    """
    where = "WHERE kind IS NULL" if only_missing else ""
    rows = db.q(
        "SELECT id, ext, camera, width, height, file_name FROM photos " + where
    )
    counts = {CAMERA: 0, SCREENSHOT: 0, RECEIVED: 0}
    conn = db.connect()
    for row in rows:
        kind = classify(row["ext"], row["camera"], row["width"], row["height"],
                        row["file_name"])
        counts[kind] += 1
        conn.execute("UPDATE photos SET kind=? WHERE id=?", (kind, row["id"]))
    conn.commit()
    counts["labelled"] = len(rows)
    return counts


# ---------------------------------------------------------------------------
# Which kinds participate in face grouping
# ---------------------------------------------------------------------------

def face_kinds() -> list[str]:
    stored = db.get_kv("face_kinds")
    if not stored:
        return list(DEFAULT_FACE_KINDS)
    return [k for k in stored if k in LABELS] or list(DEFAULT_FACE_KINDS)


def set_face_kinds(kinds: list[str]) -> None:
    db.set_kv("face_kinds", [k for k in kinds if k in LABELS])


def face_sql_filter(alias: str = "p") -> str:
    """A WHERE fragment restricting to photo kinds that may hold known people.

    Written as SQL rather than a Python filter because clustering loads face
    vectors with a single query over the whole library.
    """
    kinds = face_kinds()
    if len(kinds) == len(LABELS):
        return "1=1"
    quoted = ",".join("'%s'" % k for k in kinds)
    # NULL kind means "not classified yet" - treat it as included so a database
    # from an older version never silently loses faces.
    return "(%s.kind IN (%s) OR %s.kind IS NULL)" % (alias, quoted, alias)


def summary() -> dict:
    """Counts per kind, with how many faces each contributes."""
    out = {}
    for kind in LABELS:
        out[kind] = {
            "photos": db.scalar("SELECT COUNT(*) FROM photos WHERE kind=?", (kind,)),
            "faces": db.scalar(
                "SELECT COUNT(*) FROM faces f JOIN photos p ON p.id=f.photo_id "
                "WHERE p.kind=?", (kind,)
            ),
            "unnamed": db.scalar(
                "SELECT COUNT(*) FROM faces f JOIN photos p ON p.id=f.photo_id "
                "WHERE p.kind=? AND f.person_id IS NULL", (kind,)
            ),
            "label": LABELS[kind],
            "in_grouping": kind in face_kinds(),
        }
    out["unclassified"] = db.scalar("SELECT COUNT(*) FROM photos WHERE kind IS NULL")
    return out
