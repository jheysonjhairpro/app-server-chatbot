from urllib.parse import urljoin, urlsplit, parse_qsl, urlencode, urlunsplit
from bs4 import BeautifulSoup
from crawler.url_manager import normalize, is_document


def discover(html, base):
    soup = BeautifulSoup(html, "lxml")
    links = {}
    for a in soup.select(
        'a[href], area[href], iframe[src], link[rel="next"], link[rel="prev"]'
    ):
        raw = a.get("href") or a.get("src")
        if not raw or raw.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        try:
            url = normalize(urljoin(base, raw))
        except ValueError:
            continue
        # ASP.NET links sometimes omit entity; retain current institutional context.
        p, current = urlsplit(url), urlsplit(base)
        if (
            p.hostname == current.hostname == "www.transparencia.gob.pe"
            and p.path.startswith("/enlaces/")
        ):
            query = dict(parse_qsl(p.query))
            if "id_entidad" not in query:
                query["id_entidad"] = dict(parse_qsl(current.query)).get(
                    "id_entidad", "13625"
                )
                url = normalize(
                    urlunsplit((p.scheme, p.netloc, p.path, urlencode(query), ""))
                )
        parent = a.find_parent(["li", "tr", "article", "p"])
        label = (
            a.get_text(" ", strip=True)
            or a.get("title")
            or urlsplit(url).path.rsplit("/", 1)[-1]
        )
        if is_document(url) and label.lower() in {
            "descargar",
            "descargar pdf",
            "download",
            "ver",
        }:
            container = a.parent
            for _ in range(5):
                if not container or container.name in {"body", "html"}:
                    break
                heading = container.find(["h2", "h3", "h4", "h5"])
                document_anchors = [
                    node
                    for node in container.select("a[href]")
                    if is_document(urljoin(base, node["href"]))
                ]
                if heading and len(document_anchors) == 1:
                    label = heading.get_text(" ", strip=True)
                    parent = container
                    break
                container = container.parent
        time = parent.find("time") if parent else None
        links[url] = {
            "url": url,
            "nombre": label,
            # An entire article/list is not a description of every attached file.
            "descripcion": (
                parent.get_text(" ", strip=True)
                if parent and len(parent.get_text(" ", strip=True)) <= 500
                else None
            ),
            "fecha_publicacion": (
                (time.get("datetime") or time.get_text(strip=True)) if time else None
            ),
            "documento": is_document(url),
        }
    return list(links.values())
