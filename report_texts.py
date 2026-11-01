"""Report texts: what each number means in each position of a report, in English and Spanish.

numer.life's starter texts are original wording, written so a report reads well from day one.
A practitioner replaces any of them with her own wording, per language, position and number
(report_content table, edited at /settings/report). Anything she leaves empty keeps following
the starter text.

Starter text for a position is built from the number's own text plus a sentence that frames
the position (what a Karma number is, what a Destiny number is ...). Karma is read as the
challenges a person came to work through, so it has no strengths.
"""
from __future__ import annotations

NUMBERS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 22, 33)
LANGUAGES = {"en": "English", "es": "Español"}
FIELDS = ("title", "meaning", "strengths", "challenges", "lesson", "affirmation")
FIELD_LIMITS = {"title": 80, "meaning": 600, "strengths": 300, "challenges": 300, "lesson": 400, "affirmation": 300}
NO_STRENGTHS = ("karma",)  # Karma is always read as challenges

# --------------------------------------------------------------------------
# Each number: title, adjective (for the combination sentence), essence, strengths,
# challenges, lesson, affirmation. Strengths and challenges are comma-separated lists.
# Spanish adjectives agree with "persona" (feminine).
# --------------------------------------------------------------------------
_EN = {
    1: ("The Leader", "independent",
        "Number 1 is the energy of beginnings: independence, initiative and the courage to go first.",
        "Initiative, Courage, Originality, Determination, Self-reliance",
        "Impatience, Stubbornness, Difficulty asking for help, Fear of depending on others, Self-doubt hidden behind control",
        "Learning to trust your own judgement and lead with confidence, while staying open to other people's ideas.",
        "I trust myself to begin, and I welcome others along the way."),
    2: ("The Peacemaker", "sensitive",
        "Number 2 is the energy of partnership: sensitivity, cooperation, harmony and close relationships.",
        "Empathy, Diplomacy, Patience, Cooperation, Attention to detail",
        "Emotional insecurity, Needing others' approval, Oversensitivity, Difficulty setting boundaries, Excessive worrying",
        "Learning to value your own needs as much as other people's, and to build close relationships without losing yourself.",
        "My needs matter, and I share myself without losing myself."),
    3: ("The Communicator", "creative",
        "Number 3 is the energy of expression: communication, creativity, joy and imagination.",
        "Creativity, Communication skills, Optimism, Sociability, Ability to inspire others",
        "Lack of focus, Inconsistency, Speaking impulsively, Leaving projects unfinished, Avoiding difficult emotional conversations",
        "Learning to express ideas and feelings clearly, use creativity constructively and develop discipline.",
        "I express my truth with joy, and I finish what I start."),
    4: ("The Builder", "steady",
        "Number 4 is the energy of foundations: order, effort, loyalty and stability.",
        "Reliability, Discipline, Loyalty, Practical sense, Persistence",
        "Rigidity, Resistance to change, Overworking, Worry about security, Being too hard on yourself",
        "Learning to build a stable life while staying flexible when plans change.",
        "I build step by step, and I bend without breaking."),
    5: ("The Adventurer", "freedom-loving",
        "Number 5 is the energy of change: freedom, movement, curiosity and adaptability.",
        "Adaptability, Curiosity, Versatility, Independence, Openness to new experiences",
        "Restlessness, Impatience, Difficulty keeping routines, Impulsive behaviour, Resistance to commitment",
        "Understanding that true freedom also needs responsibility, balance and self-discipline.",
        "I use my freedom wisely, and every experience helps me grow."),
    6: ("The Nurturer", "caring",
        "Number 6 is the energy of care: love, responsibility, family, home and service.",
        "Compassion, Responsibility, Generosity, Sense of beauty, Devotion to family",
        "Over-giving, Need to be needed, Perfectionism, Taking on others' problems, Neglecting yourself",
        "Learning to care for yourself with the same love you give others, and to help without controlling.",
        "I give love freely, and I receive it too."),
    7: ("The Seeker", "reflective",
        "Number 7 is the energy of inner wisdom: reflection, intuition, study and the search for truth.",
        "Depth, Intuition, Analytical mind, Spiritual awareness, Love of learning",
        "Isolating yourself, Distrust, Keeping secrets, Excessive need for privacy, Fear of showing vulnerability",
        "Learning to trust and open up to others while keeping your inner space.",
        "I can keep my inner space without disconnecting from the people I love."),
    8: ("The Achiever", "ambitious",
        "Number 8 is the energy of achievement: authority, abundance, organisation and personal power.",
        "Leadership, Ambition, Good judgement in business, Resilience, Organisation",
        "Workaholism, Need for control, Measuring worth by success, Tension around money, Impatience with others",
        "Learning to use power and money with balance and integrity, and to value yourself beyond results.",
        "I create abundance with integrity, and I am worthy beyond what I achieve."),
    9: ("The Humanitarian", "compassionate",
        "Number 9 is the energy of completion: compassion, generosity, wisdom and service to others.",
        "Compassion, Generosity, Broad vision, Idealism, Creativity",
        "Difficulty letting go, Sacrificing yourself, Holding on to the past, Disappointment when others fall short, Scattering your energy",
        "Learning to let go gracefully, close cycles and serve others without forgetting yourself.",
        "I let go with love, and I make room for what comes next."),
    11: ("The Intuitive (master number)", "intuitive",
         "Number 11 is a master number: intuition, sensitivity, inspiration and spiritual awareness. "
         "Reduced to 2, it also speaks of cooperation, harmony and relationships.",
         "Strong intuition, Inspiration, Sensitivity, Idealism, Ability to uplift others",
         "Emotional insecurity, Needing others' approval, Oversensitivity, Difficulty setting boundaries, Nervous tension and worry",
         "Learning to trust your intuition, develop inner confidence and build balanced relationships without losing your independence.",
         "I trust my intuition, and I let my light guide me and others."),
    22: ("The Master Builder (master number)", "visionary",
         "Number 22 is a master number: big vision made real through practical, patient work. "
         "Reduced to 4, it also speaks of order and foundations.",
         "Vision, Practical genius, Leadership, Discipline, Ability to build for many",
         "Pressure to achieve, Fear of failing, Rigidity, Overwork, Doubting the size of your dream",
         "Learning to trust the scale of your vision and build it one solid step at a time.",
         "I turn great visions into real foundations, one step at a time."),
    33: ("The Master Teacher (master number)", "loving",
         "Number 33 is a master number: compassion, healing and teaching through love and example. "
         "Reduced to 6, it also speaks of care and responsibility.",
         "Unconditional love, Healing presence, Wisdom, Devotion, Inspiring example",
         "Carrying everyone's burdens, Self-sacrifice, Perfectionism, Martyrdom, Forgetting your own needs",
         "Learning to serve and teach from love without sacrificing your own wellbeing.",
         "I serve with love, and I include myself in that love."),
}

_ES = {
    1: ("El Líder", "independiente",
        "El número 1 es la energía de los comienzos: independencia, iniciativa y el valor de ir primero.",
        "Iniciativa, Valentía, Originalidad, Determinación, Autosuficiencia",
        "Impaciencia, Terquedad, Dificultad para pedir ayuda, Miedo a depender de otros, Inseguridad escondida tras el control",
        "Aprender a confiar en tu propio criterio y liderar con seguridad, sin cerrarte a las ideas de los demás.",
        "Confío en mí para empezar, y doy la bienvenida a quienes me acompañan."),
    2: ("El Pacificador", "sensible",
        "El número 2 es la energía de la pareja y la unión: sensibilidad, cooperación, armonía y relaciones cercanas.",
        "Empatía, Diplomacia, Paciencia, Cooperación, Atención al detalle",
        "Inseguridad emocional, Necesidad de aprobación, Hipersensibilidad, Dificultad para poner límites, Preocupación excesiva",
        "Aprender a valorar tus propias necesidades tanto como las de los demás, y a construir relaciones cercanas sin perderte.",
        "Mis necesidades importan, y me entrego sin perderme."),
    3: ("El Comunicador", "creativa",
        "El número 3 es la energía de la expresión: comunicación, creatividad, alegría e imaginación.",
        "Creatividad, Facilidad para comunicar, Optimismo, Sociabilidad, Capacidad de inspirar",
        "Falta de enfoque, Inconstancia, Hablar de forma impulsiva, Dejar proyectos sin terminar, Evitar conversaciones emocionales difíciles",
        "Aprender a expresar ideas y emociones con claridad, usar la creatividad de forma constructiva y desarrollar disciplina.",
        "Expreso mi verdad con alegría, y termino lo que empiezo."),
    4: ("El Constructor", "constante",
        "El número 4 es la energía de las bases firmes: orden, esfuerzo, lealtad y estabilidad.",
        "Confiabilidad, Disciplina, Lealtad, Sentido práctico, Perseverancia",
        "Rigidez, Resistencia al cambio, Exceso de trabajo, Preocupación por la seguridad, Exigirte demasiado",
        "Aprender a construir una vida estable sin perder la flexibilidad cuando los planes cambian.",
        "Construyo paso a paso, y me adapto sin romperme."),
    5: ("El Aventurero", "amante de la libertad",
        "El número 5 es la energía del cambio: libertad, movimiento, curiosidad y adaptabilidad.",
        "Adaptabilidad, Curiosidad, Versatilidad, Independencia, Apertura a nuevas experiencias",
        "Inquietud, Impaciencia, Dificultad para mantener rutinas, Impulsividad, Resistencia al compromiso",
        "Comprender que la verdadera libertad también requiere responsabilidad, equilibrio y autodisciplina.",
        "Uso mi libertad con sabiduría, y cada experiencia me ayuda a crecer."),
    6: ("El Protector", "cariñosa",
        "El número 6 es la energía del cuidado: amor, responsabilidad, familia, hogar y servicio.",
        "Compasión, Responsabilidad, Generosidad, Sentido de la belleza, Entrega a la familia",
        "Dar en exceso, Necesidad de sentirte necesario, Perfeccionismo, Cargar con los problemas ajenos, Descuidarte",
        "Aprender a cuidarte con el mismo amor que das a los demás, y a ayudar sin controlar.",
        "Doy amor con libertad, y también lo recibo."),
    7: ("El Buscador", "reflexiva",
        "El número 7 es la energía de la sabiduría interior: reflexión, intuición, estudio y búsqueda de la verdad.",
        "Profundidad, Intuición, Mente analítica, Conciencia espiritual, Amor por el aprendizaje",
        "Aislarte, Desconfianza, Guardar secretos, Necesidad excesiva de privacidad, Miedo a mostrarte vulnerable",
        "Aprender a confiar y abrirte a los demás sin perder tu espacio interior.",
        "Puedo mantener mi espacio personal sin desconectarme de las personas que amo."),
    8: ("El Triunfador", "ambiciosa",
        "El número 8 es la energía del logro: autoridad, abundancia, organización y poder personal.",
        "Liderazgo, Ambición, Buen criterio para los negocios, Resiliencia, Organización",
        "Adicción al trabajo, Necesidad de control, Medir tu valor por el éxito, Tensión con el dinero, Impaciencia con los demás",
        "Aprender a usar el poder y el dinero con equilibrio e integridad, y a valorarte más allá de los resultados.",
        "Creo abundancia con integridad, y valgo más allá de lo que logro."),
    9: ("El Humanitario", "compasiva",
        "El número 9 es la energía del cierre de ciclos: compasión, generosidad, sabiduría y servicio a los demás.",
        "Compasión, Generosidad, Visión amplia, Idealismo, Creatividad",
        "Dificultad para soltar, Sacrificarte, Aferrarte al pasado, Decepción cuando otros fallan, Dispersar tu energía",
        "Aprender a soltar con gracia, cerrar ciclos y servir a los demás sin olvidarte de ti.",
        "Suelto con amor, y hago espacio para lo que viene."),
    11: ("El Intuitivo (número maestro)", "intuitiva",
         "El 11 es un número maestro: intuición, sensibilidad, inspiración y conciencia espiritual. "
         "Reducido a 2, también habla de cooperación, armonía y relaciones.",
         "Gran intuición, Inspiración, Sensibilidad, Idealismo, Capacidad de elevar a otros",
         "Inseguridad emocional, Necesidad de aprobación, Hipersensibilidad, Dificultad para poner límites, Tensión nerviosa y preocupación",
         "Aprender a confiar en tu intuición, desarrollar seguridad interior y construir relaciones equilibradas sin perder tu independencia.",
         "Confío en mi intuición, y dejo que mi luz me guíe a mí y a otros."),
    22: ("El Maestro Constructor (número maestro)", "visionaria",
         "El 22 es un número maestro: una gran visión hecha realidad con trabajo práctico y paciente. "
         "Reducido a 4, también habla de orden y bases firmes.",
         "Visión, Genio práctico, Liderazgo, Disciplina, Capacidad de construir para muchos",
         "Presión por lograr, Miedo a fallar, Rigidez, Exceso de trabajo, Dudar del tamaño de tu sueño",
         "Aprender a confiar en la grandeza de tu visión y construirla paso a paso, sobre bases firmes.",
         "Convierto grandes visiones en bases reales, paso a paso."),
    33: ("El Maestro Sanador (número maestro)", "amorosa",
         "El 33 es un número maestro: compasión, sanación y enseñanza a través del amor y el ejemplo. "
         "Reducido a 6, también habla de cuidado y responsabilidad.",
         "Amor incondicional, Presencia sanadora, Sabiduría, Entrega, Ejemplo que inspira",
         "Cargar con todos, Sacrificio personal, Perfeccionismo, Martirio, Olvidar tus propias necesidades",
         "Aprender a servir y enseñar desde el amor sin sacrificar tu propio bienestar.",
         "Sirvo con amor, y me incluyo en ese amor."),
}

_KEYS = ("title", "adjective", "essence", "strengths", "challenges", "lesson", "affirmation")
NUMBER_TEXTS = {lang: {n: dict(zip(_KEYS, values)) for n, values in table.items()}
                for lang, table in (("en", _EN), ("es", _ES))}

# --------------------------------------------------------------------------
# Positions: label, what the position shows, and the sentence that frames it.
# --------------------------------------------------------------------------
POSITIONS = {
    "en": {
        "karma": ("Karma", "the challenges you came to work through, and the lesson they hold",
                  "Your Karma number shows the inner challenges you came to work through in this life."),
        "destiny": ("Destiny", "the direction your life is meant to take",
                    "Your Destiny number shows the direction your life is meant to take and the gifts you are here to develop."),
        "personality": ("Personality", "how others see you and the energy you show the world",
                        "Your Personality number shows the energy you show the world and how others experience you."),
        "life_path": ("Life Path", "the path you walk and the lessons this life brings",
                      "Your Life Path number shows the path you walk and the lessons this life brings."),
        "expression": ("Expression", "your natural talents and how you express them",
                       "Your Expression number shows your natural talents and how you express them."),
        "soul_urge": ("Soul Urge", "what your heart truly wants",
                      "Your Soul Urge number shows what your heart truly wants."),
        "birthday": ("Birthday", "a special gift you were born with",
                     "Your Birthday number shows a special gift you were born with."),
    },
    "es": {
        "karma": ("Karma", "los desafíos que viniste a trabajar y la lección que encierran",
                  "Tu número de Karma muestra los desafíos interiores que viniste a trabajar en esta vida."),
        "destiny": ("Destino", "la dirección que tu vida está llamada a tomar",
                    "Tu número de Destino muestra la dirección que tu vida está llamada a tomar y los dones que viniste a desarrollar."),
        "personality": ("Personalidad", "cómo te ven los demás y la energía que muestras al mundo",
                        "Tu número de Personalidad muestra la energía que proyectas al mundo y cómo te perciben los demás."),
        "life_path": ("Camino de Vida", "el camino que recorres y las lecciones de esta vida",
                      "Tu número de Camino de Vida muestra el camino que recorres y las lecciones que trae esta vida."),
        "expression": ("Expresión", "tus talentos naturales y cómo los expresas",
                       "Tu número de Expresión muestra tus talentos naturales y cómo los expresas."),
        "soul_urge": ("Impulso del Alma", "lo que tu corazón desea de verdad",
                      "Tu número de Impulso del Alma muestra lo que tu corazón desea de verdad."),
        "birthday": ("Cumpleaños", "un don especial con el que naciste",
                     "Tu número de Cumpleaños muestra un don especial con el que naciste."),
    },
}

METHOD_POSITIONS = {
    "kdp": ("karma", "destiny", "personality"),
    "classic": ("life_path", "expression", "soul_urge", "personality", "birthday"),
}

LABELS = {
    "en": {"strengths": "Strengths", "challenges": "Challenges", "lesson": "Lesson",
           "affirmation": "Affirmation", "together": "Your numbers together", "meaning": "Meaning",
           "title": "Title", "not_active_title": "Not active",
           "not_active": "Your birth name has no letters in this group, so this number isn't active for you. "
                         "Its qualities come through your other numbers instead.",
           "combination": "Your {numbers} combination describes {article} {adjectives} person.",
           "and": "and"},
    "es": {"strengths": "Fortalezas", "challenges": "Desafíos", "lesson": "Lección",
           "affirmation": "Afirmación", "together": "Tus números en conjunto", "meaning": "Significado",
           "title": "Título", "not_active_title": "No activo",
           "not_active": "Tu nombre de nacimiento no tiene letras en este grupo, así que este número no está activo "
                         "para ti. Sus cualidades llegan a través de tus otros números.",
           "combination": "Tu combinación {numbers} describe a una persona {adjectives}.",
           "and": "y"},
}


def clean_lang(lang: str | None) -> str:
    return lang if lang in LANGUAGES else "en"


def default_entry(lang: str, position: str, number: int) -> dict:
    """The starter text for one number in one position."""
    lang = clean_lang(lang)
    base = NUMBER_TEXTS[lang].get(number)
    if base is None:
        labels = LABELS[lang]
        return {"title": labels["not_active_title"], "meaning": labels["not_active"], "strengths": "",
                "challenges": "", "lesson": "", "affirmation": ""}
    frame = POSITIONS[lang].get(position, ("", "", ""))[2]
    return {
        "title": base["title"],
        "meaning": f"{frame} {base['essence']}".strip(),
        "strengths": "" if position in NO_STRENGTHS else base["strengths"],
        "challenges": base["challenges"],
        "lesson": base["lesson"],
        "affirmation": base["affirmation"],
    }


def entry(lang: str, position: str, number: int, overrides: dict | None = None) -> dict:
    """Starter text with the practitioner's own wording laid over it, field by field."""
    result = default_entry(lang, position, number)
    mine = (overrides or {}).get((position, number)) or {}
    for field in FIELDS:
        if mine.get(field):
            result[field] = mine[field]
    if position in NO_STRENGTHS:
        result["strengths"] = ""
    return result


def as_list(text: str) -> list[str]:
    return [part.strip() for part in (text or "").replace("\n", ",").split(",") if part.strip()]


def combination(lang: str, items: list[dict]) -> str:
    """One sentence describing the numbers together, e.g. "Your 11/2 · 3 · 14/5 combination describes
    a sensitive, creative and freedom-loving person." Numbers that aren't active are left out."""
    lang = clean_lang(lang)
    labels = LABELS[lang]
    adjectives = []
    for item in items:
        base = NUMBER_TEXTS[lang].get(item["number"])
        if base and base["adjective"] not in adjectives:
            adjectives.append(base["adjective"])
    if not adjectives:
        return ""
    joined = adjectives[0] if len(adjectives) == 1 else \
        f"{', '.join(adjectives[:-1])} {labels['and']} {adjectives[-1]}"
    numbers = " · ".join(item["display"] for item in items if item["number"])
    article = "an" if joined[:1].lower() in "aeiou" else "a"
    return labels["combination"].format(numbers=numbers, adjectives=joined, article=article)
