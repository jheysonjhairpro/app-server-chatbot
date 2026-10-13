import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter()


class Question(BaseModel):
    pregunta: str = Field(min_length=2, max_length=2000)
    categoria: str | None = Field(None, max_length=120)
    streaming: bool = True


def services(request: Request):
    return request.app.state.services


@router.get("/health")
async def health(request: Request):
    return await services(request).health()


@router.post("/rag/buscar")
async def search(request: Request, body: Question):
    svc = services(request)
    try:
        await svc.ready()
        return await svc.rag.search(body.pregunta, body.categoria)
    except Exception as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/rag/preguntar")
async def ask(request: Request, body: Question):
    svc = services(request)
    try:
        await svc.ready()
    except Exception as exc:
        raise HTTPException(503, str(exc)) from exc

    async def events():
        try:
            async for event in svc.rag.answer_events(body.pregunta, body.categoria):
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        except Exception as exc:
            yield "data: " + json.dumps(
                {"tipo": "error", "detalle": str(exc)}, ensure_ascii=False
            ) + "\n\n"

    if body.streaming:
        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        final = None
        async for event in svc.rag.answer_events(body.pregunta, body.categoria):
            if event["tipo"] == "fin":
                final = event
        return final
    except Exception as exc:
        raise HTTPException(503, str(exc)) from exc
