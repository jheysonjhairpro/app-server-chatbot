"""Extraccion estructural de HTML publico; no descarga archivos enlazados."""
import re
import unicodedata
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Comment, Doctype, NavigableString, Tag

VERSION = "html-structure-v3"
ERROR_PAGE = re.compile(r"pagina no encontrada|page not found|^not found|^(?:404\b|error\s+404\b)|can nott? be found", re.I)
EMOJI = re.compile("[0-9#*]\ufe0f?\u20e3|[\U0001f1e6-\U0001f1ff\U0001f300-\U0001faff\u2600-\u27bf\u2300-\u23ff]|[\ufe0e\ufe0f\u200d]")
CHROME = re.compile(r"(?:^|[-_\s])(?:navbar|navigation|breadcrumb|sidebar|copyright|chatbot|chat-bot|cookie-banner|site-header|main-menu|related-posts|social-share|visitor-counter|slick-cloned|swiper-slide-duplicate|cloned|toast|invalid-feedback)(?:$|[-_\s])", re.I)
FOOTERS = {"footer", "site-footer", "main-footer", "footer-area", "footer-wrapper", "footer-section", "m-footer", "colophon"}
NOISE = re.compile(r"^(?:©|copyright|cargando[. …]*|previous next\b|contador de visitas\b|asistente virtual en fase experimental|(?:#+\s*)?(?:inicio de sesi[oó]n|recuperar acceso|acceso al sistema)|(?:¿?olvidaste tu contrase[nñ]a)|contrase[nñ]a:|n[uú]mero de dni:|dni:|no se encontraron (?:carreras|servicios) (?:que coincidan con tu b[uú]squeda|con ese nombre en UNAMBA)[.]?)$", re.I)
BLOCKS = {"p", "div", "section", "article", "main", "li", "ul", "ol", "dl", "dt", "dd", "blockquote", "details", "summary", "address", "header"}


def clean(value):
    return re.sub(r"\s+", " ", EMOJI.sub("", unicodedata.normalize("NFKC", value))).strip()


def link_url(value, base):
    value = (value or "").strip()
    if not value or value.startswith(("#", "javascript:", "data:")):
        return ""
    absolute = urljoin(base, value)
    p = urlsplit(absolute)
    if p.scheme not in ("http", "https") or not p.hostname:
        return ""
    if any(host in p.hostname for host in ("doubleclick.net", "googlesyndication.com", "googleadservices.com", "gaviaspreview.com")) or "/recaptcha/" in p.path:
        return ""
    return urlunsplit((p.scheme, p.netloc, quote(p.path, safe="/%:@!$&'()*+,;=-._~"),
                      quote(p.query, safe="%=&+/:?@!$'()*;,[]-._~"), quote(p.fragment, safe="%/?@-._~")))


def extract_html(html, base):
    soup = BeautifulSoup(html, "html.parser")
    title = clean(soup.title.get_text(" ", strip=True)) if soup.title else ""
    for comment in soup.find_all(string=lambda x: isinstance(x, Comment)):
        comment.extract()
    # Inspect controls before removing them; otherwise password forms become undetectable.
    for form in list(soup.find_all("form")):
        if form.find("input", attrs={"type": "password"}):
            form.decompose()
    for node in list(soup.find_all(True)):
        if node.name is None or node.attrs is None or node.name in {"html", "body"}:
            continue
        identity = " ".join([node.get("id", ""), *node.get("class", [])])
        remove = node.name in {"head", "script", "style", "noscript", "svg", "canvas", "nav", "footer", "input", "select", "textarea"}
        remove |= node.get("role") in {"navigation", "contentinfo", "banner"}
        remove |= bool(CHROME.search(identity))
        remove |= bool(set(identity.split()) & FOOTERS)
        # Article headers contain the title/date; only remove page-level headers.
        remove |= node.name == "header" and not node.find_parent(["article", "main"])
        remove |= node.get("type") in {"submit", "reset"}
        remove |= node.name == "marquee" and bool(re.search(r"width\s*:\s*0(?:px)?", node.get("style", "")))
        if remove:
            node.decompose()
    for node in list(soup.select('[hidden], [aria-hidden="true"], .alert-success, .invalid-feedback')):
        if node.attrs is None:
            continue
        # Collapsed tabs/accordions can hold genuine content and must remain indexed.
        if node.name in {"input", "button"} or node.get("role") in {"alert", "status"} or "alert-success" in node.get("class", []):
            node.decompose()
    lines, buffer = [], []

    def flush():
        value = clean(" ".join(buffer))
        buffer.clear()
        if value and not re.search(r"(?:tu navegador|your browser).*(?:video|html.?5)", value, re.I):
            lines.append(value)

    def walk(node):
        if isinstance(node, (Doctype, Comment)):
            return
        if isinstance(node, NavigableString):
            buffer.append(str(node))
            return
        if not isinstance(node, Tag):
            return
        name = node.name
        if name == "div" and not node.find(["a", "table", "button"]):
            children = node.find_all("div", recursive=False)
            if len(children) == 2 and not any(c.find("div") for c in children):
                values = [clean(c.get_text(" ", strip=True)) for c in children]
                if re.fullmatch(r"\d{4,6}", values[0]) and re.search(r"[A-Za-z]", values[1]):
                    flush()
                    lines.append(" | ".join(values))
                    return
        if re.fullmatch(r"h[1-6]", name or ""):
            flush()
            value = clean(node.get_text(" ", strip=True))
            if value:
                lines.append("#" * int(name[1]) + " " + value)
            for anchor in node.find_all("a", href=True):
                url = link_url(anchor.get("href"), base)
                if url:
                    lines.append(f"{clean(anchor.get_text(' ', strip=True)) or 'Enlace complementario'}: {url}")
            return
        if name == "table":
            flush()
            if not node.find("td"):
                return
            headers = []
            for row in node.find_all("tr"):
                if row.find_parent("table") != node:
                    continue
                cells = []
                for cell in row.find_all(["th", "td"], recursive=False):
                    value = clean(cell.get_text(" ", strip=True))
                    urls = [link_url(a.get("href"), base) for a in cell.find_all("a", href=True)]
                    cells.append(value + " " + " ".join(u for u in urls if u and u not in value))
                if cells:
                    if row.find("th") and not row.find("td"):
                        headers = [c.strip() for c in cells]
                    elif headers and len(headers) == len(cells):
                        cells = [f"{h}: {c.strip()}" for h, c in zip(headers, cells)]
                    lines.append(" | ".join(c.strip() for c in cells))
            return
        if name in {"iframe", "video", "audio", "source", "embed", "object"}:
            url = link_url(node.get("src") or node.get("data-src") or node.get("data"), base)
            if url:
                flush()
                lines.append(f"{clean(node.get('title') or 'Recurso complementario')}: {url}")
            for child in node.find_all("source", recursive=False):
                walk(child)
            return
        if name in {"a", "button"}:
            value = clean(node.get_text(" ", strip=True))
            if not value and node.find("img", alt=True):
                value = clean(node.find("img", alt=True).get("alt", ""))
            target = node.get("href") or node.get("data-href") or node.get("data-url")
            if not target:
                match = re.search(r"(?:window\.open\(|(?:window\.)?location(?:\.href)?\s*=)\s*['\"]([^'\"]+)['\"]", node.get("onclick", ""))
                target = match[1] if match else None
            url = link_url(target, base)
            if url:
                flush()
                lines.append(f"{value or clean(node.get('title') or node.get('aria-label') or 'Enlace complementario')}: {url}")
            elif value and (name == "a" or re.fullmatch(r"[\d.,\s]+", value) or node.get("aria-controls") or
                            node.get("data-bs-toggle") in {"collapse", "tab"} or
                            node.get("data-toggle") in {"collapse", "tab"} or
                            "accordion-button" in node.get("class", [])):
                buffer.append(value)
            return
        if name == "img":
            return
        if name in BLOCKS or name == "br":
            flush()
        for child in node.children:
            walk(child)
        if name in BLOCKS:
            flush()

    walk(soup.body or soup)
    flush()
    lines = [l for l in lines if not NOISE.search(l) and not l.startswith("©")]
    text = "\n".join(lines)
    normalized_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    invalid = bool(ERROR_PAGE.search(normalized_title) or any(
        ERROR_PAGE.search(unicodedata.normalize("NFKD", l.lstrip("# ")).encode("ascii", "ignore").decode())
        for l in lines if l.startswith("#")))
    return title, text, invalid


def structured_chunks(text, size, overlap):
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("Se requiere 0 <= overlap < chunk_chars")
    chunks, headings, body = [], {}, []

    def prefix():
        return [headings[k] for k in sorted(headings)]

    def emit():
        if body or headings:
            chunks.append("\n".join(prefix() + body))

    for line in text.splitlines():
        match = re.match(r"^(#{1,6})\s+", line)
        if match:
            level = len(match[1])
            if body or level <= max(headings, default=0):
                emit()
            body = []
            headings = {k: v for k, v in headings.items() if k < level}
            headings[level] = line
            continue
        # URLs and rows are atomic. Very long prose is split only at whitespace.
        pieces = [line]
        if len(line) > size and "http" not in line and " | " not in line:
            pieces, current = [], ""
            for word in line.split():
                if len(current) + len(word) + 1 > size and current:
                    pieces.append(current)
                    current = ""
                current = (current + " " + word).strip()
            if current:
                pieces.append(current)
        for piece in pieces:
            if body and len("\n".join(prefix() + body + [piece])) > size:
                emit()
                tail = []
                for previous in reversed(body):
                    if len("\n".join([previous] + tail)) > overlap:
                        break
                    tail.insert(0, previous)
                body = tail if len("\n".join(prefix() + tail + [piece])) <= size else []
            body.append(piece)
    emit()
    return chunks
