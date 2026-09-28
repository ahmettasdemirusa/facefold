"""Interface language.

The interface started out Turkish-only, with every sentence written directly
into a template. That is the cheapest way to build something and the most
expensive way to share it: a Spanish speaker who wants to sort their family
photos cannot use the program at all, and a contributor who wants to help
would have to hunt sentences through twelve templates.

So the sentences live here instead, one key at a time:

    "panel.title": {
        "tr": "Panel",
        "en": "Dashboard",
    },

**Adding a language is copying the right-hand side.** Put your code in
`LANGUAGES`, then add it to each entry. You do not have to finish in one go -
anything you have not translated yet falls back to English rather than
breaking, so a half-finished language is still useful and still mergeable.

Two deliberate choices:

  * No gettext, no .po files, no extra dependency. A plain dictionary is
    something anyone can edit in the browser on GitHub, which is the whole
    point of making the project public.
  * Keys are named after where they appear (`kisiler.lede`), not after the
    text itself. Rewording a sentence then stays a one-line change. The key
    names are Turkish because that is what the screens were called when they
    were written; renaming them would churn every template for nothing.
"""

from __future__ import annotations

from typing import Any

from . import db

# Language code -> what to call it in its own language, which is what a
# speaker of it will recognise in the menu. English first: it is the default,
# and it is the one most people opening this for the first time can read.
LANGUAGES: dict[str, str] = {
    "en": "English",
    "tr": "Türkçe",
}

# What an unconfigured install starts in, and what a partial translation falls
# back to. English, because the program is meant to be useful to anyone - a
# Turkish speaker switches once in Settings and it is remembered.
DEFAULT = "en"

# Reading the setting out of SQLite on every single string would mean hundreds
# of queries to render one page, so the answer is remembered and cleared when
# the language changes.
_current: str | None = None


def current() -> str:
    """The language the interface is in right now.

    Never raises. Folder names go through here, and those are built by pure
    functions that run in tests and command-line tools long before any
    database exists - looking up a language must not be the thing that breaks
    them. A missing database simply means "not configured yet", which is
    exactly what the default is for.
    """
    global _current
    if _current is None:
        try:
            code = db.get_kv("ui_lang", DEFAULT)
        except Exception:  # noqa: BLE001 - no database yet, or no table yet
            return DEFAULT
        _current = code if code in LANGUAGES else DEFAULT
    return _current


def set_language(code: str) -> str:
    """Switch the interface language and remember it.

    Like `current()`, this never raises. Choosing a language is something a
    caller may reasonably do before a database exists - a test setting up its
    expectations, a script rendering a folder name. When there is nowhere to
    remember it, the choice still takes effect for this process; it simply
    will not survive a restart.
    """
    code = code if code in LANGUAGES else DEFAULT
    try:
        db.set_kv("ui_lang", code)
    except Exception:  # noqa: BLE001 - no database yet, or no table yet
        pass
    global _current
    _current = code
    return code


def t(key: str, **kw: Any) -> str:
    """One piece of interface text, in the current language.

    An unknown key returns the key itself. That is on purpose: it is obvious
    on screen, it never raises in the middle of rendering a page, and it tells
    whoever sees it exactly which entry is missing.
    """
    entry = STRINGS.get(key)
    if entry is None:
        return key
    # Falling back to English rather than to the key means a language that is
    # only half translated still shows sentences, not debugging output.
    text = entry.get(current()) or entry.get(DEFAULT) or key
    if kw:
        try:
            return text.format(**kw)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def missing(code: str) -> list[str]:
    """Keys a language has not translated yet - for contributors."""
    return sorted(k for k, v in STRINGS.items() if not v.get(code))


def coverage() -> dict[str, int]:
    """Percentage translated per language, for the settings screen."""
    total = len(STRINGS) or 1
    return {
        code: round(100 * (total - len(missing(code))) / total)
        for code in LANGUAGES
    }


# ---------------------------------------------------------------------------
# The strings
# ---------------------------------------------------------------------------
# Grouped by the screen they belong to. Keep a group together and keep the
# keys inside it in the order they appear on the page - it makes translating
# feel like reading the screen rather than shuffling a word list.

STRINGS: dict[str, dict[str, str]] = {

    # -- her sayfada ortak ---------------------------------------------
    "app.tagline": {"tr": "yerel · çevrimdışı", "en": "local · offline"},

    # -- sol menu ------------------------------------------------------
    "nav.categories": {"tr": "Klasorler", "en": "Folders"},
    "nav.duplicates": {"tr": "Kopyalar", "en": "Duplicates"},
    "nav.panel": {"tr": "Panel", "en": "Dashboard"},
    "nav.people": {"tr": "Kişiler", "en": "People"},
    "nav.photos": {"tr": "Fotoğraflar", "en": "Photos"},
    "nav.settings": {"tr": "Ayarlar", "en": "Settings"},
    "nav.singles": {"tr": "Tek kareler", "en": "Single faces"},
    "nav.sources": {"tr": "Kaynak klasörler", "en": "Source folders"},

    # -- her yerde gecen kisa metinler ---------------------------------
    "common.back": {"tr": "Geri", "en": "Back"},
    "common.cancel": {"tr": "Vazgeç", "en": "Cancel"},
    "common.clear_selection": {"tr": "İşareti kaldır", "en": "Clear selection"},
    "common.delete": {"tr": "Sil", "en": "Delete"},
    "common.empty": {
        "tr": "Bu filtreye uyan fotoğraf yok.",
        "en": "No photos match this filter.",
    },
    "common.face": {"tr": "yüz", "en": "face"},
    "common.faces": {"tr": "yüz", "en": "faces"},
    "common.next": {"tr": "Sonraki", "en": "Next"},
    "common.no_face": {"tr": "yüz yok", "en": "no face"},
    "common.open": {"tr": "Aç", "en": "Open"},
    "common.photo": {"tr": "fotoğraf", "en": "photo"},
    "common.photos": {"tr": "fotoğraf", "en": "photos"},
    "common.previous": {"tr": "Önceki", "en": "Previous"},
    "common.recycle_note": {
        "tr": "Silinen dosyalar <b>Geri Dönüşüm Kutusu'na</b> gider, kalıcı silinmez.",
        "en": "Deleted files go to the <b>Recycle Bin</b>; nothing is erased permanently.",
    },
    "common.save": {"tr": "Kaydet", "en": "Save"},
    "common.select_all": {"tr": "Tümünü işaretle", "en": "Select all"},
    "common.selected": {"tr": "{n} seçili", "en": "{n} selected"},
    "common.stop": {"tr": "Durdur", "en": "Stop"},

    # -- Panel ---------------------------------------------------------
    "panel.col_files": {"tr": "Dosya", "en": "Files"},
    "panel.col_folder": {"tr": "Klasor", "en": "Folder"},
    "panel.create_btn": {"tr": "Olustur", "en": "Create"},
    "panel.crowd": {"tr": "Kalabalik", "en": "Crowd"},
    "panel.crowd_size": {"tr": "{n}+ kisi", "en": "{n}+ people"},
    "panel.duplicates": {"tr": "kopya", "en": "duplicates"},
    "panel.faceless": {"tr": "Yuzsuz kareler", "en": "Photos with no faces"},
    "panel.history": {"tr": "Islem gecmisi", "en": "Recent activity"},
    "panel.history_empty": {"tr": "Henuz islem yapilmadi.", "en": "Nothing has run yet."},
    "panel.in_archives": {"tr": "{n} tanesi zip icinde", "en": "{n} of them inside zip files"},
    "panel.job_build": {"tr": "Klasorleri olustur", "en": "Create the folders"},
    "panel.job_dupes": {"tr": "Kopyalari ayikla", "en": "Pick out duplicates"},
    "panel.job_faces": {"tr": "Yuzleri bul", "en": "Find the faces"},
    "panel.job_group": {"tr": "Yuzleri grupla", "en": "Group the faces"},
    "panel.job_match": {"tr": "Bilinen kisileri ara", "en": "Look for known people"},
    "panel.job_scan": {"tr": "Klasorleri tara", "en": "Scan the folders"},
    "panel.lede": {
        "tr": "Fotograflarinizi iclerindeki kisilere gore diskinizde gercek klasorlere ayirir. Tek yapmaniz gereken yuz gruplarina isim vermek - klasorler kendiliginden olusur. Hicbir fotograf internete gonderilmez.",
        "en": "It sorts your photos into real folders on your own disk, by who is in them. All you have to do is name the face groups - the folders appear by themselves. No photo is ever sent to the internet.",
    },
    "panel.manual": {"tr": "Adimlari tek tek calistir", "en": "Run the steps one at a time"},
    "panel.manual_note": {
        "tr": "\"Taramayi baslat\" bunlarin ilk dordunu sirayla yapar. Bunlar tek bir adimi tekrarlamak icin.",
        "en": "\"Start scanning\" does the first four of these in order. These are here for repeating a single step.",
    },
    "panel.named_people": {"tr": "isimlendirilmis kisi", "en": "people named"},
    "panel.on_disk": {"tr": "Diskte olan", "en": "Already on disk"},
    "panel.open_output": {"tr": "Cikti klasorunu ac", "en": "Open the output folder"},
    "panel.pair": {"tr": "ikili", "en": "pair"},
    "panel.plan_lede": {
        "tr": "Verdiginiz isimlerden cikan duzen. Bir kisi daha isimlendirdiginizde bu liste kendiliginden zenginlesir.",
        "en": "The layout your names produce. Name one more person and this list fills out on its own.",
    },
    "panel.plan_title": {
        "tr": "Simdi olusacak klasorler",
        "en": "Folders that will be created now",
    },
    "panel.rescan": {"tr": "Yeniden tara", "en": "Scan again"},
    "panel.scan_start": {"tr": "Taramayi baslat", "en": "Start scanning"},
    "panel.step1_btn": {"tr": "Klasorler", "en": "Folders"},
    "panel.step1_count": {"tr": "{n} klasor tanimli.", "en": "{n} folders set up."},
    "panel.step1_hint": {
        "tr": "Telefon yedegi, Google Takeout klasoru, harici disk - zip dosyalari dahil.",
        "en": "A phone backup, a Google Takeout folder, an external drive - zip files included.",
    },
    "panel.step1_title": {
        "tr": "Fotograflarin nerede oldugunu soyleyin",
        "en": "Tell it where your photos are",
    },
    "panel.step1_zip": {
        "tr": "Zip arsivleri de taraniyor.",
        "en": "Zip archives are searched too.",
    },
    "panel.step2_clusters": {
        "tr": "{n} isimsiz grup bekliyor. Her birine bir kere isim verin.",
        "en": "{n} unnamed groups are waiting. Name each one once.",
    },
    "panel.step2_empty": {
        "tr": "Once taramayi baslatin, gruplar sonra belirir.",
        "en": "Start the scan first; the groups show up afterwards.",
    },
    "panel.step2_pending": {
        "tr": "{n} fotograf sirada bekliyor.",
        "en": "{n} photos are still waiting.",
    },
    "panel.step2_persons": {
        "tr": "{n} kisi tanimli. Yeni fotograflarda otomatik taninirlar.",
        "en": "{n} people named so far. They are recognized in new photos automatically.",
    },
    "panel.step2_title": {"tr": "Yuz gruplarina isim verin", "en": "Name the face groups"},
    "panel.step3_plan": {"tr": "{n} klasor olusacak", "en": "{n} folders will be created"},
    "panel.step3_title": {"tr": "Klasorleri olusturun", "en": "Create the folders"},
    "panel.steps_title": {"tr": "Uc adim", "en": "Three steps"},
    "panel.unknown": {"tr": "Taninmayan kisiler", "en": "Unrecognized people"},
    "panel.warn_model": {
        "tr": "Yuz tanima modeli henuz indirilmedi. Ilk analizde otomatik inecek (~280 MB).",
        "en": "The face recognition model hasn't been downloaded yet. It downloads on its own the first time you run an analysis (~280 MB).",
    },
    "panel.warn_no_source": {
        "tr": "Henuz kaynak klasor eklenmedi.",
        "en": "No source folders added yet.",
    },

    # -- Kaynak klasorler ----------------------------------------------
    "kaynaklar.add_btn": {"tr": "Ekle", "en": "Add"},
    "kaynaklar.add_title": {"tr": "Klasor ekle", "en": "Add a folder"},
    "kaynaklar.col_folder": {"tr": "Klasor", "en": "Folder"},
    "kaynaklar.col_photos": {"tr": "Fotograf", "en": "Photos"},
    "kaynaklar.empty": {
        "tr": "Henuz klasor eklenmedi. Yukaridaki kutuya fotograflarinizin oldugu klasorun yolunu yazin.",
        "en": "No folders yet. Type the path of a folder with your photos into the box above.",
    },
    "kaynaklar.in_zip": {"tr": "{n} zip icinde", "en": "{n} inside zip files"},
    "kaynaklar.lede": {
        "tr": "Fotograflarinizin bulundugu klasorleri ekleyin. Facefold bu klasorleri sadece <b>okur</b> - hicbir dosyayi tasimaz, silmez, degistirmez. Alt klasorler otomatik taranir.",
        "en": "Add the folders your photos are in. Facefold only <b>reads</b> them - it never moves, deletes or changes a file. Subfolders are scanned automatically.",
    },
    "kaynaklar.list_title": {"tr": "Tanimli klasorler", "en": "Folders you have added"},
    "kaynaklar.missing": {"tr": "klasor bulunamiyor", "en": "folder not found"},
    "kaynaklar.not_found": {
        "tr": "Bu klasor bulunamadi: {path}",
        "en": "Couldn't find that folder: {path}",
    },
    "kaynaklar.path_help": {
        "tr": "Windows Gezgini'nde klasoru acip adres cubugundaki yolu kopyalayip buraya yapistirin. Alt klasorler ve <b>zip arsivleri</b> de taranir - zipleri acmaniza gerek yok.",
        "en": "Open the folder in Windows Explorer, copy the path from the address bar and paste it here. Subfolders and <b>zip archives</b> are scanned too - you do not need to unzip anything.",
    },
    "kaynaklar.path_label": {"tr": "Klasorun tam yolu", "en": "Full path to the folder"},
    "kaynaklar.path_placeholder": {
        "tr": "D:\\Fotograflar\\iPhone yedek",
        "en": "D:\\Photos\\iPhone backup",
    },
    "kaynaklar.place_google": {"tr": "Google Fotograflar", "en": "Google Photos"},
    "kaynaklar.place_google_hint": {
        "tr": "takeout.google.com → indirilen klasor",
        "en": "takeout.google.com → the folder you downloaded",
    },
    "kaynaklar.place_phone": {"tr": "iPhone / telefon yedegi", "en": "iPhone / phone backup"},
    "kaynaklar.place_phone_hint": {
        "tr": "Bilgisayara kabloyla aktardiginiz klasor",
        "en": "The folder you copied over by cable",
    },
    "kaynaklar.place_windows": {"tr": "Windows resimleri", "en": "Windows pictures"},
    "kaynaklar.places_note": {
        "tr": "Ayni fotografin hem telefondan hem Google'dan gelmesi sorun degil - kopyalar otomatik ayiklanir.",
        "en": "It does not matter if the same photo arrives from both your phone and Google - the copies are sorted out for you.",
    },
    "kaynaklar.places_title": {"tr": "Sik kullanilan yerler", "en": "Common places to look"},
    "kaynaklar.remove_btn": {"tr": "Cikar", "en": "Remove"},
    "kaynaklar.remove_confirm": {
        "tr": "Bu klasor cikarilsin mi?\\n\\n{n} fotografin kaydi ve bu klasordeki yuzler silinecek. Sadece bu klasorde gorunen kisiler de silinir.\\n\\nDiskinizdeki dosyalara dokunulmaz.",
        "en": "Remove this folder?\\n\\nThe record of {n} photos and the faces found in this folder will be deleted. People who only appear here are removed too.\\n\\nNothing on your disk is touched.",
    },
    "kaynaklar.scan_all": {"tr": "Tara ve cozumle", "en": "Scan and analyse"},
    "kaynaklar.scan_new": {"tr": "Sadece yeni dosyalari ara", "en": "Look for new files only"},
    "kaynaklar.takeout_certain": {
        "tr": "Kesin atanabilir isim",
        "en": "Names that can be assigned for certain",
    },
    "kaynaklar.takeout_dates": {"tr": "Tarihi duzeltilecek", "en": "Dates to be corrected"},
    "kaynaklar.takeout_import": {
        "tr": "Google verisini iceri aktar",
        "en": "Bring the Google data in",
    },
    "kaynaklar.takeout_lede": {
        "tr": "Kaynak klasorlerinizde Google Takeout metadata dosyalari bulundu. Bunlarda Google'in daha once verdiginiz isimler ve fotograflarin <b>gercek cekim tarihi</b> duruyor - Takeout disa aktarirken cogu dosyanin EXIF'ini siliyor.",
        "en": "Google Takeout metadata files were found in your folders. They still hold the names you gave Google and the <b>real date each photo was taken</b> - Takeout strips the EXIF out of most files when it exports them.",
    },
    "kaynaklar.takeout_matched": {"tr": "Fotografla eslesen", "en": "Matched to a photo"},
    "kaynaklar.takeout_note": {
        "tr": "Yalnizca \"bir kisi etiketli, bir yuz bulunmus\" durumu dogrudan atanir. Kalabalik karelerde hangi yuzun kim oldugu tahmin edilmez.",
        "en": "Only the clear case - one person tagged, one face found - is assigned directly. In crowded shots nobody guesses which face is who.",
    },
    "kaynaklar.takeout_sidecars": {
        "tr": "Bulunan metadata dosyasi",
        "en": "Metadata files found",
    },
    "kaynaklar.takeout_title": {"tr": "Google Fotograflar verisi", "en": "Google Photos data"},

    # -- Kisiler -------------------------------------------------------
    "kisiler.face_percent": {
        "tr": "Yuzlerin %{n}'i isimlendirildi.",
        "en": "{n}% of the faces are named.",
    },
    "kisiler.go_panel": {"tr": "Panele git", "en": "Go to the dashboard"},
    "kisiler.hidden_count": {"tr": "{n} grup gizlendi.", "en": "{n} groups are hidden."},
    "kisiler.hide": {"tr": "Gizle", "en": "Hide"},
    "kisiler.lede": {
        "tr": "Facefold ayni yuzu bir araya topladi ama kim olduklarini bilmiyor. Her gruba <b>bir kere</b> isim verin; bundan sonra o kisiyi eklediginiz her yeni fotografta kendisi tanir.",
        "en": "Facefold has gathered the same face together but has no idea who it is. Name each group <b>once</b>; from then on it knows that person in every new photo you add.",
    },
    "kisiler.linked": {
        "tr": "{a} / {b} fotograf bir kisiye bagli",
        "en": "{a} / {b} photos linked to a person",
    },
    "kisiler.loose": {
        "tr": "{n} yuz hicbir gruba girmedi - genelde bir iki karede gorulmus kisiler. {link} tek tek atayabilirsiniz.",
        "en": "{n} faces did not join any group - usually people seen in a shot or two. You can assign them one by one on the {link}.",
    },
    "kisiler.loose_link": {"tr": "Tek kareler ekraninda", "en": "Single shots screen"},
    "kisiler.merge": {"tr": "Birlestir", "en": "Merge"},
    "kisiler.merge_and": {"tr": "ile", "en": "and"},
    "kisiler.merge_lede": {
        "tr": "Yuzleri birbirine cok benziyor. Genelde ayni kisinin iki farkli yazilisi olur - ornegin Google'in kisa adi ile sizin yazdiginiz tam ad.",
        "en": "Their faces look very much alike. Usually it is one person spelled two ways - the short name from Google and the full name you typed.",
    },
    "kisiler.merge_title": {
        "tr": "Bunlar ayni kisi olabilir",
        "en": "These might be the same person",
    },
    "kisiler.mixed": {"tr": "karisik", "en": "mixed"},
    "kisiler.mixed_help": {
        "tr": "<b>karisik</b> etiketi, o kisiye bagli yuzlerin birbirine yeterince benzemedigini gosterir - araya baska biri karismis olabilir. Kisiye girip yanlis yuzleri cikarabilirsiniz.",
        "en": "The <b>mixed</b> label means the faces under that name do not look enough alike - someone else may have slipped in. Open the person and take the wrong faces out.",
    },
    "kisiler.name_placeholder": {"tr": "Ismi yazin", "en": "Type a name"},
    "kisiler.named_count": {"tr": "{n} kisi isimlendirildi", "en": "{n} people named"},
    "kisiler.named_title": {"tr": "Isimlendirilmis kisiler", "en": "People you have named"},
    "kisiler.none_left": {
        "tr": "Isimsiz grup kalmadi. Yeni fotograf eklerseniz burada yeniden belirir.",
        "en": "No unnamed groups left. Add new photos and they will show up here again.",
    },
    "kisiler.others_title": {"tr": "Digerleri", "en": "Everything else"},
    "kisiler.pick_person": {"tr": "-- kisi secin --", "en": "-- pick a person --"},
    "kisiler.scan_first": {"tr": "Once fotograflari tarayin.", "en": "Scan your photos first."},
    "kisiler.similarity": {"tr": "benzerlik {n}", "en": "similarity {n}"},
    "kisiler.tail_button": {"tr": "{n} kucuk grubu gizle", "en": "Hide {n} small groups"},
    "kisiler.tail_help": {
        "tr": "{n} grup {min} yuzden az - toplam {faces} yuz. Bunlar genelde bir iki karede gecen yabancilardir. Gizlemek hicbir seyi silmez, sadece bu ekrandan cikarir.",
        "en": "{n} groups have fewer than {min} faces - {faces} faces in all. These are usually strangers passing through a shot or two. Hiding deletes nothing, it only clears them off this screen.",
    },
    "kisiler.tail_title": {"tr": "Kucuk gruplari gizle", "en": "Hide the small groups"},
    "kisiler.unhide": {"tr": "Gizlenenleri geri getir", "en": "Bring the hidden ones back"},
    "kisiler.unnamed_lede": {
        "tr": "En cok fotograf tutanlar ustte. Hepsini isimlendirmek zorunda degilsiniz - buyuk birkac grup ailenizdir, kucuk olanlar bir iki kez gorulmus yabancilardir.",
        "en": "The ones with the most photos come first. You do not have to name them all - the few big groups are your family, the small ones are strangers who turned up once or twice.",
    },
    "kisiler.unnamed_title": {"tr": "Isimsiz gruplar", "en": "Unnamed groups"},
    "kisiler.very_high": {"tr": "cok yuksek", "en": "very high"},
    "kisiler.worth": {
        "tr": "Bekleyen {n} gruptan <b>{big}</b> tanesi kayda deger; sonraki 10 tanesini isimlendirmek +%{gain} kazandirir.",
        "en": "Of the {n} groups waiting, <b>{big}</b> are worth the effort; naming the next 10 adds +{gain}%.",
    },

    # -- Tek kisi sayfasi ----------------------------------------------
    "kisi.confirm_button": {
        "tr": "Isaretlenenleri {name} olarak onayla",
        "en": "Confirm the ticked ones as {name}",
    },
    "kisi.delete_button": {"tr": "Ismi kaldir", "en": "Remove the name"},
    "kisi.delete_confirm": {
        "tr": "{name} ismi kaldirilsin mi? Fotograflar silinmez.",
        "en": "Remove the name {name}? The photos are not deleted.",
    },
    "kisi.delete_help": {
        "tr": "Fotograflar silinmez. Sadece isim kaldirilir, yuzler isimsiz havuza doner.",
        "en": "The photos are not deleted. Only the name goes, and the faces return to the unnamed pool.",
    },
    "kisi.delete_title": {"tr": "Bu kisiyi sil", "en": "Remove this person"},
    "kisi.detach": {"tr": "Bu kisi degil - cikar", "en": "Not this person - take out"},
    "kisi.edit_title": {"tr": "Duzenle", "en": "Edit"},
    "kisi.faces_lede": {
        "tr": "Araya baskasi karistiysa isaretleyip cikarin. Cikardiginiz yuz silinmez, isimsiz havuza doner - ve {name}'in taninmasi duzelir.",
        "en": "If someone else has slipped in, tick them and take them out. A face you remove is not deleted, it goes back to the unnamed pool - and {name} gets recognised better.",
    },
    "kisi.faces_title": {
        "tr": "{name} olarak isaretlenmis yuzler",
        "en": "Faces marked as {name}",
    },
    "kisi.merge_confirm": {
        "tr": "Secilen kisi {name} ile birlestirilecek. Devam?",
        "en": "The selected person will be merged into {name}. Continue?",
    },
    "kisi.merge_help": {
        "tr": "Cocuklarin yillar icinde ikiye bolunen yuz gruplari boyle birlestirilir.",
        "en": "This is how you put a child back together when their face has split into two groups over the years.",
    },
    "kisi.merge_none": {
        "tr": "Birlestirilecek baska kisi yok.",
        "en": "There is nobody else to merge with.",
    },
    "kisi.merge_title": {
        "tr": "Baska bir kisiyle birlestir",
        "en": "Merge with another person",
    },
    "kisi.move": {"tr": "Tasi", "en": "Move"},
    "kisi.move_placeholder": {"tr": "-- baskasina tasi --", "en": "-- move to someone else --"},
    "kisi.no_faces": {
        "tr": "Bu kisiye bagli yuz kalmadi.",
        "en": "No faces left under this person.",
    },
    "kisi.no_photos": {
        "tr": "Bu kisiye ait fotograf bulunamadi.",
        "en": "No photos found for this person.",
    },
    "kisi.photos_title": {"tr": "Fotograflari", "en": "Their photos"},
    "kisi.purge_button": {"tr": "Fotograflari sil", "en": "Delete the photos"},
    "kisi.purge_help": {
        "tr": "{n} fotograf <b>Geri Donusum Kutusu'na</b> gonderilir - kalici silinmez, Gezgin'den geri alabilirsiniz.",
        "en": "{n} photos go to the <b>Recycle Bin</b> - nothing is erased for good, you can get them back from File Explorer.",
    },
    "kisi.purge_title": {
        "tr": "Bu kisinin butun fotograflarini sil",
        "en": "Delete every photo of this person",
    },
    "kisi.rename_help": {
        "tr": "Var olan baska bir ismi yazarsaniz iki kisi birlestirilir. Turkce harf farki (Sahin / Sahin) ayni isim sayilir.",
        "en": "If you type a name that already exists, the two people are merged. Turkish letter differences (Sahin / Şahin) count as the same name.",
    },
    "kisi.rename_title": {"tr": "Ismi degistir", "en": "Change the name"},
    "kisi.src_auto": {"tr": "otomatik %{n}", "en": "auto {n}%"},
    "kisi.src_cluster": {"tr": "grup", "en": "group"},
    "kisi.src_manual": {"tr": "elle", "en": "by hand"},
    "kisi.suggest_lede": {
        "tr": "Bu yuzler {name}'a benziyor ama emin olunamadi. Dogru olanlari isaretleyip onaylayin - onayladikca taniyisi keskinlesir.",
        "en": "These faces look like {name}, but not for certain. Tick the right ones and confirm - every confirmation sharpens the match.",
    },
    "kisi.suggest_title": {"tr": "Bu da o mu?", "en": "Is this them too?"},

    # -- Isimsiz yuz grubu ---------------------------------------------
    "grup.age": {"tr": "~{n} yas", "en": "~{n} yrs"},
    "grup.assign": {
        "tr": "Secilenleri bu kisiye ata",
        "en": "Assign the selected to this person",
    },
    "grup.col_faces": {"tr": "Yuz", "en": "Faces"},
    "grup.col_group": {"tr": "Grup", "en": "Group"},
    "grup.col_similarity": {"tr": "Benzerlik", "en": "Similarity"},
    "grup.faces_title": {"tr": "Bu gruptaki yuzler", "en": "Faces in this group"},
    "grup.group_n": {"tr": "Grup {n}", "en": "Group {n}"},
    "grup.hide": {"tr": "Bu grubu gizle", "en": "Hide this group"},
    "grup.lede": {
        "tr": "{n} yuz. Hepsi ayni kisiye aitse asagiya ismini yazin. Aralarinda baskasi varsa, o yuzleri secip ayirabilirsiniz.",
        "en": "{n} faces. If they are all the same person, type their name below. If someone else is in there, you can pick those faces out.",
    },
    "grup.merge_with": {"tr": "Bu grupla birlestir", "en": "Merge with this group"},
    "grup.name_button": {
        "tr": "Butun gruba bu ismi ver",
        "en": "Give the whole group this name",
    },
    "grup.name_help": {
        "tr": "Zaten var olan bir ismi yazarsaniz bu grup o kisiyle birlestirilir - cocuklarin yillar icinde degisen yuzleri boyle birlestirilir.",
        "en": "If you type a name that already exists, this group is merged into that person - that is how a child's face, changing over the years, gets put back together.",
    },
    "grup.name_placeholder": {"tr": "Bu kisinin adi", "en": "This person's name"},
    "grup.selected": {"tr": "{n} yuz secildi", "en": "{n} faces selected"},
    "grup.showing": {
        "tr": "Ilk {n} yuz gosteriliyor (toplam {total}).",
        "en": "Showing the first {n} faces (of {total}).",
    },
    "grup.similar_title": {
        "tr": "Bu gruba benzeyen diger gruplar",
        "en": "Other groups that look like this one",
    },
    "grup.title": {"tr": "Isimsiz grup", "en": "Unnamed group"},

    # -- Kategoriler / klasorler ---------------------------------------
    "kategoriler.add_rule": {"tr": "Kurali ekle", "en": "Add the rule"},
    "kategoriler.advanced": {
        "tr": "Gelismis - otomatik klasorleme ayarlari ve elle kural yazma",
        "en": "Advanced - automatic folder settings and rules of your own",
    },
    "kategoriler.auto_enable": {
        "tr": "Isimlerden klasorleri kendiligindan olustur",
        "en": "Build folders from names automatically",
    },
    "kategoriler.auto_heading": {"tr": "Otomatik klasorleme", "en": "Automatic folders"},
    "kategoriler.build_btn": {
        "tr": "Klasorleri olustur / guncelle",
        "en": "Create / update folders",
    },
    "kategoriler.col_contents": {"tr": "Icerigi", "en": "What goes in"},
    "kategoriler.col_files": {"tr": "Dosya", "en": "Files"},
    "kategoriler.col_folder": {"tr": "Klasor", "en": "Folder"},
    "kategoriler.col_photos": {"tr": "Fotograf", "en": "Photos"},
    "kategoriler.col_rule": {"tr": "Kural", "en": "Rule"},
    "kategoriler.crowd_desc": {
        "tr": "{n}'den fazla taninan kisi",
        "en": "more than {n} named people",
    },
    "kategoriler.date_from": {"tr": "Baslangic", "en": "From"},
    "kategoriler.date_to": {"tr": "Bitis", "en": "To"},
    "kategoriler.delete_confirm": {
        "tr": "{name} klasoru silinsin mi? Asil fotograflariniz silinmez.",
        "en": "Delete the {name} folder? Your original photos are not deleted.",
    },
    "kategoriler.empty_plan": {
        "tr": "Henuz klasor olusturacak isim yok.",
        "en": "No names yet, so there is nothing to sort into folders.",
    },
    "kategoriler.ex_crowd": {"tr": "{n} veya daha fazla kisi", "en": "{n} people or more"},
    "kategoriler.ex_faceless": {"tr": "Hic yuz yok", "en": "No faces at all"},
    "kategoriler.ex_note": {
        "tr": "Bruno'ya isim verdiginiz an, icinde ikisi de olan kareler Alice/ klasorunden cikip kendi klasorune gider. Duzen isimlendirdikce zenginlesir.",
        "en": "The moment you name Bruno, the shots with both of them leave the Alice/ folder and move into one of their own. The more names you give, the finer the sorting gets.",
    },
    "kategoriler.ex_pair": {"tr": "Alice ve Bruno birlikte", "en": "Alice and Bruno together"},
    "kategoriler.ex_solo": {"tr": "Alice yalniz", "en": "Alice alone"},
    "kategoriler.ex_unknown": {"tr": "Yuz var, isim yok", "en": "A face, but no name"},
    "kategoriler.exactly_these": {"tr": "tam olarak {names}", "en": "exactly {names}"},
    "kategoriler.faceless_desc": {
        "tr": "manzara, ekran goruntusu, belge",
        "en": "scenery, screenshots, documents",
    },
    "kategoriler.go_people": {"tr": "Kisilere git", "en": "Go to People"},
    "kategoriler.ideas_heading": {"tr": "Hazir oneriler", "en": "Ready-made suggestions"},
    "kategoriler.ignore_unknown": {
        "tr": "Taninmayan yuzler kurali bozmasin",
        "en": "Unnamed faces should not break the rule",
    },
    "kategoriler.include_duplicates": {
        "tr": "Kopyalari da dahil et",
        "en": "Include duplicates as well",
    },
    "kategoriler.lede": {
        "tr": "Klasorler verdiginiz isimlerden kendiliginden olusur. Bir fotograf, icindeki isimlendirilmis kisilere gore <b>tek bir klasore</b> girer.",
        "en": "Folders build themselves out of the names you give. A photo goes into <b>one folder only</b>, decided by the named people in it.",
    },
    "kategoriler.manual_empty": {
        "tr": "Elle yazilmis kural yok.",
        "en": "No rules of your own yet.",
    },
    "kategoriler.manual_heading": {
        "tr": "Elle yazilmis kurallar",
        "en": "Rules you wrote yourself",
    },
    "kategoriler.manual_lede": {
        "tr": "Otomatik duzenin disinda kendi klasorunuzu tanimlayabilirsiniz. Elle yazdiginiz kurallar otomatik olanlardan once gelir ve silinmez.",
        "en": "Outside the automatic scheme you can define folders of your own. Rules you write come before the automatic ones, and they are never removed.",
    },
    "kategoriler.max_people_help": {
        "tr": "Bunun ustundeki kareler \"Kalabalik\" klasorune gider. Yuksek deger, \"Alice, Bruno, Clara ve Emma\" gibi uzun klasor adlari demektir.",
        "en": "Shots with more people than this go to the \"Crowd\" folder. A high number means long folder names like \"Alice, Bruno, Clara and Emma\".",
    },
    "kategoriler.max_people_label": {
        "tr": "Kac kisiye kadar ayri klasor",
        "en": "Up to how many people get their own folder",
    },
    "kategoriler.min_face_px": {
        "tr": "En kucuk yuz boyutu (piksel)",
        "en": "Smallest face size (pixels)",
    },
    "kategoriler.min_photos_help": {
        "tr": "Bunun altindaki kombinasyonlar \"Kalabalik\" klasorune dusurulur. Tek fotografik klasorlerden kacinmak icin yukseltin.",
        "en": "Combinations with fewer photos than this go into the \"Kalabalik\" folder instead. Raise it to avoid folders with a single photo in them.",
    },
    "kategoriler.min_photos_label": {
        "tr": "En az kac fotograf klasor hak eder",
        "en": "How many photos a folder is worth",
    },
    "kategoriler.need_names_link": {
        "tr": "yuz gruplarina isim verin",
        "en": "give the face groups names",
    },
    "kategoriler.need_names_post": {
        "tr": "Klasorler isimlerden dogar.",
        "en": "Folders grow out of names.",
    },
    "kategoriler.need_names_pre": {"tr": "Once", "en": "First,"},
    "kategoriler.new_rule": {"tr": "Yeni kural", "en": "New rule"},
    "kategoriler.no_persons": {
        "tr": "Henuz isimlendirilmis kisi yok.",
        "en": "Nobody has been named yet.",
    },
    "kategoriler.only_name": {"tr": "Sadece {name}", "en": "Only {name}"},
    "kategoriler.only_person": {"tr": "yalnizca {name}", "en": "{name} alone"},
    "kategoriler.open_output": {"tr": "Cikti klasorunu ac", "en": "Open output folder"},
    "kategoriler.plan_heading": {
        "tr": "Olusacak klasorler",
        "en": "Folders that will be created",
    },
    "kategoriler.rule_name": {"tr": "Klasorun adi", "en": "Folder name"},
    "kategoriler.rule_name_ph": {"tr": "Ornek: Cocuklar", "en": "Example: Kids"},
    "kategoriler.rule_now": {"tr": "Bu kural su an:", "en": "Right now this rule matches:"},
    "kategoriler.title": {"tr": "Klasorler", "en": "Folders"},
    "kategoriler.turn_off": {"tr": "Kapat", "en": "Turn off"},
    "kategoriler.turn_on": {"tr": "Ac", "en": "Turn on"},
    "kategoriler.unknown_desc": {
        "tr": "yuz var ama henuz isim verilmemis",
        "en": "a face, but no name yet",
    },

    # -- Fotograflar ---------------------------------------------------
    "fotograflar.assign": {"tr": "Ata", "en": "Assign"},
    "fotograflar.assign_help": {
        "tr": "Yuzu bulunamayan fotografi da bir kisiye baglayabilirsiniz - yandan, uzaktan ya da yari kadraj kalmis kareler icin.",
        "en": "You can link a photo to a person even when no face was found in it - for shots taken from the side, from a distance, or half out of frame.",
    },
    "fotograflar.assign_label": {
        "tr": "Isaretlenenleri kisiye ata:",
        "en": "Assign the selected photos to:",
    },
    "fotograflar.cleanup_exact_only": {
        "tr": "Yalnizca <b>bayt bayt ayni</b> kopyalar toplu silinir. Gorsel olarak benzer bulunanlar bir tahmindir; onlari <a href=\"{link}\">kopyalar ekranindan</a> gozden gecirin.",
        "en": "Only <b>byte-for-byte identical</b> copies are deleted in bulk. Photos that merely look alike are a guess - go through those on the <a href=\"{link}\">duplicates screen</a>.",
    },
    "fotograflar.cleanup_note": {
        "tr": "Ikisi de once ne silinecegini sayilarla gosterir, onaylamadan hicbir sey olmaz. Silinenler Geri Donusum Kutusu'nda durur.",
        "en": "Both of these show you the numbers first, and nothing happens until you confirm. Anything deleted stays in the Recycle Bin.",
    },
    "fotograflar.cleanup_title": {"tr": "Toplu temizlik", "en": "Bulk cleanup"},
    "fotograflar.clear_filter": {"tr": "Filtreyi temizle", "en": "Clear filter"},
    "fotograflar.delete_exact": {
        "tr": "Birebir kopyalari sil",
        "en": "Delete identical copies",
    },
    "fotograflar.delete_faceless": {
        "tr": "Yuzsuz kareleri sil ({n})",
        "en": "Delete photos with no faces ({n})",
    },
    "fotograflar.delete_picked": {"tr": "Isaretlenenleri sil", "en": "Delete selected"},
    "fotograflar.details": {"tr": "Ayrintilar", "en": "Details"},
    "fotograflar.filter_everyone": {"tr": "Butun kisiler", "en": "Everyone"},
    "fotograflar.filter_faceless": {"tr": "Yuz olmayanlar", "en": "Ones with no faces"},
    "fotograflar.filter_normal": {
        "tr": "Normal (kopyalar haric)",
        "en": "Normal (no duplicates)",
    },
    "fotograflar.filter_unknown": {"tr": "Taninmayan yuzler", "en": "Unknown faces"},
    "fotograflar.lede": {
        "tr": "{n} fotograf. Uzerine tiklayarak isaretler, kutuya tiklamadan acmak icin adina tiklarsiniz.",
        "en": "{n} photos. Click a photo to select it, or click the arrow on it to open it.",
    },
    "fotograflar.new_person": {"tr": "+ Yeni kisi ekle", "en": "+ Add a new person"},
    "fotograflar.pick_every": {"tr": "Tumunu isaretle ({n})", "en": "Select all ({n})"},
    "fotograflar.pick_page": {"tr": "Sayfadakileri isaretle", "en": "Select this page"},
    "fotograflar.rematch": {
        "tr": "Taninan kisileri yeniden ara",
        "en": "Look for known people again",
    },

    # -- Tek fotograf sayfasi ------------------------------------------
    "fotograf.assign": {"tr": "Ata", "en": "Assign"},
    "fotograf.camera": {"tr": "Kamera", "en": "Camera"},
    "fotograf.copies": {"tr": "Kopyalari", "en": "Its copies"},
    "fotograf.detach": {"tr": "Baglantiyi kaldir", "en": "Unlink"},
    "fotograf.dimensions": {"tr": "Boyut", "en": "Dimensions"},
    "fotograf.dup_exact": {"tr": "birebir", "en": "identical"},
    "fotograf.dup_visual": {"tr": "gorsel", "en": "looks alike"},
    "fotograf.face_count": {"tr": "Yuz sayisi", "en": "Faces"},
    "fotograf.faces_selected": {"tr": "{n} yuz secildi", "en": "{n} faces selected"},
    "fotograf.faces_title": {"tr": "Bu fotograftaki yuzler", "en": "Faces in this photo"},
    "fotograf.file_size": {"tr": "Dosya", "en": "File size"},
    "fotograf.from_filedate": {"tr": "dosya tarihinden", "en": "from the file date"},
    "fotograf.from_filename": {"tr": "dosya adindan", "en": "from the file name"},
    "fotograf.in_folders": {"tr": "Bulundugu klasorler", "en": "Folders it is in"},
    "fotograf.info": {"tr": "Bilgiler", "en": "Details"},
    "fotograf.is_copy": {
        "tr": "Bu fotograf bir kopya olarak isaretlendi (<a href=\"{link}\">aslini gor</a>). Klasorlere aslini konur.",
        "en": "This photo is marked as a copy (<a href=\"{link}\">see the original</a>). It is the original that goes into your folders.",
    },
    "fotograf.location": {"tr": "Konum", "en": "Location"},
    "fotograf.no_faces": {
        "tr": "Bu fotografta yuz bulunamadi.",
        "en": "No faces were found in this photo.",
    },
    "fotograf.on_map": {"tr": "haritada gor", "en": "see it on a map"},
    "fotograf.pick_person": {"tr": "-- kisi secin --", "en": "-- pick a person --"},
    "fotograf.taken_at": {"tr": "Cekim tarihi", "en": "Taken"},
    "fotograf.unnamed": {"tr": "isimsiz", "en": "unnamed"},

    # -- Tek kareler ---------------------------------------------------
    "tekil.assign": {"tr": "Bu kisiye ata", "en": "Assign to this person"},
    "tekil.count": {"tr": "{n} tek kare", "en": "{n} one-offs"},
    "tekil.create_person": {"tr": "Yeni kisi olarak kaydet", "en": "Save as a new person"},
    "tekil.empty_done": {
        "tr": "Tek kare kalmadi - butun yuzler bir gruba veya kisiye bagli.",
        "en": "No one-offs left - every face belongs to a group or a person.",
    },
    "tekil.empty_scan": {
        "tr": "Once fotograflari tarayin. <a href=\"{link}\">Panele git</a>",
        "en": "Scan your photos first. <a href=\"{link}\">Go to the dashboard</a>",
    },
    "tekil.lede": {
        "tr": "Bu yuzler hicbir gruba giremedi - cogu yalnizca bir iki karede goruldugu icin. Aralarinda tanidiklariniz varsa isaretleyip bir kisiye atayin; gerisi reklamlardan, ekran goruntulerinden ve arka planda gecen yabancilardan gelir.",
        "en": "None of these faces fit into a group, mostly because they only show up in a shot or two. If you recognise someone here, select the face and assign it to a person; the rest come from adverts, screenshots and strangers who happened to be in the background.",
    },
    "tekil.new_name_placeholder": {"tr": "yeni isim yaz", "en": "type a new name"},
    "tekil.or": {"tr": "veya", "en": "or"},
    "tekil.pick_person": {"tr": "-- kisi secin --", "en": "-- pick a person --"},
    "tekil.picked_label": {"tr": "Isaretlenenleri:", "en": "The faces you selected:"},
    "tekil.showing": {"tr": "{n} tanesi gosteriliyor", "en": "showing {n} of them"},
    "tekil.tag_screen": {"tr": "ekran/indirilen", "en": "screenshot/downloaded"},
    "tekil.title": {"tr": "Tek kareler", "en": "One-offs"},
    "tekil.why_group": {
        "tr": "Bir yuz grubunun olusmasi icin en az {n} benzer yuz gerekiyor. Bir kisi kutuphanenizde sadece bir iki kez goruntuyse grup olusmaz ve yuzu buraya duser.",
        "en": "It takes at least {n} similar faces for a group to form. If someone appears only once or twice in your library, no group forms and their face ends up here.",
    },
    "tekil.why_screen": {
        "tr": "<b>ekran/indirilen</b> etiketi, fotografta kamera bilgisi (EXIF) olmadigini gosterir - genelde ekran goruntusu, reklam afisi veya paylasilmis bir gorsel demektir. Bunlardaki yuzler tanidiklariniz olmayabilir.",
        "en": "The <b>screenshot/downloaded</b> tag means the photo carries no camera information (EXIF) - usually a screenshot, an advert or an image someone shared. The faces in those may well be people you do not know.",
    },
    "tekil.why_title": {"tr": "Neden buradalar", "en": "Why they are here"},

    # -- Kopyalar ------------------------------------------------------
    "kopyalar.check1": {
        "tr": "Gorsel parmak izi (yeniden sikistirilmis kopyalari yakalar),",
        "en": "A visual fingerprint, which catches re-compressed copies,",
    },
    "kopyalar.check2": {
        "tr": "En/boy orani (dikey bir kare ile yatay bir kare asla ayni degildir),",
        "en": "The shape of the frame, so an upright photo is never the same as a wide one,",
    },
    "kopyalar.check3": {
        "tr": "Piksel parlaklik imzasi (duz gokyuzu, beyaz duvar gibi detaysiz karelerin birbirine karismasini engeller).",
        "en": "A brightness signature, which stops plain shots - an empty sky, a white wall - from being mixed up with each other.",
    },
    "kopyalar.count": {
        "tr": "{n} grup gosteriliyor. En cok yer kaplayanlar ustte.",
        "en": "Showing {n} groups. The ones taking up the most space come first.",
    },
    "kopyalar.exact": {"tr": "birebir", "en": "identical"},
    "kopyalar.go_panel": {"tr": "Panele git", "en": "Go to the dashboard"},
    "kopyalar.group_meta": {
        "tr": "{n} kopya · {mb} MB fazladan",
        "en": "{n} copies · {mb} MB extra",
    },
    "kopyalar.how_lede": {
        "tr": "Iki fotograf ancak <b>uc kontrolun ucu de</b> gecerse ayni sayilir:",
        "en": "Two photos only count as the same if <b>all three checks</b> pass:",
    },
    "kopyalar.how_note": {
        "tr": "Asil olarak EXIF cekim tarihi olan ve en buyuk dosya secilir - yani genelde telefondan gelen orijinal, Google'in yeniden sikistirdigi degil.",
        "en": "The one kept is the largest file that still has an EXIF capture date - usually the original from your phone, not the copy Google re-compressed.",
    },
    "kopyalar.how_title": {"tr": "Nasil karar veriliyor", "en": "How this is decided"},
    "kopyalar.kept": {"tr": "saklanan", "en": "kept"},
    "kopyalar.lede": {
        "tr": "Ayni fotografin birden fazla kopyasi. Telefondan ve Google'dan ayni kutuphaneyi aldiysaniz bu liste uzun olur. <b>Hicbir sey silinmez</b> - kopyalar sadece klasorlere ikinci kez konmaz.",
        "en": "The same photo, more than once. If you brought the same library in from your phone and from Google, this list will be long. <b>Nothing is deleted</b> - the copies are simply not put into the folders a second time.",
    },
    "kopyalar.none": {"tr": "Kopya bulunamadi.", "en": "No duplicates found."},
    "kopyalar.original": {"tr": "asil", "en": "original"},
    "kopyalar.scan_first": {
        "tr": "Once fotograflari tarayin.",
        "en": "Scan your photos first.",
    },
    "kopyalar.visual": {"tr": "gorsel", "en": "look-alike"},

    # -- Ayarlar -------------------------------------------------------
    "ayarlar.accuracy": {"tr": "Tanima hassasiyeti", "en": "Recognition accuracy"},
    "ayarlar.accuracy_help": {
        "tr": "Varsayilanlar cogu kutuphane icin dogrudur. Degistirdikten sonra <b>yeniden gruplama</b> yapmaniz gerekir.",
        "en": "The defaults are right for most collections. If you change them you have to <b>group the faces again</b>.",
    },
    "ayarlar.cluster_distance": {
        "tr": "Gruplama sikiligi",
        "en": "How tightly faces are grouped",
    },
    "ayarlar.cluster_distance_help": {
        "tr": "Dusuk deger = daha cok ama daha saf grup. Yuksek deger = az grup ama farkli kisiler karisabilir.",
        "en": "A low value gives you more groups, each one cleaner. A high value gives you fewer groups, but different people can end up mixed together.",
    },
    "ayarlar.data_dir": {"tr": "Veri klasoru", "en": "Data folder"},
    "ayarlar.date_subfolders": {
        "tr": "Kategori klasorleri icinde yila gore alt klasor ac",
        "en": "Group photos by year inside each folder",
    },
    "ayarlar.detect_max_side": {
        "tr": "Cozumleme boyutu (piksel)",
        "en": "Analysis size (pixels)",
    },
    "ayarlar.detect_max_side_help": {
        "tr": "Buyutmek uzaktaki kucuk yuzleri bulur ama islemi yavaslatir.",
        "en": "A bigger number finds small, distant faces but slows the work down.",
    },
    "ayarlar.errors": {"tr": "Islenemeyen dosyalar", "en": "Files that could not be read"},
    "ayarlar.errors_error": {"tr": "Hata", "en": "Error"},
    "ayarlar.errors_file": {"tr": "Dosya", "en": "File"},
    "ayarlar.internet": {"tr": "Internet", "en": "Internet"},
    "ayarlar.internet_note": {
        "tr": "Model indirildikten sonra hicbir baglanti kurulmaz.",
        "en": "Once the model is downloaded, nothing ever connects out again.",
    },
    "ayarlar.link_mode": {"tr": "Dosyalar nasil yerlessin", "en": "How the files get there"},
    "ayarlar.link_mode_help": {
        "tr": "<b>Baglanti</b>: fotograf diskte bir kez durur, klasorlerde normal dosya gibi gorunur, ek yer kaplamaz. Ayni surucu gerekir.",
        "en": "<b>Link</b>: the photo sits on the disk once, shows up in the folders like an ordinary file, and takes no extra space. It needs the same drive.",
    },
    "ayarlar.maintenance": {"tr": "Bakim", "en": "Maintenance"},
    "ayarlar.match_strong": {"tr": "Otomatik tanima esigi", "en": "Automatic match threshold"},
    "ayarlar.match_strong_help": {
        "tr": "Bu benzerligin ustundeki yuzler sorulmadan kisiye baglanir.",
        "en": "Faces above this similarity are added to the person without asking you.",
    },
    "ayarlar.match_weak": {
        "tr": "\"Bu da o mu?\" alt esigi",
        "en": "\"Is this them too?\" lower threshold",
    },
    "ayarlar.match_weak_help": {
        "tr": "Bu ile ustteki esik arasindakiler size sorulur.",
        "en": "Faces between this and the threshold above are shown to you to decide.",
    },
    "ayarlar.min_det_score": {"tr": "Yuz tespit esigi", "en": "Face detection threshold"},
    "ayarlar.min_det_score_help": {
        "tr": "Dusurmek daha cok yuz bulur ama yanlis tespitler artar.",
        "en": "Lower it to find more faces, at the cost of more false ones.",
    },
    "ayarlar.min_face_px": {"tr": "En kucuk yuz (piksel)", "en": "Smallest face (pixels)"},
    "ayarlar.min_face_px_help": {
        "tr": "Bundan kucuk yuzler yok sayilir. Arka plandaki yabancilar icin.",
        "en": "Faces smaller than this are ignored. It keeps strangers in the background out.",
    },
    "ayarlar.model": {"tr": "Yuz tanima modeli", "en": "Face recognition model"},
    "ayarlar.model_missing": {"tr": "henuz indirilmedi", "en": "not downloaded yet"},
    "ayarlar.model_note": {
        "tr": "ilk analizde otomatik inecek (~280 MB)",
        "en": "downloads itself on the first analysis (~280 MB)",
    },
    "ayarlar.model_ready": {"tr": "indirildi", "en": "downloaded"},
    "ayarlar.output": {"tr": "Cikti", "en": "Where photos go"},
    "ayarlar.output_dir": {
        "tr": "Klasorlerin olusacagi yer",
        "en": "Where to create the folders",
    },
    "ayarlar.output_dir_help": {
        "tr": "Bu klasorun icinde her kategori icin bir alt klasor olusur. Kaynak klasorlerinizle ayni surucude olmasi onerilir.",
        "en": "Each of your folders gets a subfolder in here. Best kept on the same drive as your source folders.",
    },
    "ayarlar.reanalyse": {
        "tr": "Butun fotograflari yeniden cozumle",
        "en": "Analyse every photo again",
    },
    "ayarlar.reanalyse_button": {"tr": "Sifirla", "en": "Reset"},
    "ayarlar.reanalyse_confirm": {
        "tr": "Butun yuz kayitlari ve kisi isimleri silinecek. Fotograflariniz silinmez. Devam?",
        "en": "Every face record and every name will be deleted. Your photos are not touched. Continue?",
    },
    "ayarlar.reanalyse_help": {
        "tr": "Butun yuz kayitlarini ve gruplari siler, sifirdan baslar. Kisi isimleri de kaybolur. Hassasiyet ayarlarini degistirdiyseniz gerekir.",
        "en": "Deletes every face record and group and starts from scratch. The names you gave people go too. You need this if you changed the accuracy settings.",
    },
    "ayarlar.save": {"tr": "Ayarlari kaydet", "en": "Save settings"},
    "ayarlar.status": {"tr": "Durum", "en": "Status"},
    "ayarlar.version": {"tr": "Surum", "en": "Version"},
    "ayarlar.workers": {"tr": "Es zamanli is parcacigi", "en": "How many photos at once"},
    "ayarlar.workers_help": {
        "tr": "Fotograf cozmeyi hizlandirir. Cok yuksek deger bellegi zorlar.",
        "en": "Speeds the analysis up. Set it too high and memory runs short.",
    },

    # -- Ayarlar - dil secimi ------------------------------------------
    "settings.language": {"tr": "Arayüz dili", "en": "Interface language"},
    "settings.language_help": {
        "tr": "Arayüzün dili. Çeviri eksikse o cümleler İngilizce kalır; yeni dil eklemek için <code>facefold/i18n.py</code> dosyasına bakın.",
        "en": "The language of the interface. Untranslated sentences stay in English; to add a language see <code>facefold/i18n.py</code>.",
    },
    "settings.language_saved": {
        "tr": "Arayüz dili değiştirildi.",
        "en": "Interface language changed.",
    },

    # -- Kural turleri (gelismis klasor duzenleyici) -------------------
    "rule.all": {"tr": "Butun fotograflar", "en": "Every photo"},
    "rule.any": {"tr": "Bu kisilerden en az biri var", "en": "At least one of these people"},
    "rule.contains": {
        "tr": "Bu kisilerin hepsi var (baskalari da olabilir)",
        "en": "All of these people (others may be present)",
    },
    "rule.crowd": {
        "tr": "Kalabalik (cok sayida taninan kisi)",
        "en": "Crowd (many recognised people)",
    },
    "rule.exact": {"tr": "Tam olarak bu kisiler", "en": "Exactly these people"},
    "rule.kind": {
        "tr": "Belirli bir fotograf turu (ekran goruntusu vb.)",
        "en": "A particular kind of photo (screenshot and so on)",
    },
    "rule.nobody": {
        "tr": "Hic yuz yok (manzara, ekran goruntusu, belge)",
        "en": "No faces at all (landscapes, screenshots, documents)",
    },
    "rule.solo": {"tr": "Yalnizca bu kisi", "en": "Only this person"},
    "rule.unknown_only": {"tr": "Yuz var ama taninmayan", "en": "Faces, but nobody recognised"},

    # -- Cikti dosyalari nasil olusturulur -----------------------------
    "linkmode.copy": {"tr": "Gercek kopya", "en": "A real copy"},
    "linkmode.hardlink": {
        "tr": "Baglanti (disk sismez)",
        "en": "Hardlink (no extra disk space)",
    },
    "linkmode.report": {
        "tr": "Dosyalara dokunma, sadece liste cikar",
        "en": "Touch no files, just write a list",
    },

    # -- Diskte olusan klasor adlari -----------------------------------
    "folder.and": {"tr": "ve", "en": "and"},
    "folder.crowd": {"tr": "Kalabalik", "en": "Crowd"},
    "folder.faceless": {"tr": "Yuzsuz kareler", "en": "No faces"},
    "folder.screenshots": {"tr": "Ekran goruntuleri", "en": "Screenshots"},
    "folder.unknown": {"tr": "Taninmayan kisiler", "en": "Unknown people"},

    # -- Tarayicida calisan metinler (app.js bunlari window.FACEFOLD_T ile alir) ----
    "js.assign_confirm": {
        "tr": "{n} fotograf \"{name}\" kisisine baglanacak.\n\nDevam edilsin mi?",
        "en": "{n} photos will be linked to \"{name}\".\n\nGo ahead?",
    },
    "js.assign_done": {
        "tr": "{n} fotograf \"{name}\" kisisine baglandi.",
        "en": "{n} photos linked to \"{name}\".",
    },
    "js.assign_faces": {
        "tr": "{n} yuz de bu kisiye atandi.",
        "en": "{n} faces went to them as well.",
    },
    "js.assign_failed": {"tr": "Atanamadi", "en": "Couldn't link them"},
    "js.assign_grown": {
        "tr": "Benzeyen {n} yuz daha bulundu.",
        "en": "{n} more faces that look like them turned up.",
    },
    "js.choose_person": {"tr": "Kisi secin", "en": "Choose a person"},
    "js.create_failed": {"tr": "Olusturulamadi", "en": "Couldn't create them"},
    "js.created_grown": {
        "tr": "{n} yuz atandi. Ayrica benzeyen {m} yuz daha ayni kisiye baglandi.",
        "en": "{n} faces assigned. {m} more that look like them were linked to the same person.",
    },
    "js.dates_repaired": {
        "tr": "{n} gecersiz tarih temizlendi.\n{f} tanesi dosya adindan,\n{g} tanesi Google verisinden duzeltildi.",
        "en": "{n} wrong dates cleared.\n{f} of them recovered from the file name,\n{g} from your Google data.",
    },
    "js.delete_count": {"tr": "{n} fotograf ({mb} MB)", "en": "{n} photos ({mb} MB)"},
    "js.delete_failed": {"tr": "Silinemedi", "en": "Couldn't delete"},
    "js.delete_failed_count": {
        "tr": "{n} dosya silinemedi.",
        "en": "{n} files couldn't be deleted.",
    },
    "js.delete_links": {
        "tr": "ayrica cikti klasorlerindeki {n} es dosya",
        "en": "plus {n} matching files in the output folders",
    },
    "js.delete_person": {
        "tr": "\"{name}\" kisisinin butun fotograflari silinecek.",
        "en": "Every photo of \"{name}\" will be deleted.",
    },
    "js.delete_picked": {
        "tr": "Isaretlediginiz fotograflar silinecek.",
        "en": "The photos you ticked will be deleted.",
    },
    "js.delete_selected": {
        "tr": "Secilen fotograflar silinecek.",
        "en": "The selected photos will be deleted.",
    },
    "js.delete_warning": {
        "tr": "Bunlar GERI DONUSUM KUTUSU'na gonderilecek.\nKalici silinmez - Windows Gezgini'nden geri alabilirsiniz.\n\nDevam edilsin mi?",
        "en": "These go to the RECYCLE BIN.\nNothing is erased for good - you can bring them back from File Explorer.\n\nGo ahead?",
    },
    "js.deleted_ok": {
        "tr": "{n} fotograf Geri Donusum Kutusu'na gonderildi.",
        "en": "{n} photos moved to the Recycle Bin.",
    },
    "js.detach_confirm": {
        "tr": "{n} yuz bu kisiden cikarilacak.\nFotograflar silinmez, yuzler isimsiz havuza doner.",
        "en": "{n} faces will be taken off this person.\nNo photos are deleted; the faces go back to the unnamed pile.",
    },
    "js.faces_selected": {"tr": "{n} yuz secildi", "en": "{n} faces selected"},
    "js.hide_failed": {"tr": "Gizlenemedi", "en": "Couldn't hide them"},
    "js.import_failed": {"tr": "Aktarilamadi", "en": "Couldn't import it"},
    "js.importing": {"tr": "Aktariliyor...", "en": "Importing..."},
    "js.job_start_failed": {"tr": "Islem baslatilamadi.", "en": "Couldn't start that."},
    "js.list_failed": {"tr": "Liste alinamadi.", "en": "Couldn't load the list."},
    "js.loading": {"tr": "Aliniyor...", "en": "Loading..."},
    "js.match_failed": {"tr": "Calistirilamadi", "en": "Couldn't run that"},
    "js.match_found": {
        "tr": "{n} yuz taninan kisilere baglandi.\n{checked} isimsiz yuz tarandi.",
        "en": "{n} faces linked to people you have already named.\n{checked} unnamed faces looked at.",
    },
    "js.match_none": {
        "tr": "Yeni eslesme bulunamadi.\n{n} isimsiz yuz tarandi, hicbiri esik degerini gecmedi.",
        "en": "No new matches.\n{n} unnamed faces looked at, none of them close enough.",
    },
    "js.merge_confirm": {
        "tr": "Bu iki kisi birlestirilecek.\nFotograflar silinmez.\n\nDevam edilsin mi?",
        "en": "These two will be merged into one person.\nNo photos are deleted.\n\nGo ahead?",
    },
    "js.merge_failed": {"tr": "Birlestirilemedi", "en": "Couldn't merge them"},
    "js.new_person_name": {"tr": "Yeni kisinin adi:", "en": "Name of the new person:"},
    "js.open_folder_failed": {"tr": "Klasor acilamadi.", "en": "Couldn't open the folder."},
    "js.photo_count": {"tr": "{n} fotograf", "en": "{n} photos"},
    "js.photos_selected": {"tr": "{n} secili", "en": "{n} selected"},
    "js.pick_faces_first": {"tr": "Once yuz isaretleyin.", "en": "Tick some faces first."},
    "js.pick_person_first": {"tr": "Once kisi secin.", "en": "Choose a person first."},
    "js.pick_photos_first": {
        "tr": "Once fotograf isaretleyin.",
        "en": "Tick some photos first.",
    },
    "js.repair_failed": {"tr": "Onarilamadi", "en": "Couldn't fix the dates"},
    "js.repairing": {"tr": "Onariliyor...", "en": "Fixing..."},
    "js.searching": {"tr": "Araniyor...", "en": "Looking..."},
    "js.tail_hidden": {
        "tr": "{n} kucuk grup gizlendi.\nSilinmediler - istediginizde geri getirebilirsiniz.",
        "en": "{n} small groups hidden.\nNothing was deleted - you can bring them back whenever you like.",
    },
    "js.takeout_done": {
        "tr": "Google Fotograflar verisi alindi.\n\n{p} kisi olusturuldu\n{s} yuz dogrudan eslesti\n{a} yuz benzerlikle baglandi\n{d} fotografin cekim tarihi duzeltildi",
        "en": "Your Google Photos data is in.\n\n{p} people created\n{s} faces matched outright\n{a} faces linked by resemblance\n{d} photo dates corrected",
    },
    "js.type_name_first": {"tr": "Once bir isim yazin.", "en": "Type a name first."},
    "js.working": {"tr": "Calisiyor...", "en": "Working..."},

    # -- api -----------------------------------------------------------
    "api.folder_missing": {"tr": "Klasor yok", "en": "That folder isn't there"},
    "api.name_empty": {"tr": "Isim bos olamaz", "en": "The name can't be empty"},
    "api.no_face": {"tr": "Yuz secilmedi", "en": "No faces selected"},
    "api.no_person": {"tr": "Kisi secilmedi", "en": "No person selected"},
    "api.no_photo": {"tr": "Fotograf secilmedi", "en": "No photos selected"},
    "api.no_photo_delete": {
        "tr": "Silinecek fotograf secilmedi",
        "en": "No photos selected to delete",
    },
    "api.unknown_job": {"tr": "Bilinmeyen islem", "en": "Unknown task"},

}
