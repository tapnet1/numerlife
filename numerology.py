"""Pythagorean numerology: the core numbers, computed deterministically.

Rules used (the common Pythagorean method; other systems arrive with report styles):
- Letters A-Z map to 1-9 in sequence (A=1 ... I=9, J=1 ... R=9, S=1 ... Z=8).
- Accents are removed before calculating (José -> JOSE); other characters are ignored.
- Numbers are reduced by adding digits until one digit remains, except the master
  numbers 11, 22 and 33, which are kept.
- Life Path: month, day and year are each reduced first, then added and reduced.
- Expression: every letter of the full birth name.
- Soul Urge: vowels (A, E, I, O, U). Personality: all other letters. Y counts as a consonant,
  except in a name part with no other vowel (Lynn, Glynn), where Y is that part's vowel.
- If a name has no letters in a group (e.g. no consonants at all), that number is 0: it is
  shown as "not active" rather than given a meaning.
- Birthday: the day of the month, reduced (11 and 22 kept).
"""
from __future__ import annotations

import datetime as dt
import unicodedata
from dataclasses import dataclass

MASTER_NUMBERS = (11, 22, 33)
VOWELS = set("AEIOU")


def letter_value(letter: str) -> int:
    return (ord(letter) - ord("A")) % 9 + 1


def letters_of(name: str) -> str:
    """Upper-case A-Z letters of a name, with accents removed."""
    decomposed = unicodedata.normalize("NFKD", name)
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    plain = plain.replace("ß", "SS").replace("Æ", "AE").replace("æ", "AE").replace("Ø", "O").replace("ø", "O")
    return "".join(ch for ch in plain.upper() if "A" <= ch <= "Z")


def split_vowels(full_name: str) -> tuple[str, str]:
    """Vowel and consonant letters; Y is a vowel only in a name part with no other vowel."""
    vowels, consonants = [], []
    for part in full_name.split():
        letters = letters_of(part)
        y_is_vowel = not any(ch in VOWELS for ch in letters)
        for ch in letters:
            if ch in VOWELS or (ch == "Y" and y_is_vowel):
                vowels.append(ch)
            else:
                consonants.append(ch)
    return "".join(vowels), "".join(consonants)


def reduce(number: int, keep_master: bool = True) -> int:
    while number > 9 and not (keep_master and number in MASTER_NUMBERS):
        number = sum(int(digit) for digit in str(number))
    return number


def _name_number(letters: str) -> int:
    return reduce(sum(letter_value(ch) for ch in letters)) if letters else 0


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


def core_numbers(full_name: str, birth_date: dt.date) -> CoreNumbers:
    letters = letters_of(full_name)
    if not letters:
        raise ValueError("The name must contain letters")
    vowels, consonants = split_vowels(full_name)
    life_path = reduce(reduce(birth_date.month) + reduce(birth_date.day) + reduce(birth_date.year))
    return CoreNumbers(
        life_path=life_path,
        expression=_name_number(letters),
        soul_urge=_name_number(vowels),
        personality=_name_number(consonants),
        birthday=reduce(birth_date.day),
    )


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
