"""Standalone read-only explorer; can run alongside the chatbot."""

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.explorer import router
from config import Settings

app = FastAPI(title="UNAMBA - Indice local")
app.state.services = SimpleNamespace(settings=Settings())
app.include_router(router)
WEB_DIR = Path(__file__).resolve().parent / "web"
app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001)
