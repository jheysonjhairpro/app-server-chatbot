import re
from indexing.cleaner import folded


def select(question, candidates, settings):
    terms = set(re.findall(r"\w+", folded(question))) - {
        "que",
        "como",
        "para",
        "los",
        "las",
        "una",
        "del",
        "de",
        "la",
        "el",
        "unamba",
        "ofrece",
        "donde",
        "encuentro",
        "universidad",
        "informacion",
        "cuales",
        "puedo",
    }

    def score(c):
        words = set(
            re.findall(
                r"\w+",
                folded(
                    c["texto"]
                    + " "
                    + c["metadata"]["titulo"]
                    + " "
                    + c["metadata"]["url"]
                ),
            )
        )
        headings = set(
            re.findall(
                r"\w+",
                folded(
                    c["metadata"]["titulo"]
                    + " "
                    + c["metadata"].get("seccion", "")
                    + " "
                    + c["metadata"]["url"]
                ),
            )
        )
        return (
            c["similarity"]
            + 0.15 * len(words & terms) / max(1, len(terms))
            + 0.30 * len(headings & terms) / max(1, len(terms))
        )

    selected = []
    sets = []
    context = []
    used = 0
    best = max((c["similarity"] for c in candidates), default=0)
    for candidate in sorted(candidates, key=score, reverse=True):
        if candidate["similarity"] < settings.min_similarity:
            continue
        if selected and candidate["similarity"] < best - 0.18:
            continue
        words = set(re.findall(r"\w+", folded(candidate["texto"])))
        if any(len(words & s) / max(1, len(words | s)) > 0.8 for s in sets):
            continue
        source = f"[{len(selected)+1}] {candidate['metadata']['titulo']}\nFuente: {candidate['metadata']['url']}\n"
        if candidate["metadata"].get("fecha_publicacion"):
            source += (
                "Fecha publicada: " + candidate["metadata"]["fecha_publicacion"] + "\n"
            )
        available = settings.max_context_chars - used - len(source) - 2
        if available < 80:
            break
        text = candidate["texto"]
        # Compatibility cleanup for fragments produced before share controls were removed.
        text = "\n".join(
            line
            for line in text.splitlines()
            if not any(
                marker in line.lower()
                for marker in [
                    "facebook.com/sharer",
                    "twitter.com/intent",
                    "x.com/intent",
                    "linkedin.com/share",
                    "api.whatsapp.com/send",
                ]
            )
        )
        if not text.strip():
            continue
        if len(text) > available:
            text = text[:available].rsplit(" ", 1)[0]
        block = source + text
        context.append(block)
        used += len(block) + 2
        selected.append(candidate)
        sets.append(words)
        if len(selected) >= settings.top_k_final:
            break
    return "\n\n".join(context), selected
