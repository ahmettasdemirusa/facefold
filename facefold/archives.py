"""Reading photos that live inside zip archives.

A Google Takeout arrives as a pile of zips - often dozens of them, mostly
video by size. Asking people to unpack 114 GB before they can sort their
photos is not reasonable, and unpacking it for them is worse.

So archives are never unpacked. Photos are read straight out of the zip for
analysis, and a single member is extracted only at the moment it is placed in
an output folder. Since every photo lands in exactly one folder, each one ends
up written to disk exactly once - the minimum possible.

A photo inside an archive is identified by `archive.zip!member/path.jpg`. The
separator is `!` because it cannot appear in a Windows path, so splitting is
unambiguous.
"""

from __future__ import annotations

import os
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterator, NamedTuple

from . import config

SEPARATOR = "!"

ARCHIVE_EXTS = {".zip"}

# Members we never look at, whatever their extension.
_SKIP_PREFIXES = ("__MACOSX/", ".")


class Member(NamedTuple):
    archive: str
    member: str
    size: int
    mtime: float

    @property
    def key(self) -> str:
        """The value stored in photos.path - unique across the whole library."""
        return "%s%s%s" % (self.archive, SEPARATOR, self.member)

    @property
    def name(self) -> str:
        return self.member.rsplit("/", 1)[-1]


# The zip format cannot store a date before 1980-01-01, so that exact value
# is what an entry with no date looks like. Treating it as a real capture time
# put a third of one library into a "1980" folder.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def _member_time(info: zipfile.ZipInfo) -> float:
    """Modification time of a member, or 0 when the archive has none."""
    if tuple(info.date_time) <= _ZIP_EPOCH:
        return 0.0
    try:
        return datetime(*info.date_time).timestamp()
    except (ValueError, OverflowError):
        return 0.0


def is_archive(path: str | Path) -> bool:
    return Path(path).suffix.lower() in ARCHIVE_EXTS


def split_key(key: str) -> tuple[str, str] | None:
    """`archive.zip!inner/photo.jpg` -> ('archive.zip', 'inner/photo.jpg').

    An exclamation mark is perfectly legal in a Windows path, and people do use
    it - "Tatil! 2024", "Dugun!". Splitting on the first one it finds would
    turn every photo in such a folder into an unreadable phantom archive
    member. So a split only counts when what precedes it actually looks like an
    archive filename.
    """
    if SEPARATOR not in key:
        return None
    start = 0
    while True:
        pos = key.find(SEPARATOR, start)
        if pos < 0:
            return None
        left, right = key[:pos], key[pos + 1:]
        if right and Path(left).suffix.lower() in ARCHIVE_EXTS:
            return left, right
        start = pos + 1


def is_inside_archive(key: str) -> bool:
    return split_key(key) is not None


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def find_archives(root: str | Path, skip: set[str] | None = None) -> list[Path]:
    """Every zip under root, ignoring the folders in `skip` (real paths)."""
    found: list[Path] = []
    skip = skip or set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [
            d for d in dirnames
            if d.lower() not in config.SKIP_DIR_NAMES and not d.startswith(".")
            and os.path.normcase(os.path.realpath(os.path.join(dirpath, d))) not in skip
        ]
        for name in filenames:
            if Path(name).suffix.lower() in ARCHIVE_EXTS:
                found.append(Path(dirpath) / name)
    return sorted(found)


def list_images(archive: str | Path) -> list[Member]:
    """Image members of one archive.

    Only the central directory is read, so this is fast even for a multi-GB
    archive - listing a few dozen Takeout zips takes well under a second.
    """
    archive = str(archive)
    out: list[Member] = []
    try:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                if name.startswith(_SKIP_PREFIXES) or "/." in name:
                    continue
                if Path(name).suffix.lower() not in config.IMAGE_EXTS:
                    continue
                stamp = _member_time(info)
                out.append(Member(archive, name, info.file_size, stamp))
    except (zipfile.BadZipFile, OSError):
        # A corrupt or still-downloading archive must not stop the scan.
        return []
    return out


def count_members(archive: str | Path) -> dict:
    """Images / videos / other, for telling the user what an archive holds."""
    counts = {"images": 0, "videos": 0, "other": 0, "image_bytes": 0}
    try:
        with zipfile.ZipFile(str(archive)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                ext = Path(info.filename).suffix.lower()
                if ext in config.IMAGE_EXTS:
                    counts["images"] += 1
                    counts["image_bytes"] += info.file_size
                elif ext in config.VIDEO_EXTS:
                    counts["videos"] += 1
                else:
                    counts["other"] += 1
    except (zipfile.BadZipFile, OSError):
        pass
    return counts


def survey(roots: list[str]) -> dict:
    """What the archives under these folders contain, without reading them."""
    total = {"archives": 0, "images": 0, "videos": 0, "image_bytes": 0,
             "broken": [], "list": []}
    for root in roots:
        if not Path(root).exists():
            continue
        for archive in find_archives(root):
            counts = count_members(archive)
            if counts["images"] == counts["videos"] == counts["other"] == 0:
                total["broken"].append(str(archive))
                continue
            total["archives"] += 1
            total["images"] += counts["images"]
            total["videos"] += counts["videos"]
            total["image_bytes"] += counts["image_bytes"]
            total["list"].append({"path": str(archive), **counts})
    return total


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def read_member(archive: str, member: str) -> bytes:
    with zipfile.ZipFile(archive) as zf:
        return zf.read(member)


def read_key(key: str) -> bytes:
    parts = split_key(key)
    if parts is None:
        raise ValueError("Arsiv anahtari degil: %s" % key)
    return read_member(*parts)


def extract_key(key: str, dest: Path) -> Path:
    """Write one member out to `dest`, creating parent folders."""
    parts = split_key(key)
    if parts is None:
        raise ValueError("Arsiv anahtari degil: %s" % key)
    archive, member = parts
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf, zf.open(member) as src, open(dest, "wb") as out:
        while True:
            chunk = src.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    return dest


def exists(key: str) -> bool:
    parts = split_key(key)
    if parts is None:
        return False
    archive, member = parts
    if not Path(archive).exists():
        return False
    try:
        with zipfile.ZipFile(archive) as zf:
            zf.getinfo(member)
        return True
    except (KeyError, zipfile.BadZipFile, OSError):
        return False
