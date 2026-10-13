"""Lectura, filtros y alertas del catálogo del explorador.

Mantiene conexiones SQLite de solo lectura, independientes de State y Ollama.
"""

import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import date
from typing import Literal
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, Field

from indexing.cleaner import folded

INDEXED = "hash IS NOT NULL AND payload IS NOT NULL AND status != 'gone'"


def database(request):
    path = request.app.state.services.settings.state_path.resolve()
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.create_function("folded", 1, lambda value: folded(value or ""))
    return db


class Filters(BaseModel):
    q: str = Field("", max_length=300)
    categoria: str = Field("", max_length=120)
    portal: str = Field("", max_length=300)
    campo: Literal["todos", "titulo", "url", "contenido"] = "todos"
    documentos: Literal["todos", "con", "sin", "pdf", "word", "excel"] = "todos"
    calidad: Literal[
        "todos",
        "sin_fecha",
        "sin_titulo",
        "vacio",
        "breve",
        "titulo_repetido",
        "contenido_repetido",
    ] = "todos"
    fecha: Literal["publicacion", "revision"] = "publicacion"
    desde: date | None = None
    hasta: date | None = None
    orden: Literal[
        "recientes", "titulo", "portal", "publicacion", "documentos", "caracteres"
    ] = "recientes"
    direccion: Literal["asc", "desc"] = "desc"


def catalog(request):
    with closing(database(request)) as db:
        rows = db.execute(
            f"SELECT url,title,checked,payload FROM pages WHERE {INDEXED} ORDER BY url"
        ).fetchall()
        documents = {}
        for row in db.execute("SELECT source,target FROM links WHERE document=1"):
            documents.setdefault(row["source"], []).append(row["target"])
    items = []
    for row in rows:
        page = json.loads(row["payload"])
        content = " ".join(b.get("text", "") for b in page.get("blocks", [])).strip()
        urls = documents.get(row["url"], [])
        extensions = {urlsplit(url).path.lower().rsplit(".", 1)[-1] for url in urls}
        items.append(
            {
                "url": row["url"],
                "titulo": row["title"],
                "checked": row["checked"],
                "categorias": page.get("categorias", []),
                "portal": urlsplit(row["url"]).hostname or "",
                "publicacion": page.get("fecha_publicacion"),
                "documentos": len(urls),
                "caracteres": len(content),
                "extracto": content[:240],
                "_contenido": folded(content),
                "_extensiones": extensions,
            }
        )
    titles = Counter(folded(item["titulo"] or "") for item in items)
    texts = Counter(item["_contenido"] for item in items)
    for item in items:
        item["calidad"] = []
        if not item["publicacion"]:
            item["calidad"].append("sin_fecha")
        if not (item["titulo"] or "").strip():
            item["calidad"].append("sin_titulo")
        if not item["caracteres"]:
            item["calidad"].append("vacio")
        elif item["caracteres"] < 300:
            item["calidad"].append("breve")
        if item["titulo"] and titles[folded(item["titulo"])] > 1:
            item["calidad"].append("titulo_repetido")
        if item["_contenido"] and texts[item["_contenido"]] > 1:
            item["calidad"].append("contenido_repetido")
    return items


def filtered(request, filters):
    if filters.desde and filters.hasta and filters.desde > filters.hasta:
        raise HTTPException(
            422, "La fecha inicial debe ser anterior o igual a la final"
        )
    result = []
    extensions = {
        "pdf": {"pdf"},
        "word": {"doc", "docx"},
        "excel": {"xls", "xlsx", "csv"},
    }
    for item in catalog(request):
        if filters.portal and item["portal"] != filters.portal:
            continue
        if filters.categoria and filters.categoria not in item["categorias"]:
            continue
        fields = {
            "titulo": item["titulo"] or "",
            "url": item["url"],
            "contenido": item["_contenido"],
        }
        text = (
            " ".join(fields.values())
            if filters.campo == "todos"
            else fields[filters.campo]
        )
        if folded(filters.q.strip()) not in folded(text):
            continue
        if filters.documentos == "con" and not item["documentos"]:
            continue
        if filters.documentos == "sin" and item["documentos"]:
            continue
        if (
            filters.documentos in extensions
            and not item["_extensiones"] & extensions[filters.documentos]
        ):
            continue
        if filters.calidad != "todos" and filters.calidad not in item["calidad"]:
            continue
        value = (
            item["publicacion"] if filters.fecha == "publicacion" else item["checked"]
        )
        if filters.desde or filters.hasta:
            try:
                day = date.fromisoformat((value or "")[:10])
            except ValueError:
                continue
            if filters.desde and day < filters.desde:
                continue
            if filters.hasta and day > filters.hasta:
                continue
        result.append(
            {key: value for key, value in item.items() if not key.startswith("_")}
        )
    key = "checked" if filters.orden == "recientes" else filters.orden
    present = [item for item in result if item[key] is not None]
    missing = [item for item in result if item[key] is None]
    present.sort(
        key=lambda item: folded(item[key]) if isinstance(item[key], str) else item[key],
        reverse=filters.direccion == "desc",
    )
    return present + missing
