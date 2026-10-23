"""The approved roofing guides a report can carry (2026-10-22, the owner): short, plain-language
explainers in English and Spanish, shown at the end of a report under "Understanding your roof".

The AI never writes this text - it only picks which guides (by KEY) match the report's
findings; a reviewer can add or remove them before approving. Change the wording HERE (the owner
approves it); a key never changes once used, so old reports keep rendering. No prices, no
promises, no insurance or AHS approval, nothing about a specific customer.
"""
from __future__ import annotations

MAX_PER_REPORT = 3

GUIDES: dict[str, dict[str, tuple[str, str]]] = {
    "flashing": {
        "en": ("Where the roof meets a wall",
               ("Flashing is the metal or membrane that seals the joint where a roof meets a "
                "wall, a chimney or a raised section. Rain runs down the wall and straight "
                "into that joint, so it is one of the most common places for leaks. Over time "
                "the sealant dries and cracks, the flashing can loosen, and water slips "
                "behind it - often showing up inside far from the actual opening.")),
        "es": ("Donde el techo se une a una pared",
               ("El flashing es el metal o la membrana que sella la unión entre el techo y "
                "una pared, una chimenea o una sección elevada. La lluvia baja por la pared y "
                "llega directo a esa unión, por eso es uno de los lugares más comunes de "
                "filtraciones. Con el tiempo el sellador se seca y se agrieta, el flashing "
                "puede soltarse y el agua se mete por detrás - y muchas veces aparece adentro "
                "lejos de la abertura.")),
    },
    "tiles": {
        "en": ("Cracked, broken or slipped tiles",
               ("Tiles shed most of the rain, but they are not the waterproof layer - the "
                "underlayment beneath them is. A cracked, broken or slipped tile lets water "
                "and sun reach that layer directly, and it wears out much faster there. One "
                "damaged tile can lead to a leak and to more tiles loosening around it.")),
        "es": ("Tejas agrietadas, rotas o desplazadas",
               ("Las tejas desvían la mayor parte de la lluvia, pero no son la capa "
                "impermeable - esa es la membrana que está debajo. Una teja agrietada, rota o "
                "desplazada deja que el agua y el sol lleguen directo a esa capa, y allí se "
                "desgasta mucho más rápido. Una sola teja dañada puede causar una filtración "
                "y que se suelten otras.")),
    },
    "shingles": {
        "en": ("Worn, lifted or missing shingles",
               ("Shingles lose their protective granules with age and sun, curl at the edges "
                "and can be lifted or torn off by wind. Bare, curled or missing shingles let "
                "water reach the layers underneath and the wood deck, where leaks and rot "
                "begin.")),
        "es": ("Shingles gastados, levantados o faltantes",
               ("Los shingles pierden sus gránulos protectores con los años y el sol, se "
                "curvan en los bordes y el viento puede levantarlos o arrancarlos. Un shingle "
                "pelado, curvo o faltante deja que el agua llegue a las capas de abajo y a la "
                "madera, donde empiezan las filtraciones y la pudrición.")),
    },
    "underlayment": {
        "en": ("The waterproof layer under your roof",
               ("Under tiles or shingles there is an underlayment - the roof's real "
                "waterproof barrier. It lasts many years, but once it is old, torn or exposed "
                "it can no longer stop water that gets past the surface. That is why a roof "
                "can leak even when the tiles or shingles look mostly fine from the ground.")),
        "es": ("La capa impermeable debajo de su techo",
               ("Debajo de las tejas o shingles hay una membrana (underlayment) - la "
                "verdadera barrera contra el agua del techo. Dura muchos años, pero cuando "
                "está vieja, rota o expuesta ya no puede detener el agua que pasa la "
                "superficie. Por eso un techo puede filtrar aunque las tejas se vean bien "
                "desde el suelo.")),
    },
    "sealant": {
        "en": ("Old sealant and patches",
               ("Sealants and patch materials are useful repairs, but Florida sun and heat "
                "dry them out. Dark, cracked or peeling patches usually mean the seal is no "
                "longer tight. Patches also show where water has entered before, so those "
                "areas are worth watching closely.")),
        "es": ("Selladores y parches viejos",
               ("Los selladores y parches son buenas reparaciones, pero el sol y el calor de "
                "Florida los resecan. Un parche oscuro, agrietado o despegado normalmente "
                "indica que ya no sella bien. Los parches también muestran donde ya entró "
                "agua antes, así que conviene vigilar esas zonas.")),
    },
    "debris": {
        "en": ("Debris, valleys and drainage",
               ("Leaves, branches and loose pieces trap water on the roof and block valleys "
                "and gutters. Water that cannot drain finds its way under tiles, shingles and "
                "flashing, and keeps the materials wet much longer than they are made for.")),
        "es": ("Escombros, canales y desagüe",
               ("Las hojas, ramas y piezas sueltas retienen agua en el techo y tapan los "
                "canales y las canaletas. El agua que no puede correr se mete debajo de las "
                "tejas, los shingles y el flashing, y mantiene los materiales mojados mucho "
                "más tiempo del que soportan.")),
    },
    "flat_roof": {
        "en": ("Flat roofs and standing water",
               ("Flat and low-slope roofs drain slowly. Water that stays in one spot for days "
                "- called ponding - wears the surface faster and finds any small opening, "
                "seam or crack. Blisters, cracks or soft spots on a flat roof are early signs "
                "to address.")),
        "es": ("Techos planos y agua estancada",
               ("Los techos planos o de poca pendiente desaguan despacio. El agua que se "
                "queda en un mismo lugar por días desgasta más rápido la superficie y "
                "encuentra cualquier abertura, unión o grieta. Ampollas, grietas o zonas "
                "blandas en un techo plano son señales tempranas que conviene atender.")),
    },
    "penetrations": {
        "en": ("Pipes, vents and skylights",
               ("Every pipe, vent or skylight is a hole in the roof sealed with a boot or "
                "flashing. Those seals crack with sun and time, and a small gap around one of "
                "them is a frequent source of drips inside, often near a bathroom or kitchen.")),
        "es": ("Tubos, ventilaciones y tragaluces",
               ("Cada tubo, ventilación o tragaluz es un orificio en el techo sellado con una "
                "goma o flashing. Esos sellos se agrietan con el sol y el tiempo, y una "
                "pequeña abertura alrededor de uno de ellos es una causa frecuente de "
                "goteras, a menudo cerca de un baño o la cocina.")),
    },
    "water_damage": {
        "en": ("Signs of water inside",
               ("Stains on the ceiling, bubbling paint or a musty smell mean water has "
                "already reached the inside of the home. The wet spot is not always under the "
                "opening - water travels along the deck and framing first. Wood that stays "
                "wet can soften and rot, and moisture invites mold.")),
        "es": ("Señales de agua adentro",
               ("Manchas en el techo interior, pintura con burbujas o olor a humedad indican "
                "que el agua ya llegó al interior de la casa. La mancha no siempre está "
                "debajo de la abertura - el agua primero corre por la madera y la estructura. "
                "La madera que sigue mojada puede ablandarse y pudrirse, y la humedad "
                "favorece el moho.")),
    },
    "small_leaks": {
        "en": ("Why a small leak gets worse",
               ("A small leak rarely stays small. Every rain soaks the same materials again, "
                "the opening grows, and hidden damage spreads to the wood deck, insulation "
                "and ceiling before it is visible. Fixing the cause early is usually simpler "
                "than repairing the damage it leaves behind.")),
        "es": ("Por qué una filtración pequeña empeora",
               ("Una filtración pequeña casi nunca se queda pequeña. Cada lluvia vuelve a "
                "mojar los mismos materiales, la abertura crece y el daño oculto se extiende "
                "a la madera, el aislamiento y el techo interior antes de que se vea. "
                "Corregir la causa a tiempo suele ser más sencillo que reparar el daño que "
                "deja.")),
    },
    "florida_climate": {
        "en": ("Florida sun, heat and storms",
               ("Roofs in Florida work hard: strong sun and heat age sealants and materials, "
                "daily summer rain tests every seam, and storms bring wind that lifts and "
                "loosens. That is why roofs here show wear sooner and benefit from regular "
                "checks.")),
        "es": ("El sol, el calor y las tormentas de Florida",
               ("Los techos en Florida trabajan mucho: el sol fuerte y el calor envejecen los "
                "selladores y materiales, las lluvias diarias de verano ponen a prueba cada "
                "unión y las tormentas traen viento que levanta y afloja. Por eso los techos "
                "aquí se desgastan antes y se benefician de revisiones regulares.")),
    },
    "maintenance": {
        "en": ("Keeping your roof healthy",
               ("A few simple habits help a roof last: keep gutters and valleys clear, trim "
                "branches that touch the roof, look at the ceilings after heavy rain, and "
                "have the roof checked after a strong storm. Catching a problem early keeps "
                "it small.")),
        "es": ("Cómo mantener su techo en buen estado",
               ("Algunos hábitos sencillos ayudan a que el techo dure: mantener limpias las "
                "canaletas y los canales, cortar las ramas que tocan el techo, revisar los "
                "techos interiores después de lluvias fuertes y hacer revisar el techo "
                "después de una tormenta fuerte. Detectar un problema a tiempo lo mantiene "
                "pequeño.")),
    },
}

# What each guide is about, for the AI choosing them (English only; never shown to customers).
ABOUT = {
    "flashing": "roof-to-wall joints, chimneys, raised sections, flashing",
    "tiles": "cracked, broken, slipped or missing tiles",
    "shingles": "worn, curled, lifted or missing shingles",
    "underlayment": "the waterproof layer under tiles/shingles; exposed or old underlayment",
    "sealant": "old sealant, dark/cracked patches, previous repairs",
    "debris": "debris, leaves, clogged valleys or gutters, drainage",
    "flat_roof": "flat or low-slope roof, ponding water, blisters",
    "penetrations": "pipes, vents, skylights, roof boots",
    "water_damage": "stains, wet spots or damage inside the home",
    "small_leaks": "an active or recent leak (why it gets worse)",
    "florida_climate": "general sun/heat/storm wear",
    "maintenance": "general upkeep advice",
}

TITLE = {"en": "Understanding your roof", "es": "Entienda su techo"}


def valid(keys) -> list[str]:
    """Known keys, in order, no repeats, at most MAX_PER_REPORT."""
    out: list[str] = []
    for k in keys or []:
        if isinstance(k, str) and k in GUIDES and k not in out:
            out.append(k)
    return out[:MAX_PER_REPORT]


def render(keys, language: str) -> list[dict]:
    """[{key, title, body}] in the report's language (English when a language is missing)."""
    lang = language if language in ("en", "es") else "en"
    return [{"key": k, "title": GUIDES[k][lang][0], "body": GUIDES[k][lang][1]}
            for k in valid(keys)]


def catalog() -> list[dict]:
    return [{"key": k, "title": v["en"][0], "about": ABOUT[k]} for k, v in GUIDES.items()]
