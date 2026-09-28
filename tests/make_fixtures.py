"""Build a small synthetic photo library for testing, from real faces.

insightface ships a group photo containing six people. We cut those faces out
and recombine them into portraits, pairs and duplicates, which gives us a
library where the correct answer is known in advance - so the rule engine can
be checked properly without needing anyone's private photos.

Usage:
    python tests/make_fixtures.py [output_dir]
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import insightface  # noqa: E402
from PIL import Image, ImageEnhance  # noqa: E402

from facefold import faces as face_mod, imaging  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parent / "ornek_fotograflar"

# Stand-in names so the fixture reads like a real family library. Deliberately
# invented and deliberately not Turkish: the examples in the documentation are
# generated from this cast, and nobody's actual family should end up as the
# sample data of a public project. Alphabetical so a reader can see at a glance
# that they are placeholders.
NAMES = ["Alice", "Bruno", "Clara", "David", "Emma", "Felix"]


def crop_person(img: Image.Image, bbox, margin: float = 1.6, size: int = 900) -> Image.Image:
    """A portrait-framed crop: head plus enough surroundings to look natural."""
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    cx, cy = x1 + w / 2, y1 + h / 2
    half = max(w, h) * margin / 2
    box = (
        max(0, int(cx - half)), max(0, int(cy - half * 1.1)),
        min(img.width, int(cx + half)), min(img.height, int(cy + half * 1.2)),
    )
    crop = img.crop(box)
    scale = size / max(crop.size)
    return crop.resize(
        (max(1, int(crop.width * scale)), max(1, int(crop.height * scale))),
        Image.LANCZOS,
    )


def vary(img: Image.Image, brightness: float, angle: float) -> Image.Image:
    """Small changes so copies are not byte-identical but stay the same person."""
    out = ImageEnhance.Brightness(img).enhance(brightness)
    if angle:
        out = out.rotate(angle, resample=Image.BICUBIC, expand=False)
    return out


def side_by_side(a: Image.Image, b: Image.Image, height: int = 800,
                 background=(245, 243, 238), gap: int = 0) -> Image.Image:
    def fit(im):
        scale = height / im.height
        return im.resize((max(1, int(im.width * scale)), height), Image.LANCZOS)

    left, right = fit(a), fit(b)
    canvas = Image.new("RGB", (left.width + right.width + gap, height), background)
    canvas.paste(left, (0, 0))
    canvas.paste(right, (left.width + gap, 0))
    return canvas


def landscape(width: int = 1200, height: int = 800) -> Image.Image:
    """A face-free outdoor photo, to exercise the 'nobody' rule."""
    img = Image.new("RGB", (width, height))
    px = img.load()
    for y in range(height):
        for x in range(width):
            wave = int(18 * ((x * 7 + y * 3) % 23) / 23)
            px[x, y] = (
                min(255, int(60 + 120 * y / height) + wave),
                min(255, int(110 + 90 * y / height) + wave),
                max(0, int(180 - 60 * y / height) - wave),
            )
    return img


def screenshot(width: int = 900, height: int = 1600) -> Image.Image:
    """A face-free screenshot: pale background, bars, blocks of 'text'."""
    img = Image.new("RGB", (width, height), (250, 250, 252))
    px = img.load()
    for y in range(height):
        for x in range(width):
            if y < 90 or y > height - 120:
                px[x, y] = (32, 34, 40)
            elif 140 < y < 190 and 40 < x < width - 40:
                px[x, y] = (225, 228, 235)
            elif (y // 70) % 3 == 0 and 40 < x < width - 200:
                px[x, y] = (210, 214, 222)
    return img


def main(out_dir: Path) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    src = Path(insightface.__file__).parent / "data" / "images" / "t1.jpg"
    group = imaging.load_image(src)

    print("Yuzler cikariliyor...")
    engine = face_mod.get_engine()
    dets = engine.analyze(group)
    print("  %d yuz bulundu" % len(dets))
    if len(dets) < 4:
        raise SystemExit("Ornek fotograftan yeterli yuz cikarilamadi.")

    # Sort left-to-right so the naming is stable between runs.
    dets = sorted(dets, key=lambda d: d.bbox[0])
    people = [crop_person(group, d.bbox) for d in dets]
    names = NAMES[: len(people)]

    manifest: list[str] = []
    day = 1

    def save(img: Image.Image, name: str, note: str, quality: int = 92) -> Path:
        nonlocal day
        path = out_dir / name
        img.convert("RGB").save(path, "JPEG", quality=quality)
        # Spread the library across 2024 so date filters have something to do.
        stamp = time.mktime((2024, 1 + (day % 12), 1 + (day % 27), 12, 0, 0, 0, 0, -1))
        os.utime(path, (stamp, stamp))
        day += 1
        manifest.append("%-34s %s" % (name, note))
        return path

    # --- portraits: three genuinely different shots of each person ----------
    # Framing has to change, not just brightness: three near-identical frames
    # really are duplicates, and the deduplicator is right to say so.
    variants = [(1.6, 1.0, 0), (2.2, 1.12, 3), (1.3, 0.9, -4)]
    for idx, (det, name) in enumerate(zip(dets, names)):
        for n, (margin, bright, angle) in enumerate(variants):
            shot = crop_person(group, det.bbox, margin=margin)
            save(vary(shot, bright, angle),
                 "portre_%s_%d.jpg" % (name.lower(), n + 1),
                 "tek kisi: %s" % name)

    # --- pairs: same two people, different compositions ---------------------
    pair_specs = [
        (0, 1, dict(background=(245, 243, 238), gap=0)),
        (1, 0, dict(background=(30, 42, 60), gap=60)),   # swapped and re-styled
        (0, 2, dict(background=(245, 243, 238), gap=0)),
        (3, 4, dict(background=(245, 243, 238), gap=0)),
    ]
    for n, (i, j, style) in enumerate(pair_specs):
        if i < len(people) and j < len(people):
            first, second = sorted((names[i], names[j]))
            save(side_by_side(people[i], people[j], **style),
                 "ikili_%s_%s_%d.jpg" % (first.lower(), second.lower(), n + 1),
                 "iki kisi: %s + %s" % (names[i], names[j]))

    # --- the whole group ----------------------------------------------------
    original = save(group, "grup_hepsi.jpg", "hepsi bir arada")

    # --- duplicates ---------------------------------------------------------
    exact = out_dir / "grup_hepsi_KOPYA.jpg"
    shutil.copy2(original, exact)
    manifest.append("%-34s %s" % (exact.name, "birebir kopya (bayt bayt ayni)"))

    small = group.resize((group.width // 2, group.height // 2), Image.LANCZOS)
    save(small.resize(group.size, Image.LANCZOS), "grup_hepsi_yeniden_sikistirilmis.jpg",
         "gorsel kopya (yeniden sikistirilmis)", quality=60)

    # --- no faces -----------------------------------------------------------
    save(landscape(), "manzara.jpg", "yuz yok")
    save(screenshot(), "ekran_goruntusu.jpg", "yuz yok")

    print()
    print("Uretilen dosyalar (%s):" % out_dir)
    for line in manifest:
        print("  " + line)
    print()
    print("Toplam %d dosya" % len(list(out_dir.iterdir())))
    print("Kisiler: %s" % ", ".join(names))


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    main(target)
