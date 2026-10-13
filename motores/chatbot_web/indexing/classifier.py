from indexing.cleaner import folded

RULES = {
    "Universidad": ["universidad", "mision", "vision", "historia"],
    "Autoridades": ["rector", "autoridades", "decanos", "consejo universitario"],
    "Facultades": ["facultad"],
    "Escuelas profesionales": ["escuela profesional", "carrera"],
    "Admisión": ["admision", "ingresantes", "examen de ingreso"],
    "Matrícula": ["matricula"],
    "Trámites académicos": [
        "tramite academico",
        "constancia",
        "certificado",
        "bachiller",
        "titulo profesional",
    ],
    "Trámites administrativos": ["tramite administrativo", "tupa", "mesa de partes"],
    "Grados y títulos": ["bachiller", "grado academico", "titulo profesional"],
    "Constancias y certificados": ["constancia", "certificado"],
    "Servicios estudiantiles": [
        "bienestar",
        "servicios universitarios",
        "comedor",
        "biblioteca",
    ],
    "Becas": ["beca", "pronabec"],
    "Investigación": ["investigacion", "investigador"],
    "Posgrado": ["posgrado", "maestria", "doctorado"],
    "Reglamentos": ["reglamento", "directiva", "estatuto"],
    "Resoluciones": ["resolucion", "resoluciones"],
    "Transparencia": ["transparencia", "rendicion", "presupuesto"],
    "Convocatorias": ["convocatoria", "concurso"],
    "Noticias institucionales": ["noticias", "actualidad"],
    "Oficinas administrativas": ["oficina", "direccion administrativa"],
    "Pagos y tasas": ["pago", "costo", "tasa", "tarifa"],
    "Calendarios y cronogramas": ["calendario", "cronograma"],
}


def classify(url, title, text):
    strong = folded(url + " " + title)
    body = folded(text)
    scored = [
        (cat, sum(3 if k in strong else 1 if k in body else 0 for k in keys))
        for cat, keys in RULES.items()
    ]
    return [cat for cat, score in sorted(scored, key=lambda x: -x[1]) if score > 0] or [
        "Información general"
    ]


def query_category(question):
    q = folded(question)
    hits = [
        (cat, sum(k in q for k in keys))
        for cat, keys in RULES.items()
        if cat != "Universidad"
    ]
    hits.sort(key=lambda x: -x[1])
    return (
        hits[0][0]
        if hits[0][1]
        and (
            hits[0][1] > hits[1][1]
            or hits[0][0]
            in {
                "Constancias y certificados",
                "Resoluciones",
                "Admisión",
                "Transparencia",
            }
        )
        else None
    )
