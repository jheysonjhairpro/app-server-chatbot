import asyncio
import ipaddress
import posixpath
import socket
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, unquote

DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".odt",
    ".ods",
    ".rtf",
    ".zip",
    ".rar",
    ".csv",
}
ASSET_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".webp",
    ".mp4",
    ".mp3",
    ".css",
    ".js",
    ".woff",
    ".ico",
    ".xml",
}


def normalize(url: str) -> str:
    p = urlsplit(url)
    if (
        p.scheme.lower() not in {"http", "https"}
        or not p.hostname
        or p.username
        or p.password
    ):
        raise ValueError("Invalid public URL")
    if p.port not in (None, 80, 443):
        raise ValueError("Nonstandard port")
    host = p.hostname.lower().encode("idna").decode()
    path = posixpath.normpath(p.path or "/")
    decoded = unquote(p.path).lower()
    if any(segment in {".", ".."} for segment in decoded.split("/")) and "%" in p.path:
        raise ValueError("Encoded traversal rejected")
    if "%2f" in p.path.lower() or "%5c" in p.path.lower() or "\\" in p.path:
        raise ValueError("Ambiguous path rejected")
    if p.path.endswith("/") and not path.endswith("/"):
        path += "/"
    query = [
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}
    ]
    return urlunsplit((p.scheme.lower(), host, path, urlencode(sorted(query)), ""))


def is_document(url: str) -> bool:
    p = urlsplit(url)
    # Download endpoints are catalogued without requesting their bodies.
    return (
        posixpath.splitext(unquote(p.path).lower())[1] in DOCUMENT_EXTENSIONS
        or any(
            posixpath.splitext(unquote(v).lower())[1] in DOCUMENT_EXTENSIONS
            for _, v in parse_qsl(p.query)
        )
        or any(
            word in p.path.lower()
            for word in ["/uploads/", "/download", "/descarga", "/attachment"]
        )
    )


class URLPolicy:
    def __init__(self, settings):
        self.domains = set(settings.allowed_domains)
        self.scopes = settings.shared_scopes

    def allowed(self, url: str, auxiliary=False, resource=False) -> bool:
        try:
            url = normalize(url)
            p = urlsplit(url)
            if p.hostname not in self.domains or is_document(url):
                return False
            if any(
                segment
                in {
                    "login",
                    "logout",
                    "wp-admin",
                    "wp-login.php",
                    "admin",
                    "delete",
                    "eliminar",
                    "institution_feedbacks",
                }
                for segment in p.path.lower().split("/")
            ):
                return False
            if any(
                k.lower() in {"action", "accion"}
                and v.lower() in {"delete", "remove", "eliminar", "logout"}
                for k, v in parse_qsl(p.query)
            ):
                return False
            if auxiliary and (p.path == "/robots.txt" or "sitemap" in p.path.lower()):
                return True
            if posixpath.splitext(p.path.lower())[1] in ASSET_EXTENSIONS and not (
                resource and p.path.lower().endswith(".js")
            ):
                return False
            scopes = self.scopes.get(p.hostname)
            if scopes and not any(
                p.path == s
                or p.path.startswith(s.rstrip("/") + "/")
                or (
                    p.hostname == "www.gob.pe"
                    and re.fullmatch(r"/\d+", s)
                    and p.path.startswith(s + "-")
                    and "/" not in p.path[1:]
                )
                for s in scopes
            ):
                return False
            if p.hostname == "www.transparencia.gob.pe":
                params = dict(parse_qsl(p.query))
                if params.get("id_entidad", "13625") != "13625":
                    return False
                # All content requests on this shared portal must carry the entity.
                if params.get("id_entidad") != "13625":
                    return False
            return True
        except (ValueError, UnicodeError):
            return False

    async def public_ip(self, url: str, auxiliary=False, resource=False) -> str:
        if not self.allowed(url, auxiliary, resource):
            raise ValueError("URL outside authorized scope")
        host = urlsplit(url).hostname
        records = await asyncio.to_thread(
            socket.getaddrinfo, host, None, type=socket.SOCK_STREAM
        )
        addresses = [r[4][0] for r in records]
        if not addresses or any(
            not ipaddress.ip_address(a).is_global for a in addresses
        ):
            raise ValueError("Private, local or reserved address rejected")
        return addresses[0]
