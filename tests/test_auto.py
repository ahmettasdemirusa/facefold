"""The new working model: naming people is the only configuration.

Checks the promise the whole design rests on - a photo lands in the folder
named after exactly the people in it, and naming one more person rearranges
things on its own without the user touching a rule.

Also covers zip archives, Turkish name matching and the delete guard rails.

    python tests/make_fixtures.py
    python tests/test_auto.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURES = Path(__file__).resolve().parent / "ornek_fotograflar"
_TMP = Path(tempfile.mkdtemp(prefix="facefold-auto-"))
os.environ["FACEFOLD_DATA"] = str(_TMP)

from facefold import (archives, auto, clustering, db, distribute, i18n,  # noqa: E402
                     kinds, names, pipeline, rules, scanner, trash)

PASS, FAIL = "  [gecti]", "  [KALDI]"
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("%s %s" % (PASS if ok else FAIL, label)) + (" - %s" % detail if detail else ""))
    if not ok:
        failures.append(label)


def folders(out: Path) -> dict[str, int]:
    if not out.exists():
        return {}
    return {
        d.name: len([f for f in d.rglob("*") if f.is_file()])
        for d in out.iterdir() if d.is_dir()
    }


def main() -> int:
    if not FIXTURES.exists():
        print("Once 'python tests/make_fixtures.py' calistirin.")
        return 2

    # Folder names follow the interface language, and every expected name in
    # this file is written in Turkish. Saying so out loud keeps the test
    # honest: it should pass because the foldering is right, not because the
    # default language happens to be the one it was written against.
    i18n.set_language("tr")

    print("\n== Turkce isim karsilastirmasi ==")
    check("s / s ayni isim", names.same("Ayse Sahin", "Ayşe Şahin"))
    check("buyuk/kucuk harf farketmez", names.same("BRUNO", "bruno"))
    # Turkish has a dotted and a dotless I, and str.lower() maps I to i, which
    # is wrong here. "İREM" and "irem" are the same person; without folding
    # they are two.
    check("noktali I dogru katlaniyor", names.same("İREM", "irem"))
    check("noktasiz i dogru katlaniyor", names.same("IŞIL", "ışıl"))
    check("farkli isimler ayri", not names.same("Alice", "Mehmet"))
    check("ikili klasor adi", names.combo(["Bruno", "Alice"]) == "Alice ve Bruno",
          names.combo(["Bruno", "Alice"]))
    check("uclu klasor adi",
          names.combo(["Felix", "Alice", "Bruno"]) == "Alice, Bruno ve Felix",
          names.combo(["Felix", "Alice", "Bruno"]))

    print("\n== Hazirlik ==")
    out = _TMP / "cikti"
    db.init_db()
    s = db.get_settings()
    s.sources = [str(FIXTURES)]
    s.output_dir = str(out)
    s.date_subfolders = False
    db.save_settings(s)

    scanner.walk(s.sources)
    pipeline.process_new()
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
            best = max(counts, key=counts.get)
            named[best] = clustering.name_cluster(g["id"], best)
    check("6 kisi isimlendirildi", len(named) == 6, ", ".join(sorted(named)))

    print("\n== Otomatik klasor plani ==")
    plan = auto.plan()
    got = {c["folder"]: c["count"] for c in plan["combos"]}
    print("     plan: %s" % got)

    check("'Alice' klasoru var (3 portre)", got.get("Alice") == 3, str(got.get("Alice")))
    check("'Alice ve Bruno' kendiliginden olustu (2)",
          got.get("Alice ve Bruno") == 2, str(got.get("Alice ve Bruno")))
    check("'Alice ve Clara' kendiliginden olustu (1)",
          got.get("Alice ve Clara") == 1, str(got.get("Alice ve Clara")))
    check("6 kisilik grup 'Kalabalik'a dustu", plan["crowd"] == 1, str(plan["crowd"]))

    # "ekran_goruntusu.jpg" is recognised as a screenshot by name, so it leaves
    # the faceless bucket and gets its own folder - which is the point.
    check("manzara yuzsuz kareler klasorunde", plan["faceless"] == 1,
          str(plan["faceless"]))
    check("ekran goruntusu ayri klasore ayrildi", plan["screenshots"] == 1,
          str(plan["screenshots"]))

    total = (sum(got.values()) + plan["crowd"] + plan["unknown"]
             + plan["faceless"] + plan["screenshots"])
    live = db.scalar("SELECT COUNT(*) FROM photos WHERE state='done' AND duplicate_of IS NULL")
    check("her fotograf tam olarak bir klasorde", total == live,
          "%d yerlestirildi / %d fotograf" % (total, live))

    print("\n== Klasorler diskte ==")
    result = distribute.build()
    made = folders(out)
    print("     %s" % made)
    check("klasorler olustu", made.get("Alice") == 3 and made.get("Alice ve Bruno") == 2,
          str(result["created"]))
    check("kategori elle olusturulmadi",
          db.scalar("SELECT COUNT(*) FROM categories WHERE origin='manual'") == 0)

    print("\n== Bir isim daha verince duzen kendiliginden degisir ==")
    # Un-name Bruno: her photos should collapse back into Alice's folder.
    clustering.delete_person(named["Bruno"])
    distribute.build()
    after = folders(out)
    check("Bruno'nin ismi kalkinca ikili klasor kayboldu",
          "Alice ve Bruno" not in after, str(sorted(after)))
    check("o kareler Alice klasorune gecti", after.get("Alice") == 5,
          str(after.get("Alice")))

    # Name her again; the pair folder must come back on its own.
    cluster = db.q1(
        "SELECT c.id FROM clusters c JOIN faces f ON f.cluster_id=c.id "
        "JOIN photos p ON p.id=f.photo_id WHERE p.file_name LIKE 'portre_bruno%' LIMIT 1"
    )
    if cluster:
        clustering.name_cluster(cluster["id"], "Bruno")
    else:
        clustering.recluster()
        for g in db.q("SELECT id FROM clusters WHERE person_id IS NULL"):
            r = db.q1(
                "SELECT p.file_name FROM faces f JOIN photos p ON p.id=f.photo_id "
                "WHERE f.cluster_id=? AND p.file_name LIKE 'portre_bruno%' LIMIT 1",
                (g["id"],),
            )
            if r:
                clustering.name_cluster(g["id"], "Bruno")
                break
    distribute.build()
    back = folders(out)
    check("isim geri verilince ikili klasor geri geldi",
          back.get("Alice ve Bruno") == 2, str(back.get("Alice ve Bruno")))
    check("Alice klasoru yine 3", back.get("Alice") == 3, str(back.get("Alice")))

    print("\n== Zip arsivi ==")
    zip_path = _TMP / "arsiv.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name in ["portre_alice_1.jpg", "grup_hepsi.jpg", "manzara.jpg"]:
            zf.write(FIXTURES / name, "Takeout/Fotograflar/%s" % name)

    members = archives.list_images(zip_path)
    check("arsiv icindeki resimler listelendi", len(members) == 3, str(len(members)))
    check("arsiv anahtari cozuluyor",
          archives.split_key(members[0].key)[0] == str(zip_path))

    from facefold import imaging
    img = imaging.load_image(members[0].key)
    check("arsiv icinden resim acildi", img.width > 0, str(img.size))
    check("arsiv icinden sha1 alindi", len(imaging.sha1_file(members[0].key)) == 40)

    dest = _TMP / "cikarilan" / members[0].name
    archives.extract_key(members[0].key, dest)
    check("arsivden tek dosya cikarildi", dest.exists() and dest.stat().st_size > 0)

    print("\n== Silme guvenligi ==")
    ok, message = trash.available()
    check("geri donusum kutusu kullanilabilir", ok, message)
    ids = [r["id"] for r in db.q("SELECT id FROM photos LIMIT 3")]
    pre = trash.preview(ids)
    check("onizleme ne silinecegini sayiyor", pre["photos"] == 3, str(pre))
    check("bos secim hicbir sey silmez", trash.delete_photos([])["deleted"] == 0)

    print("\n== Kaynak dosyalar ==")
    check("kaynak klasor bozulmadi", len(list(FIXTURES.iterdir())) == 27,
          "%d dosya" % len(list(FIXTURES.iterdir())))

    print("\n" + "=" * 60)
    if failures:
        print("%d test KALDI: %s" % (len(failures), "; ".join(failures)))
        return 1
    print("Butun otomatik klasorleme testleri gecti.")
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        db.close()
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(code)
