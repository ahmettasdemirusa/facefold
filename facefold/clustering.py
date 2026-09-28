"""Grouping faces into people.

Two jobs live here:

  * turning a pile of unlabelled face vectors into clean groups the user can
    name once ("this group of 340 faces is Alice"), and
  * recognising an already-named person in photos added later, so naming is a
    one-time cost rather than a chore after every phone dump.

Clustering runs in two stages so it stays fast on large libraries:

  1. a greedy pass with a tight threshold collapses near-identical shots
     (burst photos, the same moment) into micro-groups - O(n x k);
  2. average-linkage agglomerative clustering runs on the far smaller set of
     micro-group centroids, which is where the real grouping decisions happen.

Doing agglomerative directly on 50k faces would need a 20 GB distance matrix;
this gets essentially the same result for a fraction of the cost.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable

import numpy as np

from . import config, db, faces as face_mod, kinds, names as name_util

ProgressFn = Callable[[int, int, str], None]

# Faces at least this similar are the same shot/session - safe to collapse.
MICRO_THRESHOLD = 0.75

# Above this many micro-groups, the full distance matrix stops being sensible.
MAX_AGGLOMERATIVE = 6000


# ---------------------------------------------------------------------------
# Loading vectors
# ---------------------------------------------------------------------------

def _load_faces(where: str, params: tuple = (),
                respect_kind: bool = True) -> tuple[list[int], np.ndarray]:
    """Face vectors matching a condition.

    Photo kinds the user excluded (screenshots by default) are left out, so
    strangers from adverts and memes never reach the naming screen.
    """
    clause = where
    if respect_kind:
        clause = "(%s) AND %s" % (where, kinds.face_sql_filter("p"))
    rows = db.q(
        "SELECT f.id, f.embedding FROM faces f "
        "JOIN photos p ON p.id = f.photo_id "
        "WHERE f.embedding IS NOT NULL AND " + clause,
        params,
    )
    ids = [r["id"] for r in rows]
    if not ids:
        return [], np.zeros((0, 512), dtype=np.float32)
    vecs = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    return ids, vecs


def person_centroid(person_id: int) -> np.ndarray | None:
    """The 'average face' of a person, built from confirmed faces only."""
    rows = db.q(
        "SELECT embedding FROM faces WHERE person_id=? AND embedding IS NOT NULL",
        (person_id,),
    )
    if not rows:
        return None
    vecs = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    return face_mod.centroid(vecs)


def person_centroids() -> tuple[list[int], np.ndarray]:
    ids: list[int] = []
    mats: list[np.ndarray] = []
    for row in db.q("SELECT id FROM persons ORDER BY id"):
        c = person_centroid(row["id"])
        if c is not None:
            ids.append(row["id"])
            mats.append(c)
    if not mats:
        return [], np.zeros((0, 512), dtype=np.float32)
    return ids, np.stack(mats)


# ---------------------------------------------------------------------------
# Recognising known people in new photos
# ---------------------------------------------------------------------------

def assign_known(threshold: float | None = None, progress: ProgressFn | None = None) -> dict:
    """Attach unassigned faces to already-named people when confident.

    Only fires above the strong threshold. Anything less certain is left for
    the review screen rather than being guessed at - a wrong auto-assignment is
    far more annoying than an unsorted face.
    """
    settings = db.get_settings()
    thr = threshold if threshold is not None else settings.match_strong

    pids, centroids = person_centroids()
    if not pids:
        return {"assigned": 0, "checked": 0, "persons": 0}

    ids, vecs = _load_faces("f.person_id IS NULL")
    if not ids:
        return {"assigned": 0, "checked": 0, "persons": len(pids)}

    sims = vecs @ centroids.T           # (faces, persons)
    best = sims.argmax(axis=1)
    best_sim = sims.max(axis=1)

    conn = db.connect()
    assigned = 0
    for i, face_id in enumerate(ids):
        if best_sim[i] >= thr:
            conn.execute(
                "UPDATE faces SET person_id=?, assign_source='auto', confidence=? "
                "WHERE id=?",
                (pids[best[i]], float(best_sim[i]), face_id),
            )
            assigned += 1
        if progress and i % 500 == 0:
            progress(i, len(ids), "taniniyor")
    conn.commit()
    return {"assigned": assigned, "checked": len(ids), "persons": len(pids)}


def suggestions_for_person(person_id: int, limit: int = 60) -> list[dict]:
    """Faces in the 'maybe' band - shown as "is this also them?" prompts."""
    settings = db.get_settings()
    centroid = person_centroid(person_id)
    if centroid is None:
        return []

    ids, vecs = _load_faces("f.person_id IS NULL")
    if not ids:
        return []

    sims = vecs @ centroid
    order = np.argsort(-sims)
    out = []
    for idx in order[: limit * 3]:
        s = float(sims[idx])
        if s < settings.match_weak:
            break
        if s >= settings.match_strong:
            continue                     # already handled by assign_known
        row = db.q1(
            "SELECT f.id, f.thumb, f.age, f.gender, p.file_name, p.thumb AS photo_thumb "
            "FROM faces f JOIN photos p ON p.id=f.photo_id WHERE f.id=?",
            (int(ids[idx]),),
        )
        if row:
            item = dict(row)
            item["similarity"] = round(s, 3)
            out.append(item)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------

def _micro_groups(vecs: np.ndarray, threshold: float = MICRO_THRESHOLD) -> list[list[int]]:
    """Greedy collapse of near-identical vectors. Returns index groups."""
    n = len(vecs)
    unused = np.ones(n, dtype=bool)
    groups: list[list[int]] = []
    order = np.arange(n)

    for i in order:
        if not unused[i]:
            continue
        sims = vecs[unused] @ vecs[i]
        idx_map = np.flatnonzero(unused)
        take = idx_map[sims >= threshold]
        groups.append([int(j) for j in take])
        unused[take] = False
    return groups


def _agglomerate(centroids: np.ndarray, distance: float) -> np.ndarray:
    """Average-linkage clustering on cosine distance. Returns labels."""
    from sklearn.cluster import AgglomerativeClustering

    if len(centroids) == 1:
        return np.zeros(1, dtype=int)
    model = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=distance,
        metric="cosine",
        linkage="average",
    )
    return model.fit_predict(centroids)


def _incremental(centroids: np.ndarray, distance: float) -> np.ndarray:
    """Fallback for very large libraries: online centroid matching."""
    threshold = 1.0 - distance
    labels = np.full(len(centroids), -1, dtype=int)
    reps: list[np.ndarray] = []
    counts: list[int] = []

    for i, vec in enumerate(centroids):
        if reps:
            mat = np.stack(reps)
            sims = mat @ vec
            best = int(sims.argmax())
            if sims[best] >= threshold:
                labels[i] = best
                counts[best] += 1
                reps[best] = face_mod.normalise(
                    reps[best] * (counts[best] - 1) / counts[best] + vec / counts[best]
                )
                continue
        labels[i] = len(reps)
        reps.append(vec.copy())
        counts.append(1)
    return labels


def recluster(job_id: int | None = None, progress: ProgressFn | None = None) -> dict:
    """Rebuild groups for every face that is not yet assigned to a person.

    Named people are never touched: naming is the user's work and clustering
    must not undo it.
    """
    settings = db.get_settings()
    distance = float(settings.cluster_distance or config.CLUSTER_DISTANCE)

    ids, vecs = _load_faces("f.person_id IS NULL")
    stats = {"faces": len(ids), "clusters": 0, "loose": 0}
    if not ids:
        db.execute("DELETE FROM clusters WHERE person_id IS NULL")
        return stats

    if progress:
        progress(0, 3, "benzer kareler birlestiriliyor")
    micro = _micro_groups(vecs)
    micro_centroids = np.stack([face_mod.centroid(vecs[g]) for g in micro])

    if progress:
        progress(1, 3, "%d grup cozumleniyor" % len(micro_centroids))
    if len(micro_centroids) <= MAX_AGGLOMERATIVE:
        labels = _agglomerate(micro_centroids, distance)
    else:
        labels = _incremental(micro_centroids, distance)

    # Expand micro-group labels back to individual faces.
    face_labels: dict[int, int] = {}
    for group_idx, group in enumerate(micro):
        label = int(labels[group_idx])
        for face_idx in group:
            face_labels[face_idx] = label

    by_label: dict[int, list[int]] = defaultdict(list)
    for face_idx, label in face_labels.items():
        by_label[label].append(face_idx)

    if progress:
        progress(2, 3, "kaydediliyor")

    conn = db.connect()

    # "Hide this group" is a decision, and a decision has to survive the next
    # scan. Rebuilding throws every unnamed group away, so the faces the user
    # hid are remembered here and the judgement is carried onto whichever new
    # group they end up in. Without this, hiding hundreds of one-off strangers
    # was undone by the next photo import.
    hidden_faces = {
        r["id"] for r in db.q(
            "SELECT f.id FROM faces f JOIN clusters c ON c.id = f.cluster_id "
            "WHERE c.ignored = 1 AND c.person_id IS NULL"
        )
    }

    conn.execute("UPDATE faces SET cluster_id=NULL WHERE person_id IS NULL")
    conn.execute("DELETE FROM clusters WHERE person_id IS NULL")

    min_size = config.MIN_CLUSTER_SIZE
    for label, members in sorted(by_label.items(), key=lambda kv: -len(kv[1])):
        member_ids = [ids[i] for i in members]
        if len(member_ids) < min_size:
            stats["loose"] += len(member_ids)
            continue
        centroid = face_mod.centroid(vecs[members])
        # A regrouped set stays hidden when it is still mostly the same faces
        # the user hid. A simple majority is the right test: a hidden group
        # that absorbed a few new faces is the same stranger, while one that
        # was mostly rebuilt from new faces deserves to be looked at again.
        was_hidden = sum(1 for fid in member_ids if fid in hidden_faces)
        ignored = 1 if was_hidden * 2 > len(member_ids) else 0
        cur = conn.execute(
            "INSERT INTO clusters(person_id, size, centroid, ignored) VALUES(NULL, ?, ?, ?)",
            (len(member_ids), db.pack_vec(centroid), ignored),
        )
        cluster_id = cur.lastrowid
        conn.executemany(
            "UPDATE faces SET cluster_id=? WHERE id=?",
            [(cluster_id, fid) for fid in member_ids],
        )
        stats["clusters"] += 1
        stats["kept_hidden"] = stats.get("kept_hidden", 0) + ignored
    conn.commit()
    return stats


# ---------------------------------------------------------------------------
# User actions
# ---------------------------------------------------------------------------

def find_person(name: str) -> int | None:
    """Look a person up the way a human would read the name.

    "Ayse Sahin" typed without Turkish letters and "Ayşe Şahin"
    imported from Google are the same person; SQL's NOCASE does not know that,
    so the comparison happens here.
    """
    key = name_util.fold(name)
    if not key:
        return None
    for row in db.q("SELECT id, name FROM persons"):
        if name_util.fold(row["name"]) == key:
            return row["id"]
    return None


def get_or_create_person(name: str, notes: str | None = None) -> int:
    name = name_util.tidy(name)
    if not name:
        raise ValueError("Isim bos olamaz")
    found = find_person(name)
    if found is not None:
        return found
    return int(
        db.execute("INSERT INTO persons(name, notes) VALUES(?,?)", (name, notes)).lastrowid
    )


def name_cluster(cluster_id: int, name: str) -> int:
    """Give a group a name. Creates the person if new, merges if the name exists."""
    name = name_util.tidy(name)
    if not name:
        raise ValueError("Isim bos olamaz")

    found = find_person(name)
    if found is not None:
        person_id = found
    else:
        person_id = int(db.execute("INSERT INTO persons(name) VALUES(?)", (name,)).lastrowid)

    conn = db.connect()
    conn.execute(
        "UPDATE faces SET person_id=?, assign_source='cluster', confidence=1.0 "
        "WHERE cluster_id=?",
        (person_id, cluster_id),
    )
    conn.execute("UPDATE clusters SET person_id=? WHERE id=?", (person_id, cluster_id))
    conn.commit()
    _refresh_cover(person_id)
    # Naming is meant to be a one-time act: the moment a group has a name, any
    # other face of that person - in a different group, or loose because they
    # were only seen once there - should attach itself without the user having
    # to run anything.
    grow_person(person_id)
    return person_id


def grow_person(person_id: int, threshold: float | None = None) -> int:
    """Attach unassigned faces that clearly belong to this person.

    Same rule as assign_known, but for one person, so it is cheap enough to run
    every time a name is given.
    """
    centroid = person_centroid(person_id)
    if centroid is None:
        return 0
    settings = db.get_settings()
    thr = threshold if threshold is not None else settings.match_strong

    ids, vecs = _load_faces("f.person_id IS NULL")
    if not ids:
        return 0

    sims = vecs @ centroid
    conn = db.connect()
    grown = 0
    for i, face_id in enumerate(ids):
        if sims[i] >= thr:
            conn.execute(
                "UPDATE faces SET person_id=?, assign_source='auto', confidence=? "
                "WHERE id=?",
                (person_id, float(sims[i]), face_id),
            )
            grown += 1
    if grown:
        conn.commit()
        _refresh_cover(person_id)
    return grown


def assign_faces(face_ids: list[int], person_id: int | None) -> int:
    """Manually attach (or detach with None) specific faces."""
    if not face_ids:
        return 0
    conn = db.connect()
    conn.executemany(
        "UPDATE faces SET person_id=?, assign_source='manual', confidence=1.0 WHERE id=?",
        [(person_id, fid) for fid in face_ids],
    )
    conn.commit()
    if person_id:
        _refresh_cover(person_id)
        # Confirming faces by hand sharpens the person's average face, so it is
        # worth looking again for others that now clear the bar.
        grow_person(person_id)
    return len(face_ids)


def assign_photos(photo_ids: list[int], person_id: int) -> int:
    """Say "this person is in these photos" without going through a face.

    The detector misses people constantly - turned away, too far back, half out
    of frame, or a photo that is *of* someone without a readable face. Those
    photos are the ones the user can see are wrong, so there has to be a way to
    say so directly. The link counts as a recognised face everywhere else.
    """
    if not photo_ids or not person_id:
        return 0
    conn = db.connect()
    conn.executemany(
        "INSERT INTO photo_people(photo_id, person_id) VALUES(?,?) "
        "ON CONFLICT(photo_id, person_id) DO NOTHING",
        [(int(pid), int(person_id)) for pid in photo_ids],
    )
    conn.commit()
    return len(photo_ids)


def unassign_photos(photo_ids: list[int], person_id: int | None = None) -> int:
    """Undo a hand-made link. Detected faces are untouched."""
    if not photo_ids:
        return 0
    conn = db.connect()
    marks = ",".join("?" * len(photo_ids))
    params = [int(p) for p in photo_ids]
    sql = "DELETE FROM photo_people WHERE photo_id IN (%s)" % marks
    if person_id:
        sql += " AND person_id=?"
        params.append(int(person_id))
    cur = conn.execute(sql, params)
    conn.commit()
    return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def manual_photos(person_id: int) -> list[int]:
    return [
        r["photo_id"] for r in db.q(
            "SELECT photo_id FROM photo_people WHERE person_id=?", (person_id,)
        )
    ]


def merge_persons(keep_id: int, drop_id: int) -> None:
    """Fold one person into another - the fix for 'the child aged' splits."""
    if keep_id == drop_id:
        return
    conn = db.connect()
    conn.execute("UPDATE faces SET person_id=? WHERE person_id=?", (keep_id, drop_id))
    conn.execute("UPDATE clusters SET person_id=? WHERE person_id=?", (keep_id, drop_id))
    # Photos the user attached by hand live in their own table, and that table
    # cascades on person deletion. Without carrying them across first, merging
    # two people silently destroys every correction the user made for photos
    # the detector could not read - the exact work that is hardest to redo.
    # An INSERT rather than an UPDATE, because the same photo may already be
    # linked to both people and the primary key would collide.
    conn.execute(
        "INSERT INTO photo_people(photo_id, person_id) "
        "SELECT photo_id, ? FROM photo_people WHERE person_id=? "
        "ON CONFLICT(photo_id, person_id) DO NOTHING",
        (keep_id, drop_id),
    )
    conn.execute("DELETE FROM persons WHERE id=?", (drop_id,))
    conn.commit()
    _refresh_cover(keep_id)


def rename_person(person_id: int, name: str) -> None:
    name = name_util.tidy(name)
    if not name:
        raise ValueError("Isim bos olamaz")
    existing = find_person(name)
    if existing is not None and existing != person_id:
        merge_persons(existing, person_id)
        return
    db.execute("UPDATE persons SET name=? WHERE id=?", (name, person_id))


def delete_person(person_id: int) -> None:
    """Unname a person; their faces go back into the unnamed pool."""
    conn = db.connect()
    conn.execute(
        "UPDATE faces SET person_id=NULL, assign_source=NULL, confidence=NULL "
        "WHERE person_id=?",
        (person_id,),
    )
    conn.execute("UPDATE clusters SET person_id=NULL WHERE person_id=?", (person_id,))
    conn.execute("DELETE FROM persons WHERE id=?", (person_id,))
    conn.commit()


def ignore_cluster(cluster_id: int, ignored: bool = True) -> None:
    """Hide a group (strangers in the background, posters, reflections)."""
    db.execute("UPDATE clusters SET ignored=? WHERE id=?", (1 if ignored else 0, cluster_id))


def merge_clusters(target_id: int, other_ids: list[int]) -> None:
    """Combine unnamed groups that are obviously the same person."""
    if not other_ids:
        return
    conn = db.connect()
    for other in other_ids:
        if other == target_id:
            continue
        conn.execute("UPDATE faces SET cluster_id=? WHERE cluster_id=?", (target_id, other))
        conn.execute("DELETE FROM clusters WHERE id=?", (other,))
    size = db.scalar("SELECT COUNT(*) FROM faces WHERE cluster_id=?", (target_id,))
    conn.execute("UPDATE clusters SET size=? WHERE id=?", (size, target_id))
    conn.commit()


def _refresh_cover(person_id: int) -> None:
    """Pick the sharpest, most frontal, largest face as the person's avatar."""
    row = db.q1(
        "SELECT id FROM faces WHERE person_id=? AND thumb IS NOT NULL AND thumb<>'' "
        "ORDER BY (face_px * 1.0) * (1 + COALESCE(blur,0)/500.0) "
        "         * (CASE WHEN ABS(COALESCE(yaw,0)) < 20 THEN 1.5 ELSE 1.0 END) DESC "
        "LIMIT 1",
        (person_id,),
    )
    if row:
        db.execute("UPDATE persons SET cover_face_id=? WHERE id=?", (row["id"], person_id))


def suggest_person_merges(threshold: float = 0.45) -> list[dict]:
    """People who are probably the same person under two names.

    This happens constantly and the user cannot be expected to spot it:
    Google's export calls someone "Ayse" while the user named their face
    group "Ayşe Şahin", and the library quietly splits in two.
    Comparing average faces catches it whatever the spelling - including cases
    a name comparison never could, like a nickname or a maiden name.
    """
    pids, centroids = person_centroids()
    if len(pids) < 2:
        return []

    names = {r["id"]: r["name"] for r in db.q("SELECT id, name FROM persons")}
    counts = {
        r["person_id"]: r["n"] for r in db.q(
            "SELECT person_id, COUNT(*) n FROM faces WHERE person_id IS NOT NULL "
            "GROUP BY person_id"
        )
    }

    sims = centroids @ centroids.T
    out = []
    for i in range(len(pids)):
        for j in range(i + 1, len(pids)):
            score = float(sims[i, j])
            if score < threshold:
                continue
            a, b = pids[i], pids[j]
            # Offer to keep whichever already has more faces.
            keep, drop = (a, b) if counts.get(a, 0) >= counts.get(b, 0) else (b, a)
            out.append({
                "keep_id": keep,
                "drop_id": drop,
                "keep_name": names.get(keep, "?"),
                "drop_name": names.get(drop, "?"),
                "keep_faces": counts.get(keep, 0),
                "drop_faces": counts.get(drop, 0),
                "similarity": round(score, 3),
                "confident": score >= 0.55,
            })
    out.sort(key=lambda d: -d["similarity"])
    return out


def similar_clusters(cluster_id: int, limit: int = 8) -> list[dict]:
    """Other unnamed groups closest to this one - merge candidates."""
    row = db.q1("SELECT centroid FROM clusters WHERE id=?", (cluster_id,))
    if not row or not row["centroid"]:
        return []
    ref = np.frombuffer(row["centroid"], dtype=np.float32)

    out = []
    for other in db.q(
        "SELECT id, size, centroid FROM clusters "
        "WHERE person_id IS NULL AND ignored=0 AND id<>? AND centroid IS NOT NULL",
        (cluster_id,),
    ):
        vec = np.frombuffer(other["centroid"], dtype=np.float32)
        out.append({"id": other["id"], "size": other["size"],
                    "similarity": round(float(np.dot(ref, vec)), 3)})
    out.sort(key=lambda d: -d["similarity"])
    return out[:limit]
