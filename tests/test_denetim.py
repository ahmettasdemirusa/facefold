"""Regression tests for the faults a code audit turned up.

Each test here exists because something was quietly wrong and nothing caught
it. They are written to fail against the code as it was, not merely to pass
against the code as it is - a test that would have passed either way is worse
than no test, because it reads like protection.

    python tests/make_fixtures.py
    python tests/test_denetim.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURES = Path(__file__).resolve().parent / "ornek_fotograflar"
_TMP = Path(tempfile.mkdtemp(prefix="facefold-denetim-"))
os.environ["FACEFOLD_DATA"] = str(_TMP)

from PIL import Image  # noqa: E402

from facefold import (archives, auto, clustering, db, distribute, imaging,  # noqa: E402
                     pipeline, rules, scanner, trash)

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

    db.init_db()

    # ------------------------------------------------------------------
    print("\n== Duz kareler kopya sanilmamali ==")
    # Two plain colours a person can tell apart at a glance. Their perceptual
    # hashes are identical - the hash has no structure to describe - so the
    # old phash-only comparison merged them and offered both for deletion.
    flat_dir = _TMP / "duz"
    flat_dir.mkdir(parents=True, exist_ok=True)
    a_path = flat_dir / "duz_a.jpg"
    b_path = flat_dir / "duz_b.jpg"
    Image.new("RGB", (1200, 900), (200, 200, 200)).save(a_path, "JPEG", quality=92)
    Image.new("RGB", (1200, 900), (150, 150, 150)).save(b_path, "JPEG", quality=92)

    img_a, img_b = imaging.load_image(a_path), imaging.load_image(b_path)
    ph_a, ph_b = imaging.phash(img_a), imaging.phash(img_b)
    check("iki duz karenin parmak izi ayni (testin anlamli olmasi icin sart)",
          imaging.hamming(ph_a, ph_b) <= 6,
          "hamming=%d" % imaging.hamming(ph_a, ph_b))

    row_a = {"phash": ph_a, "sig": imaging.signature(img_a), "width": 1200, "height": 900}
    row_b = {"phash": ph_b, "sig": imaging.signature(img_b), "width": 1200, "height": 900}
    check("gozle ayirt edilen iki duz kare kopya sayilmiyor",
          not scanner._same_shot(row_a, row_b))

    # And the same photo really is still recognised as the same photo.
    row_a2 = dict(row_a)
    check("ayni kare hala kopya sayiliyor", scanner._same_shot(row_a, row_a2))

    print("\n== Parmak izi kanitsiz kopya kurmamali ==")
    check("piksel imzasi yoksa kopya denmiyor",
          not scanner._same_shot({**row_a, "sig": None}, row_b))

    # ------------------------------------------------------------------
    print("\n== Unlem isaretli klasor arsiv sanilmamali ==")
    check("'Tatil! 2024' klasoru normal yol",
          not archives.is_inside_archive(r"C:\foto\Tatil! 2024\a.jpg"))
    check("gercek arsiv anahtari taniniyor",
          archives.is_inside_archive(r"D:\x.zip!Takeout/a.jpg"))
    check("arsiv adinda unlem olsa bile dogru bolunuyor",
          archives.split_key(r"C:\a!b.zip!ic/foto.jpg") == (r"C:\a!b.zip", "ic/foto.jpg"),
          str(archives.split_key(r"C:\a!b.zip!ic/foto.jpg")))

    bang = _TMP / "Tatil! 2024"
    bang.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FIXTURES / "portre_alice_1.jpg", bang / "foto.jpg")
    try:
        opened = imaging.load_image(bang / "foto.jpg")
        check("unlemli klasordeki fotograf okunabiliyor", opened.width > 0)
    except Exception as exc:  # noqa: BLE001
        check("unlemli klasordeki fotograf okunabiliyor", False, str(exc))

    # ------------------------------------------------------------------
    print("\n== Kutuphane hazirligi ==")
    out = _TMP / "cikti"
    s = db.get_settings()
    s.sources = [str(FIXTURES)]
    s.output_dir = str(out)
    s.date_subfolders = False
    db.save_settings(s)

    scanner.walk(s.sources)
    pipeline.process_new()
    from facefold import kinds
    kinds.classify_all()
    scanner.dedupe()
    clustering.recluster()

    named = {}
    for g in db.q("SELECT id FROM clusters WHERE person_id IS NULL ORDER BY size DESC"):
        counts: dict[str, int] = {}
        for r in db.q(
            "SELECT p.file_name FROM faces f JOIN photos p ON p.id=f.photo_id "
            "WHERE f.cluster_id=?", (g["id"],)
        ):
            if r["file_name"].startswith("portre_"):
                key = r["file_name"].split("_")[1].capitalize()
                counts[key] = counts.get(key, 0) + 1
        if counts:
            named[max(counts, key=counts.get)] = clustering.name_cluster(
                g["id"], max(counts, key=counts.get))
    check("kisiler isimlendirildi", len(named) >= 5, ", ".join(sorted(named)))

    # ------------------------------------------------------------------
    print("\n== Bolumleme butunlugu ==")
    auto.sync_from_settings()
    matches = rules.evaluate_all()
    cats = {c["id"]: c for c in db.q("SELECT * FROM categories WHERE enabled=1")}
    part = distribute.check_partition(matches, cats)
    print("     %s" % part)
    check("hicbir fotograf iki otomatik klasorde degil", part["duplicated"] == 0,
          str(part.get("duplicated_example", "")))
    check("hicbir fotograf klasorsuz kalmadi", part["unplaced"] == 0,
          str(part.get("unplaced_example", "")))

    print("\n== Elle isimlendirme tur suzgecini yener ==")
    shot = db.q1("SELECT id FROM photos WHERE kind='screenshot' LIMIT 1")
    person_id = next(iter(named.values()))
    if shot:
        clustering.assign_photos([shot["id"]], person_id)
        auto.sync_from_settings()
        matches = rules.evaluate_all()
        cats = {c["id"]: c for c in db.q("SELECT * FROM categories WHERE enabled=1")}
        where = [cats[cid]["name"] for cid, ids in matches.items()
                 if shot["id"] in ids and cats[cid]["origin"] == "auto"]
        check("elle isimlendirilen ekran goruntusu kisinin klasorunde", len(where) == 1,
              str(where))
        part = distribute.check_partition(matches, cats)
        check("bolumleme yine butun", part["ok"], str(part))
        clustering.unassign_photos([shot["id"]])
    else:
        check("elle isimlendirme testi (ekran goruntusu yok - atlandi)", True)

    # ------------------------------------------------------------------
    print("\n== Kisi birlestirme elle baglantilari korur ==")
    victim = db.q1("SELECT id FROM photos WHERE face_count=0 LIMIT 1")
    keep, drop = list(named.values())[:2]
    if victim:
        clustering.assign_photos([victim["id"]], drop)
        before = db.scalar("SELECT COUNT(*) FROM photo_people WHERE person_id=?", (drop,))
        check("elle baglanti kuruldu", before == 1, str(before))
        clustering.merge_persons(keep, drop)
        after = db.scalar(
            "SELECT COUNT(*) FROM photo_people WHERE person_id=? AND photo_id=?",
            (keep, victim["id"]),
        )
        check("birlestirmede elle baglanti kaybolmadi", after == 1, str(after))
    else:
        check("birlestirme testi (yuzsuz kare yok - atlandi)", True)

    # ------------------------------------------------------------------
    print("\n== Cikti klasoru degisince yeni klasor doluyor ==")
    distribute.build()
    first = sum(1 for f in out.rglob("*") if f.is_file())
    check("ilk cikti olustu", first > 0, str(first))

    out2 = _TMP / "cikti2"
    s = db.get_settings()
    s.output_dir = str(out2)
    db.save_settings(s)
    distribute.build()
    second = sum(1 for f in out2.rglob("*") if f.is_file())
    check("yeni cikti klasoru dolu", second > 0,
          "%d dosya (eski klasorde %d)" % (second, first))

    # ------------------------------------------------------------------
    print("\n== Toplu silme tahmine dayanmiyor ==")
    db.execute("UPDATE photos SET duplicate_of=NULL, dup_kind=NULL")
    ids = [r["id"] for r in db.q("SELECT id FROM photos LIMIT 3")]
    db.execute("UPDATE photos SET duplicate_of=?, dup_kind='similar' WHERE id=?",
               (ids[0], ids[1]))
    db.execute("UPDATE photos SET duplicate_of=?, dup_kind='exact' WHERE id=?",
               (ids[0], ids[2]))
    kesin = trash.photo_ids_for_duplicates("exact")
    check("toplu silme yalnizca birebir kopyalari donduruyor",
          ids[2] in kesin and ids[1] not in kesin, str(kesin))

    print("\n== Kaynak dosyalar ==")
    check("kaynak klasor bozulmadi", len(list(FIXTURES.iterdir())) == 27,
          "%d dosya" % len(list(FIXTURES.iterdir())))

    print("\n" + "=" * 60)
    if failures:
        print("%d test KALDI: %s" % (len(failures), "; ".join(failures)))
        return 1
    print("Butun denetim regresyon testleri gecti.")
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        db.close()
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(code)
