from indexing.cleaner import clean, content_hash


def chunk_page(page, size=900, overlap=120):
    prefix = "UNAMBA > " + page["categorias"][0] + " > " + page["titulo"]
    prefix = prefix[: min(180, size // 3)]
    output, current, heading = [], "", ""

    def emit(text, section):
        body = clean(text)
        if body:
            output.append(
                {
                    "texto": prefix
                    + (" > " + section[:100] if section else "")
                    + "\n"
                    + body,
                    "seccion": section,
                }
            )

    for block in page["blocks"]:
        section, text = block["heading"], block["text"]
        budget = max(100, size - len(prefix) - len(section[:100]) - 5)
        if current and (section != heading or len(current) + len(text) + 2 > budget):
            emit(current, heading)
            current = ""
        heading = section
        if len(text) <= budget:
            current = (current + "\n" + text).strip()
            continue
        if current:
            emit(current, heading)
            current = ""
        words, start = text.split(), 0
        while start < len(words):
            end, count = start, 0
            while end < len(words) and (
                count + len(words[end]) + 1 <= budget or end == start
            ):
                count += len(words[end]) + 1
                end += 1
            emit(" ".join(words[start:end]), heading)
            if end == len(words):
                break
            back, chars = end, 0
            while back > start + 1 and chars + len(words[back - 1]) + 1 <= overlap:
                back -= 1
                chars += len(words[back]) + 1
            start = back
    if current:
        emit(current, heading)
    unique = {}
    for chunk in output:
        unique.setdefault(content_hash(chunk["texto"]), chunk)
    return list(unique.values())
