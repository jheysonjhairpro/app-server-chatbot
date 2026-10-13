"""Read-only catalog of the saved index, independent of Ollama."""

import json
import csv
import io
from urllib.parse import urlsplit
from contextlib import closing

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response


from services.catalog import INDEXED, Filters, catalog, database, filtered

router = APIRouter(prefix="/indice", tags=["Indice"])


@router.get("/resumen")
def summary(request: Request):
    with closing(database(request)) as db:
        counts = db.execute(
            f"SELECT COUNT(*), MAX(checked) FROM pages WHERE {INDEXED}"
        ).fetchone()
        categories = db.execute(
            f"SELECT DISTINCT value FROM pages, json_each(payload, '$.categorias') "
            f"WHERE {INDEXED} ORDER BY value"
        ).fetchall()
        documents = db.execute(
            f"SELECT COUNT(DISTINCT target) FROM links WHERE document=1 "
            f"AND source IN (SELECT url FROM pages WHERE {INDEXED})"
        ).fetchone()[0]
    return {
        "paginas": counts[0],
        "ultima_revision": counts[1],
        "documentos": documents,
        "categorias": [row[0] for row in categories],
    }


@router.get("/portales")
def portals(request: Request):
    with closing(database(request)) as db:
        return sorted(
            {
                urlsplit(row[0]).hostname
                for row in db.execute(f"SELECT url FROM pages WHERE {INDEXED}")
            }
        )


@router.get("/paginas")
def pages(
    request: Request,
    filters: Filters = Depends(),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    items = filtered(request, filters)
    return {
        "total": len(items),
        "items": items[offset : offset + limit],
        "offset": offset,
        "limit": limit,
    }


@router.get("/exportar")
def export(request: Request, filters: Filters = Depends()):
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        [
            "Titulo",
            "URL",
            "Portal",
            "Categorias",
            "Publicacion",
            "Revision",
            "Documentos enlazados",
            "Caracteres",
            "Calidad",
        ]
    )
    for item in filtered(request, filters):
        values = [
            item["titulo"],
            item["url"],
            item["portal"],
            ", ".join(item["categorias"]),
            item["publicacion"],
            item["checked"],
            item["documentos"],
            item["caracteres"],
            ", ".join(item["calidad"]),
        ]
        # Avoid spreadsheet formulas in titles and other source-controlled fields.
        writer.writerow(
            [
                (
                    "'" + str(value)
                    if str(value or "").lstrip().startswith(("=", "+", "-", "@"))
                    else value
                )
                for value in values
            ]
        )
    return Response(
        ("\ufeff" + output.getvalue()).encode("utf-8"),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="indice-unamba.csv"'},
    )


@router.get("/pagina")
def detail(request: Request, url: str = Query(..., max_length=8192)):
    with closing(database(request)) as db:
        row = db.execute(
            f"SELECT payload,checked FROM pages WHERE {INDEXED} AND url=?", (url,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Pagina no encontrada en el indice")
        documents = [
            json.loads(r[0])
            for r in db.execute(
                "SELECT details FROM links WHERE source=? AND document=1 ORDER BY target",
                (url,),
            )
        ]
    return {
        "pagina": json.loads(row["payload"]),
        "checked": row["checked"],
        "documentos": documents,
    }
