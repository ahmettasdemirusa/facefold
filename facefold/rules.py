"""Category rules: deciding which photos belong in which folder.

This is the part that turns "Google Photos can filter by face" into "my disk
has a folder called 'Alice ve Bruno' that fills itself".

Rule types
----------
solo          only this person - a portrait, nobody else named
exact         exactly this set of people and no other named person
contains      these people are all present, others may be too
any           at least one of these people is present
nobody        no faces at all: landscapes, screenshots, documents
unknown_only  faces present but nobody recognised yet
all           every photo (useful as a date-sorted master folder)

Every rule also accepts filters: date range, face count, minimum face size,
and whether to include duplicates.

The size filter matters more than it looks. In a 15-person wedding photo both
Alice and Bruno are present, but the shot is not a photo *of* them - and an
"exact" rule would already exclude it because 13 other people are there. The
size filter handles the opposite case: a stranger's face in the background
should not stop a photo from counting as a portrait.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from typing import Any

from . import db

# The rule kinds the engine understands. Stored in the database and matched
# against in evaluate(), so these strings are part of the data format and must
# never be translated - what the user reads comes from rule_types() below.
RULE_TYPES = (
    "solo", "exact", "contains", "any", "crowd", "kind", "nobody",
    "unknown_only", "all",
)


def rule_types() -> dict[str, str]:
    """Rule kinds with the label the user actually sees, in their language."""
    from . import i18n
    return {key: i18n.t("rule.%s" % key) for key in RULE_TYPES}

DEFAULT_OPTIONS: dict[str, Any] = {
    "ignore_unknown": True,   # a stranger in the background does not break a rule
    "include_duplicates": False,
    "min_face_px": 0,         # ignore faces smaller than this when matching
    "min_faces": 0,
    "max_faces": 0,           # 0 = no limit
    "over_people": 0,         # 'crowd': more than this many named people
    "photo_kind": "",         # 'kind': camera | screenshot | received
    "exclude_kinds": [],      # photo kinds this rule ignores entirely
    "spilled_sets": [],       # 'crowd': person sets too rare for own folder
    "date_from": "",
    "date_to": "",
}


# ---------------------------------------------------------------------------
# Photo facts
# ---------------------------------------------------------------------------

def photo_facts(min_face_px: int = 0) -> dict[int, dict]:
    """Build 'who is in each photo' once, then evaluate every rule against it.

    Faces smaller than min_face_px are ignored entirely - they are background
    crowd, not subjects.
    """
    facts: dict[int, dict] = {}
    for row in db.q(
        "SELECT id, taken_at, face_count, duplicate_of, kind FROM photos "
        "WHERE state='done'"
    ):
        facts[row["id"]] = {
            "named": set(),
            "unknown": 0,
            "faces": 0,
            "taken_at": row["taken_at"],
            "is_duplicate": row["duplicate_of"] is not None,
            "kind": row["kind"],
            "manual": False,
            "person_px": {},
        }

    sql = "SELECT photo_id, person_id, face_px FROM faces"
    if min_face_px:
        sql += " WHERE face_px >= %d" % int(min_face_px)
    for row in db.q(sql):
        f = facts.get(row["photo_id"])
        if f is None:
            continue
        f["faces"] += 1
        if row["person_id"]:
            f["named"].add(row["person_id"])
            px = row["face_px"] or 0
            if px > f["person_px"].get(row["person_id"], 0):
                f["person_px"][row["person_id"]] = px
        else:
            f["unknown"] += 1

    # People attached by hand count exactly like a recognised face, so a photo
    # the detector could not read still lands in the right folder.
    for row in db.q("SELECT photo_id, person_id FROM photo_people"):
        f = facts.get(row["photo_id"])
        if f is not None:
            f["named"].add(row["person_id"])
            f["manual"] = True
    return facts


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def _passes_filters(fact: dict, opts: dict) -> bool:
    if fact["is_duplicate"] and not opts.get("include_duplicates"):
        return False

    excluded = opts.get("exclude_kinds") or []
    if excluded and fact.get("kind") in excluded and not fact["named"]:
        # A kind filter is there to keep strangers from adverts out of people
        # folders. Once the user has put a name on a photo, that judgement
        # outranks the guess about what kind of file it is - otherwise their
        # own correction is silently thrown away.
        return False

    min_faces = int(opts.get("min_faces") or 0)
    max_faces = int(opts.get("max_faces") or 0)
    if min_faces and fact["faces"] < min_faces:
        return False
    if max_faces and fact["faces"] > max_faces:
        return False

    taken = fact.get("taken_at") or ""
    date_from = (opts.get("date_from") or "").strip()
    date_to = (opts.get("date_to") or "").strip()
    if date_from and taken[:10] < date_from:
        return False
    if date_to and taken[:10] > date_to:
        return False
    return True


def _matches_rule(fact: dict, rule_type: str, people: set[int], opts: dict) -> bool:
    named = fact["named"]
    ignore_unknown = bool(opts.get("ignore_unknown", True))
    clean = ignore_unknown or fact["unknown"] == 0

    if rule_type == "all":
        return True
    if rule_type == "nobody":
        # A photo the user attached a name to is not "nobody", however few
        # faces the detector managed to read in it.
        return fact["faces"] == 0 and not named
    if rule_type == "unknown_only":
        return fact["faces"] > 0 and not named
    if rule_type == "kind":
        return fact.get("kind") == (opts.get("photo_kind") or "") and not named
    if rule_type == "crowd":
        # Group shots, so folder names do not grow into
        # "Alice, Bruno, Clara ve Emma". This bucket also catches combinations
        # too rare to earn a folder of their own - without that, those photos
        # would match no rule at all and quietly never be filed.
        if len(named) > int(opts.get("over_people") or 3):
            return True
        spilled = opts.get("spilled_sets") or []
        if spilled and named:
            return sorted(named) in [sorted(s) for s in spilled]
        return False
    if not people:
        return False
    if rule_type == "solo":
        return named == people and len(people) == 1 and clean
    if rule_type == "exact":
        return named == people and clean
    if rule_type == "contains":
        return people <= named
    if rule_type == "any":
        return bool(people & named)
    return False


def evaluate(category: dict | Any, facts: dict[int, dict] | None = None) -> list[int]:
    """Photo ids matching one category."""
    opts = dict(DEFAULT_OPTIONS)
    try:
        opts.update(json.loads(category["options"] or "{}"))
    except (json.JSONDecodeError, TypeError):
        pass
    people = set(json.loads(category["people"] or "[]"))
    rule_type = category["rule_type"]

    if facts is None:
        facts = photo_facts(int(opts.get("min_face_px") or 0))

    out = []
    for photo_id, fact in facts.items():
        if not _passes_filters(fact, opts):
            continue
        if _matches_rule(fact, rule_type, people, opts):
            out.append(photo_id)
    return out


def evaluate_all() -> dict[int, list[int]]:
    """Every enabled category -> its photo ids.

    Facts are cached per distinct min_face_px so a library is scanned once per
    distinct size filter rather than once per category.
    """
    cats = db.q("SELECT * FROM categories WHERE enabled=1 ORDER BY sort_order, id")
    cache: dict[int, dict] = {}
    result: dict[int, list[int]] = {}
    for cat in cats:
        try:
            opts = json.loads(cat["options"] or "{}")
        except (json.JSONDecodeError, TypeError):
            opts = {}
        key = int(opts.get("min_face_px") or 0)
        if key not in cache:
            cache[key] = photo_facts(key)
        result[cat["id"]] = evaluate(cat, cache[key])
    return result


def preview_count(rule_type: str, people: list[int], options: dict | None = None) -> int:
    """How many photos a rule would catch - shown live while editing."""
    fake = {
        "rule_type": rule_type,
        "people": json.dumps(people),
        "options": json.dumps(options or {}),
    }
    return len(evaluate(fake))


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

_SAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Windows refuses these as folder names whatever the extension, a leftover from
# DOS device files. A person really can be called "Con" or "Aux".
_RESERVED = {
    "con", "prn", "aux", "nul",
    *("com%d" % i for i in range(1, 10)),
    *("lpt%d" % i for i in range(1, 10)),
}


def safe_folder_name(name: str) -> str:
    """A Windows-legal folder name; keeps Turkish letters intact.

    Windows silently strips a trailing space or dot from a folder name at
    creation time. A category called "Alice ve Bruno " would therefore be built
    into "Alice ve Bruno", while everything downstream kept looking for the
    name with the space - so the folder existed but the app could never find,
    prune or update it again. Trimming has to survive the length cut too,
    which is why it happens after the truncation and not only before it.
    """
    cleaned = _SAFE_NAME.sub("-", name).strip()
    cleaned = cleaned[:120].strip().rstrip(". ")
    if cleaned.lower() in _RESERVED:
        cleaned += "-klasor"
    return cleaned or "Kategori"


def create_category(name: str, rule_type: str, people: list[int],
                    options: dict | None = None, folder: str | None = None) -> int:
    if rule_type not in RULE_TYPES:
        raise ValueError("Bilinmeyen kural: %s" % rule_type)
    opts = dict(DEFAULT_OPTIONS)
    opts.update(options or {})
    order = db.scalar("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM categories", (), 1)
    cur = db.execute(
        "INSERT INTO categories(name, rule_type, people, options, folder, sort_order) "
        "VALUES(?,?,?,?,?,?)",
        (name.strip(), rule_type, json.dumps(people),
         json.dumps(opts, ensure_ascii=False),
         folder or safe_folder_name(name), order),
    )
    return int(cur.lastrowid)


def update_category(cat_id: int, **fields) -> None:
    allowed = {"name", "rule_type", "people", "options", "folder", "enabled", "sort_order"}
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key in ("people", "options") and not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        sets.append("%s=?" % key)
        params.append(value)
    if not sets:
        return
    params.append(cat_id)
    db.execute("UPDATE categories SET %s WHERE id=?" % ", ".join(sets), params)


def delete_category(cat_id: int) -> None:
    db.execute("DELETE FROM categories WHERE id=?", (cat_id,))


def list_categories() -> list[dict]:
    out = []
    for row in db.q("SELECT * FROM categories ORDER BY sort_order, id"):
        item = dict(row)
        item["people_ids"] = json.loads(row["people"] or "[]")
        item["options_dict"] = json.loads(row["options"] or "{}")
        item["people_names"] = [
            r["name"] for r in db.q(
                "SELECT name FROM persons WHERE id IN (%s)"
                % ",".join("?" * len(item["people_ids"])),
                item["people_ids"],
            )
        ] if item["people_ids"] else []
        item["linked"] = db.scalar(
            "SELECT COUNT(*) FROM links WHERE category_id=?", (row["id"],)
        )
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------

def suggest_pairs(limit: int = 12, min_count: int = 3) -> list[dict]:
    """Which people actually appear together, and how often.

    This drives the 'Alice and Bruno are in 312 photos together - want a folder?'
    prompt, which is far more useful than making the user guess combinations.
    """
    per_photo: dict[int, set[int]] = defaultdict(set)
    for row in db.q(
        "SELECT f.photo_id, f.person_id FROM faces f "
        "JOIN photos p ON p.id=f.photo_id "
        "WHERE f.person_id IS NOT NULL AND p.duplicate_of IS NULL"
    ):
        per_photo[row["photo_id"]].add(row["person_id"])

    counter: Counter = Counter()
    for people in per_photo.values():
        if len(people) < 2:
            continue
        ordered = sorted(people)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                counter[(a, b)] += 1

    names = {r["id"]: r["name"] for r in db.q("SELECT id, name FROM persons")}
    out = []
    for (a, b), count in counter.most_common(limit):
        if count < min_count:
            break
        out.append({
            "people": [a, b],
            "names": [names.get(a, "?"), names.get(b, "?")],
            "count": count,
            "suggested_name": "%s ve %s" % (names.get(a, "?"), names.get(b, "?")),
        })
    return out


def suggest_solo(limit: int = 30) -> list[dict]:
    """Per-person portrait counts, for one-click 'make a folder for each person'."""
    facts = photo_facts()
    counts: Counter = Counter()
    for fact in facts.values():
        if fact["is_duplicate"]:
            continue
        if len(fact["named"]) == 1:
            counts[next(iter(fact["named"]))] += 1

    names = {r["id"]: r["name"] for r in db.q("SELECT id, name FROM persons")}
    return [
        {"person_id": pid, "name": names.get(pid, "?"), "count": count}
        for pid, count in counts.most_common(limit)
    ]
