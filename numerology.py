"""Pythagorean numerology: the core numbers, computed deterministically.

Rules used (the common Pythagorean method; other systems arrive with report styles):
- Letters A-Z map to 1-9 in sequence (A=1 ... I=9, J=1 ... R=9, S=1 ... Z=8).
- Accents are removed before calculating (José -> JOSE); other characters are ignored.
- Numbers are reduced by adding digits until one digit remains, except the master
  numbers 11, 22 and 33, which are kept.
- Life Path: month, day and year are each reduced first, then added and reduced.
- Practitioner styles (see STYLE_DEFAULTS) choose the method (classic, or Karma / Destiny /
  Personality), whether names are totalled whole or reduced per name first, and the Y rule.
- Expression: every letter of the full birth name.
- Soul Urge: vowels (A, E, I, O, U). Personality: all other letters. Y counts as a consonant,
  except in a name part with no other vowel (Lynn, Glynn), where Y is that part's vowel.
- If a name has no letters in a group (e.g. no consonants at all), that number is 0: it is
  shown as "not active" rather than given a meaning.
- Birthday: the day of the month, reduced (11 and 22 kept).
"""
from __future__ import annotations

import datetime as dt
import json
import unicodedata
from dataclasses import dataclass

MASTER_NUMBERS = (11, 22, 33)
VOWELS = set("AEIOU")


def letter_value(letter: str) -> int:
    return (ord(letter) - ord("A")) % 9 + 1


# Latin letters whose Unicode name doesn't end in a plain A-Z letter.
_LATIN_NAMED = {
    "SHARP S": "SS", "AE": "AE", "OE": "OE", "ETH": "D", "THORN": "TH", "ENG": "NG",
    "SCHWA": "E", "TURNED E": "E", "REVERSED E": "E", "OPEN E": "E", "OPEN O": "O",
    "DOTLESS I": "I", "DOTLESS J": "J", "LONG S": "S", "YOGH": "Y", "WYNN": "W", "KRA": "K",
    "EZH": "Z", "GLOTTAL STOP": "",
    # Latin letters named after Greek ones
    "ALPHA": "A", "BETA": "B", "GAMMA": "G", "DELTA": "D", "EPSILON": "E", "IOTA": "I",
    "LAMBDA": "L", "OMEGA": "O", "UPSILON": "U", "PHI": "PH", "CHI": "CH", "THETA": "TH",
}


def is_name_letter(ch: str) -> bool:
    """Alphabetic, but not a modifier letter such as the ʻokina (ʻ) or ʼ, which act as apostrophes."""
    return ch.isalpha() and unicodedata.category(ch) != "Lm"


def _latin_to_ascii(ch: str) -> str | None:
    """A-Z spelling of one Latin letter, '' for letters with no sound value, None if not Latin."""
    if "A" <= ch.upper() <= "Z" and ch.isascii():
        return ch.upper()
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return None
    if not name.startswith("LATIN "):
        return None
    if " LIGATURE " in name:  # Œ, Ĳ ...: the ligature's letters, in order
        letters = name.split(" LIGATURE ", 1)[1].split(" WITH ")[0]
        return letters if letters.isalpha() and letters.isascii() else None
    if " LETTER " not in name:
        return None
    base = name.split(" LETTER ", 1)[1]
    base = base.split(" WITH ")[0]
    for prefix in ("SMALL CAPITAL ", "CAPITAL ", "SMALL "):
        if base.startswith(prefix):
            base = base[len(prefix):]
    if len(base) == 1 and "A" <= base <= "Z":
        return base
    for key, value in _LATIN_NAMED.items():
        if base == key or base.endswith(" " + key) or base.startswith(key + " "):
            return value
    last = base.split()[-1]  # e.g. "AFRICAN D", "INSULAR G", "TURNED V"
    if len(last) == 1 and "A" <= last <= "Z":
        return last
    return None


def unsupported_letters(name: str) -> set[str]:
    """Letters we can't spell in A-Z (e.g. Cyrillic or Greek); the form asks for a Latin spelling."""
    decomposed = unicodedata.normalize("NFKD", name)
    return {ch for ch in decomposed
            if is_name_letter(ch) and not unicodedata.combining(ch) and _latin_to_ascii(ch) is None}


def letters_of(name: str) -> str:
    """Upper-case A-Z letters of a name: accents removed, other Latin letters transliterated."""
    decomposed = unicodedata.normalize("NFKD", name)
    out = []
    for ch in decomposed:
        if unicodedata.combining(ch) or not is_name_letter(ch):
            continue
        spelled = _latin_to_ascii(ch)
        if spelled:
            out.append(spelled)
    return "".join(out)


def name_parts(full_name: str) -> list[str]:
    """The name's parts as typed, split at anything that isn't a letter or accent (spaces, hyphens, apostrophes)."""
    parts, current = [], []
    for ch in unicodedata.normalize("NFKD", full_name):
        if is_name_letter(ch) or unicodedata.combining(ch):
            current.append(ch)
        elif current:
            parts.append("".join(current))
            current = []
    if current:
        parts.append("".join(current))
    return parts


def _split_part(letters: str, y_rule: str) -> tuple[str, str]:
    if y_rule == "always":
        y_is_vowel = True
    elif y_rule == "never":
        y_is_vowel = False
    else:  # standard: Y is a vowel only in a part with no other vowel (Lynn, Glynn)
        y_is_vowel = not any(ch in VOWELS for ch in letters)
    vowels = "".join(ch for ch in letters if ch in VOWELS or (ch == "Y" and y_is_vowel))
    consonants = "".join(ch for ch in letters if not (ch in VOWELS or (ch == "Y" and y_is_vowel)))
    return vowels, consonants


def name_groups(full_name: str, y_rule: str = "standard") -> list[tuple[str, str, str]]:
    """(all letters, vowels, consonants) for each name part."""
    groups = []
    for part in name_parts(full_name):
        letters = letters_of(part)
        if letters:
            groups.append((letters, *_split_part(letters, y_rule)))
    return groups


def split_vowels(full_name: str, y_rule: str = "standard") -> tuple[str, str]:
    """Vowel and consonant letters of the whole name (see name_groups for the Y rule)."""
    groups = name_groups(full_name, y_rule)
    return "".join(g[1] for g in groups), "".join(g[2] for g in groups)


def reduce(number: int, keep_master: bool = True) -> int:
    while number > 9 and not (keep_master and number in MASTER_NUMBERS):
        number = sum(int(digit) for digit in str(number))
    return number


def _letters_total(letters: str) -> int:
    return sum(letter_value(ch) for ch in letters)


def name_total(parts: list[str], totalling: str = "whole") -> int:
    """Total of a letter group across the name parts, before the final reduction.

    whole: add every letter of the full name, then reduce (master numbers in the total survive).
    per_name: reduce each name part first, then add the reduced parts.
    """
    sums = [_letters_total(letters) for letters in parts if letters]
    if totalling == "per_name":
        return sum(reduce(value) for value in sums)
    return sum(sums)


def _name_number(letters: str) -> int:
    return reduce(_letters_total(letters)) if letters else 0


@dataclass(frozen=True)
class CoreNumbers:
    life_path: int
    expression: int
    soul_urge: int
    personality: int
    birthday: int

    def as_dict(self) -> dict[str, int]:
        return {key: getattr(self, key) for key in NUMBER_ORDER}


NUMBER_ORDER = ("life_path", "expression", "soul_urge", "personality", "birthday")

NUMBER_LABELS = {
    "life_path": "Life Path",
    "expression": "Expression",
    "soul_urge": "Soul Urge",
    "personality": "Personality",
    "birthday": "Birthday",
}

NUMBER_PURPOSE = {
    "life_path": "the path you walk and the lessons this life brings",
    "expression": "your natural talents and how you express them",
    "soul_urge": "what your heart truly wants",
    "personality": "how others see you at first",
    "birthday": "a special gift you were born with",
}


# --------------------------------------------------------------------------
# Practitioner styles: which method, and how names are totalled
# --------------------------------------------------------------------------
METHODS = {
    "classic": "Classic: Life Path, Expression, Soul Urge, Personality, Birthday",
    "kdp": "Karma, Destiny, Personality",
}
TOTALLING = {
    "whole": "Add the whole name first, then reduce (keeps master numbers in the total)",
    "per_name": "Reduce each name first, then add them together",
}
Y_RULES = {
    "standard": "Y is a vowel only in a name with no other vowel (Lynn, Glynn)",
    "always": "Y is always a vowel",
    "never": "Y is always a consonant",
}
STYLE_DEFAULTS = {"method": "classic", "totalling": "whole", "y_rule": "standard", "lang": "en"}
REPORT_LANGUAGES = {"en": "English", "es": "Español"}
KARMIC_DEBT = (13, 14, 16, 19)


def clean_style(values) -> dict:
    """A complete, valid style from a mapping (form or stored row); unknown values fall back to defaults."""
    get = values.get if hasattr(values, "get") else (lambda key, default=None: default)
    return {
        "method": get("method") if get("method") in METHODS else STYLE_DEFAULTS["method"],
        "totalling": get("totalling") if get("totalling") in TOTALLING else STYLE_DEFAULTS["totalling"],
        "y_rule": get("y_rule") if get("y_rule") in Y_RULES else STYLE_DEFAULTS["y_rule"],
        "lang": get("lang") if get("lang") in REPORT_LANGUAGES else STYLE_DEFAULTS["lang"],
    }


def style_of(user_row) -> dict:
    """The numerology style of a practitioner (users row), or the default for none."""
    if user_row is None:
        return dict(STYLE_DEFAULTS)
    keys = user_row.keys()
    return clean_style({"method": user_row["report_method"] if "report_method" in keys else None,
                        "totalling": user_row["name_totalling"] if "name_totalling" in keys else None,
                        "y_rule": user_row["y_rule"] if "y_rule" in keys else None,
                        "lang": user_row["report_lang"] if "report_lang" in keys else None})


def display_number(total: int) -> str:
    """How a number is written: 11/2 for master numbers, 14/5 for karmic-debt totals, else the digit."""
    if total == 0:
        return "0"
    value = total
    while value > 9 and value not in MASTER_NUMBERS and value not in KARMIC_DEBT:
        value = sum(int(digit) for digit in str(value))
    if value in MASTER_NUMBERS:
        return f"{value}/{reduce(value, keep_master=False)}"
    if value in KARMIC_DEBT:
        return f"{value}/{reduce(value)}"
    return str(value)


def _entry(key: str, total: int) -> dict:
    number = reduce(total) if total else 0
    return {"key": key, "total": total, "number": number, "display": display_number(total) if total else "0"}


def core_numbers(full_name: str, birth_date: dt.date, style: dict | None = None) -> CoreNumbers:
    style = clean_style(style or {})
    groups = name_groups(full_name, style["y_rule"])
    if not groups:
        raise ValueError("The name must contain letters")
    total = lambda index: name_total([g[index] for g in groups], style["totalling"])  # noqa: E731
    life_path = reduce(reduce(birth_date.month) + reduce(birth_date.day) + reduce(birth_date.year))
    return CoreNumbers(
        life_path=life_path,
        expression=reduce(total(0)),
        soul_urge=reduce(total(1)),
        personality=reduce(total(2)),
        birthday=reduce(birth_date.day),
    )


def report_numbers(full_name: str, birth_date: dt.date, style: dict | None = None) -> dict:
    """The numbers of the practitioner's method, in report order (the first one is the free teaser).

    Stored with the lead, so the report keeps the method it was made with even if the
    practitioner later changes style.
    """
    style = clean_style(style or {})
    groups = name_groups(full_name, style["y_rule"])
    if not groups:
        raise ValueError("The name must contain letters")
    total = lambda index: name_total([g[index] for g in groups], style["totalling"])  # noqa: E731
    if style["method"] == "kdp":
        items = [_entry("karma", total(1)), _entry("destiny", total(2)), _entry("personality", total(0))]
    else:
        core = core_numbers(full_name, birth_date, style)
        items = [_entry("life_path", core.life_path), _entry("expression", total(0)),
                 _entry("soul_urge", total(1)), _entry("personality", total(2)),
                 _entry("birthday", core.birthday)]
        # Life Path and Birthday come from the date, already reduced along the way.
        for item in (items[0], items[4]):
            item["display"] = display_number(item["number"])
    return {"method": style["method"], "lang": style["lang"], "style": style, "items": items}


def position_label(position: str, lang: str = "en") -> tuple[str, str]:
    """(label, what it shows) for a report position, in the report's language."""
    import report_texts
    label, purpose, _frame = report_texts.POSITIONS[report_texts.clean_lang(lang)].get(
        position, (position.replace("_", " ").title(), "", ""))
    return label, purpose


def classic_report(numbers: dict) -> dict:
    """Report numbers for a lead stored before methods existed (classic, from its core numbers)."""
    items = [{"key": key, "total": numbers[key], "number": numbers[key],
              "display": display_number(numbers[key]) if numbers[key] else "0"} for key in NUMBER_ORDER]
    return {"method": "classic", "style": dict(STYLE_DEFAULTS), "items": items}


def lead_report(lead) -> dict:
    stored = lead["report"] if "report" in lead.keys() else None
    if stored:
        return json.loads(stored)
    return classic_report(json.loads(lead["numbers"]))


def report_items(lead, overrides: dict | None = None) -> list[dict]:
    """The lead's numbers in report order (teaser first), each with its label and full text in the
    report's language: title, meaning, strengths, challenges, lesson, affirmation. `text` is the
    meaning, kept for the free report and short emails. Pass the practitioner's overrides
    (reports.practitioner_overrides) to use her own wording."""
    import report_texts
    report = lead_report(lead)
    lang = report_texts.clean_lang(report.get("lang"))
    items = []
    for entry in report["items"]:
        label, purpose = position_label(entry["key"], lang)
        texts = report_texts.entry(lang, entry["key"], entry["number"], overrides)
        items.append({**entry, **texts, "label": label, "purpose": purpose, "text": texts["meaning"],
                      "lang": lang})
    return items


def teaser(lead) -> dict:
    """The free number shown before the email is given (Life Path or Karma, by method)."""
    return report_items(lead)[0]


# Short, original meanings used until practitioners write their own templates.
MEANINGS = {
    1: ("The Leader", "Independent, driven and original. You are here to start things, trust your own "
        "judgement and learn to lead without needing anyone's permission."),
    2: ("The Peacemaker", "Sensitive, diplomatic and cooperative. You bring people together and notice "
        "what others miss; your growth comes from valuing your own needs as much as theirs."),
    3: ("The Communicator", "Creative, expressive and warm. Words, art and joy come easily to you, and "
        "your challenge is to focus that gift instead of scattering it."),
    4: ("The Builder", "Practical, loyal and hard-working. You create lasting foundations through "
        "patience and order, and learn to stay flexible when plans change."),
    5: ("The Adventurer", "Curious, adaptable and freedom-loving. You learn through experience and "
        "change, and grow by finding freedom that doesn't depend on running from commitment."),
    6: ("The Nurturer", "Caring, responsible and devoted to home and community. You are the one people "
        "lean on; your lesson is to care for yourself with the same love."),
    7: ("The Seeker", "Thoughtful, intuitive and private. You look for the truth beneath the surface, "
        "and grow by trusting your inner knowing and letting others in."),
    8: ("The Achiever", "Ambitious, capable and strong-willed. You are built for success and influence, "
        "and your lesson is to use power and money with balance and integrity."),
    9: ("The Humanitarian", "Compassionate, generous and wise. You see the bigger picture and are here "
        "to serve; your growth comes from letting go gracefully."),
    11: ("The Intuitive (master number)", "Highly intuitive and inspiring, with the sensitivity of the 2 "
         "amplified. You can uplift others, as long as you ground your nervous energy."),
    22: ("The Master Builder (master number)", "Visionary and practical at once. You can turn big dreams "
         "into real structures that help many people, if you trust your scale."),
    33: ("The Master Teacher (master number)", "Loving, healing and selfless, with the care of the 6 "
         "amplified. You teach through compassion and example."),
}


NOT_ACTIVE = ("Not active", "Your birth name has no letters in this group, so this number isn't "
              "active for you. Its qualities come through your other numbers instead.")


def meaning(number: int) -> tuple[str, str]:
    if number == 0:
        return NOT_ACTIVE
    return MEANINGS.get(number, ("", ""))
