"""Face detection and identity embedding.

Wraps insightface's buffalo_l pack: SCRFD for detection, ArcFace (w600k_r50)
for the 512-d identity vector, plus an age/gender head we use as a hint when
the same person changes a lot over the years.

The model is downloaded once to ~/.insightface and then runs fully offline.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from . import config, imaging

_engine_lock = threading.Lock()
_engine = None


@dataclass
class DetectedFace:
    """One face in one photo, in ORIGINAL image pixel coordinates."""

    bbox: tuple[float, float, float, float]
    det_score: float
    embedding: np.ndarray          # L2-normalised, 512-d
    face_px: int                   # longest side of the box, in original pixels
    age: int | None = None
    gender: int | None = None      # 1 = male, 0 = female (model's guess)
    yaw: float | None = None       # head turn in degrees; ~0 is frontal
    blur: float = 0.0
    kps: list = field(default_factory=list)

    def bbox_json(self) -> str:
        return json.dumps([round(float(v), 2) for v in self.bbox])


class FaceEngine:
    """Lazy-loaded singleton. Loading the ONNX models costs a few seconds."""

    def __init__(self, det_size: tuple[int, int] = config.DET_SIZE,
                 modules: list[str] | None = None):
        from insightface.app import FaceAnalysis

        self.app = FaceAnalysis(
            name=config.MODEL_NAME,
            providers=["CPUExecutionProvider"],
            allowed_modules=modules if modules is not None else config.ACTIVE_MODULES,
        )
        self.app.prepare(ctx_id=-1, det_size=det_size)
        self.det_size = det_size

    def analyze(
        self,
        img: Image.Image,
        *,
        max_side: int = config.DETECT_MAX_SIDE,
        min_det_score: float = config.MIN_DET_SCORE,
        min_face_px: int = config.MIN_FACE_PX,
        with_blur: bool = True,
    ) -> list[DetectedFace]:
        """Detect every usable face and return identity vectors.

        Detection runs on a downscaled copy for speed; boxes are scaled back so
        callers always work in original-image coordinates.
        """
        w, h = img.size
        longest = max(w, h)
        scale = 1.0
        if max_side and longest > max_side:
            scale = max_side / float(longest)

        bgr = imaging.to_bgr(img, max_side=max_side)
        raw = self.app.get(bgr)

        out: list[DetectedFace] = []
        inv = 1.0 / scale if scale else 1.0
        for f in raw:
            score = float(getattr(f, "det_score", 0.0) or 0.0)
            if score < min_det_score:
                continue

            x1, y1, x2, y2 = (float(v) * inv for v in f.bbox)
            face_px = int(max(x2 - x1, y2 - y1))
            if face_px < min_face_px:
                # Too small to carry reliable identity: background strangers.
                continue

            emb = getattr(f, "normed_embedding", None)
            if emb is None:
                emb = getattr(f, "embedding", None)
                if emb is None:
                    continue
                norm = np.linalg.norm(emb)
                emb = emb / norm if norm else emb
            emb = np.asarray(emb, dtype=np.float32)

            yaw = None
            pose = getattr(f, "pose", None)
            if pose is not None and len(pose) >= 2:
                yaw = float(pose[1])

            age = getattr(f, "age", None)
            gender = getattr(f, "gender", None)

            bbox = (x1, y1, x2, y2)
            out.append(
                DetectedFace(
                    bbox=bbox,
                    det_score=score,
                    embedding=emb,
                    face_px=face_px,
                    age=int(age) if age is not None else None,
                    gender=int(gender) if gender is not None else None,
                    yaw=yaw,
                    blur=imaging.blur_score(img, bbox) if with_blur else 0.0,
                    kps=[[float(a) * inv, float(b) * inv] for a, b in
                         (getattr(f, "kps", None) if getattr(f, "kps", None) is not None else [])],
                )
            )

        # Biggest face first: the subject of the photo usually leads.
        out.sort(key=lambda d: d.face_px, reverse=True)
        return out


def get_engine() -> FaceEngine:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = FaceEngine()
    return _engine


def model_is_downloaded() -> bool:
    """True when buffalo_l is already on disk (so we can warn before a scan)."""
    root = Path.home() / ".insightface" / "models" / config.MODEL_NAME
    return (root / "w600k_r50.onnx").exists() and (root / "det_10g.onnx").exists()


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------

def similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of two normalised ArcFace embeddings, in [-1, 1]."""
    if a is None or b is None:
        return -1.0
    return float(np.dot(a, b))


def similarity_matrix(vecs: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """All-vs-one similarities; vecs is (N, 512), ref is (512,)."""
    if vecs.size == 0:
        return np.zeros((0,), dtype=np.float32)
    return vecs @ ref


def normalise(vec: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vec)
    return (vec / norm).astype(np.float32) if norm else vec.astype(np.float32)


def centroid(vecs: np.ndarray) -> np.ndarray:
    """Mean vector, re-normalised - the 'average face' of a group."""
    if len(vecs) == 0:
        return np.zeros((512,), dtype=np.float32)
    return normalise(np.mean(vecs, axis=0))
