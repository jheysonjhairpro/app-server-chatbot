import asyncio
import time
from urllib.parse import urlsplit
from crawler.url_manager import normalize
from crawler.discovery import discover
from crawler.html_parser import parse_html
from crawler.sitemap import discover_sitemaps
from crawler.http import Blocked, NotHTML
from crawler.javascript import needs_javascript, render


class Spider:
    def __init__(self, settings, state, fetcher, indexer):
        self.settings, self.state, self.fetcher, self.indexer = (
            settings,
            state,
            fetcher,
            indexer,
        )
        self.report = {}

    async def run(self, job_id, mode, resume=False):
        try:
            return await asyncio.wait_for(
                self._run(job_id, mode, resume), self.settings.max_seconds
            )
        except asyncio.TimeoutError:
            self.report.setdefault("limites", []).append("tiempo_total")
            self.report["estado"] = self.state.stats()
            self.report["duracion_segundos"] = self.settings.max_seconds
            self.report["pendientes"] = self.report["estado"]["paginas"].get(
                "pending", 0
            )
            self.state.job(job_id, mode, "finished", self.report)
            return self.report

    async def _run(self, job_id, mode, resume=False):
        s = self.settings
        started = time.monotonic()
        getattr(self.fetcher, "robots", {}).clear()
        getattr(self.fetcher, "delays", {}).clear()
        self.report = {
            "portales": len(s.seeds),
            "procesadas": 0,
            "sin_cambios": 0,
            "fragmentos": 0,
            "embeddings": 0,
            "errores": 0,
            "bloqueadas": 0,
            "limites": [],
            "sitemaps_errores": [],
            "javascript_pendiente": [],
            "coleccion": s.web_collection,
            "modelo": s.embedding_model,
            "cobertura_completa": False,
        }
        self.state.job(job_id, mode, "running", self.report)
        if not resume:
            self.state.requeue()
        for url in s.seeds:
            if not self.fetcher.policy.allowed(url):
                raise ValueError(f"Unauthorized seed: {url}")
            self.state.discover(normalize(url), 0)
            if resume:
                self.state.mark(normalize(url), "pending")
        sitemap_seeds = []
        for url in s.seeds:
            p = urlsplit(url)
            try:
                robot = await self.fetcher.robot(url)
                sitemap_seeds.extend(
                    robot.site_maps() or [f"{p.scheme}://{p.netloc}/sitemap.xml"]
                )
            except Exception as exc:
                self.report["sitemaps_errores"].append({"url": url, "error": str(exc)})
        # Reserve the frontier for navigation from all seed portals; a large sitemap
        # must not consume every slot before the other institutions' seeds are parsed.
        sitemap_budget = max(1, s.max_pages // 4)
        urls, errors = await discover_sitemaps(
            self.fetcher, sitemap_seeds, self.fetcher.policy, max_urls=sitemap_budget
        )
        self.report["sitemaps_errores"].extend(errors)
        for url in urls:
            if self.state.stats()["descubiertas"] >= s.max_pages:
                break
            self.state.discover(normalize(url), 1)
        claimed = 0

        async def process(row):
            url = row["url"]
            if row["depth"] > s.max_depth:
                self.state.mark(url, "limited", "Depth limit")
                return
            try:
                headers = {}
                # Only conditional requests for successfully saved HTML. Error retries
                # must fetch a full body to repair interrupted Chroma writes.
                if row["hash"] and not row["error"]:
                    if row["etag"]:
                        headers["If-None-Match"] = row["etag"]
                    if row["modified"]:
                        headers["If-Modified-Since"] = row["modified"]
                response = await self.fetcher.get(url, headers=headers)
                if response.status == 304:
                    if not row["hash"]:
                        raise ValueError("304 without stored content")
                    # Discover known links again so unvisited children survive incremental runs.
                    import json

                    links = [
                        json.loads(r[0])
                        for r in self.state.db.execute(
                            "SELECT details FROM links WHERE source=?", (url,)
                        )
                    ]
                    self.enqueue(row, links)
                    self.state.mark(url, "indexed")
                    self.report["sin_cambios"] += 1
                    return
                if response.status in {404, 410}:
                    await self.indexer.remove(url)
                    return
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
                html = response.body.decode(
                    (
                        response.headers.get("content-type", "")
                        .split("charset=")[-1]
                        .split(";")[0]
                        if "charset=" in response.headers.get("content-type", "")
                        else "utf-8"
                    ),
                    errors="replace",
                )
                page = await asyncio.to_thread(parse_html, html, response.url)
                if needs_javascript(html, page):
                    if s.javascript_enabled:
                        html = await render(response.url, self.fetcher)
                        page = await asyncio.to_thread(parse_html, html, response.url)
                    else:
                        self.report["javascript_pendiente"].append(response.url)
                links = await asyncio.to_thread(discover, html, response.url)
                for link in links:
                    previous = self.state.page(link["url"])
                    if (
                        previous
                        and previous["status"] == "non_html"
                        and previous["error"] == "document"
                    ):
                        link["documento"] = True
                links = sorted(links, key=lambda l: l["url"])
                for link in links:
                    link["categoria"] = page["categorias"][0]
                    link["pagina_origen"] = response.url
                # Document references become searchable HTML-derived text, never file contents.
                docs = [l for l in links if l["documento"]]
                if docs:
                    for l in docs:
                        text = (
                            f"Documento enlazado (contenido no leído): {l['nombre']} | {l['url']}"
                            + (f" | {l['descripcion']}" if l["descripcion"] else "")
                        )
                        page["blocks"].append(
                            {
                                "heading": "Documentos oficiales enlazados > "
                                + l["nombre"],
                                "kind": "links",
                                "text": text,
                            }
                        )
                        page["texto"] += "\n" + text
                page["portal"] = (
                    "gob.pe"
                    if urlsplit(response.url).hostname == "www.gob.pe"
                    else (
                        "transparencia"
                        if "transparencia.gob.pe" in response.url
                        else "unamba"
                    )
                )
                page["url"] = response.url
                page["url_final"] = response.url
                # Explicit fields only when the page labels them; no inference from prices elsewhere.
                for field, labels in {
                    "requisitos": ["requisito"],
                    "costo": ["costo", "tasa"],
                    "oficina_responsable": ["oficina responsable"],
                    "plazo": ["plazo"],
                    "horario": ["horario"],
                }.items():
                    selected = [
                        b["text"]
                        for b in page["blocks"]
                        if any(label in b["heading"].lower() for label in labels)
                    ]
                    page["campos"][field] = "\n".join(selected) if selected else None
                self.enqueue(row, links)
                if response.url != url:
                    # Store only canonical redirect target. A previous source copy is removed.
                    if row["hash"]:
                        await self.indexer.remove(url)
                    self.state.discover(response.url, row["depth"])
                result = await self.indexer.index(
                    response.url, page, links, response.headers
                )
                if response.url != url:
                    self.state.mark(url, "redirected", f"Canonical: {response.url}")
                for key, value in result.items():
                    self.report[key] += value
                self.report["procesadas"] += 1
            except Blocked as exc:
                self.state.mark(url, "blocked", str(exc))
                self.report["bloqueadas"] += 1
            except NotHTML as exc:
                self.state.mark(
                    url, "non_html", "document" if exc.document else str(exc)
                )
                if exc.document:
                    self.state.promote_document(url)
            except asyncio.CancelledError:
                self.state.mark(url, "pending", "Interrupted; force full refetch")
                raise
            except Exception as exc:
                self.state.mark(url, "error", str(exc))
                self.report["errores"] += 1
            finally:
                self.state.job(job_id, mode, "running", self.report)

        try:
            while claimed < s.max_pages:
                remaining = s.max_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    self.report["limites"].append("tiempo")
                    break
                rows = []
                for _ in range(min(s.crawl_concurrency, s.max_pages - claimed)):
                    row = self.state.claim()
                    if row:
                        rows.append(row)
                if not rows:
                    break
                claimed += len(rows)
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*(process(r) for r in rows)), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    self.report["limites"].append("tiempo")
                    break
            if claimed >= s.max_pages:
                self.report["limites"].append("paginas")
            self.report["duracion_segundos"] = round(time.monotonic() - started, 3)
            self.report["estado"] = self.state.stats()
            self.report["pendientes"] = self.report["estado"]["paginas"].get(
                "pending", 0
            )
            self.state.job(job_id, mode, "finished", self.report)
            return self.report
        except asyncio.CancelledError:
            self.state.job(job_id, mode, "interrupted", self.report)
            raise

    def enqueue(self, row, links):
        added = 0
        for link in links:
            if link["documento"] or not self.fetcher.policy.allowed(link["url"]):
                continue
            if row["depth"] >= self.settings.max_depth:
                if "profundidad" not in self.report["limites"]:
                    self.report["limites"].append("profundidad")
                continue
            if self.state.stats()["descubiertas"] >= self.settings.max_pages:
                if "descubrimiento" not in self.report["limites"]:
                    self.report["limites"].append("descubrimiento")
                break
            if self.state.page(link["url"]):
                continue
            if added >= max(10, self.settings.max_pages // 4):
                if "enlaces_por_pagina" not in self.report["limites"]:
                    self.report["limites"].append("enlaces_por_pagina")
                break
            self.state.discover(link["url"], row["depth"] + 1)
            added += 1
