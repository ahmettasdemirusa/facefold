"""Automatic foldering: naming people is the only configuration.

The idea, in one sentence: **a photo belongs to the folder named after exactly
the people in it.**

    Alice alone                 ->  Alice/
    Alice and Bruno together     ->  Alice ve Bruno/
    Alice, Bruno and Clara       ->  Alice, Bruno ve Clara/
    five named people           ->  Kalabalik/
    faces, but nobody named yet ->  Taninmayan kisiler/
    no faces at all             ->  Yuzsuz kareler/

That is the whole model. There is nothing for the user to configure beyond
putting names on face groups, and the arrangement gets richer on its own as
more people are named: the moment Bruno gets a name, every Alice+Bruno photo
moves out of Alice/ and into its own folder.

Implementation note for contributors
------------------------------------
This module does not distribute anything itself. It *generates category rows*
(marked origin='auto') that describe the partition, and the existing rules and
distribute modules do the rest unchanged. So there is one foldering engine, not
two, and a user can still inspect - or pin - any auto folder in the advanced
screen. Categories the user made by hand (origin='manual') are never touched.
"""

from __future__ import annotations

import json
from collections import Counter

from . import db, i18n, kinds, names, rules


# Folder names for the buckets that are not a specific set of people.
#
# These are functions rather than constants because the user reads them in
# Explorer every day: someone using the English interface should get "Crowd",
# not "Kalabalik". Switching language therefore renames these folders on the
# next build - distribute moves the files rather than leaving two copies.
def crowd_folder() -> str:
    return i18n.t("folder.crowd")


def unknown_folder() -> str:
    return i18n.t("folder.unknown")


def faceless_folder() -> str:
    return i18n.t("folder.faceless")


def screenshot_folder() -> str:
    return i18n.t("folder.screenshots")

# Above this many named people in one photo, use the crowd folder instead of
# stringing every name together.
DEFAULT_MAX_PEOPLE = 3

# A combination seen fewer times than this is not worth its own folder; those
# photos fall through to the crowd folder. 1 = give every combination a folder.
DEFAULT_MIN_PHOTOS = 1


# Folder naming lives in names.py so the import path and the UI agree with it.
combo_name = names.combo


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def plan(max_people: int = DEFAULT_MAX_PEOPLE,
         min_photos: int = DEFAULT_MIN_PHOTOS) -> dict:
    """Work out which folders the current naming implies.

    Returns the combinations that earn a folder plus counts for the three
    catch-all buckets, without writing anything.
    """
    person_names = {r["id"]: r["name"] for r in db.q("SELECT id, name FROM persons")}
    facts = rules.photo_facts()

    combos: Counter = Counter()
    crowd = unknown = faceless = shots = 0
    # Screenshots keep out of the people folders entirely. Their faces are
    # strangers from adverts and memes, and mixing them in would put a poster
    # of a singer into somebody's family folder.
    skip_kinds = {k for k in kinds.LABELS if k not in kinds.face_kinds()}

    for fact in facts.values():
        if fact["is_duplicate"]:
            continue
        if fact.get("kind") in skip_kinds and not fact["named"]:
            shots += 1
            continue
        named = fact["named"]
        if not named:
            if fact["faces"] == 0:
                faceless += 1
            else:
                unknown += 1
            continue
        if len(named) > max_people:
            crowd += 1
            continue
        combos[tuple(sorted(named))] += 1

    kept, spilled = [], 0
    spilled_sets: list[list[int]] = []
    for people, count in combos.most_common():
        if count < min_photos:
            # Too rare for a folder of its own. The photos still have to land
            # somewhere, so the crowd bucket is told which person-sets it must
            # also accept - otherwise they match no rule at all and are never
            # filed, while the plan still counts them.
            spilled += count
            spilled_sets.append(list(people))
            continue
        kept.append({
            "people": list(people),
            "names": [person_names.get(pid, "?") for pid in people],
            "folder": combo_name([person_names.get(pid, "?") for pid in people]),
            "count": count,
        })

    kept.sort(key=lambda c: (len(c["people"]), names.sort_key(c["folder"])))
    return {
        "combos": kept,
        "crowd": crowd + spilled,
        "unknown": unknown,
        "faceless": faceless,
        "screenshots": shots,
        "spilled_sets": spilled_sets,
        "skip_kinds": sorted(skip_kinds),
        "max_people": max_people,
        "total_folders": (len(kept) + bool(crowd + spilled) + bool(unknown)
                          + bool(faceless) + bool(shots)),
    }


# ---------------------------------------------------------------------------
# Writing the plan into categories
# ---------------------------------------------------------------------------

def sync(max_people: int = DEFAULT_MAX_PEOPLE,
         min_photos: int = DEFAULT_MIN_PHOTOS) -> dict:
    """Make the auto categories match what the current naming implies.

    Categories are matched by name rather than dropped and recreated, so a
    folder that still means the same thing keeps its id - and therefore its
    rows in the links table. Without that, every sync would look like "every
    category is new", and distribute would delete and relink every file on
    disk. Manual categories are never touched.
    """
    result = plan(max_people=max_people, min_photos=min_photos)

    desired: dict[str, tuple] = {}
    skip = result.get("skip_kinds") or []
    for combo in result["combos"]:
        desired[combo["folder"]] = ("exact", combo["people"], {"exclude_kinds": skip})
    if result["crowd"]:
        desired[crowd_folder()] = ("crowd", [], {
            "over_people": max_people,
            "exclude_kinds": skip,
            "spilled_sets": result.get("spilled_sets") or [],
        })
    if result["unknown"]:
        desired[unknown_folder()] = ("unknown_only", [], {"exclude_kinds": skip})
    if result["faceless"]:
        desired[faceless_folder()] = ("nobody", [], {"exclude_kinds": skip})
    if result.get("screenshots"):
        desired[screenshot_folder()] = ("kind", [], {"photo_kind": kinds.SCREENSHOT})

    existing = {
        r["name"]: r for r in db.q("SELECT * FROM categories WHERE origin='auto'")
    }
    # A folder name the user claimed by hand belongs to them, not to us.
    manual = {
        r["name"] for r in db.q("SELECT name FROM categories WHERE origin='manual'")
    } | {
        r["folder"] for r in db.q("SELECT folder FROM categories WHERE origin='manual'")
    }

    conn = db.connect()
    made = kept = dropped = 0

    for order, (name, (rule_type, people, options)) in enumerate(desired.items()):
        if name in manual or rules.safe_folder_name(name) in manual:
            continue
        opts = dict(rules.DEFAULT_OPTIONS)
        opts.update(options)
        payload = (rule_type, json.dumps(people),
                   json.dumps(opts, ensure_ascii=False),
                   rules.safe_folder_name(name), order)
        row = existing.pop(name, None)
        if row is None:
            conn.execute(
                "INSERT INTO categories(name, rule_type, people, options, folder, "
                "sort_order, origin, enabled) VALUES(?,?,?,?,?,?,'auto',1)",
                (name,) + payload,
            )
            made += 1
        else:
            conn.execute(
                "UPDATE categories SET rule_type=?, people=?, options=?, folder=?, "
                "sort_order=?, enabled=1 WHERE id=?",
                payload + (row["id"],),
            )
            kept += 1

    # Whatever is left in `existing` no longer describes anything. It is
    # switched off rather than deleted: deleting the row would cascade its
    # links away, and the files those links point at would then be orphaned on
    # disk with nothing left to say they should go. distribute prunes disabled
    # categories first and cleanup_disabled() removes the rows afterwards.
    for row in existing.values():
        conn.execute("UPDATE categories SET enabled=0 WHERE id=?", (row["id"],))
        dropped += 1

    conn.commit()
    result.update(created=made, unchanged=kept, removed=dropped)
    return result


def cleanup_disabled() -> int:
    """Drop auto categories that are switched off and no longer own any files.

    Called after distribute has pruned their output, so by this point there is
    nothing left on disk that refers to them.
    """
    rows = db.q(
        "SELECT c.id FROM categories c WHERE c.origin='auto' AND c.enabled=0 "
        "AND NOT EXISTS (SELECT 1 FROM links l WHERE l.category_id = c.id)"
    )
    if not rows:
        return 0
    conn = db.connect()
    for row in rows:
        conn.execute("DELETE FROM categories WHERE id=?", (row["id"],))
    conn.commit()
    return len(rows)


def is_enabled() -> bool:
    return bool(db.get_kv("auto_folders", True))


def set_enabled(value: bool) -> None:
    db.set_kv("auto_folders", bool(value))


def settings() -> dict:
    return {
        "enabled": is_enabled(),
        "max_people": int(db.get_kv("auto_max_people", DEFAULT_MAX_PEOPLE)),
        "min_photos": int(db.get_kv("auto_min_photos", DEFAULT_MIN_PHOTOS)),
    }


def sync_from_settings() -> dict:
    """Re-plan using the stored preferences; called before every build."""
    cfg = settings()
    if not cfg["enabled"]:
        # Switching automatic folders off has to actually switch them off.
        # Returning early left every existing auto category enabled, so the
        # next build rebuilt all of them and the setting looked broken. They
        # are disabled rather than deleted, exactly as in sync(): distribute
        # prunes their files first, then cleanup_disabled() drops the rows.
        cur = db.execute(
            "UPDATE categories SET enabled=0 WHERE origin='auto' AND enabled=1"
        )
        off = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        return {"combos": [], "created": 0, "crowd": 0, "unknown": 0,
                "faceless": 0, "total_folders": 0, "skipped": True,
                "removed": off}
    return sync(max_people=cfg["max_people"], min_photos=cfg["min_photos"])
