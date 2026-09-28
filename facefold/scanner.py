"""Walking source folders and finding duplicates.

Scanning is split in two so the user gets feedback immediately:

  1. walk()      - cheap directory listing, records paths. Instant.
  2. pipeline    - the expensive per-photo work (decode, EXIF, hash, faces).
  3. dedupe()    - groups identical and near-identical shots.

Step 3 matters more than it sounds: a phone dump plus a Google Takeout export
of the same library is typically 30-50% duplicates, and the Google copy is
re-compressed so the bytes differ.
"""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterator

from . import archives, config, db, imaging

ProgressFn = Callable[[int, int, str], None]


# ---------------------------------------------------------------------------
# Walking
# ---------------------------------------------------------------------------

def excluded_dirs() -> set[str]:
    """Folders the scan must never descend into, resolved to real paths.

    The output folder is the one that matters. Its default name is on the
    skip-list, but the user can point it anywhere - including inside the very
    folder being scanned, which is a perfectly natural thing to do ("put the
    tidy copy next to the messy one"). Reading it back in would file the app's
    own hardlinks as fresh photos, double every face and every count, and give
    each rebuild more to chew on than the last.
    """
    out: set[str] = set()
    try:
        settings = db.get_settings()
    except Exception:  # noqa: BLE001 - a scan must not fail over settings
        return out
    for path in (settings.output_dir, str(config.DATA_DIR)):
        if not path:
            continue
        try:
            out.add(os.path.normcase(str(Path(path).resolve())))
        except OSError:
            continue
    return out


def iter_files(root: str | Path, skip: set[str] | None = None) -> Iterator[Path]:
    """Yield every image file under root, skipping system and output folders."""
    root = Path(root)
    if not root.exists():
        return
    skip = excluded_dirs() if skip is None else skip
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # Prune in place so os.walk does not descend into them at all.
        dirnames[:] = [
            d for d in dirnames
            if d.lower() not in config.SKIP_DIR_NAMES and not d.startswith(".")
            and os.path.normcase(os.path.realpath(os.path.join(dirpath, d))) not in skip
        ]
        for name in filenames:
            if name.lower() in config.SKIP_NAMES or name.startswith("._"):
                continue
            if Path(name).suffix.lower() in config.IMAGE_EXTS:
                yield Path(dirpath) / name


def walk(roots: list[str], progress: ProgressFn | None = None) -> dict:
    """Register every image found under the given roots.

    Returns counts. Re-running is cheap and safe: known paths are skipped
    unless the file changed on disk.
    """
    stats = {"seen": 0, "added": 0, "updated": 0, "skipped": 0, "videos": 0,
             "archives": 0, "in_archives": 0}
    known: dict[str, tuple] = {}
    for row in db.q("SELECT path, size, mtime FROM photos"):
        known[row["path"]] = (row["size"], row["mtime"])

    conn = db.connect()
    batch: list[tuple] = []

    def flush() -> None:
        if not batch:
            return
        conn.executemany(
            "INSERT INTO photos(path, source_root, archive, member, file_name, ext, "
            "size, mtime, state) VALUES(?,?,?,?,?,?,?,?, 'new') "
            "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, "
            "archive=excluded.archive, member=excluded.member, "
            "state='new', error=NULL",
            batch,
        )
        conn.commit()
        batch.clear()

    skip = excluded_dirs()
    for root in roots:
        root_str = str(Path(root))
        for path in iter_files(root, skip):
            stats["seen"] += 1
            st = imaging.safe_stat(path)
            if st is None:
                stats["skipped"] += 1
                continue
            key = str(path)
            prev = known.get(key)
            if prev is not None and prev[0] == st.st_size and abs((prev[1] or 0) - st.st_mtime) < 2:
                stats["skipped"] += 1
                continue
            batch.append((
                key, root_str, None, None, path.name, path.suffix.lower(),
                st.st_size, st.st_mtime,
            ))
            if prev is None:
                stats["added"] += 1
            else:
                stats["updated"] += 1
            if len(batch) >= 500:
                flush()
            if progress and stats["seen"] % 200 == 0:
                progress(stats["seen"], 0, str(path.parent))

        # Photos that live inside zips. Only the archive index is read here -
        # the pixels stay in the archive until the photo is actually analysed.
        for archive in archives.find_archives(root, skip):
            stats["archives"] += 1
            members = archives.list_images(archive)
            if not members:
                continue
            if progress:
                progress(stats["seen"], 0, "%s (%d resim)" % (archive.name, len(members)))
            for m in members:
                stats["seen"] += 1
                prev = known.get(m.key)
                if prev is not None and prev[0] == m.size:
                    stats["skipped"] += 1
                    continue
                batch.append((
                    m.key, root_str, m.archive, m.member, m.name,
                    Path(m.name).suffix.lower(), m.size, m.mtime,
                ))
                if prev is None:
                    stats["added"] += 1
                else:
                    stats["updated"] += 1
                stats["in_archives"] += 1
                if len(batch) >= 500:
                    flush()
    flush()
    return stats


def forget_source(root: str) -> dict:
    """Drop everything a source folder contributed to the database.

    Removing a folder from the list has to remove its photos too, otherwise
    faces from a folder the user no longer cares about keep shaping the face
    groups and keep showing up under people's names. Files on disk are never
    touched - only what we recorded about them.
    """
    stats = {"photos": 0, "faces": 0, "persons": 0}

    stats["photos"] = db.scalar(
        "SELECT COUNT(*) FROM photos WHERE source_root=?", (root,)
    )
    stats["faces"] = db.scalar(
        "SELECT COUNT(*) FROM faces WHERE photo_id IN "
        "(SELECT id FROM photos WHERE source_root=?)",
        (root,),
    )

    # Which people this folder actually touched. Only those are candidates for
    # removal afterwards. Sweeping every person who happens to have no faces
    # would take out anyone the user built by hand from faceless photos - and
    # after a "faces reset", when no person has a face yet, it would take out
    # the whole naming in one click.
    touched = {
        r["person_id"] for r in db.q(
            "SELECT DISTINCT f.person_id FROM faces f JOIN photos p ON p.id=f.photo_id "
            "WHERE p.source_root=? AND f.person_id IS NOT NULL", (root,)
        )
    }
    touched |= {
        r["person_id"] for r in db.q(
            "SELECT DISTINCT pp.person_id FROM photo_people pp "
            "JOIN photos p ON p.id=pp.photo_id WHERE p.source_root=?", (root,)
        )
    }

    conn = db.connect()
    # links and faces cascade from photos; clear them explicitly so the rows
    # go even if foreign keys are off on an old database.
    conn.execute(
        "DELETE FROM links WHERE photo_id IN (SELECT id FROM photos WHERE source_root=?)",
        (root,),
    )
    conn.execute(
        "DELETE FROM faces WHERE photo_id IN (SELECT id FROM photos WHERE source_root=?)",
        (root,),
    )
    conn.execute("DELETE FROM photos WHERE source_root=?", (root,))

    # A person whose every face came from that folder, and who is not attached
    # to anything else, no longer means anything.
    for person_id in touched:
        left = conn.execute(
            "SELECT (SELECT COUNT(*) FROM faces WHERE person_id=?) "
            "     + (SELECT COUNT(*) FROM photo_people WHERE person_id=?)",
            (person_id, person_id),
        ).fetchone()[0]
        if left:
            continue
        conn.execute("DELETE FROM persons WHERE id=?", (person_id,))
        stats["persons"] += 1
    conn.execute(
        "DELETE FROM clusters WHERE id NOT IN "
        "(SELECT DISTINCT cluster_id FROM faces WHERE cluster_id IS NOT NULL) "
        "AND person_id IS NULL"
    )
    conn.commit()
    return stats


def count_videos(roots: list[str]) -> int:
    """How many videos we are ignoring - worth telling the user about."""
    total = 0
    for root in roots:
        p = Path(root)
        if not p.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(p, followlinks=False):
            dirnames[:] = [d for d in dirnames if d.lower() not in config.SKIP_DIR_NAMES]
            total += sum(
                1 for n in filenames if Path(n).suffix.lower() in config.VIDEO_EXTS
            )
    return total


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

def _pick_master(rows: list) -> int:
    """Choose which copy of a duplicate group to keep as the original.

    Prefers real EXIF capture time (Google's export often loses it), then the
    largest file (least re-compressed), then the earliest added.
    """
    def rank(r):
        return (
            0 if r["taken_source"] == "exif" else 1,
            -(r["size"] or 0),
            r["id"],
        )

    return sorted(rows, key=rank)[0]["id"]


# Two photos are only called "the same shot" when all three agree. pHash alone
# collides on low-detail images (sky, walls, gradients), so on its own it will
# happily merge a landscape with a screenshot.
ASPECT_TOLERANCE = 0.08     # relative difference in width/height
SIG_TOLERANCE = 14.0        # mean brightness difference, 0-255


# A near-flat image - a blank screen, a white wall, an unloaded page - has
# almost no structure for a perceptual hash to describe, so twenty different
# ones collapse onto the same value. Measured: 20 plain colours all hash to
# 8000000000000000. For those, only a near-exact pixel match counts.
FLAT_SIGNATURE_VARIANCE = 25.0
FLAT_SIG_TOLERANCE = 3.0


def _flat(sig: bytes | None) -> bool:
    if not sig:
        return False
    import numpy as np
    return float(np.frombuffer(sig, dtype=np.uint8).var()) < FLAT_SIGNATURE_VARIANCE


def _same_shot(a, b) -> bool:
    if imaging.hamming(a["phash"], b["phash"]) > config.PHASH_THRESHOLD:
        return False

    # Without the pixel signature there is nothing to confirm the hash with,
    # and "no evidence" must not default to "the same photo".
    if not a["sig"] or not b["sig"]:
        return False

    ra = imaging.aspect_ratio(a["width"], a["height"])
    rb = imaging.aspect_ratio(b["width"], b["height"])
    if ra and rb and abs(ra - rb) / max(ra, rb) > ASPECT_TOLERANCE:
        # A portrait and a landscape are never the same photo, however similar
        # their coarse structure looks.
        return False

    distance = imaging.sig_distance(a["sig"], b["sig"])
    if _flat(a["sig"]) or _flat(b["sig"]):
        return distance <= FLAT_SIG_TOLERANCE
    return distance <= SIG_TOLERANCE


def dedupe(progress: ProgressFn | None = None) -> dict:
    """Mark duplicates: byte-identical first, then visually identical.

    Nothing is deleted. Duplicates are flagged so they can be excluded from
    output folders, and the user can review them.
    """
    stats = {"exact": 0, "similar": 0, "groups": 0}

    db.execute("UPDATE photos SET duplicate_of=NULL, dup_kind=NULL")

    rows = db.q(
        "SELECT id, sha1, phash, sig, size, taken_at, taken_source, width, height "
        "FROM photos WHERE state='done'"
    )

    # --- byte-identical ---------------------------------------------------
    by_sha: dict[str, list] = defaultdict(list)
    for r in rows:
        if r["sha1"]:
            by_sha[r["sha1"]].append(r)

    handled: set[int] = set()
    conn = db.connect()
    for group in by_sha.values():
        if len(group) < 2:
            continue
        master = _pick_master(group)
        stats["groups"] += 1
        for r in group:
            if r["id"] == master:
                continue
            conn.execute(
                "UPDATE photos SET duplicate_of=?, dup_kind='exact' WHERE id=?",
                (master, r["id"]),
            )
            handled.add(r["id"])
            stats["exact"] += 1
    conn.commit()

    # --- visually identical ----------------------------------------------
    # Bucket by the top 16 bits of the phash so we only compare plausible
    # candidates instead of every pair (which would be O(n^2) on 50k photos).
    buckets: dict[str, list] = defaultdict(list)
    for r in rows:
        if r["id"] in handled or not r["phash"]:
            continue
        buckets[r["phash"][:4]].append(r)

    total = len(buckets)
    for i, (_, group) in enumerate(buckets.items()):
        if len(group) > 1:
            used: set[int] = set()
            for a_idx, a in enumerate(group):
                if a["id"] in used:
                    continue
                cluster = [a]
                for b in group[a_idx + 1:]:
                    if b["id"] in used:
                        continue
                    if _same_shot(a, b):
                        cluster.append(b)
                        used.add(b["id"])
                if len(cluster) > 1:
                    master = _pick_master(cluster)
                    stats["groups"] += 1
                    for r in cluster:
                        if r["id"] == master:
                            continue
                        conn.execute(
                            "UPDATE photos SET duplicate_of=?, dup_kind='similar' WHERE id=?",
                            (master, r["id"]),
                        )
                        stats["similar"] += 1
        if progress and i % 50 == 0:
            progress(i, total, "kopya taramasi")
    conn.commit()
    stats["chains"] = _flatten_chains()
    return stats


def _flatten_chains() -> int:
    """Make sure no photo is both an original and somebody else's copy.

    The two passes can build a chain: the exact pass calls A a copy of B, then
    the visual pass calls B a copy of C. B is then shown with a green
    "original" badge on the duplicates screen while "delete all copies" quietly
    includes it. Pointing every copy at the end of its chain removes the
    contradiction - what the user sees marked as kept is what is kept.
    """
    rows = db.q("SELECT id, duplicate_of FROM photos WHERE duplicate_of IS NOT NULL")
    if not rows:
        return 0
    parent = {r["id"]: r["duplicate_of"] for r in rows}

    fixed = 0
    conn = db.connect()
    for photo_id, master in parent.items():
        root, seen = master, {photo_id}
        while root in parent and root not in seen:
            seen.add(root)
            root = parent[root]
        if root != master and root != photo_id:
            conn.execute("UPDATE photos SET duplicate_of=? WHERE id=?", (root, photo_id))
            fixed += 1
        elif root == photo_id:
            # A cycle, which should be impossible; break it rather than leave
            # a photo listed as its own copy.
            conn.execute(
                "UPDATE photos SET duplicate_of=NULL, dup_kind=NULL WHERE id=?",
                (photo_id,),
            )
            fixed += 1
    conn.commit()
    return fixed


def duplicate_groups(limit: int = 200) -> list[dict]:
    """Duplicate sets for the review screen, biggest wasted space first."""
    masters = db.q(
        "SELECT p.id, p.file_name, p.path, p.thumb, p.size, p.taken_at, "
        "       COUNT(d.id) AS copies, SUM(d.size) AS wasted "
        "FROM photos p JOIN photos d ON d.duplicate_of = p.id "
        "GROUP BY p.id ORDER BY wasted DESC LIMIT ?",
        (limit,),
    )
    out = []
    for m in masters:
        copies = db.q(
            "SELECT id, file_name, path, size, dup_kind, thumb FROM photos "
            "WHERE duplicate_of=? ORDER BY size DESC",
            (m["id"],),
        )
        out.append({"master": dict(m), "copies": [dict(c) for c in copies]})
    return out
