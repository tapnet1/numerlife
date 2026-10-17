"""Default guidance for each personal day number, in English and Spanish.

These are numer.life's own starter texts. Each practitioner can replace any of them with
her own wording (calendar_content table); her version always wins.
"""
from __future__ import annotations

NUMBERS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 22, 33)
FIELDS = ("title", "focus", "avoid", "lucky", "color")

DEFAULTS = {
    "en": {
        1: {"title": "New beginnings", "focus": "Start something, take the lead, make the first move.",
            "avoid": "Waiting for permission or doubting your own idea.", "lucky": "1, 10, 19", "color": "Red"},
        2: {"title": "Partnership", "focus": "Listen, cooperate, smooth things over, be patient.",
            "avoid": "Forcing decisions or arguing to win.", "lucky": "2, 11, 20", "color": "Silver"},
        3: {"title": "Expression", "focus": "Create, write, share good news, enjoy people.",
            "avoid": "Scattering your energy or gossip.", "lucky": "3, 12, 21", "color": "Yellow"},
        4: {"title": "Foundations", "focus": "Organise, plan, finish practical tasks, look after your body.",
            "avoid": "Shortcuts and putting off what matters.", "lucky": "4, 13, 22", "color": "Green"},
        5: {"title": "Change", "focus": "Try something new, move, travel, say yes to the unexpected.",
            "avoid": "Impulsive spending and excess.", "lucky": "5, 14, 23", "color": "Turquoise"},
        6: {"title": "Home and care", "focus": "Family, home, people who need you; beautify your space.",
            "avoid": "Carrying everyone's problems as your own.", "lucky": "6, 15, 24", "color": "Pink"},
        7: {"title": "Reflection", "focus": "Rest, study, meditate, trust your intuition.",
            "avoid": "Big launches and noisy crowds.", "lucky": "7, 16, 25", "color": "Violet"},
        8: {"title": "Achievement", "focus": "Money, business, decisions, ask for what you're worth.",
            "avoid": "Being controlling or careless with money.", "lucky": "8, 17, 26", "color": "Gold"},
        9: {"title": "Completion", "focus": "Finish, forgive, clear out, help someone.",
            "avoid": "Starting big new projects or holding grudges.", "lucky": "9, 18, 27", "color": "White"},
        11: {"title": "Inspiration", "focus": "Follow your intuition; teach, inspire, notice signs.",
             "avoid": "Nervous overthinking; ground yourself.", "lucky": "11, 2, 29", "color": "Pearl"},
        22: {"title": "Master builder", "focus": "Think big and lay one solid brick towards it.",
             "avoid": "Overwhelm; break the vision into steps.", "lucky": "22, 4, 13", "color": "Coral"},
        33: {"title": "Master teacher", "focus": "Serve with love: teach, heal, encourage someone.",
             "avoid": "Self-sacrifice to the point of exhaustion.", "lucky": "33, 6, 15", "color": "Rose gold"},
    },
    "es": {
        1: {"title": "Nuevos comienzos", "focus": "Inicia algo, toma la iniciativa, da el primer paso.",
            "avoid": "Esperar permiso o dudar de tu propia idea.", "lucky": "1, 10, 19", "color": "Rojo"},
        2: {"title": "Asociación", "focus": "Escucha, coopera, suaviza tensiones, ten paciencia.",
            "avoid": "Forzar decisiones o discutir para ganar.", "lucky": "2, 11, 20", "color": "Plateado"},
        3: {"title": "Expresión", "focus": "Crea, escribe, comparte buenas noticias, disfruta de la gente.",
            "avoid": "Dispersar tu energía o los chismes.", "lucky": "3, 12, 21", "color": "Amarillo"},
        4: {"title": "Bases sólidas", "focus": "Organiza, planifica, termina tareas prácticas, cuida tu cuerpo.",
            "avoid": "Los atajos y posponer lo importante.", "lucky": "4, 13, 22", "color": "Verde"},
        5: {"title": "Cambio", "focus": "Prueba algo nuevo, muévete, viaja, di sí a lo inesperado.",
            "avoid": "Gastos impulsivos y excesos.", "lucky": "5, 14, 23", "color": "Turquesa"},
        6: {"title": "Hogar y cuidado", "focus": "Familia, hogar, quienes te necesitan; embellece tu espacio.",
            "avoid": "Cargar con los problemas de todos como propios.", "lucky": "6, 15, 24", "color": "Rosa"},
        7: {"title": "Reflexión", "focus": "Descansa, estudia, medita, confía en tu intuición.",
            "avoid": "Grandes lanzamientos y multitudes ruidosas.", "lucky": "7, 16, 25", "color": "Violeta"},
        8: {"title": "Logro", "focus": "Dinero, negocios, decisiones; pide lo que vales.",
            "avoid": "Ser controlador o descuidado con el dinero.", "lucky": "8, 17, 26", "color": "Dorado"},
        9: {"title": "Cierre", "focus": "Termina, perdona, despeja, ayuda a alguien.",
            "avoid": "Empezar grandes proyectos o guardar rencor.", "lucky": "9, 18, 27", "color": "Blanco"},
        11: {"title": "Inspiración", "focus": "Sigue tu intuición; enseña, inspira, atiende las señales.",
             "avoid": "Darle demasiadas vueltas; conéctate con la tierra.", "lucky": "11, 2, 29", "color": "Perla"},
        22: {"title": "Maestro constructor", "focus": "Piensa en grande y pon hoy un ladrillo firme.",
             "avoid": "Agobiarte; divide la visión en pasos.", "lucky": "22, 4, 13", "color": "Coral"},
        33: {"title": "Maestro sanador", "focus": "Sirve con amor: enseña, sana, anima a alguien.",
             "avoid": "Sacrificarte hasta el agotamiento.", "lucky": "33, 6, 15", "color": "Oro rosa"},
    },
}

LANGUAGES = {"en": "English", "es": "Español"}

# Interface labels for the client's calendar page, PDF and emails
LABELS = {
    "en": {
        "calendar": "Personal calendar", "personal_year": "Personal year", "personal_month": "Personal month",
        "personal_day": "Personal day", "focus": "Focus", "avoid": "Avoid", "lucky": "Lucky numbers",
        "color": "Colour", "note": "Note", "download_pdf": "Download PDF", "prev": "Previous month",
        "next": "Next month", "not_ready": "This month's calendar isn't ready yet. Please check back soon.",
        "prepared_by": "Prepared by", "for": "for", "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "moon": {"new": "New moon", "first_quarter": "First quarter", "full": "Full moon", "last_quarter": "Last quarter"},
        "strong": "Strong period", "weak": "Weak period", "connections": "Connections", "life_path": "Life Path",
        "footer": "For reflection and personal growth.",
    },
    "es": {
        "calendar": "Calendario personal", "personal_year": "Año personal", "personal_month": "Mes personal",
        "personal_day": "Día personal", "focus": "Enfoque", "avoid": "Evita", "lucky": "Números de suerte",
        "color": "Color", "note": "Nota", "download_pdf": "Descargar PDF", "prev": "Mes anterior",
        "next": "Mes siguiente", "not_ready": "El calendario de este mes aún no está listo. Vuelve pronto.",
        "prepared_by": "Preparado por", "for": "para", "weekdays": ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"],
        "moon": {"new": "Luna nueva", "first_quarter": "Cuarto creciente", "full": "Luna llena", "last_quarter": "Cuarto menguante"},
        "strong": "Periodo fuerte", "weak": "Periodo débil", "connections": "Tabla de conexiones",
        "life_path": "Número de camino", "footer": "Para la reflexión y el crecimiento personal.",
    },
}

MONTH_NAMES = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"],
    "es": ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre",
           "Noviembre", "Diciembre"],
}


# Extra languages live in calendar_languages; merged here so every lookup sees them.
import calendar_languages as _extra  # noqa: E402

for _code, _name in _extra.LANGUAGES.items():
    LANGUAGES[_code] = _name
    LABELS[_code] = _extra.LABELS[_code]
    MONTH_NAMES[_code] = _extra.MONTH_NAMES[_code]
    DEFAULTS[_code] = _extra.DEFAULTS[_code]


def reduce_to_digit(number: int) -> int:
    while number > 9:
        number = sum(int(d) for d in str(number))
    return number or 9


def guidance(number: int, lang: str, overrides: dict | None = None) -> dict:
    """The text for a personal day number: the practitioner's version, else the default."""
    lang = lang if lang in DEFAULTS else "en"
    base = dict(DEFAULTS[lang].get(number) or DEFAULTS[lang][reduce_to_digit(number)])
    for key, value in ((overrides or {}).get(number) or {}).items():
        if value:
            base[key] = value
    return base
