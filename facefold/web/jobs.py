"""Running long tasks in the background so the browser stays responsive.

Scanning tens of thousands of photos takes hours. The web request that starts
a job returns immediately; the worker thread writes progress into the jobs
table and the page polls it. One job at a time - the model and the database
are not worth contending over.
"""

from __future__ import annotations

import threading
import traceback
from typing import Callable

from .. import clustering, db, distribute, kinds, pipeline, scanner

_lock = threading.Lock()
_thread: threading.Thread | None = None


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()


def current() -> dict | None:
    row = db.active_job()
    if row is None:
        return None
    data = dict(row)
    total = data.get("total") or 0
    done = data.get("done") or 0
    data["percent"] = round(100.0 * done / total, 1) if total else 0.0
    data["alive"] = is_running()
    return data


def start(kind: str, fn: Callable[[int], dict], message: str = "") -> tuple[bool, str]:
    """Start a job unless one is already going."""
    global _thread
    with _lock:
        if is_running():
            return False, "Zaten calisan bir islem var."

        # A job left 'running' by a crash or a closed window would block
        # everything, so clear it before starting.
        db.execute(
            "UPDATE jobs SET state='kesildi', ended_at=datetime('now') WHERE state='running'"
        )
        job_id = db.job_start(kind, message=message)

        def runner() -> None:
            try:
                result = fn(job_id)
                if db.job_cancelled(job_id):
                    db.job_finish(job_id, "iptal", "Kullanici durdurdu")
                else:
                    db.job_finish(job_id, "done", _summarise(kind, result))
            except Exception:  # noqa: BLE001
                db.job_finish(job_id, "hata", traceback.format_exc()[-400:])
            finally:
                db.close()

        _thread = threading.Thread(target=runner, name="facefold-%s" % kind, daemon=True)
        _thread.start()
        return True, "Baslatildi"


def cancel() -> None:
    row = db.active_job()
    if row:
        db.job_cancel(row["id"])


def _summarise(kind: str, result: dict | None) -> str:
    if not isinstance(result, dict):
        return "Tamamlandi"
    if kind == "tarama":
        return "%d yeni fotograf bulundu, %d zaten kayitliydi." % (
            result.get("added", 0), result.get("skipped", 0))
    if kind == "analiz":
        return ("%d fotograf islendi, %d yuz bulundu, %d yuz taninan kisilere "
                "baglandi, %d yeni grup, %d hata." % (
                    result.get("done", 0), result.get("faces", 0),
                    result.get("assigned", 0), result.get("clusters", 0),
                    result.get("errors", 0)))
    if kind == "kopya":
        return "%d birebir, %d gorsel kopya bulundu." % (
            result.get("exact", 0), result.get("similar", 0))
    if kind == "gruplama":
        return "%d kisi grubu olustu." % result.get("clusters", 0)
    if kind == "tanima":
        return "%d yuz taninan kisilere baglandi." % result.get("assigned", 0)
    if kind == "hepsi":
        return ("%d yeni fotograf, %d yuz, %d kopya, %d kisi grubu."
                % (result.get("added", 0), result.get("faces", 0),
                   result.get("exact", 0) + result.get("similar", 0),
                   result.get("clusters", 0)))
    if kind == "dagitim":
        extra = ""
        if result.get("fallback_copies"):
            extra = " (%d dosya baglanti yerine kopyalandi)" % result["fallback_copies"]
        return "%d dosya olusturuldu, %d silindi, %d degismedi.%s" % (
            result.get("created", 0), result.get("removed", 0),
            result.get("kept", 0), extra)
    return "Tamamlandi"


# ---------------------------------------------------------------------------
# The jobs themselves
# ---------------------------------------------------------------------------

def job_scan(job_id: int) -> dict:
    settings = db.get_settings()
    if not settings.sources:
        raise RuntimeError("Once en az bir kaynak klasor eklemelisiniz.")

    def progress(done, total, detail):
        db.job_update(job_id, done=done, total=total or done, detail=detail)

    db.job_update(job_id, message="Klasorler taraniyor")
    return scanner.walk(settings.sources, progress=progress)


def job_analyse(job_id: int) -> dict:
    """Analyse, then finish the job properly.

    Finding faces and stopping there leaves the library in a half-done state -
    thousands of faces sitting in no group, and the user reasonably concluding
    that recognition is broken. Anything that produces faces must end with
    deduplication, grouping and matching, or it has not actually finished.
    """
    db.job_update(job_id, message="1/4 Fotograflar cozumleniyor")
    stats = pipeline.process_new(job_id=job_id)
    if db.job_cancelled(job_id):
        return stats

    db.job_update(job_id, message="2/4 Fotograf turleri ayriliyor", done=0, total=0)
    stats["kinds"] = kinds.classify_all()

    db.job_update(job_id, message="3/4 Kopyalar ayikleniyor")
    duped = scanner.dedupe()
    stats["exact"] = duped.get("exact", 0)
    stats["similar"] = duped.get("similar", 0)

    db.job_update(job_id, message="4/4 Yuzler grupleniyor")
    stats["assigned"] = clustering.assign_known().get("assigned", 0)
    stats["clusters"] = clustering.recluster(job_id=job_id).get("clusters", 0)
    return stats


def job_dedupe(job_id: int) -> dict:
    db.job_update(job_id, message="Kopyalar araniyor")

    def progress(done, total, detail):
        db.job_update(job_id, done=done, total=total, detail=detail)

    return scanner.dedupe(progress=progress)


def job_cluster(job_id: int) -> dict:
    db.job_update(job_id, message="Yuzler grupleniyor")

    def progress(done, total, detail):
        db.job_update(job_id, done=done, total=total, detail=detail)

    clustering.assign_known()
    return clustering.recluster(job_id=job_id, progress=progress)


def job_recognise(job_id: int) -> dict:
    db.job_update(job_id, message="Bilinen kisiler araniyor")

    def progress(done, total, detail):
        db.job_update(job_id, done=done, total=total, detail=detail)

    return clustering.assign_known(progress=progress)


def job_distribute(job_id: int) -> dict:
    db.job_update(job_id, message="Klasorler olusturuluyor")

    def progress(done, total, detail):
        db.job_update(job_id, done=done, total=total, detail=detail)

    return distribute.build(job_id=job_id, progress=progress)


def job_full(job_id: int) -> dict:
    """Scan, analyse, dedupe and group in one go - the main button."""
    settings = db.get_settings()
    if not settings.sources:
        raise RuntimeError("Once en az bir kaynak klasor eklemelisiniz.")

    db.job_update(job_id, message="1/4 Klasorler taraniyor")
    walked = scanner.walk(settings.sources)
    if db.job_cancelled(job_id):
        return walked

    db.job_update(job_id, message="2/4 Fotograflar cozumleniyor", done=0)
    analysed = pipeline.process_new(job_id=job_id)
    if db.job_cancelled(job_id):
        return analysed

    db.job_update(job_id, message="3/4 Kopyalar ayikleniyor", done=0)
    duped = scanner.dedupe()

    db.job_update(job_id, message="4/4 Yuzler grupleniyor", done=0)
    kinds.classify_all()
    clustering.assign_known()
    clustered = clustering.recluster(job_id=job_id)

    return {
        "added": walked.get("added", 0),
        "done": analysed.get("done", 0),
        "faces": analysed.get("faces", 0),
        "errors": analysed.get("errors", 0),
        "exact": duped.get("exact", 0),
        "similar": duped.get("similar", 0),
        "clusters": clustered.get("clusters", 0),
    }


JOBS = {
    "tarama": job_scan,
    "analiz": job_analyse,
    "kopya": job_dedupe,
    "gruplama": job_cluster,
    "tanima": job_recognise,
    "dagitim": job_distribute,
    "hepsi": job_full,
}
