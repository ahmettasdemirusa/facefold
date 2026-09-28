"""Paths, tunable constants and persisted user settings."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Everything Facefold generates lives under DATA_DIR so the repo stays clean
# and the user can delete a single folder to start over.
DATA_DIR = Path(os.environ.get("FACEFOLD_DATA", PROJECT_ROOT / "veri"))
DB_PATH = DATA_DIR / "facefold.db"
THUMB_DIR = DATA_DIR / "kucuk"          # photo thumbnails for the web UI
FACE_DIR = DATA_DIR / "yuzler"          # cropped face images for the web UI
LOG_DIR = DATA_DIR / "kayit"

# Default output root; overridable from the settings page.
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "Duzenlenmis"


def ensure_dirs() -> None:
    for d in (DATA_DIR, THUMB_DIR, FACE_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# File types
# --------------------------------------------------------------------------

# Formats Pillow (+pillow-heif) can open. HEIC/HEIF matter most: that is what
# an iPhone actually writes.
IMAGE_EXTS = {
    ".jpg", ".jpeg", ".jpe", ".png", ".webp", ".bmp", ".tif", ".tiff",
    ".heic", ".heif", ".avif", ".gif",
}

# Recognised but deliberately skipped in v1 (video support is planned).
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp", ".mpg", ".mpeg"}

# Junk that shows up in phone dumps and Google Takeout exports.
SKIP_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
SKIP_DIR_NAMES = {
    "$recycle.bin", "system volume information", ".git", "__pycache__",
    "veri", "duzenlenmis",
}


# --------------------------------------------------------------------------
# Recognition tuning
# --------------------------------------------------------------------------

# buffalo_l = SCRFD detector + ArcFace w600k_r50 (512-d) + age/gender head.
MODEL_NAME = "buffalo_l"

# insightface runs every loaded model on every face. The buffalo_l pack ships
# two landmark models we do not need: 2d106det is unused entirely, and 1k3d68
# exists only to give us a head-turn angle that slightly biases which face
# becomes a person's avatar. Together they cost ~44% of analysis time, so they
# stay off. Add "landmark_3d_68" here to get face.pose back.
ACTIVE_MODULES = ["detection", "recognition", "genderage"]

# Detector input size. Bigger finds smaller/farther faces but costs time.
DET_SIZE = (640, 640)

# Faces below this detector score are noise (walls, patterns, reflections).
MIN_DET_SCORE = 0.55

# A face smaller than this (pixels, longest side of the box) carries too little
# identity information to trust. Background crowds land here.
MIN_FACE_PX = 42

# Cosine-similarity thresholds for ArcFace embeddings, empirically sane:
#   > 0.55  almost certainly the same person
#   0.40-0.55  probably the same person -> ask the user
#   < 0.40  treat as different
MATCH_STRONG = 0.55
MATCH_WEAK = 0.40

# Agglomerative clustering cut-off, expressed as cosine *distance* (1 - sim).
# Lower = stricter = more, purer groups. 0.55 keeps groups clean enough that
# merging by hand is quick, which beats un-mixing a polluted group.
CLUSTER_DISTANCE = 0.55

# Groups smaller than this are parked in "Kararsiz" instead of cluttering the
# naming screen.
MIN_CLUSTER_SIZE = 3

# Long side the image is resized to before detection. 1600 keeps small faces
# findable while staying quick on a CPU.
DETECT_MAX_SIDE = 1600

THUMB_SIZE = 480
FACE_THUMB_SIZE = 160

# Perceptual-hash distance under which two photos are "the same shot".
PHASH_THRESHOLD = 6


# --------------------------------------------------------------------------
# User settings (persisted in the DB, editable from the web UI)
# --------------------------------------------------------------------------

@dataclass
class Settings:
    """Values the user can change without touching code."""

    sources: list = field(default_factory=list)         # folders to scan
    output_dir: str = str(DEFAULT_OUTPUT_DIR)
    link_mode: str = "hardlink"                          # hardlink|copy|report
    date_subfolders: bool = True                         # .../Kategori/2024/...
    skip_duplicates: bool = True
    min_det_score: float = MIN_DET_SCORE
    min_face_px: int = MIN_FACE_PX
    cluster_distance: float = CLUSTER_DISTANCE
    match_strong: float = MATCH_STRONG
    match_weak: float = MATCH_WEAK
    detect_max_side: int = DETECT_MAX_SIDE
    language: str = "tr"
    workers: int = max(1, (os.cpu_count() or 4) // 2)

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "Settings":
        data = json.loads(raw) if raw else {}
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})
