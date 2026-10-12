"""Educational content for the home page: core numbers, Life Path meanings and FAQ.

Kept in one place so the visible FAQ and its schema.org FAQPage data always match.
"""
from __future__ import annotations

import string

import numerology

CORE_NUMBERS = [
    {"label": "Life Path", "source": "date of birth",
     "about": "The most important number in your chart. It describes the road you're on, the "
              "lessons this life brings and the qualities you grow into."},
    {"label": "Expression", "source": "full birth name",
     "about": "Also called the Destiny number. It shows your natural talents, abilities and the "
              "way you tend to express yourself in the world."},
    {"label": "Soul Urge", "source": "name's vowels",
     "about": "Also called the Heart's Desire. It reveals what truly motivates you and what your "
              "heart wants, often privately."},
    {"label": "Personality", "source": "name's consonants",
     "about": "The side of you others meet first: the impression you give before people get to "
              "know you."},
    {"label": "Birthday", "source": "day of birth",
     "about": "A special gift or talent tied to the day of the month you were born, which supports "
              "your Life Path."},
]

FAQS = [
    ("What is numerology?",
     "Numerology is the study of the symbolic meaning of numbers. It uses your full birth name and "
     "date of birth to calculate a set of personal numbers, such as your Life Path, Expression and "
     "Soul Urge, each describing a different side of your character and path in life."),
    ("How do I calculate my Life Path number?",
     "Reduce your birth month, day and year to single digits separately (keeping 11, 22 and 33), "
     "add the three results together, then reduce that total to a single digit or master number. "
     "For example, 15 July 1990 gives 7 + 6 + 1 = 14, and 1 + 4 = 5. Our free report does this "
     "for you instantly."),
    ("What is the most important number in numerology?",
     "Most numerologists consider the Life Path number the most important, because it comes from "
     "your date of birth, which never changes, and describes the overall direction of your life."),
    ("Which name should I use for my numerology report?",
     "Use your full name exactly as it appears on your birth certificate, including middle names. "
     "Nicknames and married names can be explored later, but your birth name gives your core "
     "Expression, Soul Urge and Personality numbers."),
    ("What are master numbers?",
     "Master numbers are 11, 22 and 33. They are not reduced to a single digit because they are "
     "thought to carry a stronger, more demanding version of the energy of 2, 4 and 6."),
    ("What is the difference between Pythagorean and Chaldean numerology?",
     "Pythagorean numerology, the most common system in the West, gives letters the values 1 to 9 "
     "in alphabetical order. Chaldean numerology is an older system that assigns values 1 to 8 "
     "based on the sound of each letter. This report uses the Pythagorean system."),
    ("Why do you ask for my place of birth?",
     "Your name and birth date give your core numbers. Some numerologists also look at the "
     "spelling of your birth city and country for additional insight, so we ask for it to give "
     "a numerologist the full picture."),
    ("Is numerology scientific?",
     "No. Numerology is a centuries-old symbolic practice, not a science, and its claims are not "
     "supported by scientific evidence. Many people enjoy it as a tool for reflection and "
     "self-discovery, and that is the spirit in which we offer these reports."),
    ("Is the numerology report really free?",
     "Yes. Your Life Path number appears instantly and free. Add your email to unlock the rest of "
     "your report, also free."),
    ("What do you do with my information?",
     "We use your details to calculate and send your report. If you came through a numerologist's "
     "link, your details are shared with that numerologist so they can follow up. We never sell "
     "your information. See our privacy policy for details."),
]


def faq_items() -> list[dict]:
    return [{
        "q": q, "a": a,
        "ld": {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}},
    } for q, a in FAQS]


def meaning_items() -> list[dict]:
    items = []
    for number in list(range(1, 10)) + [11, 22, 33]:
        title, text = numerology.meaning(number)
        items.append({"number": number, "title": title, "text": text})
    return items


def letter_rows() -> list[list[str]]:
    letters = string.ascii_uppercase
    return [list(letters[i:i + 9]) for i in range(0, 26, 9)]


def home_context() -> dict:
    return {"core": CORE_NUMBERS, "faqs": faq_items(), "meanings": meaning_items(),
            "letter_rows": letter_rows()}
