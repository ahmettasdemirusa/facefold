"""Tests for the interface language.

The risky part of translating this program is not the sentences - it is that
folder names are translated too. "Alice ve Bruno" becomes "Alice and Bruno", and
those are real directories on someone's disk with their photos in them. A
careless implementation would leave the old folders behind and build the new
ones beside them, so the user would end up with two copies of their library
under two spellings. That is the case worth testing hardest.

    python tests/make_fixtures.py
    python tests/test_dil.py
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURES = Path(__file__).resolve().parent / "ornek_fotograflar"
_TMP = Path(tempfile.mkdtemp(prefix="facefold-dil-"))
os.environ["FACEFOLD_DATA"] = str(_TMP)

from facefold import (auto, clustering, db, distribute, i18n, kinds,  # noqa: E402
                     names, pipeline, rules, scanner)

PASS, FAIL = "  [gecti]", "  [KALDI]"
failures: list[str] = []

# {n} and friends, which must appear in every translation of a string or the
# sentence comes out with a number missing.
PLACEHOLDER = re.compile(r"\{(\w+)\}")


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
    print("\n== Sozluk butunlugu ==")
    check("iki dil tanimli", set(i18n.LANGUAGES) >= {"tr", "en"},
          ", ".join(sorted(i18n.LANGUAGES)))
    check("anahtarlar dolu", len(i18n.STRINGS) > 200, "%d anahtar" % len(i18n.STRINGS))

    for kod in i18n.LANGUAGES:
        eksik = i18n.missing(kod)
        check("'%s' cevirisi tam" % kod, not eksik,
              "eksik: %s" % ", ".join(eksik[:5]) if eksik else "")

    # A sentence like "{n} kisi isimlendirildi" must keep its {n} in every
    # language, otherwise the number silently disappears from the screen.
    bozuk = []
    for anahtar, degerler in i18n.STRINGS.items():
        beklenen = set(PLACEHOLDER.findall(degerler.get("tr", "")))
        for kod, metin in degerler.items():
            if set(PLACEHOLDER.findall(metin)) != beklenen:
                bozuk.append("%s/%s" % (anahtar, kod))
    check("yer tutucular her dilde ayni", not bozuk, ", ".join(bozuk[:5]))

    print("\n== Bilinmeyen anahtar cokertmez ==")
    check("bilinmeyen anahtar kendini dondurur",
          i18n.t("boyle.bir.anahtar.yok") == "boyle.bir.anahtar.yok")

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
    kinds.classify_all()
    scanner.dedupe()
    clustering.recluster()

    isimli = 0
    for g in db.q("SELECT id FROM clusters WHERE person_id IS NULL ORDER BY size DESC"):
        sayim: dict[str, int] = {}
        for r in db.q(
            "SELECT p.file_name FROM faces f JOIN photos p ON p.id=f.photo_id "
            "WHERE f.cluster_id=?", (g["id"],)
        ):
            if r["file_name"].startswith("portre_"):
                ad = r["file_name"].split("_")[1].capitalize()
                sayim[ad] = sayim.get(ad, 0) + 1
        if sayim:
            clustering.name_cluster(g["id"], max(sayim, key=sayim.get))
            isimli += 1
    check("kisiler isimlendirildi", isimli >= 5, "%d kisi" % isimli)

    # ------------------------------------------------------------------
    print("\n== Klasor adlari dili izliyor ==")
    i18n.set_language("tr")
    tr_ikili = names.combo(["Alice", "Bruno"])
    tr_kalabalik = auto.crowd_folder()
    i18n.set_language("en")
    en_ikili = names.combo(["Alice", "Bruno"])
    en_kalabalik = auto.crowd_folder()

    check("Turkce ikili klasor", tr_ikili == "Alice ve Bruno", tr_ikili)
    check("Ingilizce ikili klasor", en_ikili == "Alice and Bruno", en_ikili)
    check("kova adi da cevriliyor", tr_kalabalik != en_kalabalik,
          "%s / %s" % (tr_kalabalik, en_kalabalik))

    # ------------------------------------------------------------------
    print("\n== Dil degisince klasorler TASINIYOR (kopyalanmiyor) ==")
    i18n.set_language("tr")
    distribute.build()
    tr_klasorler = sorted(p.name for p in out.iterdir() if p.is_dir())
    tr_dosya = sum(1 for f in out.rglob("*") if f.is_file())
    check("Turkce klasorler kuruldu", tr_dosya > 0,
          "%d dosya, %d klasor" % (tr_dosya, len(tr_klasorler)))
    check("Turkce ikili klasor diskte", tr_ikili in tr_klasorler,
          ", ".join(tr_klasorler[:6]))

    i18n.set_language("en")
    distribute.build()
    en_klasorler = sorted(p.name for p in out.iterdir() if p.is_dir())
    en_dosya = sum(1 for f in out.rglob("*") if f.is_file())

    check("Ingilizce ikili klasor diskte", en_ikili in en_klasorler,
          ", ".join(en_klasorler[:6]))
    # The whole point: the Turkish folders must be gone, not sitting there
    # next to the English ones with a second copy of every photo.
    check("eski Turkce klasor kalmadi", tr_ikili not in en_klasorler,
          "kalan: %s" % ", ".join(en_klasorler))
    check("dosya sayisi ayni kaldi (kopya olusmadi)", en_dosya == tr_dosya,
          "%d -> %d" % (tr_dosya, en_dosya))

    part = distribute.check_partition(
        rules.evaluate_all(),
        {c["id"]: c for c in db.q("SELECT * FROM categories WHERE enabled=1")},
    )
    check("bolumleme hala butun", part["ok"], str(part))

    # ------------------------------------------------------------------
    print("\n== Arayuz her iki dilde aciliyor ==")
    from facefold.web.app import create_app
    client = create_app().test_client()
    yollar = ["/", "/kaynaklar", "/kisiler", "/kategoriler", "/fotograflar",
              "/kopyalar", "/ayarlar", "/tekil"]

    for kod in ("tr", "en"):
        i18n.set_language(kod)
        kotu = []
        for yol in yollar:
            cevap = client.get(yol)
            if cevap.status_code != 200:
                kotu.append("%s=%d" % (yol, cevap.status_code))
        check("'%s' butun sayfalar aciliyor" % kod, not kotu, ", ".join(kotu))

    # A raw key on screen means a template asked for something the dictionary
    # does not have - it renders as "panel.title" and looks broken.
    i18n.set_language("en")
    sizan = []
    for yol in yollar:
        govde = client.get(yol).get_data(as_text=True)
        for anahtar in re.findall(r"\b(?:panel|kisiler|kisi|grup|kategoriler|"
                                  r"fotograflar|fotograf|tekil|kopyalar|ayarlar|"
                                  r"kaynaklar|nav|common|js)\.[a-z_]+\b", govde):
            if anahtar in i18n.STRINGS or anahtar not in i18n.STRINGS:
                # Only flag it when it is genuinely an unknown key being shown.
                if anahtar not in i18n.STRINGS:
                    sizan.append("%s: %s" % (yol, anahtar))
    check("ekranda ham anahtar gorunmuyor", not sizan, ", ".join(sorted(set(sizan))[:5]))

    print("\n== Dil secimi kaydediliyor ==")
    client.post("/dil", data={"lang": "en"})
    check("secim veritabaninda", db.get_kv("ui_lang") == "en",
          str(db.get_kv("ui_lang")))
    client.post("/dil", data={"lang": "tr"})
    check("geri alinabiliyor", i18n.current() == "tr", i18n.current())
    client.post("/dil", data={"lang": "klingon"})
    check("bilinmeyen dil varsayilana duser", i18n.current() == i18n.DEFAULT,
          i18n.current())

    print("\n== Kaynak dosyalar ==")
    check("kaynak klasor bozulmadi", len(list(FIXTURES.iterdir())) == 27,
          "%d dosya" % len(list(FIXTURES.iterdir())))

    print("\n" + "=" * 60)
    if failures:
        print("%d test KALDI: %s" % (len(failures), "; ".join(failures)))
        return 1
    print("Butun dil testleri gecti.")
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        i18n.set_language("tr")
        db.close()
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(code)
