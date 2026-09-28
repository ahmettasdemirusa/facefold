"""The local web interface.

Runs on 127.0.0.1 only. Nothing is exposed to the network and nothing leaves
the machine; the browser is used purely because clicking through face groups
is far nicer than a command line.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   send_file, url_for)

from .. import (__version__, archives, auto, clustering, config, db, distribute,
                faces as face_mod, i18n, kinds, names as name_util, pipeline,
                progress, rules, scanner, takeout, trash)
from . import jobs


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["JSON_AS_ASCII"] = False
    config.ensure_dirs()
    db.init_db()
    _seed_defaults()

    # ------------------------------------------------------------------
    # Template helpers
    # ------------------------------------------------------------------

    @app.context_processor
    def inject() -> dict:
        return {
            "version": __version__,
            "counts": pipeline.counts(),
            "job": jobs.current(),
            "settings": db.get_settings(),
            "rule_types": rules.rule_types(),
            "link_modes": distribute.link_modes(),
            # Interface text. `t` is short on purpose: it appears in every
            # template on almost every line, and a long name would bury the
            # sentence it is wrapping.
            "t": i18n.t,
            "lang": i18n.current(),
            "languages": i18n.LANGUAGES,
            # app.js cannot reach the dictionary, so base.html hands it the
            # sentences it needs. Only the "js." ones: no reason to ship the
            # whole interface to the browser on every page.
            "js_strings": {
                key: i18n.t(key)
                for key in i18n.STRINGS
                if key.startswith("js.")
            },
        }

    @app.template_filter("say")
    def say(count: int, one: str, many: str | None = None) -> str:
        return "%s %s" % ("{:,}".format(count).replace(",", "."), many or one)

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    @app.route("/")
    def panel():
        settings = db.get_settings()
        recent = db.q("SELECT * FROM jobs ORDER BY id DESC LIMIT 8")
        warnings = []
        if not settings.sources:
            warnings.append(("kaynak", i18n.t("panel.warn_no_source")))
        if not face_mod.model_is_downloaded():
            warnings.append(("model", i18n.t("panel.warn_model")))
        if settings.sources and settings.link_mode == "hardlink":
            ok, msg = distribute.hardlink_supported(settings.sources[0], settings.output_dir)
            if not ok:
                warnings.append(("baglanti", msg))
        return render_template(
            "panel.html", recent=recent, warnings=warnings,
            summary=distribute.output_summary(),
            auto_plan=auto.plan() if db.scalar("SELECT COUNT(*) FROM persons") else None,
            auto_on=auto.is_enabled(),
            state=progress.overview(),
            kind_summary=kinds.summary(),
        )

    @app.route("/kaynaklar")
    def kaynaklar():
        settings = db.get_settings()
        info = []
        for src in settings.sources:
            path = Path(src)
            info.append({
                "path": src,
                "exists": path.exists(),
                "photos": db.scalar(
                    "SELECT COUNT(*) FROM photos WHERE source_root=?", (src,)
                ),
                "in_archives": db.scalar(
                    "SELECT COUNT(*) FROM photos WHERE source_root=? AND archive IS NOT NULL",
                    (src,),
                ),
            })
        return render_template(
            "kaynaklar.html", sources=info,
            takeout_preview=takeout.preview() if settings.sources else None,
        )

    @app.route("/kisiler")
    def kisiler():
        persons = db.q(
            "SELECT p.*, "
            " (SELECT COUNT(*) FROM faces f WHERE f.person_id=p.id) AS face_count, "
            " (SELECT COUNT(DISTINCT f.photo_id) FROM faces f WHERE f.person_id=p.id) AS photo_count, "
            " (SELECT thumb FROM faces WHERE id=p.cover_face_id) AS cover "
            "FROM persons p ORDER BY face_count DESC"
        )
        page = max(1, int(request.args.get("s", 1)))
        per_page = 30
        groups = progress.ranked_groups(per_page, (page - 1) * per_page)
        hidden = db.scalar("SELECT COUNT(*) FROM clusters WHERE ignored=1")
        loose = db.scalar(
            "SELECT COUNT(*) FROM faces WHERE person_id IS NULL AND cluster_id IS NULL"
        )
        return render_template(
            "kisiler.html", persons=persons, groups=groups,
            hidden=hidden, loose=loose, page=page, per_page=per_page,
            state=progress.overview(),
            merges=clustering.suggest_person_merges(),
            health={h["id"]: h for h in progress.all_person_health()},
            tail_min=progress.BIG_GROUP_MIN,
        )

    @app.route("/grup/<int:cluster_id>")
    def grup(cluster_id: int):
        cluster = db.q1("SELECT * FROM clusters WHERE id=?", (cluster_id,))
        if not cluster:
            abort(404)
        members = db.q(
            "SELECT f.*, p.file_name, p.taken_at FROM faces f "
            "JOIN photos p ON p.id=f.photo_id WHERE f.cluster_id=? "
            "ORDER BY f.face_px DESC LIMIT 200",
            (cluster_id,),
        )
        return render_template(
            "grup.html", cluster=cluster, members=members,
            persons=db.q("SELECT id, name FROM persons ORDER BY name"),
            similar=clustering.similar_clusters(cluster_id),
        )

    @app.route("/kisi/<int:person_id>")
    def kisi(person_id: int):
        person = db.q1("SELECT * FROM persons WHERE id=?", (person_id,))
        if not person:
            abort(404)
        # The faces, not just the photos: a wrong face can only be taken out of
        # a person if the user can see and pick the face itself.
        person_faces = db.q(
            "SELECT f.*, p.file_name, p.taken_at, p.id AS photo_id "
            "FROM faces f JOIN photos p ON p.id = f.photo_id "
            "WHERE f.person_id=? ORDER BY f.face_px DESC LIMIT 400",
            (person_id,),
        )
        # Photos reached through a face, plus ones the user attached by hand.
        photos = db.q(
            "SELECT DISTINCT p.* FROM photos p "
            "WHERE EXISTS (SELECT 1 FROM faces f WHERE f.photo_id=p.id AND f.person_id=?) "
            "   OR EXISTS (SELECT 1 FROM photo_people pp WHERE pp.photo_id=p.id "
            "              AND pp.person_id=?) "
            "ORDER BY p.taken_at DESC LIMIT 300",
            (person_id, person_id),
        )
        manual = set(clustering.manual_photos(person_id))
        return render_template(
            "kisi.html", person=person, photos=photos, person_faces=person_faces,
            manual_photos=manual,
            suggestions=clustering.suggestions_for_person(person_id),
            others=db.q("SELECT id, name FROM persons WHERE id<>? ORDER BY name",
                        (person_id,)),
            face_count=db.scalar("SELECT COUNT(*) FROM faces WHERE person_id=?",
                                 (person_id,)),
            trash_ok=trash.available(),
        )

    @app.route("/tekil")
    def tekil():
        """Faces that ended up in no group - usually people seen once or twice."""
        page = max(1, int(request.args.get("s", 1)))
        per_page = 120
        total = db.scalar(
            "SELECT COUNT(*) FROM faces WHERE person_id IS NULL AND cluster_id IS NULL"
        )
        loose = db.q(
            "SELECT f.*, p.file_name, p.taken_at, p.camera, p.id AS photo_id, p.ext "
            "FROM faces f JOIN photos p ON p.id = f.photo_id "
            "WHERE f.person_id IS NULL AND f.cluster_id IS NULL "
            "ORDER BY f.face_px DESC LIMIT ? OFFSET ?",
            (per_page, (page - 1) * per_page),
        )
        return render_template(
            "tekil.html", faces=loose, total=total, page=page, per_page=per_page,
            persons=db.q("SELECT id, name FROM persons ORDER BY name"),
        )

    @app.route("/kategoriler")
    def kategoriler():
        return render_template(
            "kategoriler.html",
            categories=rules.list_categories(),
            auto_plan=auto.plan(),
            auto_settings=auto.settings(),
            persons=db.q("SELECT id, name FROM persons ORDER BY name"),
            pair_ideas=rules.suggest_pairs(),
            solo_ideas=rules.suggest_solo(),
        )

    def _photo_filter(person_id: int | None, only: str) -> tuple[str, list]:
        """The WHERE clause behind the photos page.

        Shared with the id-list endpoint so "select everything matching this
        filter" cannot drift away from what the page is showing.
        """
        where, params = ["p.state='done'"], []
        if person_id:
            where.append(
                "(EXISTS (SELECT 1 FROM faces f WHERE f.photo_id=p.id AND f.person_id=?) "
                " OR EXISTS (SELECT 1 FROM photo_people pp WHERE pp.photo_id=p.id "
                "            AND pp.person_id=?))"
            )
            params += [person_id, person_id]
        if only == "kopya":
            where.append("p.duplicate_of IS NOT NULL")
        elif only == "yuzsuz":
            where.append("p.face_count=0")
        elif only == "taninmayan":
            where.append(
                "p.face_count>0 AND NOT EXISTS "
                "(SELECT 1 FROM faces f WHERE f.photo_id=p.id AND f.person_id IS NOT NULL) "
                "AND NOT EXISTS (SELECT 1 FROM photo_people pp WHERE pp.photo_id=p.id)"
            )
        else:
            where.append("p.duplicate_of IS NULL")
        return " AND ".join(where), params

    @app.get("/api/fotograf/idler")
    def api_photo_ids():
        """Every photo id matching the current filter, for 'select all'."""
        clause, params = _photo_filter(
            request.args.get("kisi", type=int), request.args.get("tur", "")
        )
        rows = db.q("SELECT p.id FROM photos p WHERE " + clause, params)
        return jsonify(ok=True, ids=[r["id"] for r in rows])

    @app.route("/fotograflar")
    def fotograflar():
        page = max(1, int(request.args.get("s", 1)))
        per_page = 60
        person_id = request.args.get("kisi", type=int)
        only = request.args.get("tur", "")

        clause, params = _photo_filter(person_id, only)
        total = db.scalar("SELECT COUNT(*) FROM photos p WHERE " + clause, params)
        rows = db.q(
            "SELECT p.* FROM photos p WHERE " + clause +
            " ORDER BY p.taken_at DESC, p.id DESC LIMIT ? OFFSET ?",
            params + [per_page, (page - 1) * per_page],
        )
        return render_template(
            "fotograflar.html", photos=rows, page=page, per_page=per_page,
            total=total, person_id=person_id, only=only,
            persons=db.q("SELECT id, name FROM persons ORDER BY name"),
        )

    @app.route("/fotograf/<int:photo_id>")
    def fotograf(photo_id: int):
        photo = db.q1("SELECT * FROM photos WHERE id=?", (photo_id,))
        if not photo:
            abort(404)
        return render_template(
            "fotograf.html", photo=photo,
            faces=db.q(
                "SELECT f.*, pr.name FROM faces f "
                "LEFT JOIN persons pr ON pr.id=f.person_id "
                "WHERE f.photo_id=? ORDER BY f.face_px DESC", (photo_id,)),
            persons=db.q("SELECT id, name FROM persons ORDER BY name"),
            copies=db.q("SELECT * FROM photos WHERE duplicate_of=?", (photo_id,)),
            categories=db.q(
                "SELECT c.name FROM links l JOIN categories c ON c.id=l.category_id "
                "WHERE l.photo_id=?", (photo_id,)),
        )

    @app.route("/kopyalar")
    def kopyalar():
        return render_template("kopyalar.html", groups=scanner.duplicate_groups())

    @app.route("/ayarlar")
    def ayarlar():
        settings = db.get_settings()
        ok, msg = (True, "")
        if settings.sources:
            ok, msg = distribute.hardlink_supported(settings.sources[0], settings.output_dir)
        return render_template("ayarlar.html", link_check=(ok, msg),
                               model_ready=face_mod.model_is_downloaded(),
                               lang_coverage=i18n.coverage(),
                               errors=db.q(
                                   "SELECT id, file_name, error FROM photos "
                                   "WHERE state='error' LIMIT 50"))

    @app.post("/dil")
    def dil():
        """Switch the interface language."""
        i18n.set_language((request.form.get("lang") or "").strip())
        # Back where they were, so changing the language does not also lose
        # the user's place.
        return redirect(request.form.get("next") or url_for("ayarlar"))

    # ------------------------------------------------------------------
    # Media
    # ------------------------------------------------------------------

    @app.route("/kucuk/<int:photo_id>")
    def kucuk(photo_id: int):
        row = db.q1("SELECT thumb FROM photos WHERE id=?", (photo_id,))
        if not row or not row["thumb"] or not Path(row["thumb"]).exists():
            abort(404)
        return send_file(row["thumb"], mimetype="image/jpeg")

    @app.route("/yuz/<int:face_id>")
    def yuz(face_id: int):
        row = db.q1("SELECT thumb FROM faces WHERE id=?", (face_id,))
        if not row or not row["thumb"] or not Path(row["thumb"]).exists():
            abort(404)
        return send_file(row["thumb"], mimetype="image/jpeg")

    @app.route("/grup-kapak/<int:cluster_id>")
    def grup_kapak(cluster_id: int):
        """Cover image for an unnamed group: its largest, sharpest face."""
        row = db.q1(
            "SELECT thumb FROM faces WHERE cluster_id=? AND thumb IS NOT NULL "
            "AND thumb<>'' ORDER BY face_px DESC LIMIT 1",
            (cluster_id,),
        )
        if not row or not Path(row["thumb"]).exists():
            abort(404)
        return send_file(row["thumb"], mimetype="image/jpeg")

    @app.route("/tam/<int:photo_id>")
    def tam(photo_id: int):
        row = db.q1("SELECT path FROM photos WHERE id=?", (photo_id,))
        if not row or not Path(row["path"]).exists():
            abort(404)
        return send_file(row["path"])

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    @app.post("/api/is/<kind>")
    def api_job(kind: str):
        fn = jobs.JOBS.get(kind)
        if fn is None:
            return jsonify(ok=False, error=i18n.t("api.unknown_job")), 400
        ok, message = jobs.start(kind, fn)
        return jsonify(ok=ok, message=message)

    @app.post("/api/is/durdur")
    def api_job_cancel():
        jobs.cancel()
        return jsonify(ok=True)

    @app.get("/api/durum")
    def api_status():
        return jsonify(job=jobs.current(), counts=pipeline.counts())

    @app.post("/api/kaynak/ekle")
    def api_source_add():
        path = (request.form.get("path") or "").strip().strip('"')
        settings = db.get_settings()
        if not path:
            return redirect(url_for("kaynaklar"))
        if not Path(path).exists():
            return render_template("kaynaklar.html", sources=[
                {"path": s, "exists": Path(s).exists(),
                 "photos": db.scalar("SELECT COUNT(*) FROM photos WHERE source_root=?", (s,))}
                for s in settings.sources
            ], error=i18n.t("kaynaklar.not_found", path=path))
        if path not in settings.sources:
            settings.sources.append(path)
            db.save_settings(settings)
        return redirect(url_for("kaynaklar"))

    @app.post("/api/kaynak/sil")
    def api_source_remove():
        path = request.form.get("path") or ""
        settings = db.get_settings()
        settings.sources = [s for s in settings.sources if s != path]
        db.save_settings(settings)
        # Forget what that folder contributed, otherwise its faces keep shaping
        # the groups and keep appearing under people's names.
        scanner.forget_source(path)
        return redirect(url_for("kaynaklar"))

    @app.post("/api/grup/<int:cluster_id>/isim")
    def api_name_cluster(cluster_id: int):
        name = request.form.get("name") or request.json.get("name", "") if request.is_json \
            else request.form.get("name", "")
        try:
            person_id = clustering.name_cluster(cluster_id, name)
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 400
        if request.is_json:
            return jsonify(ok=True, person_id=person_id)
        return redirect(url_for("kisiler"))

    @app.post("/api/gruplar/kuyrugu-gizle")
    def api_ignore_tail():
        """Hide the long tail of tiny groups - the people seen once."""
        below = int((request.get_json(silent=True) or {}).get("below")
                    or progress.BIG_GROUP_MIN)
        return jsonify(ok=True, hidden=progress.ignore_tail(below))

    @app.post("/api/gruplar/gizlileri-goster")
    def api_unignore():
        return jsonify(ok=True, shown=progress.unignore_all())

    @app.post("/api/kisi/birlestirme-onerisi")
    def api_apply_merge():
        data = request.get_json(silent=True) or {}
        keep, drop = data.get("keep_id"), data.get("drop_id")
        if not keep or not drop:
            return jsonify(ok=False, error=i18n.t("api.no_person")), 400
        clustering.merge_persons(int(keep), int(drop))
        return jsonify(ok=True)

    @app.post("/api/tarihleri-onar")
    def api_repair_dates():
        result = pipeline.repair_dates()
        result["takeout"] = takeout.import_all().get("dates", 0)
        return jsonify(ok=True, **result)

    @app.post("/api/grup/<int:cluster_id>/gizle")
    def api_ignore_cluster(cluster_id: int):
        clustering.ignore_cluster(cluster_id, True)
        return jsonify(ok=True)

    @app.post("/api/grup/birlestir")
    def api_merge_clusters():
        data = request.get_json(silent=True) or {}
        clustering.merge_clusters(int(data.get("target")), [int(x) for x in data.get("others", [])])
        return jsonify(ok=True)

    @app.post("/api/yuz/ata")
    def api_assign_faces():
        data = request.get_json(silent=True) or {}
        person_id = data.get("person_id")
        count = clustering.assign_faces(
            [int(x) for x in data.get("face_ids", [])],
            int(person_id) if person_id else None,
        )
        return jsonify(ok=True, count=count)

    @app.post("/api/kisi/olustur")
    def api_create_person():
        """Turn a hand-picked set of faces into a new person."""
        data = request.get_json(silent=True) or {}
        name = name_util.tidy(data.get("name", ""))
        face_ids = [int(x) for x in (data.get("face_ids") or [])]
        if not name:
            return jsonify(ok=False, error=i18n.t("api.name_empty")), 400
        if not face_ids:
            return jsonify(ok=False, error=i18n.t("api.no_face")), 400
        person_id = clustering.get_or_create_person(name)
        count = clustering.assign_faces(face_ids, person_id)
        # A fresh name is worth spreading straight away.
        grown = clustering.grow_person(person_id)
        return jsonify(ok=True, person_id=person_id, count=count, grown=grown)

    @app.post("/api/kisi/<int:person_id>/ad")
    def api_rename_person(person_id: int):
        try:
            clustering.rename_person(person_id, request.form.get("name", ""))
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 400
        return redirect(url_for("kisi", person_id=person_id))

    @app.post("/api/kisi/<int:person_id>/birlestir")
    def api_merge_person(person_id: int):
        other = request.form.get("other", type=int)
        if other:
            clustering.merge_persons(person_id, other)
        return redirect(url_for("kisi", person_id=person_id))

    @app.post("/api/kisi/<int:person_id>/sil")
    def api_delete_person(person_id: int):
        clustering.delete_person(person_id)
        return redirect(url_for("kisiler"))

    @app.post("/api/kategori/ekle")
    def api_category_add():
        payload = request.get_json(silent=True)
        if payload is not None:
            name = payload.get("name", "")
            rule_type = payload.get("rule_type", "solo")
            people = [int(x) for x in (payload.get("people") or [])]
            options = payload.get("options") or {}
        else:
            # HTML form: checkboxes arrive as repeated fields, and the option
            # inputs are flat rather than nested.
            name = request.form.get("name", "")
            rule_type = request.form.get("rule_type", "solo")
            people = [int(x) for x in request.form.getlist("people") if x.strip()]
            options = _options_from_form(request.form)

        try:
            cat_id = rules.create_category(name.strip(), rule_type, people, options)
        except Exception as exc:  # noqa: BLE001
            return jsonify(ok=False, error=str(exc)), 400
        if payload is not None:
            return jsonify(ok=True, id=cat_id)
        return redirect(url_for("kategoriler"))

    @app.post("/api/kategori/<int:cat_id>/guncelle")
    def api_category_update(cat_id: int):
        data = request.get_json(silent=True) or request.form
        fields = {}
        if "name" in data:
            fields["name"] = data["name"]
            fields["folder"] = rules.safe_folder_name(data["name"])
        if "rule_type" in data:
            fields["rule_type"] = data["rule_type"]
        if "enabled" in data:
            fields["enabled"] = 1 if str(data["enabled"]) in ("1", "true", "on") else 0
        if "people" in data:
            if hasattr(data, "getlist"):
                people = data.getlist("people")     # repeated form fields
            else:
                people = data["people"]
            if isinstance(people, str):
                people = [x for x in people.split(",") if x.strip()]
            fields["people"] = [int(x) for x in people]
        if "options" in data:
            opts = data["options"]
            fields["options"] = json.loads(opts) if isinstance(opts, str) else opts
        rules.update_category(cat_id, **fields)
        if request.is_json:
            return jsonify(ok=True)
        return redirect(url_for("kategoriler"))

    @app.post("/api/kategori/<int:cat_id>/sil")
    def api_category_delete(cat_id: int):
        rules.delete_category(cat_id)
        return redirect(url_for("kategoriler"))

    @app.post("/api/kategori/onizleme")
    def api_category_preview():
        data = request.get_json(silent=True) or {}
        try:
            count = rules.preview_count(
                data.get("rule_type", "solo"),
                [int(x) for x in data.get("people", [])],
                data.get("options") or {},
            )
        except Exception as exc:  # noqa: BLE001
            return jsonify(ok=False, error=str(exc)), 400
        return jsonify(ok=True, count=count)

    @app.post("/api/ayarlar")
    def api_settings():
        settings = db.get_settings()
        form = request.form
        settings.output_dir = (form.get("output_dir") or settings.output_dir).strip()
        settings.link_mode = form.get("link_mode", settings.link_mode)
        settings.date_subfolders = form.get("date_subfolders") == "on"
        settings.language = form.get("language", settings.language)
        for key, cast in (("min_det_score", float), ("min_face_px", int),
                          ("cluster_distance", float), ("match_strong", float),
                          ("match_weak", float), ("detect_max_side", int),
                          ("workers", int)):
            raw = form.get(key)
            if raw not in (None, ""):
                try:
                    setattr(settings, key, cast(raw))
                except ValueError:
                    pass
        db.save_settings(settings)
        return redirect(url_for("ayarlar"))

    # ---- attaching people to photos by hand ----------------------------

    @app.post("/api/fotograf/kisi-ata")
    def api_assign_photos():
        """Link selected photos to a person, with or without a detected face."""
        data = request.get_json(silent=True) or {}
        photo_ids = [int(x) for x in (data.get("photo_ids") or [])]
        if not photo_ids:
            return jsonify(ok=False, error=i18n.t("api.no_photo")), 400

        person_id = data.get("person_id")
        new_name = name_util.tidy(data.get("new_name", ""))
        if new_name:
            person_id = clustering.get_or_create_person(new_name)
        if not person_id:
            return jsonify(ok=False, error=i18n.t("api.no_person")), 400
        person_id = int(person_id)

        count = clustering.assign_photos(photo_ids, person_id)

        # Faces the detector *did* find in those photos belong to this person
        # too when there is only one of them - which is the usual case for a
        # photo the user is pointing at.
        attached = 0
        conn = db.connect()
        for pid in photo_ids:
            faces_here = db.q(
                "SELECT id FROM faces WHERE photo_id=? AND person_id IS NULL", (pid,)
            )
            if len(faces_here) == 1:
                conn.execute(
                    "UPDATE faces SET person_id=?, assign_source='manual', "
                    "confidence=1.0 WHERE id=?",
                    (person_id, faces_here[0]["id"]),
                )
                attached += 1
        conn.commit()

        grown = clustering.grow_person(person_id) if attached else 0
        person = db.q1("SELECT name FROM persons WHERE id=?", (person_id,))
        return jsonify(ok=True, person_id=person_id,
                       name=person["name"] if person else "",
                       count=count, attached=attached, grown=grown)

    @app.post("/api/fotograf/kisi-kaldir")
    def api_unassign_photos():
        data = request.get_json(silent=True) or {}
        photo_ids = [int(x) for x in (data.get("photo_ids") or [])]
        removed = clustering.unassign_photos(photo_ids, data.get("person_id"))
        return jsonify(ok=True, removed=removed)

    @app.post("/api/eslestir")
    def api_match_now():
        """Re-run recognition against every named person.

        Runs inline rather than as a background job: it is pure vector maths
        over embeddings that already exist, so it finishes in seconds even on a
        large library, and it stays usable while a scan is going.
        """
        result = clustering.assign_known()
        return jsonify(ok=True, **result)

    # ---- deleting photos (always to the recycle bin) -------------------

    @app.post("/api/fotograf/sil")
    def api_delete_photos():
        data = request.get_json(silent=True) or {}
        ids = [int(x) for x in (data.get("photo_ids") or [])]

        # Named selections, so the UI does not have to send thousands of ids.
        which = data.get("which")
        if which == "person" and data.get("person_id"):
            ids = trash.photo_ids_for_person(int(data["person_id"]))
        elif which == "duplicates":
            # Byte-identical only. A visual match is a judgement and should be
            # looked at on the duplicates screen, not deleted by one click.
            ids = trash.photo_ids_for_duplicates("exact")
        elif which == "faceless":
            ids = trash.photo_ids_faceless()

        if not ids:
            return jsonify(ok=False, error=i18n.t("api.no_photo_delete")), 400
        if data.get("preview"):
            return jsonify(ok=True, preview=trash.preview(ids))
        try:
            result = trash.delete_photos(ids)
        except trash.TrashUnavailable as exc:
            return jsonify(ok=False, error=str(exc)), 400
        return jsonify(ok=True, **result)

    # ---- Google Photos Takeout -----------------------------------------

    @app.get("/api/takeout/onizleme")
    def api_takeout_preview():
        return jsonify(ok=True, **takeout.preview())

    @app.post("/api/takeout/aktar")
    def api_takeout_import():
        result = takeout.import_all()
        return jsonify(ok=True, **result)

    # ---- automatic foldering -------------------------------------------

    @app.get("/api/otomatik/plan")
    def api_auto_plan():
        cfg = auto.settings()
        return jsonify(ok=True, settings=cfg,
                       plan=auto.plan(cfg["max_people"], cfg["min_photos"]))

    @app.post("/api/otomatik")
    def api_auto_settings():
        form = request.form if request.form else (request.get_json(silent=True) or {})
        auto.set_enabled(str(form.get("enabled", "")) in ("1", "true", "on"))
        for key, kv in (("max_people", "auto_max_people"),
                        ("min_photos", "auto_min_photos")):
            raw = form.get(key)
            if raw not in (None, ""):
                try:
                    db.set_kv(kv, max(1, int(raw)))
                except ValueError:
                    pass
        if request.is_json:
            return jsonify(ok=True, settings=auto.settings())
        return redirect(url_for("kategoriler"))

    @app.post("/api/yeniden-analiz")
    def api_reanalyse():
        pipeline.reset_faces()
        return redirect(url_for("panel"))

    @app.post("/api/klasor-ac")
    def api_open_folder():
        """Open a folder in Windows Explorer - the natural 'show me' action."""
        path = (request.form.get("path") or request.json.get("path", "")) \
            if request.is_json else request.form.get("path", "")
        target = Path(path)
        if not target.exists():
            return jsonify(ok=False, error=i18n.t("api.folder_missing")), 404
        try:
            if sys.platform == "win32":
                os.startfile(str(target))  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except Exception as exc:  # noqa: BLE001
            return jsonify(ok=False, error=str(exc)), 500
        return jsonify(ok=True)

    return app


def _options_from_form(form) -> dict:
    """Collect the flat rule-option inputs an HTML form sends into one dict."""
    return {
        "ignore_unknown": form.get("ignore_unknown") == "on",
        "include_duplicates": form.get("include_duplicates") == "on",
        "min_face_px": int(form.get("min_face_px") or 0),
        "date_from": (form.get("date_from") or "").strip(),
        "date_to": (form.get("date_to") or "").strip(),
    }


def _seed_defaults() -> None:
    """First run: put sensible starting settings in place."""
    if db.get_kv("seeded"):
        return
    settings = db.get_settings()
    if not settings.output_dir:
        settings.output_dir = str(config.DEFAULT_OUTPUT_DIR)
    db.save_settings(settings)
    db.set_kv("seeded", True)
