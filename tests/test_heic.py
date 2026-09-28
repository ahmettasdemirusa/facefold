"""iPhone HEIC support, and the cross-format duplicate case.

The scenario this covers is the one that motivated the project: the same photo
arrives twice - once as HEIC straight off the phone, once as a re-compressed
JPEG out of a Google Photos export. The bytes differ, the format differs, the
dimensions may differ, and it must still be recognised as one photo.

    python tests/test_heic.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURES = Path(__file__).resolve().parent / "ornek_fotograflar"
_TMP = Path(tempfile.mkdtemp(prefix="facefold-heic-"))
os.environ["FACEFOLD_DATA"] = str(_TMP)

from PIL import Image  # noqa: E402

from facefold import db, imaging, pipeline, scanner  # noqa: E402

PASS, FAIL = "  [gecti]", "  [KALDI]"
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("%s %s" % (PASS if ok else FAIL, label)) + (" - %s" % detail if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    if not FIXTURES.exists():
        print("Once 'python tests/make_fixtures.py' calistirin.")
        return 2

    check("pillow-heif yuklu", imaging.HEIF_OK)
    if not imaging.HEIF_OK:
        return 1

    photos = _TMP / "telefon"
    photos.mkdir(parents=True)

    source = Image.open(FIXTURES / "grup_hepsi.jpg")

    # 1. Straight off the phone: HEIC, original size, iPhone naming.
    heic = photos / "IMG_4471.HEIC"
    source.save(heic, format="HEIF", quality=90)

    # 2. Back from a Google Photos export: JPEG, re-compressed, half size.
    google = photos / "IMG_4471.jpg"
    smaller = source.resize((source.width // 2, source.height // 2), Image.LANCZOS)
    smaller.resize(source.size, Image.LANCZOS).save(google, "JPEG", quality=62)

    # 3. A genuinely different photo, which must NOT be merged with them.
    other = photos / "IMG_20240315_142233.HEIC"
    Image.open(FIXTURES / "ikili_david_emma_4.jpg").save(other, format="HEIF", quality=90)

    print("\n== Okuma ==")
    img = imaging.load_image(heic)
    check("HEIC acildi", img.size == source.size, str(img.size))
    check("HEIC RGB'ye cevrildi", img.mode == "RGB", img.mode)

    print("\n== Tarama ve cozumleme ==")
    db.init_db()
    settings = db.get_settings()
    settings.sources = [str(photos)]
    db.save_settings(settings)

    walked = scanner.walk(settings.sources)
    check("3 dosya bulundu (HEIC dahil)", walked["added"] == 3, str(walked["added"]))

    stats = pipeline.process_new()
    check("hepsi hatasiz islendi", stats["done"] == 3 and stats["errors"] == 0,
          "islenen=%d hata=%d" % (stats["done"], stats["errors"]))

    heic_row = db.q1("SELECT * FROM photos WHERE file_name='IMG_4471.HEIC'")
    check("HEIC'te yuz bulundu", heic_row["face_count"] == 6,
          "%d yuz" % heic_row["face_count"])

    print("\n== Dosya adindan tarih ==")
    dated = db.q1("SELECT * FROM photos WHERE file_name='IMG_20240315_142233.HEIC'")
    check("iPhone dosya adindan tarih cikarildi",
          (dated["taken_at"] or "").startswith("2024-03-15"),
          "%s (%s)" % (dated["taken_at"], dated["taken_source"]))

    print("\n== Formatlar arasi kopya ==")
    dup = scanner.dedupe()
    check("HEIC ile yeniden sikistirilmis JPEG eslesti", dup["similar"] == 1,
          "%d gorsel kopya" % dup["similar"])

    rows = {r["file_name"]: r for r in db.q("SELECT * FROM photos")}
    linked = [n for n, r in rows.items() if r["duplicate_of"] is not None]
    check("kopya olarak isaretlenen tek dosya var", len(linked) == 1, str(linked))
    check("farkli fotograf kopya sayilmadi",
          rows["IMG_20240315_142233.HEIC"]["duplicate_of"] is None)

    kept = [n for n, r in rows.items()
            if r["duplicate_of"] is None and n.startswith("IMG_4471")]
    check("asil olarak HEIC secildi (daha buyuk dosya)", kept == ["IMG_4471.HEIC"],
          str(kept))

    print("\n" + "=" * 60)
    if failures:
        print("%d test KALDI: %s" % (len(failures), "; ".join(failures)))
        return 1
    print("Butun HEIC testleri gecti.")
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        db.close()
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(code)
