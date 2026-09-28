"""Reading photos: HEIC support, EXIF metadata, hashes and thumbnails.

Everything that has to cope with "what a phone actually writes to disk" lives
here, so the rest of the codebase can assume a clean, upright RGB image.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps

from . import archives, config

# iPhones write HEIC by default. Without this, half a phone dump is unreadable.
try:  # pragma: no cover - depends on optional wheel
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_OK = True
except Exception:  # noqa: BLE001
    HEIF_OK = False

# Phone photos are big; refuse only the genuinely absurd.
Image.MAX_IMAGE_PIXELS = 400_000_000

EXIF_DATETIME_ORIGINAL = 0x9003
EXIF_DATETIME_DIGITIZED = 0x9004
EXIF_DATETIME = 0x0132
EXIF_ORIENTATION = 0x0112
EXIF_MAKE = 0x010F
EXIF_MODEL = 0x0110
EXIF_GPS_IFD = 0x8825


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def open_source(path: str | Path, data: bytes | None = None):
    """A file-like handle for a photo, whether it is loose or inside a zip.

    Everything downstream works the same either way, which is what keeps
    archive support from leaking into the rest of the codebase.

    Pass `data` when the caller has already read the bytes. Analysing one photo
    needs three passes over it - decode, metadata, checksum - and for a photo
    inside a zip each pass otherwise reopened the archive and re-read its
    central directory, which on a Takeout export of tens of thousands of
    members is most of the work.
    """
    if data is not None:
        return io.BytesIO(data)
    text = str(path)
    if archives.is_inside_archive(text):
        return io.BytesIO(archives.read_key(text))
    return open(text, "rb")


def read_source(path: str | Path) -> bytes:
    """The whole photo as bytes - one read, for callers that need it thrice."""
    with open_source(path) as fh:
        return fh.read()


def load_image(path: str | Path, data: bytes | None = None) -> Image.Image:
    """Open a photo as upright RGB, honouring the EXIF orientation flag.

    Phones almost never rotate pixels; they set a flag instead. Skipping this
    step makes every portrait photo a sideways face the detector will miss.
    """
    with open_source(path, data) as handle:
        img = Image.open(handle)
        img.load()
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")
    return img


def to_bgr(img: Image.Image, max_side: int | None = None) -> np.ndarray:
    """PIL RGB -> OpenCV-style BGR array, optionally downscaled.

    Returns the array insightface expects. Detection on a 1600px long side is
    a good speed/recall trade-off: faces smaller than that in the original are
    usually background strangers anyway.
    """
    if max_side:
        w, h = img.size
        longest = max(w, h)
        if longest > max_side:
            scale = max_side / float(longest)
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                             Image.LANCZOS)
    arr = np.asarray(img, dtype=np.uint8)
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    return arr[:, :, ::-1].copy()


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

_FILENAME_DATE_PATTERNS = [
    # IMG_20240315_123456 / PXL_20240315_123456789 / VID_20240315_...
    (re.compile(r"(?:^|[^0-9])(20\d{2})(\d{2})(\d{2})[_\-\s]?(\d{2})(\d{2})(\d{2})"),
     ("Y", "m", "d", "H", "M", "S")),
    # 2024-03-15 12.34.56 / 2024_03_15-12_34_56 (WhatsApp, Signal, Screenshots)
    (re.compile(r"(20\d{2})[-_.](\d{2})[-_.](\d{2})[-_.\s]+(\d{2})[-_.](\d{2})[-_.](\d{2})"),
     ("Y", "m", "d", "H", "M", "S")),
    # Date only: 2024-03-15 or 20240315
    (re.compile(r"(?:^|[^0-9])(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?:[^0-9]|$)"),
     ("Y", "m", "d")),
]


def _parse_exif_datetime(raw: Any) -> datetime | None:
    if not raw:
        return None
    text = str(raw).strip().replace("/", ":")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
    return None


def _date_from_filename(name: str) -> datetime | None:
    for pattern, fields in _FILENAME_DATE_PATTERNS:
        m = pattern.search(name)
        if not m:
            continue
        parts = dict(zip(fields, (int(g) for g in m.groups())))
        try:
            return datetime(
                parts.get("Y", 1970), parts.get("m", 1), parts.get("d", 1),
                parts.get("H", 0), parts.get("M", 0), parts.get("S", 0),
            )
        except ValueError:
            continue
    return None


# Digital photos start in the late 1990s in practice. Anything older is a
# default value from somewhere - a zip with no date, a camera with a flat
# clock battery - not a real capture time, and pretending otherwise buries
# photos in a folder for a year they were not taken in.
MIN_PLAUSIBLE_YEAR = 1995
MAX_PLAUSIBLE_YEAR = 2100


def plausible(dt: datetime | None) -> bool:
    return bool(dt) and MIN_PLAUSIBLE_YEAR <= dt.year <= MAX_PLAUSIBLE_YEAR


def _gps_to_degrees(value) -> float | None:
    try:
        d, m, s = (float(x) for x in value)
        return d + m / 60.0 + s / 3600.0
    except (TypeError, ValueError):
        return None


def read_meta(path: str | Path, img: Image.Image | None = None,
              mtime: float | None = None, data: bytes | None = None) -> dict:
    """Collect width/height, capture time, camera and GPS.

    Capture time falls back through EXIF -> filename -> file mtime, and we
    record which one was used so the UI can be honest about accuracy.
    """
    p = Path(path)
    meta: dict[str, Any] = {
        "width": None, "height": None, "orientation": 1,
        "taken_at": None, "taken_source": None,
        "camera": None, "gps_lat": None, "gps_lon": None,
    }

    opened_here = False
    handle = None
    try:
        if img is None:
            # A fresh handle, because the caller's image has already been
            # rotated by exif_transpose and lost its orientation tag.
            handle = open_source(path, data)
            img = Image.open(handle)
            opened_here = True
        meta["width"], meta["height"] = img.size

        exif = img.getexif()
        if exif:
            meta["orientation"] = int(exif.get(EXIF_ORIENTATION, 1) or 1)
            if meta["orientation"] in (5, 6, 7, 8):
                # The photo is stored on its side with a flag saying so.
                # Everything else here works on the upright image - the
                # thumbnail, the perceptual hash, the pixel signature, the face
                # boxes - so the recorded size has to be the upright size too.
                # Leaving the raw values made every portrait photo look like a
                # landscape one, and the duplicate check compares aspect ratios.
                meta["width"], meta["height"] = meta["height"], meta["width"]

            make = str(exif.get(EXIF_MAKE, "") or "").strip()
            model = str(exif.get(EXIF_MODEL, "") or "").strip()
            camera = " ".join(x for x in (make, model) if x)
            meta["camera"] = camera or None

            dt = None
            try:
                sub = exif.get_ifd(0x8769)
            except Exception:  # noqa: BLE001
                sub = {}
            for tag, table in (
                (EXIF_DATETIME_ORIGINAL, sub),
                (EXIF_DATETIME_DIGITIZED, sub),
                (EXIF_DATETIME, exif),
            ):
                dt = _parse_exif_datetime((table or {}).get(tag))
                if dt:
                    break
            if plausible(dt):
                meta["taken_at"] = dt.strftime("%Y-%m-%d %H:%M:%S")
                meta["taken_source"] = "exif"

            try:
                gps = exif.get_ifd(EXIF_GPS_IFD)
            except Exception:  # noqa: BLE001
                gps = {}
            if gps:
                lat = _gps_to_degrees(gps.get(2))
                lon = _gps_to_degrees(gps.get(4))
                if lat is not None and str(gps.get(1, "N")).upper().startswith("S"):
                    lat = -lat
                if lon is not None and str(gps.get(3, "E")).upper().startswith("W"):
                    lon = -lon
                meta["gps_lat"], meta["gps_lon"] = lat, lon
    except Exception:  # noqa: BLE001
        pass
    finally:
        if opened_here and img is not None:
            img.close()
        if handle is not None:
            handle.close()

    if not meta["taken_at"]:
        dt = _date_from_filename(p.name)
        if plausible(dt):
            meta["taken_at"] = dt.strftime("%Y-%m-%d %H:%M:%S")
            meta["taken_source"] = "filename"

    if not meta["taken_at"]:
        stamp = mtime
        if stamp is None:
            try:
                stamp = p.stat().st_mtime
            except OSError:
                stamp = None
        if stamp:
            try:
                dt = datetime.fromtimestamp(stamp)
            except (OSError, ValueError, OverflowError):
                dt = None
            if plausible(dt):
                meta["taken_at"] = dt.strftime("%Y-%m-%d %H:%M:%S")
                meta["taken_source"] = "mtime"
    # Leaving taken_at empty is the honest answer when nothing reliable exists;
    # the UI says "tarih bilinmiyor" rather than inventing a year.

    return meta


# ---------------------------------------------------------------------------
# Hashes
# ---------------------------------------------------------------------------

def sha1_file(path: str | Path, chunk: int = 1 << 20,
              data: bytes | None = None) -> str:
    """Byte-exact fingerprint: catches the same file arriving twice."""
    h = hashlib.sha1()
    with open_source(path, data) as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def phash(img: Image.Image, hash_size: int = 8, highfreq_factor: int = 4) -> str:
    """Perceptual hash (DCT based).

    Catches the *visually* same shot even when bytes differ - which is exactly
    what happens when one copy comes off the phone and another comes back from
    Google Photos after re-compression.
    """
    from scipy.fft import dct

    size = hash_size * highfreq_factor
    small = img.convert("L").resize((size, size), Image.LANCZOS)
    pixels = np.asarray(small, dtype=np.float64)
    coeffs = dct(dct(pixels, axis=0, norm="ortho"), axis=1, norm="ortho")
    block = coeffs[:hash_size, :hash_size]
    # Exclude the DC term: it only encodes overall brightness.
    med = np.median(block.flatten()[1:])
    bits = (block > med).flatten()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return "%016x" % value


def hamming(a: str | None, b: str | None) -> int:
    """Bit distance between two hex phashes; 64 means 'nothing in common'."""
    if not a or not b:
        return 64
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return 64


def signature(img: Image.Image, grid: int = 8) -> bytes:
    """An 8x8 grey thumbnail kept as 64 raw bytes.

    pHash alone is not enough to declare two photos the same. It throws away
    absolute brightness and keeps only coarse structure, so low-detail images -
    a clear sky, a white wall, a smooth gradient - collide with each other.
    Comparing actual pixel values catches those; the two checks together are
    much harder to fool than either alone.
    """
    small = img.convert("L").resize((grid, grid), Image.LANCZOS)
    return np.asarray(small, dtype=np.uint8).tobytes()


def sig_distance(a: bytes | None, b: bytes | None) -> float:
    """Mean absolute brightness difference, 0-255. Under ~12 is 'same photo'."""
    if not a or not b or len(a) != len(b):
        return 255.0
    va = np.frombuffer(a, dtype=np.uint8).astype(np.int16)
    vb = np.frombuffer(b, dtype=np.uint8).astype(np.int16)
    return float(np.abs(va - vb).mean())


def aspect_ratio(width: int | None, height: int | None) -> float:
    if not width or not height:
        return 0.0
    return float(width) / float(height)


# ---------------------------------------------------------------------------
# Thumbnails
# ---------------------------------------------------------------------------

def save_thumb(img: Image.Image, dest: Path, size: int = config.THUMB_SIZE) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    thumb = img.copy()
    thumb.thumbnail((size, size), Image.LANCZOS)
    thumb.convert("RGB").save(dest, "JPEG", quality=82, optimize=True)
    return str(dest)


def save_face_crop(img: Image.Image, bbox, dest: Path,
                   size: int = config.FACE_THUMB_SIZE, margin: float = 0.35) -> str:
    """Crop a face with breathing room so the UI shows a recognisable head."""
    x1, y1, x2, y2 = (float(v) for v in bbox)
    w, h = x2 - x1, y2 - y1
    cx, cy = x1 + w / 2, y1 + h / 2
    half = max(w, h) * (1 + margin) / 2
    box = (
        max(0, int(cx - half)), max(0, int(cy - half)),
        min(img.width, int(cx + half)), min(img.height, int(cy + half)),
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    crop = img.crop(box).resize((size, size), Image.LANCZOS)
    crop.convert("RGB").save(dest, "JPEG", quality=85, optimize=True)
    return str(dest)


def blur_score(img: Image.Image, bbox) -> float:
    """Variance of Laplacian over the face box - low means blurry.

    A blurry face still yields an embedding, but a bad one. Recording the score
    lets us prefer sharp faces when picking a cover photo or a match anchor.
    """
    x1, y1, x2, y2 = (int(v) for v in bbox)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(img.width, x2), min(img.height, y2)
    if x2 - x1 < 8 or y2 - y1 < 8:
        return 0.0
    patch = np.asarray(img.crop((x1, y1, x2, y2)).convert("L"), dtype=np.float64)
    lap = (
        -4 * patch[1:-1, 1:-1]
        + patch[:-2, 1:-1] + patch[2:, 1:-1]
        + patch[1:-1, :-2] + patch[1:-1, 2:]
    )
    return float(lap.var())


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def is_image(path: str | Path) -> bool:
    return Path(path).suffix.lower() in config.IMAGE_EXTS


def is_video(path: str | Path) -> bool:
    return Path(path).suffix.lower() in config.VIDEO_EXTS


def safe_stat(path: str | Path) -> os.stat_result | None:
    try:
        return os.stat(path)
    except OSError:
        return None


def source_exists(path: str | Path) -> bool:
    """True if the photo can still be read - loose file or archive member."""
    text = str(path)
    if archives.is_inside_archive(text):
        return archives.exists(text)
    return Path(text).exists()
