"""Building the output folders.

Default mode is hardlinks: the photo bytes exist once on disk, but the file
appears in as many category folders as it belongs to. Windows Explorer shows a
perfectly normal file - you can open it, copy it, back it up - and ten
categories cost no extra space.

Safety rules, deliberately strict:
  * source files are never moved, renamed, modified or deleted;
  * the only files this module deletes are ones it created itself, recorded in
    the links table AND located inside the configured output folder.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
from pathlib import Path
from typing import Callable

from . import archives, auto, db, rules

ProgressFn = Callable[[int, int, str], None]

# How output files are created. Stored in settings, so the keys are part of
# the data format; the labels come from link_modes().
LINK_MODES = ("hardlink", "copy", "report")


def link_modes() -> dict[str, str]:
    """Output modes with the label the user sees, in their language."""
    from . import i18n
    return {key: i18n.t("linkmode.%s" % key) for key in LINK_MODES}


class LinkError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Placing one file
# ---------------------------------------------------------------------------

def _dest_for(photo: dict, folder: Path, date_subfolders: bool) -> Path:
    name = photo["file_name"] or ("%d.jpg" % photo["id"])
    if date_subfolders and photo["taken_at"]:
        year = str(photo["taken_at"])[:4]
        if year.isdigit():
            folder = folder / year
    return folder / name


def _key(path: str | Path) -> str:
    return os.path.normcase(str(path))


def _unique(dest: Path, photo_id: int, taken: set[str]) -> Path:
    """Pick a destination name, avoiding two different photos colliding.

    `taken` is every path the links table already claims. That distinction
    matters: a file sitting at `dest` that nothing claims is our own leftover
    from a run that was interrupted before it could record the link. Renaming
    around it would leave "IMG_0001.jpg" and "IMG_0001_12.jpg" side by side
    forever, the first one orphaned - no link row, so nothing will ever clean
    it up. So an unclaimed file is replaced, and only a genuinely claimed name
    gets a number.
    """
    if _key(dest) not in taken:
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                return _numbered(dest, photo_id, taken)
        return dest
    return _numbered(dest, photo_id, taken)


def _numbered(dest: Path, photo_id: int, taken: set[str]) -> Path:
    stem, suffix = dest.stem, dest.suffix
    candidate = dest.with_name("%s_%d%s" % (stem, photo_id, suffix))
    counter = 2
    while candidate.exists() or _key(candidate) in taken:
        candidate = dest.with_name("%s_%d_%d%s" % (stem, photo_id, counter, suffix))
        counter += 1
    return candidate


def place(src: str, dest: Path, mode: str) -> tuple[Path, str]:
    """Create one output file. Returns the real destination and mode used."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if archives.is_inside_archive(src):
        # The photo only exists inside a zip. Extracting it here is the one and
        # only time it gets written to disk, since every photo lands in exactly
        # one folder.
        archives.extract_key(src, dest)
        return dest, "extract"
    if mode == "hardlink":
        try:
            os.link(src, dest)
            return dest, "hardlink"
        except OSError:
            # Different volume, or a filesystem without hardlinks (exFAT/FAT32
            # on an external drive). Copying is the honest fallback.
            shutil.copy2(src, dest)
            return dest, "copy"
    shutil.copy2(src, dest)
    return dest, "copy"


# ---------------------------------------------------------------------------
# Building everything
# ---------------------------------------------------------------------------

def build(job_id: int | None = None, progress: ProgressFn | None = None,
          prune: bool = True) -> dict:
    """Materialise every enabled category into the output folder."""
    settings = db.get_settings()
    out_root = Path(settings.output_dir)
    mode = settings.link_mode or "hardlink"

    stats = {
        "categories": 0, "created": 0, "kept": 0, "removed": 0, "moved": 0,
        "errors": 0, "fallback_copies": 0, "mode": mode,
        "output": str(out_root),
    }

    # Naming people is the only configuration; the folders that implies are
    # worked out here, right before they are built, so a name added a minute
    # ago is already reflected.
    stats["auto"] = auto.sync_from_settings()

    matches = rules.evaluate_all()
    cats = {c["id"]: c for c in db.q("SELECT * FROM categories WHERE enabled=1")}
    stats["categories"] = len(cats)
    stats["partition"] = check_partition(matches, cats)

    if mode == "report":
        stats["report"] = write_report(matches, cats, out_root)
        return stats

    out_root.mkdir(parents=True, exist_ok=True)
    conn = db.connect()

    photos = {
        r["id"]: dict(r)
        for r in db.q("SELECT id, path, file_name, taken_at FROM photos")
    }

    # Every path the database already claims, across all categories. Used to
    # tell "another photo is using this name" from "this is my own leftover".
    taken = {_key(r["dest"]) for r in db.q("SELECT dest FROM links")}

    total = sum(len(v) for v in matches.values())
    seen = 0

    for cat_id, photo_ids in matches.items():
        cat = cats.get(cat_id)
        if cat is None:
            continue
        folder = out_root / (cat["folder"] or rules.safe_folder_name(cat["name"]))

        existing = {
            r["photo_id"]: r["dest"]
            for r in db.q("SELECT photo_id, dest FROM links WHERE category_id=?", (cat_id,))
        }
        wanted = set(photo_ids)

        for photo_id in photo_ids:
            seen += 1
            if job_id is not None and seen % 100 == 0 and db.job_cancelled(job_id):
                stats["cancelled"] = True
                conn.commit()
                return stats

            photo = photos.get(photo_id)
            if photo is None:
                continue

            want = _dest_for(photo, folder, settings.date_subfolders)

            prev = existing.get(photo_id)
            if prev and _under(prev, out_root) and Path(prev).exists():
                # In the right place already - the common case, and the reason
                # a rebuild of 12.000 photos takes seconds rather than an hour.
                if _key(Path(prev).parent) == _key(want.parent):
                    stats["kept"] += 1
                    continue
                # The folder underneath it changed: the category was renamed,
                # a person's name was corrected, or year subfolders were turned
                # on or off. Without this the file stayed where it was and the
                # output tree quietly drifted away from what the app believed.
                if _remove_link(cat_id, photo_id, prev, out_root):
                    stats["moved"] += 1
                taken.discard(_key(prev))

            try:
                dest = _unique(want, photo_id, taken)
                real, used = place(photo["path"], dest, mode)
                taken.add(_key(real))
                if mode == "hardlink" and used == "copy":
                    stats["fallback_copies"] += 1
                conn.execute(
                    "INSERT INTO links(category_id, photo_id, dest, mode) VALUES(?,?,?,?) "
                    "ON CONFLICT(category_id, photo_id) DO UPDATE SET dest=excluded.dest, "
                    "mode=excluded.mode",
                    (cat_id, photo_id, str(real), used),
                )
                stats["created"] += 1
            except Exception:  # noqa: BLE001
                stats["errors"] += 1

            if seen % 200 == 0:
                conn.commit()
                if job_id is not None:
                    db.job_update(job_id, done=seen, total=total,
                                  detail="%s" % cat["name"])
                if progress:
                    progress(seen, total, cat["name"])

        if prune:
            stale = [pid for pid in existing if pid not in wanted]
            for photo_id in stale:
                if _remove_link(cat_id, photo_id, existing[photo_id], out_root):
                    stats["removed"] += 1

        conn.commit()

    # The folder names this app is responsible for, captured now: retiring an
    # auto category below deletes its row, and after that its folder would no
    # longer look like ours - so the emptied folder would sit there forever.
    ours = _our_folders()

    if prune:
        # Categories that were switched off - including auto folders whose
        # meaning disappeared when a name changed - lose their output here.
        stats["removed"] += _prune_orphans(out_root, set(cats))
        stats["retired"] = auto.cleanup_disabled()
    _cleanup_empty_dirs(out_root, ours)
    return stats


def _under(path: str | Path, root: Path) -> bool:
    """Is `path` inside `root`? Used to tell our own output from anything else.

    An existing link only counts as "already done" when it sits under the
    current output folder. Otherwise, changing the output folder in settings
    would make every photo look already-placed, the new folder would stay
    empty, and the job would still report success.
    """
    try:
        resolved = Path(path).resolve()
        base = root.resolve()
    except OSError:
        return False
    return resolved == base or base in resolved.parents


def check_partition(matches: dict[int, list[int]], cats: dict) -> dict:
    """Verify the promise: every photo lands in exactly one automatic folder.

    The automatic folders are meant to be a partition of the library. Several
    separate bugs have broken that promise in different ways - a bucket that
    forgot to exclude screenshots put photos in two folders at once, a rare
    person-combination fell through every rule and was filed nowhere - and each
    was invisible until someone counted files by hand.

    So the invariant is checked rather than assumed. Manual categories are
    excluded: those are deliberate extra folders and are allowed to overlap.
    """
    auto_ids = {cid for cid, c in cats.items() if c["origin"] == "auto"}
    if not auto_ids:
        return {"checked": 0, "duplicated": 0, "unplaced": 0, "ok": True}

    # Overlap is only a fault among the automatic folders; a folder the user
    # wrote by hand is meant to sit alongside them.
    seen: dict[int, list[int]] = {}
    for cat_id in auto_ids:
        for photo_id in matches.get(cat_id, []):
            seen.setdefault(photo_id, []).append(cat_id)

    duplicated = {pid: cs for pid, cs in seen.items() if len(cs) > 1}

    # Coverage, though, counts every enabled folder: a photo that only a manual
    # rule catches is still filed, and calling it unplaced would be wrong.
    placed = set(seen)
    for cat_id in cats:
        if cat_id not in auto_ids:
            placed.update(matches.get(cat_id, []))

    live = {
        r["id"] for r in db.q(
            "SELECT id FROM photos WHERE state='done' AND duplicate_of IS NULL"
        )
    }
    unplaced = live - placed

    report = {
        "checked": len(live),
        "duplicated": len(duplicated),
        "unplaced": len(unplaced),
        "ok": not duplicated and not unplaced,
    }
    if duplicated:
        example = next(iter(duplicated.items()))
        report["duplicated_example"] = {
            "photo_id": example[0],
            "folders": [cats[c]["name"] for c in example[1]],
        }
    if unplaced:
        report["unplaced_example"] = sorted(unplaced)[:5]
    return report


def _remove_link(cat_id: int, photo_id: int, dest: str, out_root: Path) -> bool:
    """Delete one generated file, refusing anything outside the output folder."""
    try:
        path = Path(dest).resolve()
        root = out_root.resolve()
        if root not in path.parents:
            # Never touch a path we cannot prove we created.
            db.execute("DELETE FROM links WHERE category_id=? AND photo_id=?",
                       (cat_id, photo_id))
            return False
        if path.exists():
            path.unlink()
        db.execute("DELETE FROM links WHERE category_id=? AND photo_id=?",
                   (cat_id, photo_id))
        return True
    except OSError:
        return False


def _prune_orphans(out_root: Path, live_cats: set[int]) -> int:
    """Remove links whose category was deleted or disabled."""
    removed = 0
    for row in db.q("SELECT category_id, photo_id, dest FROM links"):
        if row["category_id"] not in live_cats:
            if _remove_link(row["category_id"], row["photo_id"], row["dest"], out_root):
                removed += 1
    return removed


def _our_folders() -> set[str]:
    """Folder names that belong to a category, normalised for comparison."""
    names = {
        r["folder"] or rules.safe_folder_name(r["name"])
        for r in db.q("SELECT name, folder FROM categories")
    }
    return {os.path.normcase(name) for name in names if name}


def _cleanup_empty_dirs(root: Path, ours: set[str] | None = None) -> None:
    """Remove the category folders we emptied - and nothing else.

    The output folder is very often somewhere ordinary: Pictures, the desktop,
    a drive root. Sweeping every empty directory under it would delete folders
    the user made themselves and simply had not filled yet, which is a small
    but real act of vandalism. So only the folders this app is responsible for
    are candidates: a category folder, or a year subfolder inside one.
    """
    if not root.exists():
        return
    ours = _our_folders() if ours is None else ours
    if not ours:
        return

    for entry in root.iterdir():
        if not entry.is_dir() or os.path.normcase(entry.name) not in ours:
            continue
        # Year subfolders first, then the category folder itself.
        for sub in sorted(entry.iterdir(), reverse=True):
            if sub.is_dir():
                try:
                    sub.rmdir()          # fails harmlessly when not empty
                except OSError:
                    pass
        try:
            entry.rmdir()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Report mode
# ---------------------------------------------------------------------------

def write_report(matches: dict[int, list[int]], cats: dict, out_root: Path) -> str:
    """Write a CSV listing instead of creating any files."""
    out_root.mkdir(parents=True, exist_ok=True)
    dest = out_root / "facefold-rapor.csv"
    photos = {
        r["id"]: dict(r)
        for r in db.q("SELECT id, path, file_name, taken_at FROM photos")
    }
    with open(dest, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["Kategori", "Dosya", "Tarih", "Tam yol"])
        for cat_id, ids in matches.items():
            cat = cats.get(cat_id)
            if not cat:
                continue
            for photo_id in ids:
                p = photos.get(photo_id)
                if p:
                    writer.writerow([cat["name"], p["file_name"], p["taken_at"], p["path"]])
    return str(dest)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def hardlink_supported(source_dir: str, output_dir: str) -> tuple[bool, str]:
    """Check up front whether hardlinks will work between these two folders.

    Hardlinks require the same volume and a filesystem that supports them, so
    it is better to tell the user now than to silently copy 40 GB later.
    """
    src = Path(source_dir)
    dst = Path(output_dir)
    try:
        dst.mkdir(parents=True, exist_ok=True)
        if src.drive.lower() != dst.drive.lower():
            return False, "Kaynak %s surucusunde, cikti %s surucusunde. " \
                          "Baglanti ayni surucude olmak zorunda." % (src.drive, dst.drive)
        probe_src = dst / ".facefold-probe"
        probe_dst = dst / ".facefold-probe-link"
        probe_src.write_text("x", encoding="utf-8")
        try:
            os.link(probe_src, probe_dst)
            ok = True
            msg = "Baglanti destekleniyor."
        except OSError as exc:
            ok = False
            msg = "Bu diskte baglanti kurulamiyor (%s). Kopyalama kullanilacak." % exc.strerror
        finally:
            for p in (probe_dst, probe_src):
                try:
                    p.unlink()
                except OSError:
                    pass
        return ok, msg
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def output_summary() -> list[dict]:
    """What is currently on disk per category."""
    rows = db.q(
        "SELECT c.id, c.name, c.folder, c.enabled, COUNT(l.id) AS files "
        "FROM categories c LEFT JOIN links l ON l.category_id = c.id "
        "GROUP BY c.id ORDER BY c.sort_order, c.id"
    )
    return [dict(r) for r in rows]
