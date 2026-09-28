"""End-to-end check of the whole pipeline against a known-answer library.

Run tests/make_fixtures.py first, then:
    python tests/test_pipeline.py

The fixture library contains six people in portraits, pairs and one group shot
plus two duplicates and two face-free photos, so every stage has a correct
answer we can assert against rather than eyeball.
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

# Run against a scratch database so a real library is never touched.
_TMP = Path(tempfile.mkdtemp(prefix="facefold-test-"))
os.environ["FACEFOLD_DATA"] = str(_TMP)

from facefold import clustering, config, db, distribute, pipeline, rules, scanner  # noqa: E402

PASS, FAIL = "  [gecti]", "  [KALDI]"
failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(("%s %s" % (PASS if condition else FAIL, label)) + (" - %s" % detail if detail else ""))
    if not condition:
        failures.append(label)


def main() -> int:
    if not FIXTURES.exists():
        print("Once 'python tests/make_fixtures.py' calistirin.")
        return 2

    out_dir = _TMP / "cikti"
    db.init_db()
    settings = db.get_settings()
    settings.sources = [str(FIXTURES)]
    settings.output_dir = str(out_dir)
    settings.link_mode = "hardlink"
    settings.date_subfolders = False
    db.save_settings(settings)

    print("\n== 1. Tarama ==")
    stats = scanner.walk(settings.sources)
    check("27 fotograf bulundu", stats["added"] == 27, "eklenen=%d" % stats["added"])

    print("\n== 2. Yuz analizi ==")
    stats = pipeline.process_new()
    check("hepsi islendi", stats["done"] == 27 and stats["errors"] == 0,
          "islenen=%d hata=%d" % (stats["done"], stats["errors"]))
    check("yuz bulundu", stats["faces"] >= 30, "%d yuz" % stats["faces"])
    check("yuzsuz kareler ayrildi", stats["no_face"] == 2, "%d kare" % stats["no_face"])

    print("\n== 3. Kopya ayiklama ==")
    dup = scanner.dedupe()
    check("birebir kopya yakalandi (tam 1)", dup["exact"] == 1, "%d adet" % dup["exact"])
    check("yeniden sikistirilmis kopya yakalandi (tam 1)", dup["similar"] == 1,
          "%d adet" % dup["similar"])

    # The landscape and the screenshot are both low-detail: pHash alone merges
    # them. They must stay separate.
    manzara = db.q1("SELECT duplicate_of FROM photos WHERE file_name='manzara.jpg'")
    ekran = db.q1("SELECT duplicate_of FROM photos WHERE file_name='ekran_goruntusu.jpg'")
    check("manzara ile ekran goruntusu karistirilmadi",
          manzara["duplicate_of"] is None and ekran["duplicate_of"] is None)

    print("\n== 4. Gruplama ==")
    cl = clustering.recluster()
    check("6 kisi grubu olustu", cl["clusters"] == 6,
          "%d grup, %d dagimik yuz" % (cl["clusters"], cl["loose"]))

    groups = db.q(
        "SELECT c.id, c.size FROM clusters c WHERE c.person_id IS NULL ORDER BY c.size DESC"
    )
    print("     grup boyutlari: %s" % [g["size"] for g in groups])

    # Every group must be one person only: check internal purity by filename.
    impure = []
    for g in groups:
        names = set()
        for row in db.q(
            "SELECT p.file_name FROM faces f JOIN photos p ON p.id=f.photo_id "
            "WHERE f.cluster_id=?", (g["id"],)
        ):
            fn = row["file_name"]
            if fn.startswith("portre_"):
                names.add(fn.split("_")[1])
        if len(names) > 1:
            impure.append((g["id"], names))
    check("gruplar saf (bir grupta tek kisi)", not impure, str(impure))

    print("\n== 5. Isimlendirme ==")
    # Name each group after the person whose portraits dominate it.
    named = {}
    for g in groups:
        counts: dict[str, int] = {}
        for row in db.q(
            "SELECT p.file_name FROM faces f JOIN photos p ON p.id=f.photo_id "
            "WHERE f.cluster_id=?", (g["id"],)
        ):
            fn = row["file_name"]
            if fn.startswith("portre_"):
                key = fn.split("_")[1].capitalize()
                counts[key] = counts.get(key, 0) + 1
        if counts:
            best = max(counts, key=counts.get)
            named[best] = clustering.name_cluster(g["id"], best)
    check("6 kisi isimlendirildi", len(named) == 6, ", ".join(sorted(named)))

    assigned = clustering.assign_known()
    print("     ek olarak taninan yuz: %d" % assigned["assigned"])

    unnamed = db.scalar("SELECT COUNT(*) FROM faces WHERE person_id IS NULL")
    total_faces = db.scalar("SELECT COUNT(*) FROM faces")
    check("yuzlerin cogu bir kisiye baglandi", unnamed <= total_faces * 0.15,
          "%d/%d yuz isimsiz" % (unnamed, total_faces))

    print("\n== 6. Kategori kurallari ==")
    alice, bruno = named.get("Alice"), named.get("Bruno")
    clara = named.get("Clara")

    solo = rules.preview_count("solo", [alice])
    check("'Yalnizca Alice' 3 portre buldu", solo == 3, "%d fotograf" % solo)

    pair = rules.preview_count("exact", [alice, bruno])
    check("'Tam olarak Alice ve Bruno' 2 fotograf buldu", pair == 2, "%d fotograf" % pair)

    contains = rules.preview_count("contains", [alice])
    check("'Icinde Alice gecen' 3+2+1+1 = 7 fotograf", contains == 7,
          "%d fotograf" % contains)

    any_of = rules.preview_count("any", [alice, clara])
    check("'Alice veya Clara' 10 fotograf", any_of == 10, "%d fotograf" % any_of)

    nobody = rules.preview_count("nobody", [])
    check("'Hic yuz yok' 2 fotograf", nobody == 2, "%d fotograf" % nobody)

    dupes_excluded = rules.preview_count("contains", [alice], {"include_duplicates": True})
    check("kopyalar varsayilan olarak disarida", dupes_excluded > contains,
          "kopyalarla %d, kopyasiz %d" % (dupes_excluded, contains))

    print("\n== 7. Klasor olusturma ==")
    rules.create_category("Alice", "solo", [alice])
    rules.create_category("Alice ve Bruno", "exact", [alice, bruno])
    rules.create_category("Bruno (hepsi)", "contains", [bruno])
    rules.create_category("Yuzsuz", "nobody", [])

    ok, msg = distribute.hardlink_supported(str(FIXTURES), str(out_dir))
    print("     baglanti destegi: %s (%s)" % (ok, msg))

    result = distribute.build()
    check("dosyalar yerlestirildi", result["created"] > 0,
          "%d dosya, %d hata" % (result["created"], result["errors"]))

    made = {}
    for folder in sorted(out_dir.iterdir()):
        if folder.is_dir():
            files = list(folder.rglob("*.jpg"))
            made[folder.name] = len(files)
    print("     olusan klasorler: %s" % json.dumps(made, ensure_ascii=False))
    check("'Alice' klasorunde 3 dosya", made.get("Alice") == 3)
    check("'Alice ve Bruno' klasorunde 2 dosya", made.get("Alice ve Bruno") == 2)
    check("'Bruno (hepsi)' klasorunde 6 dosya", made.get("Bruno (hepsi)") == 6)
    check("'Yuzsuz' klasorunde 2 dosya", made.get("Yuzsuz") == 2)

    # Hardlinks must not consume a second copy of the bytes.
    if ok:
        sample = next((out_dir / "Alice").glob("*.jpg"))
        check("baglanti gercekten baglanti (disk sismedi)",
              sample.stat().st_nlink > 1, "nlink=%d" % sample.stat().st_nlink)

    print("\n== 8. Kural degisince klasor guncelleniyor mu ==")
    cat = db.q1("SELECT id FROM categories WHERE name='Alice'")
    rules.update_category(cat["id"], rule_type="contains")
    result = distribute.build()
    after = len(list((out_dir / "Alice").rglob("*.jpg")))
    check("kural genisleyince klasor buyudu", after == 7, "%d dosya" % after)

    rules.update_category(cat["id"], rule_type="solo")
    distribute.build()
    after = len(list((out_dir / "Alice").rglob("*.jpg")))
    check("kural daralinca fazlaliklar silindi", after == 3, "%d dosya" % after)

    print("\n== 9. Kaynak dosyalara dokunulmadi mi ==")
    check("kaynak klasor hala 27 dosya", len(list(FIXTURES.iterdir())) == 27,
          "%d dosya" % len(list(FIXTURES.iterdir())))

    print("\n" + "=" * 60)
    if failures:
        print("%d test KALDI: %s" % (len(failures), "; ".join(failures)))
        return 1
    print("Butun testler gecti.")
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        db.close()
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(code)
