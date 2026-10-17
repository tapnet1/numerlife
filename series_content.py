"""Words for the follow-up email series, in English and Spanish.

numer.life's own original texts. Numbers always come from code (numerology.py); these only
describe them. Spanish phrasing is kept gender-neutral ("una persona ...").
"""
from __future__ import annotations

LANGS = ("en", "es")

NUMBER_LABELS = {
    "en": {"life_path": "Life Path", "expression": "Expression", "soul_urge": "Soul Urge",
           "personality": "Personality", "birthday": "Birthday"},
    "es": {"life_path": "Camino de Vida", "expression": "Expresión", "soul_urge": "Impulso del Alma",
           "personality": "Personalidad", "birthday": "Día de nacimiento"},
}

# title, gift (talent), desire (heart), seen (first impression)
TRAITS = {
    "en": {
        1: ("Leadership", "starting things and leading with your own ideas", "freedom to do things your own way",
            "confident and self-assured, someone who knows where they're going"),
        2: ("Harmony", "reading people and bringing them together", "deep connection and peace with the people you love",
            "gentle, kind and easy to talk to"),
        3: ("Expression", "words, creativity and lifting people's spirits", "to express yourself and truly be heard",
            "warm, funny and full of life"),
        4: ("Foundation", "turning plans into real, lasting results", "security and something solid you built yourself",
            "reliable, steady and someone people trust"),
        5: ("Freedom", "adapting fast and making the most of change", "freedom, variety and new experiences",
            "lively, curious and fun to be around"),
        6: ("Care", "caring for people and making any place feel like home", "a loving home and people who need you",
            "caring, responsible and dependable"),
        7: ("Wisdom", "thinking deeply and seeing what others miss", "time alone to understand life's deeper questions",
            "calm, private and a little mysterious"),
        8: ("Achievement", "organising, deciding and making things succeed",
            "success, independence and recognition for your work", "capable, strong and in charge"),
        9: ("Compassion", "understanding people and serving a bigger cause", "to make a real difference in the world",
            "generous, wise and open-hearted"),
        11: ("Intuition", "intuition and inspiring others", "to follow a calling that truly means something",
             "sensitive, inspiring and quietly magnetic"),
        22: ("Vision", "turning big visions into real structures", "to build something that lasts and helps many",
             "visionary yet practical"),
        33: ("Healing love", "healing, teaching and encouraging", "to give a love that changes lives",
             "deeply loving and wise"),
    },
    "es": {
        1: ("Liderazgo", "iniciar proyectos y liderar con tus propias ideas", "libertad para hacer las cosas a tu manera",
            "una persona segura de sí misma que sabe adónde va"),
        2: ("Armonía", "entender a las personas y unirlas", "una conexión profunda y paz con quienes amas",
            "una persona amable y con quien es fácil hablar"),
        3: ("Expresión", "las palabras, la creatividad y alegrar a los demás", "expresarte y que tu voz sea escuchada",
            "una persona cálida, divertida y llena de vida"),
        4: ("Construcción", "convertir planes en resultados reales y duraderos",
            "seguridad y algo sólido construido por ti", "una persona fiable, constante y de confianza"),
        5: ("Libertad", "adaptarte rápido y aprovechar el cambio", "libertad, variedad y experiencias nuevas",
            "una persona vivaz, curiosa y divertida"),
        6: ("Cuidado", "cuidar a los demás y hacer que cualquier lugar se sienta como un hogar",
            "un hogar con amor y personas que te necesiten", "una persona cariñosa, responsable y de confianza"),
        7: ("Sabiduría", "pensar a fondo y ver lo que otros no ven",
            "tiempo a solas para entender las grandes preguntas de la vida",
            "una persona tranquila, reservada y algo misteriosa"),
        8: ("Logro", "organizar, decidir y hacer que las cosas funcionen",
            "éxito, independencia y reconocimiento por tu trabajo", "una persona capaz, fuerte y con autoridad"),
        9: ("Compasión", "comprender a las personas y servir a una causa mayor", "dejar una huella real en el mundo",
            "una persona generosa, sabia y de gran corazón"),
        11: ("Intuición", "la intuición e inspirar a los demás", "seguir una vocación con verdadero sentido",
             "una persona sensible, inspiradora y con un magnetismo tranquilo"),
        22: ("Gran visión", "convertir grandes visiones en estructuras reales",
             "construir algo duradero que ayude a muchos", "una persona visionaria y a la vez práctica"),
        33: ("Amor que sana", "sanar, enseñar y animar", "dar un amor que transforme vidas",
             "una persona profundamente amorosa y sabia"),
    },
}

TEXT = {
    "en": {
        "hi": "Hi {name},",
        "card_label": "Your numerologist", "card_book": "Book a reading", "card_about": "About",
        "revisit": "Revisit your report",
        "book": "Book a reading with {pfirst}",
        "see_report": "See your report",
        "get_calendar": "Get my free calendar",
        "why": "You're receiving this because you agreed to hear from us when you requested your report.",
        "unsubscribe": "Unsubscribe",
        "subjects": {
            "expression": "{name}, your Expression {n} is your hidden talent",
            "soul_urge": "What your heart is asking for, {name} (Soul Urge {n})",
            "year": "{name}, {year} is a {py} year for you",
            "personality": "Why people see you as a {n}, {name}",
            "days": "{name}, your best days in the next two weeks",
            "harmony": "{name}, how your {lp} and your {ex} work together",
            "last": "{name}, one last thing about your numbers",
        },
        "expression": [
            "Your Expression number is {n}: {title}. It describes the talents you were born with and how you naturally show them.",
            "For you, that means a gift for {gift}. When your work and daily life use this gift, things flow more easily and people notice.",
            "Numerology works like a mirror: it puts words to strengths you may take for granted, so you can choose work, projects and relationships that fit who you are.",
        ],
        "soul_urge": [
            "Your Soul Urge number is {n}: {title}. It's the quiet voice behind your choices: what your heart truly wants.",
            "Deep down, you're looking for {desire}. When that need goes unmet for too long, life can feel restless or flat, even when everything looks fine from the outside.",
            "Knowing your Soul Urge helps you recognise when a decision is right for you, not just right on paper.",
        ],
        "year": [
            "Every year carries its own number for you. {year} is your personal year {py}: {ytitle}.",
            "This year is good for: {focus} Be careful with: {avoid}",
            "Your personal year is about timing: when to start, when to build and when to let go. In a personal reading you can map out the months ahead together.",
        ],
        "personality": [
            "Your Personality number is {n}: {title}. It's the first impression you give, before people really know you.",
            "Others tend to see you as {seen}. That isn't always how you feel inside, especially when your Soul Urge ({su}) is a different number, and that gap explains a lot.",
            "Understanding it helps in interviews, first dates and new teams: you can lead with your strengths on purpose.",
        ],
        "days_intro": "Your personal month right now is {pm}: {mtitle}. These are the days in the next two weeks most in tune with you:",
        "days_same": "personal day {lp}, the same as your Life Path: you're in your element",
        "days_one": "personal day 1: good for starting something new",
        "days_eight": "personal day 8: good for money and decisions",
        "days_more": "Want the whole month, day by day? Get your free personal calendar from your report page.",
        "harmony_intro": "Your Life Path {lp} ({lptitle}) is the road you walk. Your Expression {ex} ({extitle}) is the set of tools you carry.",
        "harmony_support": "These two numbers support each other: what you're here to learn and the way you naturally work point the same way. When you trust that, progress comes more easily.",
        "harmony_same": "They're the same number, which gives you a strong, clear direction. Your challenge is balance: too much of one energy can become a blind spot.",
        "harmony_tension": "These two numbers pull in different directions, which can feel like being torn between what you should do and what comes naturally. It isn't a flaw: it's the most interesting part of your chart, and where real growth happens.",
        "harmony_more": "Your free report shows each number on its own. The full picture is in how they work together.",
        "last": [
            "Over these weeks you've seen your Expression, Soul Urge, Personality and this year's energy. Together with your Life Path {lp}, they form one picture that is yours alone.",
        ],
        "last_book": "{pname} can walk you through your whole chart in a personal reading and answer your questions about what's ahead.",
        "last_none": "Your report stays available, and you can come back to it any time.",
        "last_bye": "This is the last email in this series. Wishing you a wonderful {year}.",
        "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "months": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    },
    "es": {
        "hi": "Hola {name}:",
        "card_label": "Tu guía en numerología", "card_book": "Reservar una lectura", "card_about": "Sobre",
        "revisit": "Volver a tu informe",
        "book": "Reserva una lectura con {pfirst}",
        "see_report": "Ver tu informe",
        "get_calendar": "Pedir mi calendario gratuito",
        "why": "Recibes este correo porque aceptaste recibir noticias nuestras al pedir tu informe.",
        "unsubscribe": "Darte de baja",
        "subjects": {
            "expression": "{name}, tu Expresión {n} es tu talento oculto",
            "soul_urge": "Lo que tu corazón te pide, {name} (Impulso del Alma {n})",
            "year": "{name}, {year} es un año {py} para ti",
            "personality": "Por qué te ven como un {n}, {name}",
            "days": "{name}, tus mejores días de las próximas dos semanas",
            "harmony": "{name}, cómo se llevan tu {lp} y tu {ex}",
            "last": "{name}, una última cosa sobre tus números",
        },
        "expression": [
            "Tu número de Expresión es el {n}: {title}. Describe los talentos con los que naciste y cómo los muestras de forma natural.",
            "En tu caso, es un don para {gift}. Cuando tu trabajo y tu día a día usan este don, todo fluye mejor y los demás lo notan.",
            "La numerología funciona como un espejo: pone en palabras fortalezas que quizá das por sentadas, para que elijas trabajos, proyectos y relaciones que encajen contigo.",
        ],
        "soul_urge": [
            "Tu número del Impulso del Alma es el {n}: {title}. Es la voz silenciosa detrás de tus decisiones: lo que tu corazón realmente quiere.",
            "En el fondo buscas {desire}. Cuando esa necesidad queda sin atender mucho tiempo, la vida puede sentirse inquieta o apagada, aunque todo parezca bien por fuera.",
            "Conocer tu Impulso del Alma te ayuda a reconocer cuándo una decisión es buena para ti, no solo en el papel.",
        ],
        "year": [
            "Cada año tiene su propio número para ti. {year} es tu año personal {py}: {ytitle}.",
            "Este año es bueno para: {focus} Ten cuidado con: {avoid}",
            "Tu año personal habla del momento oportuno: cuándo empezar, cuándo construir y cuándo soltar. En una lectura personal se pueden trazar juntos los meses que vienen.",
        ],
        "personality": [
            "Tu número de Personalidad es el {n}: {title}. Es la primera impresión que das, antes de que te conozcan de verdad.",
            "Los demás suelen verte como {seen}. No siempre coincide con lo que sientes por dentro, sobre todo si tu Impulso del Alma ({su}) es otro número, y esa diferencia explica muchas cosas.",
            "Entenderla ayuda en entrevistas, primeras citas y equipos nuevos: puedes mostrar tus fortalezas a propósito.",
        ],
        "days_intro": "Tu mes personal ahora es el {pm}: {mtitle}. Estos son los días de las próximas dos semanas más en sintonía contigo:",
        "days_same": "día personal {lp}, igual que tu Camino de Vida: estás en tu elemento",
        "days_one": "día personal 1: bueno para empezar algo nuevo",
        "days_eight": "día personal 8: bueno para el dinero y las decisiones",
        "days_more": "¿Quieres el mes completo, día a día? Pide tu calendario personal gratuito desde tu informe.",
        "harmony_intro": "Tu Camino de Vida {lp} ({lptitle}) es el camino que recorres. Tu Expresión {ex} ({extitle}) son las herramientas que llevas contigo.",
        "harmony_support": "Estos dos números se apoyan: lo que vienes a aprender y tu forma natural de trabajar apuntan en la misma dirección. Cuando confías en eso, avanzas con más facilidad.",
        "harmony_same": "Son el mismo número, lo que te da una dirección fuerte y clara. Tu reto es el equilibrio: demasiado de una misma energía puede convertirse en un punto ciego.",
        "harmony_tension": "Estos dos números tiran en direcciones distintas, y puede sentirse como estar entre lo que deberías hacer y lo que te sale natural. No es un defecto: es la parte más interesante de tu carta, y donde ocurre el verdadero crecimiento.",
        "harmony_more": "Tu informe gratuito muestra cada número por separado. La imagen completa está en cómo se combinan.",
        "last": [
            "En estas semanas has visto tu Expresión, tu Impulso del Alma, tu Personalidad y la energía de este año. Junto con tu Camino de Vida {lp}, forman una imagen que es solo tuya.",
        ],
        "last_book": "{pname} puede explicarte tu carta completa en una lectura personal y responder tus preguntas sobre lo que viene.",
        "last_none": "Tu informe sigue disponible y puedes volver a él cuando quieras.",
        "last_bye": "Este es el último correo de esta serie. Te deseo un maravilloso {year}.",
        "weekdays": ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"],
        "months": ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"],
    },
}

# Numbers that traditionally work in concord (masters reduce first)
CONCORDS = ({1, 5, 7}, {2, 4, 8}, {3, 6, 9})


def traits(number: int, lang: str) -> tuple[str, str, str, str]:
    table = TRAITS[lang if lang in TRAITS else "en"]
    if number in table:
        return table[number]
    reduced = number
    while reduced > 9:
        reduced = sum(int(d) for d in str(reduced))
    return table[reduced or 9]


def relation(a: int, b: int) -> str:
    """'same', 'support' or 'tension' between two core numbers."""
    def root(n):
        while n > 9:
            n = sum(int(d) for d in str(n))
        return n
    if a == b:
        return "same"
    ra, rb = root(a), root(b)
    if ra == rb or any(ra in group and rb in group for group in CONCORDS):
        return "support"
    return "tension"
