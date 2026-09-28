"""Person names: comparing them, and turning a set of them into a folder name.

Turkish makes both jobs slightly awkward, and getting them wrong is visible to
the user straight away:

  * "Ayse Sahin" typed on a keyboard without Turkish letters and
    "Ayşe Şahin" imported from Google are the same person. Treating them as
    two splits a library in half for no reason.
  * `str.lower()` maps I to i, which is wrong in Turkish, and sorting by raw
    code points puts Ç, Ğ, İ, Ö, Ş and Ü after Z instead of where a reader
    looks for them.
"""

from __future__ import annotations

import re
import unicodedata

# Turkish letters folded to their ASCII lookalikes, in both cases.
_FOLD = str.maketrans({
    "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "I": "i",
    "İ": "i", "i": "i", "ö": "o", "Ö": "o", "ş": "s", "Ş": "s",
    "ü": "u", "Ü": "u", "â": "a", "Â": "a", "î": "i", "Î": "i",
    "û": "u", "Û": "u",
})

_SPACES = re.compile(r"\s+")


def fold(name: str) -> str:
    """A comparison key: case, Turkish letters and spacing all normalised.

    fold("Ayşe Şahin") == fold("ayse sahin") == "ayse sahin"
    """
    if not name:
        return ""
    text = unicodedata.normalize("NFC", name).translate(_FOLD)
    # Anything still non-ASCII (e.g. accents from other languages) is stripped
    # down rather than left to sort unpredictably.
    text = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )
    return _SPACES.sub(" ", text).strip().lower()


def same(a: str, b: str) -> bool:
    return bool(a) and fold(a) == fold(b)


def sort_key(name: str) -> str:
    """Alphabetical order that puts Turkish letters where a reader expects."""
    return fold(name)


def tidy(name: str) -> str:
    """Trim and collapse whitespace without changing the spelling the user chose."""
    return _SPACES.sub(" ", (name or "").strip())


def combo(names: list[str]) -> str:
    """Name a folder after a set of people, the way a person would say it.

    ['Alice']                  -> "Alice ve Bruno" becomes "Alice and Bruno"
    ['Alice', 'Bruno']          -> "Alice ve Bruno"  / "Alice and Bruno"
    ['Alice', 'Bruno', 'Clara'] -> "Alice, Bruno ve Clara" / "Alice, Bruno and Clara"

    The joining word follows the interface language, because the folder name
    is something the user reads every day in Explorer - an English speaker
    should not end up with a folder called "Alice ve Bruno".
    """
    ordered = sorted((n for n in names if n), key=sort_key)
    if not ordered:
        return ""
    if len(ordered) == 1:
        return ordered[0]
    # Imported here rather than at module level: i18n reads the database, and
    # names.py is imported by code that runs before the database exists.
    from . import i18n
    return "%s %s %s" % (", ".join(ordered[:-1]), i18n.t("folder.and"),
                         ordered[-1])
