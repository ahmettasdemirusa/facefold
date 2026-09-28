"""Pixelate every face in a screenshot before it is published.

The test fixtures are built from a sample photo of real people. Their faces
must not end up in the README under invented names, so screenshots are run
through Facefold's own detector and every face it finds is pixelated.

    python docs/yuzleri_bulaniklastir.py docs/ekranlar/kisiler.png ...
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from facefold import faces as face_mod  # noqa: E402

# Screenshots contain small avatar crops, so detection has to be more eager
# than it is for real photos.
MIN_SCORE = 0.30
MIN_PX = 20
PAD = 0.18          # expand each box so hair and chin are covered too
BLOCK = 7           # pixel block size; smaller number = coarser mosaic


def pixelate(img: Image.Image, box: tuple[int, int, int, int]) -> None:
    x1, y1, x2, y2 = box
    if x2 - x1 < 4 or y2 - y1 < 4:
        return
    region = img.crop(box)
    small = region.resize((max(2, region.width // BLOCK),
                           max(2, region.height // BLOCK)), Image.BILINEAR)
    img.paste(small.resize(region.size, Image.NEAREST), box)


def process(path: Path) -> int:
    img = Image.open(path).convert("RGB")
    engine = face_mod.get_engine()
    found = engine.analyze(
        img, max_side=2400, min_det_score=MIN_SCORE, min_face_px=MIN_PX,
        with_blur=False,
    )
    for face in found:
        x1, y1, x2, y2 = face.bbox
        w, h = x2 - x1, y2 - y1
        box = (
            max(0, int(x1 - w * PAD)), max(0, int(y1 - h * PAD)),
            min(img.width, int(x2 + w * PAD)), min(img.height, int(y2 + h * PAD)),
        )
        pixelate(img, box)
    img.save(path, "PNG", optimize=True)
    return len(found)


def main(paths: list[str]) -> int:
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            print("  atlandi (yok): %s" % path)
            continue
        count = process(path)
        print("  %-24s %d yuz bulaniklastirildi" % (path.name, count))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1:]))
