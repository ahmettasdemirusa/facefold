"""Reading Google Photos Takeout metadata.

A Takeout export puts a JSON sidecar next to every photo. Two things in it are
worth having:

  * **people** - the names you already gave faces in Google Photos. Importing
    them means a library that arrives half-organised instead of blank, and
    every imported name becomes a seed the face matcher can grow from.
  * **photoTakenTime** - Google frequently strips EXIF from the exported file,
    so the sidecar is often the only place the real capture date survives.

Sidecar naming is messier than it looks. Google truncates long filenames, so
the same suffix appears as `.supplemental-metadata`, `.supplemental-meta`,
`.supplemen`, `.suppl` and others:

    IMG_1234.jpg.supplemental-metadata.json
    4F726F8A-....jpg.suppl.json

Matching on the JSON's `title` field does not work either - it holds the
*original* name, which differs from the exported one whenever Google had to
deduplicate. So we strip the trailing `.suppl*` segment from the sidecar's own
filename instead, which is what actually lines up with the file on disk.
"""

from __future__ import annotations

import json
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from . import archives, config, db

ProgressFn = Callable[[int, int, str], None]

# ".jpg.supplemental-metadata.json" -> ".jpg"; also ".suppl", ".supplemen", ...
_SIDECAR = re.compile(r"\.suppl[a-z\-]*\.json$", re.IGNORECASE)


def is_sidecar(name: str) -> bool:
    return bool(_SIDECAR.search(name)) or name.lower().endswith(".json")


def photo_name_for(sidecar_name: str) -> str:
    """Filename of the photo a sidecar belongs to."""
    stripped = _SIDECAR.sub("", sidecar_name)
    if stripped == sidecar_name and sidecar_name.lower().endswith(".json"):
        stripped = sidecar_name[: -len(".json")]
    return stripped


def folder_key(path: str) -> str:
    """The album folder a photo or sidecar sits in, lowercased.

    A Takeout export copies the same photo into every album it belonged to, so
    the same filename legitimately appears many times - and two genuinely
    different photos called IMG_0001.jpg in two different albums are entirely
    ordinary. The folder is what tells them apart.
    """
    text = str(path).replace("\\", "/").rstrip("/")
    parent = text.rsplit("/", 1)[0] if "/" in text else ""
    return parent.rsplit("/", 1)[-1].lower()


def iter_sidecars(root: str | Path) -> Iterator[tuple[str, str, dict]]:
    """Yield (album folder, photo filename, parsed sidecar) for every JSON.

    Looks inside zip archives as well as loose folders. A Takeout that was
    never unpacked keeps its sidecars in the zips, and those hold the only
    surviving capture date for photos Google stripped the EXIF from - which is
    most of them.
    """
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d.lower() not in config.SKIP_DIR_NAMES]
        for name in filenames:
            if name.lower().endswith(".json"):
                try:
                    with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                        data = json.load(fh)
                except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(data, dict):
                    yield folder_key(os.path.join(dirpath, name)), photo_name_for(name), data

    for archive in archives.find_archives(root):
        yield from _iter_archive_sidecars(archive)


def _iter_archive_sidecars(archive: str | Path) -> Iterator[tuple[str, str, dict]]:
    """Sidecars stored inside one zip. Only the JSON members are read."""
    try:
        with zipfile.ZipFile(str(archive)) as zf:
            for info in zf.infolist():
                if info.is_dir() or not info.filename.lower().endswith(".json"):
                    continue
                # Sidecars are tiny; anything large is not one and reading it
                # would only waste time.
                if info.file_size > 256 * 1024:
                    continue
                try:
                    data = json.loads(zf.read(info.filename).decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError, OSError,
                        zipfile.BadZipFile):
                    continue
                if isinstance(data, dict):
                    yield (folder_key(info.filename),
                           photo_name_for(info.filename.rsplit("/", 1)[-1]),
                           data)
    except (zipfile.BadZipFile, OSError):
        return


class Sidecars:
    """Everything the sidecars say, looked up the way a careful person would.

    Two indexes, deliberately. `exact` is keyed by (album folder, filename) and
    is always right. `loose` is keyed by the filename alone and only holds the
    names where every sidecar in the export agrees - because a Takeout copies
    the same photo into each album it belonged to, and those copies do agree.
    Where they disagree there are two different photos sharing a name, and the
    entry is simply left out: a wrong date and someone else's name attached to
    your photo is far worse than no date and no name.
    """

    def __init__(self) -> None:
        self.exact: dict[tuple[str, str], dict] = {}
        self.loose: dict[str, dict] = {}
        self.dropped = 0

    def __len__(self) -> int:
        return len(self.exact)

    def lookup(self, folder: str, file_name: str) -> dict | None:
        name = (file_name or "").lower()
        return self.exact.get((folder, name)) or self.loose.get(name)


def _merge(entry: dict, people: list[str], taken: str | None) -> dict:
    for name in people:
        if name not in entry["people"]:
            entry["people"].append(name)
    entry["taken_at"] = entry["taken_at"] or taken
    return entry


def collect(roots: list[str]) -> Sidecars:
    """Read every sidecar under the given roots into a Sidecars index."""
    out = Sidecars()
    for root in roots:
        if not Path(root).exists():
            continue
        for folder, photo_name, data in iter_sidecars(root):
            people = [
                (p.get("name") or "").strip()
                for p in (data.get("people") or [])
                if isinstance(p, dict) and (p.get("name") or "").strip()
            ]
            taken = _taken_at(data)
            if not people and not taken:
                continue
            key = (folder, photo_name.lower())
            _merge(out.exact.setdefault(key, {"people": [], "taken_at": None}),
                   people, taken)

    # Now decide which bare filenames are safe to trust on their own.
    by_name: dict[str, list[dict]] = {}
    for (_, name), entry in out.exact.items():
        by_name.setdefault(name, []).append(entry)
    for name, entries in by_name.items():
        first = entries[0]
        agree = all(
            e["people"] == first["people"] and e["taken_at"] == first["taken_at"]
            for e in entries
        )
        if agree:
            out.loose[name] = first
        else:
            out.dropped += 1
    return out


def _taken_at(data: dict) -> str | None:
    for key in ("photoTakenTime", "creationTime"):
        block = data.get(key) or {}
        stamp = block.get("timestamp")
        if stamp:
            try:
                dt = datetime.fromtimestamp(int(stamp), tz=timezone.utc)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, OSError, OverflowError):
                continue
    return None


# ---------------------------------------------------------------------------
# Importing
# ---------------------------------------------------------------------------

def preview(roots: list[str] | None = None) -> dict:
    """What an import would find, without changing anything."""
    roots = roots or db.get_settings().sources
    meta = collect(roots)

    names: dict[str, int] = {}
    certain = ambiguous = dateless = matched = 0
    for row in db.q(
        "SELECT id, file_name, member, path, face_count, taken_source FROM photos"
    ):
        entry = meta.lookup(folder_key(row["member"] or row["path"]), row["file_name"])
        if entry is None:
            continue
        matched += 1
        if entry["taken_at"] and row["taken_source"] in (None, "mtime", "filename"):
            dateless += 1
        if not entry["people"]:
            continue
        for name in entry["people"]:
            names[name] = names.get(name, 0) + 1
        if len(entry["people"]) == 1 and (row["face_count"] or 0) == 1:
            certain += 1
        else:
            ambiguous += 1

    return {
        "sidecars": len(meta),
        "matched": matched,
        "people": sorted(names.items(), key=lambda kv: -kv[1]),
        "certain": certain,
        "ambiguous": ambiguous,
        "dates": dateless,
        "skipped": meta.dropped,
    }


def import_all(roots: list[str] | None = None, job_id: int | None = None,
               progress: ProgressFn | None = None) -> dict:
    """Bring Google's names and dates into the database.

    Only the unambiguous case is trusted for naming: one person named in the
    sidecar and exactly one face detected in the photo. Those become seeds, and
    the ordinary face matcher spreads each name to the rest of the library from
    there - which reaches far more photos than the sidecars alone cover, and
    without ever guessing which of five faces Google meant.
    """
    roots = roots or db.get_settings().sources
    meta = collect(roots)

    stats = {"sidecars": len(meta), "seeded": 0, "persons": 0, "dates": 0,
             "ambiguous": 0, "assigned": 0, "skipped": meta.dropped}
    if not len(meta):
        return stats

    rows = db.q(
        "SELECT id, file_name, member, path, face_count, taken_at, taken_source "
        "FROM photos WHERE state='done'"
    )
    conn = db.connect()
    person_ids: dict[str, int] = {}

    from . import clustering

    def person_for(name: str) -> int:
        if name in person_ids:
            return person_ids[name]
        found = clustering.find_person(name)
        if found is None:
            conn.commit()
            found = clustering.get_or_create_person(
                name, "Google Fotograflar'dan alindi"
            )
            stats["persons"] += 1
        person_ids[name] = found
        return found

    total = len(rows)
    for i, row in enumerate(rows):
        entry = meta.lookup(folder_key(row["member"] or row["path"]), row["file_name"])
        if entry is None:
            continue

        # Google's date beats a filesystem timestamp or a guess from the name.
        if entry["taken_at"] and row["taken_source"] in (None, "mtime", "filename"):
            conn.execute(
                "UPDATE photos SET taken_at=?, taken_source='takeout' WHERE id=?",
                (entry["taken_at"], row["id"]),
            )
            stats["dates"] += 1

        people = entry["people"]
        if not people:
            continue
        if len(people) == 1 and (row["face_count"] or 0) == 1:
            face = db.q1(
                "SELECT id FROM faces WHERE photo_id=? AND person_id IS NULL",
                (row["id"],),
            )
            if face:
                conn.execute(
                    "UPDATE faces SET person_id=?, assign_source='takeout', "
                    "confidence=1.0 WHERE id=?",
                    (person_for(people[0]), face["id"]),
                )
                stats["seeded"] += 1
        else:
            # More than one candidate. Creating the people here would leave
            # names with no faces attached, which is worse than not importing
            # them: guessing which of five faces Google meant is not something
            # we can do, and an empty person is just clutter.
            stats["ambiguous"] += 1

        if progress and i % 50 == 0:
            progress(i, total, "Google etiketleri")
        if job_id is not None and i % 100 == 0 and db.job_cancelled(job_id):
            break

    conn.commit()

    # Grow each seeded name across the rest of the library.
    for person_id in person_ids.values():
        clustering._refresh_cover(person_id)
    stats["assigned"] = clustering.assign_known().get("assigned", 0)
    return stats
