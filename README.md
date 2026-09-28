# Facefold

**Sorts the pile of photos dumped from your phone and cloud backups into real folders on your disk, by who is in them. Nothing is ever uploaded.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Runs offline](https://img.shields.io/badge/runs-offline-success.svg)](#privacy)
[![Interface: English + Türkçe](https://img.shields.io/badge/interface-English%20%2B%20T%C3%BCrk%C3%A7e-informational.svg)](facefold/i18n.py)

*Türkçe okumak için: [README.tr.md](README.tr.md)*

![The Facefold dashboard](docs/ekranlar/panel.png)

> **Quick start:** run [`kur.bat`](kur.bat), then [`baslat.bat`](baslat.bat), then open `127.0.0.1:8760`.
> Four steps: point it at a folder, let it scan, name the face groups, build the folders.

---

## The problem

Your phone fills up. You plug in the cable and copy everything to the computer. You download your library from Google Photos too. What you get is tens of thousands of files called `IMG_4471.HEIC`, and no way to tell what is in any of them.

Google Photos can group faces — but it does that **on their servers**. The moment you download your own library it is an undifferentiated pile again. There is no folder on your disk called "Mum", "The two of us", or "The kids".

## What Facefold does

1. Scans the folders you point it at — **including inside zip archives** — and finds the faces in every photo.
2. Gathers the same person's faces together on its own.
3. You name each group **once**.
4. That is all. The folders come from the names.

There are no rules to write and no categories to set up. **A photo goes into exactly one folder, named after exactly the people in it:**

```
Sorted/
├── Alice/                  ← Alice on his own
├── Alice and Bruno/         ← exactly the two of them
├── Alice, Bruno and Clara/  ← exactly the three
├── Crowd/                  ← 4+ recognised people
├── Unknown people/         ← faces, but no names yet
└── No faces/               ← landscapes, screenshots, documents
```

The arrangement gets richer as you name more people. **The moment you name Bruno**, every photo with both of them moves out of `Alice/` and into its own folder. Take the name away and they move back. Add new photos later and it recognises the people it already knows.

## What makes it different

**Combination folders build themselves.** "Alice and Bruno" means *exactly* those two — a fifteen-person wedding photo does not land there, it goes to `Crowd/`. You can filter for two people in Google Photos, but that filter never becomes a permanent folder you can browse, back up or copy to a drive.

**You do not have to unzip anything.** A Google Takeout export arrives as dozens of zip files and can add up to hundreds of gigabytes. Facefold never unpacks them: it reads each photo out of the archive for analysis, and only extracts the single file when it is placed into a folder. Scanning a fifty-archive export takes **under two seconds**.

**It imports the names Google already knows.** Takeout's metadata files hold the people you named in Google Photos and the **real capture date** — which matters, because Takeout strips the EXIF from most exported files.

**You can delete what you do not want.** Bulk delete for one person's photos, all duplicates, every photo with no face in it, or whatever you tick by hand. Everything goes to the **Recycle Bin** — there is no permanent-delete path in the program at all.

**Your disk does not grow.** Hardlinks by default: the photo exists once on disk and shows up as an ordinary file in as many folders as it belongs to. Spreading 4,000 photos across 10 folders costs zero extra space.

**Your source files are never touched.** Nothing is moved, renamed or deleted. The only files the program can delete are ones it created itself inside the output folder.

**It finds duplicates properly.** The same photo from your phone (HEIC) and back from Google (re-compressed JPEG) is recognised as one photo despite the different format and size, and placed once. The phone original is kept as the master. ([test](tests/test_heic.py))

**No internet.** After the model downloads once, the program never makes a network connection again. Family photos and children's faces do not leave your computer.

**English and Turkish.** Switch under Settings. Folder names follow the language too — "Alice and Bruno" becomes "Alice ve Bruno" in Turkish, and the files are *moved*, not copied.

## Privacy

Privacy is the entire reason this program exists. Family photos, children's faces and face-recognition data belong on your own disk, not on a company's server. So here is exactly what happens, in terms you can check:

**No photo is ever sent anywhere.** The program downloads the face model once, on first run (~280 MB). After that it makes no network connections at all — you can unplug the cable and keep using it. The interface listens on `127.0.0.1` only, so not even another machine on your own network can open it.

**The analysis stays local too.** Face vectors, thumbnails and face crops live on your computer under `veri/`:

| Where | What is in it |
|---|---|
| `veri/facefold.db` | File paths, dates, names, face vectors |
| `veri/kucuk/` | Small previews of your photos |
| `veri/yuzler/` | Face crops (the pictures on the people cards) |

**Deleting everything is deleting one folder.** Remove `veri/` and the program starts from scratch. Your photos are untouched, because it never modifies your source files.

**Your photos cannot end up in this repository.** `.gitignore` blocks photo, video and archive files *by type*, not just by folder name, so an accidental `git add -A` cannot publish your library. Anyone who clones this project gets source code and nothing else.

### If you are contributing

Please do not publish your own photos or data along with your change. Before `git add -A`:

- Run `git status --ignored` and confirm `veri/` is excluded.
- If you are adding a screenshot, take it against the sample library from `python tests/make_fixtures.py` — those faces are already pixelated. Make sure your **Windows username** is not visible (`C:\Users\...`); run the program from a neutral folder such as `C:\Facefold-demo`.
- Do not put real people's names in code comments, tests or interface text. The project has an invented example person for that: `Ayşe Şahin`.
- If something gets committed by accident, deleting the file is not enough — it stays in the git history where everyone can reach it. Open an issue and we will clean the history together.

## Installing

Needs Python 3.10 or newer. ([python.org/downloads](https://www.python.org/downloads/) — tick **"Add Python to PATH"** during setup.)

**On Windows:** double-click `kur.bat`, then double-click `baslat.bat` when it finishes.

**By hand:**

```bash
python -m pip install -r requirements.txt
python calistir.py
```

Your browser opens at `http://127.0.0.1:8760`. The face-recognition model downloads automatically on the first analysis (~280 MB, once).

## Using it

| Step | What you do |
|---|---|
| 1 | **Source folders** → paste the path to the folder holding your photos |
| 2 | **Start scanning** → scanning, face analysis, duplicate detection and grouping run in order |
| 3 | **People** → give each face group a name (once) |
| 4 | **Build folders** → the output folder fills up |

A face that landed in the wrong place can be removed from the person's page, two people can be merged, and single frames that fit no group can be assigned one by one from the **Singles** screen.

![The People screen](docs/ekranlar/kisiler.png)

> The faces in these screenshots are deliberately pixelated — the test data is generated from a sample photo containing real people.

### Rule types

The automatic arrangement is enough for most people. If you want, you can write your own rules under **Folders → Advanced**:

| Rule | Meaning |
|---|---|
| **Only this person** | Nobody else recognised in the frame. |
| **Exactly these people** | Your selection is present and nobody else recognised. |
| **All of these** | Your selection is present; others may be too. |
| **At least one of** | Broad collections like "any of the kids". |
| **Crowd** | More recognised people than the number you set. |
| **No faces at all** | Landscapes, screenshots, photos of documents. |
| **Faces, nobody recognised** | Frames with people you have not named. |

Each rule can also take a date range, a minimum face size, and a "strangers in the background do not break the rule" option. Rules you write by hand take priority over the automatic ones and are never deleted.

![The Folders screen](docs/ekranlar/kategoriler.png)

## How it works

| Layer | Method |
|---|---|
| Foldering | Partition by the set of named people (`auto.py`) |
| Face detection | SCRFD (`det_10g`) |
| Identity vector | ArcFace `w600k_r50`, 512 dimensions |
| Age / gender | insightface `genderage` — a hint for children's faces changing over the years |
| Grouping | Tight-threshold pre-merge, then average-linkage hierarchical clustering |
| Duplicates | SHA-1 + perceptual hash (DCT) + aspect ratio + pixel signature |
| Storage | SQLite; output folders can be rebuilt from scratch at any time |
| Archives | Read from inside zips, extract a single member on placement |
| Deleting | Recycle Bin only (`send2trash`) |
| Interface | Flask, bound to `127.0.0.1` |

A duplicate decision has to pass **all three checks**. A perceptual hash on its own confuses low-detail frames — flat sky, a white wall — so the aspect ratio and a pixel signature have to agree with it.

Grouping happens in two stages: very similar frames (burst shots, the same moment) are merged first with a tight threshold, then the real clustering runs on that much smaller set. Hierarchical clustering applied directly to 50,000 faces would need a 20 GB distance matrix; this reaches the same answer far more cheaply.

## Speed

Measured on 12-megapixel HEIC files (4032×3024, iPhone size), a realistic mix (12 single portraits + 4 pairs + 2 crowds + 2 with no faces), desktop CPU, no GPU, 4 threads.

**1.12 seconds per photo.**

| Photos | Roughly |
|---|---|
| 1,000 | ~20 minutes |
| 5,000 | ~1.5 hours |
| 20,000 | ~6 hours |

The number of faces in a frame matters directly: a landscape with no faces takes 0.4 s, a crowded frame with six people takes 2.6 s. Small photos (1–2 MP) run about five times faster.

Only the first time. Later scans process new files only, and an interrupted run picks up where it stopped.

> insightface runs four models per face by default. Two of them are landmark models Facefold does not use, and they are switched off — a **44%** speedup on its own (see `config.ACTIVE_MODULES`).

## Development

```bash
python tests/make_fixtures.py    # builds a sample library with known answers
python tests/test_pipeline.py    # the whole pipeline
python tests/test_web.py         # end to end through the interface
python tests/test_heic.py        # iPhone HEIC + cross-format duplicates
python tests/test_auto.py        # automatic foldering, zips, Turkish names, deleting
python tests/test_dil.py         # language switching and folders being moved
python tests/test_denetim.py     # bugs found once do not come back
```

The test library is generated from the sample group photo shipped with insightface: portraits, pairs, duplicates and frames with no faces. Because the right answer is known in advance, the tests make real assertions instead of relying on someone eyeballing the output.

```
facefold/
├── config.py       settings, thresholds
├── db.py           SQLite schema
├── imaging.py      HEIC, EXIF, fingerprints, thumbnails
├── archives.py     photos inside zips
├── scanner.py      folder walking, duplicate detection
├── faces.py        face detection + identity vector
├── pipeline.py     the analysis pipeline (multi-threaded)
├── clustering.py   grouping, naming, merging
├── names.py        Turkish-aware name comparison, folder naming
├── i18n.py         interface language — a new language is one column here
├── auto.py         automatic foldering — a person is a folder
├── rules.py        the rule engine
├── takeout.py      Google Photos metadata
├── trash.py        bulk delete (Recycle Bin)
├── distribute.py   building folders (hardlink / copy / report)
└── web/            the Flask interface
```

## Contributing

Contributions are welcome. The most useful right now:

- **Video support** — pulling faces from frames, telling iPhone Live Photo files apart (`archives.py` already counts videos, the groundwork is there)
- **GPU support** — 10–20× faster with `onnxruntime-gpu`
- **More languages** — a new language is one column in [`facefold/i18n.py`](facefold/i18n.py). Untranslated sentences fall back rather than breaking, so a partial translation is genuinely useful and mergeable
- **macOS and Linux** — the code was written to be portable but has only been tested on Windows
- **A folder picker** — paths are currently pasted by hand

Code and comments are in English; text the user sees goes into the `i18n.py` dictionary rather than into a template. `tests/test_pipeline.py`, `tests/test_web.py` and `tests/test_dil.py` must pass. Please read the privacy notes in [CONTRIBUTING.md](CONTRIBUTING.md) before sending a change.

## License

MIT — see [LICENSE](LICENSE).

The face-recognition models belong to the [insightface](https://github.com/deepinsight/insightface) project and carry their own licence terms.
