import asyncio
import time
import random
from dataclasses import dataclass
from urllib.parse import urlsplit, urljoin
from urllib.robotparser import RobotFileParser
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
import httpx
from crawler.url_manager import normalize, is_document

USER_AGENT = "UNAMBAWebIndexer/1.0"


class Blocked(Exception):
    pass


class NotHTML(Exception):
    def __init__(self, message, document=False):
        super().__init__(message)
        self.document = document


@dataclass
class Response:
    url: str
    status: int
    body: bytes
    headers: dict


class Fetcher:
    def __init__(self, settings, policy):
        self.settings, self.policy = settings, policy
        self.client = httpx.AsyncClient(
            timeout=settings.request_timeout, follow_redirects=False, trust_env=False
        )
        self.locks = {}
        self.last = {}
        self.robots = {}
        self.robot_locks = {}
        self.delays = {}

    async def _request(
        self, url, auxiliary=False, xml=False, headers=None, resource=False
    ):
        host = urlsplit(url).hostname
        lock = self.locks.setdefault(host, asyncio.Lock())
        async with lock:
            delay = max(self.settings.domain_delay, self.delays.get(host, 0))
            await asyncio.sleep(
                max(0, delay - (time.monotonic() - self.last.get(host, 0)))
            )
            self.last[host] = time.monotonic()
            # Resolve, reject ANY non-public answer, then connect to the validated IP.
            # SNI and certificate validation use the original host; no second DNS lookup.
            address = await self.policy.public_ip(url, auxiliary, resource)
            target = httpx.URL(url).copy_with(host=address)
            request_headers = {
                "Host": host,
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
            }
            request_headers.update(headers or {})
            async with self.client.stream(
                "GET",
                target,
                headers=request_headers,
                extensions={"sni_hostname": host},
            ) as r:
                h = dict(r.headers)
                if r.status_code != 200:
                    return Response(url, r.status_code, b"", h)
                ctype = h.get("content-type", "").split(";")[0].lower()
                valid = (
                    {"application/xml", "text/xml", "text/plain", "text/html"}
                    if auxiliary or xml
                    else {"text/html", "application/xhtml+xml"}
                )
                if auxiliary and urlsplit(url).path.endswith(".xml.gz"):
                    valid |= {
                        "application/gzip",
                        "application/x-gzip",
                        "application/octet-stream",
                    }
                if resource:
                    valid |= {
                        "application/javascript",
                        "text/javascript",
                        "application/json",
                    }
                if (
                    ctype not in valid
                    or "attachment" in h.get("content-disposition", "").lower()
                ):
                    document = (
                        ctype
                        in {
                            "application/pdf",
                            "application/msword",
                            "text/csv",
                            "application/rtf",
                        }
                        or "officedocument" in ctype
                        or "opendocument" in ctype
                        or "attachment" in h.get("content-disposition", "").lower()
                    )
                    raise NotHTML("Non-HTML body refused before download", document)
                if int(h.get("content-length", "0")) > self.settings.max_response_bytes:
                    raise ValueError("Response too large")
                body = bytearray()
                async for part in r.aiter_bytes():
                    body.extend(part)
                    if len(body) > self.settings.max_response_bytes:
                        raise ValueError("Response exceeds byte limit")
                return Response(url, r.status_code, bytes(body), h)

    async def robot(self, url):
        p = urlsplit(url)
        origin = f"{p.scheme}://{p.netloc}"
        async with self.robot_locks.setdefault(origin, asyncio.Lock()):
            if origin not in self.robots:
                parser = RobotFileParser(origin + "/robots.txt")
                r = await self.get(origin + "/robots.txt", auxiliary=True)
                if r.status in {404, 410}:
                    parser.parse([])
                elif r.status == 200:
                    parser.parse(r.body.decode("utf-8", errors="replace").splitlines())
                else:
                    raise Blocked(
                        f"robots.txt unavailable: HTTP {r.status}; fail closed"
                    )
                self.robots[origin] = parser
                self.delays[p.hostname] = (
                    parser.crawl_delay(USER_AGENT) or parser.crawl_delay("*") or 0
                )
            return self.robots[origin]

    async def get(self, url, auxiliary=False, xml=False, headers=None, resource=False):
        url = normalize(url)
        for _ in range(6):
            if not self.policy.allowed(url, auxiliary, resource):
                raise Blocked(f"Unauthorized URL or redirect: {url}")
            if not auxiliary:
                robot = await self.robot(url)
                if not robot.can_fetch(USER_AGENT, url):
                    raise Blocked("Disallowed by robots.txt")
            r = None
            for attempt in range(self.settings.max_retries + 1):
                try:
                    r = await self._request(url, auxiliary, xml, headers, resource)
                    if r.status not in {429, 500, 502, 503, 504}:
                        break
                    if attempt == self.settings.max_retries:
                        break
                    retry = r.headers.get("retry-after", "")
                    try:
                        wait = float(retry)
                    except ValueError:
                        try:
                            wait = (
                                parsedate_to_datetime(retry)
                                - datetime.now(timezone.utc)
                            ).total_seconds()
                        except (ValueError, TypeError):
                            wait = 2**attempt + random.random()
                    if wait > 60:
                        raise Blocked(
                            f"Server requests deferred retry after {wait:.0f}s"
                        )
                    await asyncio.sleep(max(0, wait))
                except (httpx.TransportError, asyncio.TimeoutError):
                    if attempt == self.settings.max_retries:
                        raise
                    await asyncio.sleep(2**attempt + random.random())
            if r.status in {301, 302, 303, 307, 308}:
                url = normalize(urljoin(url, r.headers.get("location", "")))
                headers = None
                if is_document(url):
                    raise NotHTML(
                        "Document redirect catalogued without fetching target", True
                    )
                continue
            return r
        raise Blocked("Too many redirects")

    async def close(self):
        await self.client.aclose()
