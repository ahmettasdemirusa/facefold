"""How far along the user is, and what naming the next group would actually do.

A real library produces hundreds of face groups. Showing them as an equal grid
of cards implies they all matter equally, and they very much do not: in one
12,000-photo library the biggest 15 groups covered 30% of all faces while 296
groups of five faces or fewer covered another 30% between them - almost all of
them strangers seen once.

Nobody should name 317 groups. They should name the dozen that are their
family and stop. For that the interface has to answer two questions the user
cannot answer themselves:

    "Is this group worth naming?"   -> how many photos it would file
    "Am I done?"                    -> what share of the library is sorted

Everything here is read-only arithmetic over counts that already exist.
"""

from __future__ import annotations

from . import db, kinds

# Groups at or above this share of the library are worth putting in front of
# the user first; the tail is offered as one "these are strangers" action.
BIG_GROUP_MIN = 5


def overview() -> dict:
    """One picture of where the library stands."""
    total_photos = db.scalar(
        "SELECT COUNT(*) FROM photos WHERE state='done' AND duplicate_of IS NULL"
    )
    kind_filter = kinds.face_sql_filter("p")

    # Faces that can belong to someone the user knows (screenshots excluded).
    relevant = db.scalar(
        "SELECT COUNT(*) FROM faces f JOIN photos p ON p.id=f.photo_id "
        "WHERE %s" % kind_filter
    )
    named = db.scalar(
        "SELECT COUNT(*) FROM faces f JOIN photos p ON p.id=f.photo_id "
        "WHERE f.person_id IS NOT NULL AND %s" % kind_filter
    )

    # Photos that ended up somewhere meaningful: a person is in them.
    sorted_photos = db.scalar(
        "SELECT COUNT(DISTINCT p.id) FROM photos p WHERE p.duplicate_of IS NULL AND ("
        "  EXISTS (SELECT 1 FROM faces f WHERE f.photo_id=p.id AND f.person_id IS NOT NULL)"
        "  OR EXISTS (SELECT 1 FROM photo_people pp WHERE pp.photo_id=p.id))"
    )

    groups = db.q(
        "SELECT id, size FROM clusters WHERE person_id IS NULL AND ignored=0 "
        "ORDER BY size DESC"
    )
    waiting = sum(g["size"] for g in groups)
    big = [g for g in groups if g["size"] >= BIG_GROUP_MIN]
    tail = [g for g in groups if g["size"] < BIG_GROUP_MIN]

    return {
        "photos": total_photos,
        "sorted_photos": sorted_photos,
        "photo_percent": _pct(sorted_photos, total_photos),
        "faces": relevant,
        "named_faces": named,
        "face_percent": _pct(named, relevant),
        "persons": db.scalar("SELECT COUNT(*) FROM persons"),
        "groups": len(groups),
        "big_groups": len(big),
        "tail_groups": len(tail),
        "tail_faces": sum(g["size"] for g in tail),
        "waiting_faces": waiting,
        # What the next few groups would add, so "is it worth going on?" has
        # a number behind it.
        "next_gain": _pct(sum(g["size"] for g in big[:10]), relevant),
    }


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def group_value(cluster_id: int) -> dict:
    """What naming one group would actually accomplish."""
    faces = db.scalar("SELECT COUNT(*) FROM faces WHERE cluster_id=?", (cluster_id,))
    photos = db.scalar(
        "SELECT COUNT(DISTINCT photo_id) FROM faces WHERE cluster_id=?", (cluster_id,)
    )
    # Photos where this group is the only unnamed face - those become a clean
    # single-person folder the moment it gets a name.
    alone = db.scalar(
        "SELECT COUNT(*) FROM (SELECT f.photo_id FROM faces f WHERE f.cluster_id=? "
        "GROUP BY f.photo_id HAVING COUNT(*) = ("
        "  SELECT COUNT(*) FROM faces g WHERE g.photo_id = f.photo_id))",
        (cluster_id,),
    )
    span = db.q1(
        "SELECT MIN(p.taken_at) AS first, MAX(p.taken_at) AS last "
        "FROM faces f JOIN photos p ON p.id=f.photo_id WHERE f.cluster_id=?",
        (cluster_id,),
    )
    return {
        "faces": faces,
        "photos": photos,
        "alone": alone,
        "first_seen": (span["first"] or "")[:10] if span else "",
        "last_seen": (span["last"] or "")[:10] if span else "",
    }


def ranked_groups(limit: int = 40, offset: int = 0) -> list[dict]:
    """Unnamed groups, most worth naming first, with what each would file."""
    rows = db.q(
        "SELECT c.id, c.size, "
        " (SELECT COUNT(DISTINCT f.photo_id) FROM faces f WHERE f.cluster_id=c.id) AS photos, "
        " (SELECT MIN(p.taken_at) FROM faces f JOIN photos p ON p.id=f.photo_id "
        "   WHERE f.cluster_id=c.id) AS first_seen, "
        " (SELECT MAX(p.taken_at) FROM faces f JOIN photos p ON p.id=f.photo_id "
        "   WHERE f.cluster_id=c.id) AS last_seen "
        "FROM clusters c WHERE c.person_id IS NULL AND c.ignored=0 "
        "ORDER BY c.size DESC LIMIT ? OFFSET ?",
        (limit, offset),
    )
    return [dict(r) for r in rows]


def ignore_tail(below: int = BIG_GROUP_MIN) -> int:
    """Hide every group smaller than `below` - the one-off strangers.

    Nothing is deleted and nothing is decided permanently: the groups are only
    hidden from the naming screen, and un-hiding them is one setting away.
    """
    cur = db.execute(
        "UPDATE clusters SET ignored=1 WHERE person_id IS NULL AND ignored=0 AND size<?",
        (below,),
    )
    return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def unignore_all() -> int:
    cur = db.execute("UPDATE clusters SET ignored=0 WHERE ignored=1")
    return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


# ---------------------------------------------------------------------------
# Person health
# ---------------------------------------------------------------------------

def person_health(person_id: int) -> dict:
    """How consistent a person's faces are with each other.

    A person the user built by hand can end up as a mixed bag - it happens
    when a group was named too eagerly, or used as a holding pen. The average
    similarity to the person's own centre is a direct measurement of that, so
    the app can point it out instead of leaving the user to notice a stranger
    in their family folder.
    """
    import numpy as np

    rows = db.q(
        "SELECT id, embedding FROM faces WHERE person_id=? AND embedding IS NOT NULL",
        (person_id,),
    )
    if len(rows) < 2:
        return {"faces": len(rows), "score": 1.0, "odd": [], "mixed": False}

    vecs = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    centre = vecs.mean(axis=0)
    norm = np.linalg.norm(centre)
    if norm:
        centre = centre / norm
    sims = vecs @ centre

    odd = [int(rows[i]["id"]) for i in np.argsort(sims)[:12] if sims[i] < 0.45]
    return {
        "faces": len(rows),
        "score": round(float(sims.mean()), 3),
        "worst": round(float(sims.min()), 3),
        "odd": odd,
        # Below this the group is not one person any more. A clean person sits
        # around 0.72-0.81 in practice; a mixed bag around 0.44.
        "mixed": bool(sims.mean() < 0.60),
    }


def all_person_health() -> list[dict]:
    out = []
    for row in db.q("SELECT id, name FROM persons ORDER BY name"):
        health = person_health(row["id"])
        health.update(id=row["id"], name=row["name"])
        out.append(health)
    return out
