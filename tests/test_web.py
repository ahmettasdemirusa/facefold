"""Drives the web interface the way a user would, and checks what it renders.

Every page is rendered with real data in it, because a template that works on
an empty database and breaks on a real one is the usual failure mode.

    python tests/make_fixtures.py
    python tests/test_web.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURES = Path(__file__).resolve().parent / "ornek_fotograflar"
_TMP = Path(tempfile.mkdtemp(prefix="facefold-web-"))
os.environ["FACEFOLD_DATA"] = str(_TMP)

from facefold import clustering, db, rules  # noqa: E402
from facefold.web import create_app  # noqa: E402
from facefold.web import jobs  # noqa: E402

PASS, FAIL = "  [gecti]", "  [KALDI]"
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("%s %s" % (PASS if ok else FAIL, label)) + (" - %s" % detail if detail else ""))
    if not ok:
        failures.append(label)


def wait_for_job(timeout: float = 900.0) -> None:
    start = time.time()
    while time.time() - start < timeout:
        if not jobs.is_running():
            return
        time.sleep(0.5)
    raise TimeoutError("Islem zamaninda bitmedi")


def main() -> int:
    if not FIXTURES.exists():
        print("Once 'python tests/make_fixtures.py' calistirin.")
        return 2

    app = create_app()
    client = app.test_client()

    settings = db.get_settings()
    settings.output_dir = str(_TMP / "cikti")
    settings.date_subfolders = False
    db.save_settings(settings)

    print("\n== Kaynak ekleme ==")
    res = client.post("/api/kaynak/ekle", data={"path": str(FIXTURES)})
    check("kaynak eklendi", res.status_code in (200, 302))
    check("ayarlara yazildi", str(FIXTURES) in db.get_settings().sources)

    # What matters is the behaviour, not the wording: a folder that does not
    # exist must not end up in the settings. Asserting on the message text
    # would tie this test to whichever language happens to be the default.
    res = client.post("/api/kaynak/ekle", data={"path": "Z:\\olmayan-klasor"})
    check("olmayan klasor reddedildi",
          "Z:\\olmayan-klasor" not in db.get_settings().sources
          and "olmayan-klasor" in res.get_data(as_text=True),
          "kaynaklar: %s" % db.get_settings().sources)

    print("\n== Tam islem (tarama + analiz + kopya + gruplama) ==")
    res = client.post("/api/is/hepsi")
    check("islem basladi", res.get_json().get("ok") is True)
    wait_for_job()

    job = db.q1("SELECT * FROM jobs ORDER BY id DESC LIMIT 1")
    check("islem tamamlandi", job["state"] == "done", "%s: %s" % (job["state"], job["message"]))
    print("     %s" % job["message"])

    counts = client.get("/api/durum").get_json()["counts"]
    check("27 fotograf islendi", counts["done"] == 27, str(counts["done"]))
    check("44 yuz bulundu", counts["faces"] == 44, str(counts["faces"]))
    check("6 grup olustu", counts["clusters"] == 6, str(counts["clusters"]))

    print("\n== Ikinci calistirma bos gecmeli ==")
    res = client.post("/api/is/tarama")
    wait_for_job()
    job = db.q1("SELECT * FROM jobs ORDER BY id DESC LIMIT 1")
    check("yeniden taramada yeni dosya yok", "0 yeni" in (job["message"] or ""),
          job["message"])

    print("\n== Isimlendirme ==")
    groups = db.q("SELECT id FROM clusters WHERE person_id IS NULL ORDER BY size DESC")
    names = {}
    for g in groups:
        counts_by_name: dict[str, int] = {}
        for row in db.q(
            "SELECT p.file_name FROM faces f JOIN photos p ON p.id=f.photo_id "
            "WHERE f.cluster_id=?", (g["id"],)
        ):
            if row["file_name"].startswith("portre_"):
                key = row["file_name"].split("_")[1].capitalize()
                counts_by_name[key] = counts_by_name.get(key, 0) + 1
        if counts_by_name:
            name = max(counts_by_name, key=counts_by_name.get)
            res = client.post("/api/grup/%d/isim" % g["id"], data={"name": name})
            names[name] = True
    check("6 kisi isimlendirildi (arayuzden)", len(names) == 6, ", ".join(sorted(names)))

    persons = {p["name"]: p["id"] for p in db.q("SELECT id, name FROM persons")}
    check("kisiler sayfasi kisileri gosteriyor",
          b"Alice" in client.get("/kisiler").data)

    print("\n== Kategori olusturma (form uzerinden) ==")
    res = client.post("/api/kategori/ekle", data={
        "name": "Alice ve Bruno",
        "rule_type": "exact",
        "people": [str(persons["Alice"]), str(persons["Bruno"])],
        "ignore_unknown": "on",
        "min_face_px": "0",
    })
    check("kategori olustu", res.status_code in (200, 302))
    cat = db.q1("SELECT * FROM categories WHERE name='Alice ve Bruno'")
    check("iki kisi de kaydedildi", cat is not None and cat["people"].count(",") == 1,
          cat["people"] if cat else "yok")

    client.post("/api/kategori/ekle", data={
        "name": "Alice", "rule_type": "solo",
        "people": [str(persons["Alice"])], "ignore_unknown": "on", "min_face_px": "0",
    })
    client.post("/api/kategori/ekle", data={
        "name": "Yuzsuz kareler", "rule_type": "nobody",
        "ignore_unknown": "on", "min_face_px": "0",
    })

    print("\n== Canli onizleme ==")
    res = client.post("/api/kategori/onizleme", json={
        "rule_type": "exact",
        "people": [persons["Alice"], persons["Bruno"]],
        "options": {"ignore_unknown": True},
    })
    data = res.get_json()
    check("onizleme 2 fotograf dedi", data.get("count") == 2, str(data))

    print("\n== Oneriler ==")
    pairs = rules.suggest_pairs(min_count=1)
    check("ikili onerisi uretti", len(pairs) > 0,
          ", ".join("%s(%d)" % (p["suggested_name"], p["count"]) for p in pairs[:3]))

    print("\n== Klasor olusturma ==")
    res = client.post("/api/is/dagitim")
    check("dagitim basladi", res.get_json().get("ok") is True)
    wait_for_job()
    job = db.q1("SELECT * FROM jobs ORDER BY id DESC LIMIT 1")
    check("dagitim tamamlandi", job["state"] == "done", job["message"] or "")
    print("     %s" % job["message"])

    out = Path(db.get_settings().output_dir)
    made = {d.name: len(list(d.rglob("*.jpg"))) for d in out.iterdir() if d.is_dir()}
    print("     klasorler: %s" % made)
    check("'Alice ve Bruno' 2 dosya", made.get("Alice ve Bruno") == 2)
    check("'Alice' 3 dosya", made.get("Alice") == 3)
    check("'Yuzsuz kareler' 2 dosya", made.get("Yuzsuz kareler") == 2)

    print("\n== Butun sayfalar dolu veriyle aciliyor mu ==")
    person_id = persons["Alice"]
    cluster = db.q1("SELECT id FROM clusters LIMIT 1")
    photo = db.q1("SELECT id FROM photos WHERE face_count > 0 LIMIT 1")
    face = db.q1("SELECT id FROM faces LIMIT 1")

    pages = [
        "/", "/kaynaklar", "/kisiler", "/kategoriler", "/fotograflar",
        "/fotograflar?tur=kopya", "/fotograflar?tur=yuzsuz",
        "/fotograflar?kisi=%d" % person_id,
        "/kopyalar", "/ayarlar",
        "/kisi/%d" % person_id,
        "/grup/%d" % cluster["id"],
        "/fotograf/%d" % photo["id"],
        "/kucuk/%d" % photo["id"],
        "/yuz/%d" % face["id"],
        "/grup-kapak/%d" % cluster["id"],
    ]
    bad = []
    for url in pages:
        r = client.get(url)
        if r.status_code != 200:
            bad.append("%s -> %d" % (url, r.status_code))
    check("%d sayfa/gorsel acildi" % len(pages), not bad, "; ".join(bad))

    print("\n== Yuz elle atama ==")
    unnamed = db.q1("SELECT id FROM faces WHERE person_id IS NULL LIMIT 1")
    if unnamed:
        res = client.post("/api/yuz/ata", json={
            "face_ids": [unnamed["id"]], "person_id": person_id})
        check("elle atama calisti", res.get_json().get("count") == 1)
    else:
        check("elle atama (atanacak isimsiz yuz yok - atlandi)", True)

    print("\n== Kisi birlestirme ==")
    before = db.scalar("SELECT COUNT(*) FROM persons")
    client.post("/api/kisi/%d/birlestir" % persons["Alice"],
                data={"other": persons["Felix"]})
    after = db.scalar("SELECT COUNT(*) FROM persons")
    check("iki kisi birlesti", after == before - 1, "%d -> %d" % (before, after))

    print("\n== Kaynak dosyalar ==")
    check("kaynak klasor bozulmadi", len(list(FIXTURES.iterdir())) == 27)

    print("\n" + "=" * 60)
    if failures:
        print("%d test KALDI: %s" % (len(failures), "; ".join(failures)))
        return 1
    print("Butun arayuz testleri gecti.")
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        db.close()
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(code)
