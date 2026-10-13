from bs4 import BeautifulSoup
from indexing.cleaner import clean
from indexing.classifier import classify


def parse_html(html, url):
    soup = BeautifulSoup(html, "lxml")
    for a in soup.select("a[href]"):
        if any(
            marker in a["href"].lower()
            for marker in [
                "facebook.com/sharer",
                "twitter.com/intent",
                "x.com/intent",
                "linkedin.com/share",
                "api.whatsapp.com/send",
            ]
        ):
            a.decompose()
    root = (
        soup.find("main")
        or soup.select_one(".entry-content")
        or soup.select_one("#contenido")
        or soup.select_one("#content")
        or soup.find("article")
        or soup.body
        or soup
    )
    title = root.find("h1") or soup.title
    title = title.get_text(" ", strip=True) if title else url
    date = soup.select_one(
        'meta[property="article:published_time"], meta[name="date"], time[datetime]'
    )
    published = (date.get("content") or date.get("datetime")) if date else None
    for node in soup.select(
        'script,style,noscript,nav,[role="navigation"],.navbar,.menu,.cookie-banner,#cookie-banner,.advertisement,form'
    ):
        node.decompose()
    for node in soup.find_all("header"):
        if (
            root not in node.parents
            and node is not root
            and root not in node.descendants
        ):
            node.decompose()
    from urllib.parse import urljoin

    for a in root.select("a[href]"):
        href = urljoin(url, a["href"])
        name = a.get_text(" ", strip=True)
        if (
            name
            and href.startswith(("http://", "https://"))
            and not a.find_parent(["h1", "h2", "h3", "h4"])
        ):
            a.clear()
            a.append(name + " (" + href + ")")
    blocks, heading, seen = [], "", set()
    headings = {}
    for node in root.find_all(
        ["h1", "h2", "h3", "h4", "p", "ul", "ol", "table", "address", "a"]
    ):
        if node.find_parent(["table", "ul", "ol"]):
            continue
        if node.name == "a" and node.find_parent(
            ["p", "address", "h1", "h2", "h3", "h4"]
        ):
            continue
        if node.name.startswith("h"):
            level = int(node.name[1])
            headings = {k: v for k, v in headings.items() if k < level}
            headings[level] = clean(node.get_text(" ", strip=True))
            heading = " > ".join(headings.values())
            continue
        if node.name == "table":
            text = "\n".join(
                " | ".join(
                    c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])
                )
                for tr in node.find_all("tr")
            )
        elif node.name in {"ul", "ol"}:
            text = "\n".join(
                "- " + li.get_text(" ", strip=True)
                for li in node.find_all("li", recursive=False)
            )
        else:
            text = node.get_text(" ", strip=True)
        text = clean(text)
        if text and text not in seen:
            seen.add(text)
            blocks.append({"heading": heading, "text": text, "kind": node.name})
    if not blocks:
        import trafilatura

        text = trafilatura.extract(html, include_tables=True, include_links=True) or ""
        if text:
            blocks.append({"heading": "", "text": clean(text), "kind": "p"})
    # Contact data frequently lives outside <main>. Keep explicit contacts in the footer.
    import re

    for footer in soup.find_all("footer"):
        if footer is root or root in footer.parents:
            continue
        for node in footer.find_all(["p", "address", "a"]):
            value = clean(node.get_text(" ", strip=True))
            if (
                value
                and value not in seen
                and (
                    node.name == "address"
                    or re.search(
                        r"@|\b\d[\d -]{5,}\d\b|horario|ubicaci[oó]n|avenida|av\.",
                        value,
                        re.I,
                    )
                )
            ):
                seen.add(value)
                blocks.append(
                    {"heading": "Contacto oficial", "text": value, "kind": "contact"}
                )
    text = "\n\n".join(
        (b["heading"] + "\n" if b["heading"] else "") + b["text"] for b in blocks
    )
    return {
        "url": url,
        "titulo": title,
        "fecha_publicacion": published,
        "blocks": blocks,
        "texto": text,
        "categorias": classify(url, title, text),
        "campos": {
            key: None
            for key in [
                "requisitos",
                "costo",
                "oficina_responsable",
                "plazo",
                "horario",
            ]
        },
    }
