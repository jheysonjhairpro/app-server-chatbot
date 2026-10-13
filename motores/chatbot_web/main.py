import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.routes import router
from api.explorer import router as explorer_router
from config import Settings
from services.runtime import Services

log = logging.getLogger("unamba.chatbot")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)


@asynccontextmanager
async def lifespan(app):
    svc = await asyncio.to_thread(Services, Settings())
    app.state.services = svc
    try:
        yield
    finally:
        await svc.close()


app = FastAPI(title="UNAMBA Chatbot Web", version="1.0.0", lifespan=lifespan)
app.include_router(router)
app.include_router(explorer_router)
WEB_DIR = Path(__file__).resolve().parent / "web"
app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")


@app.get("/", include_in_schema=False)
async def explorer():
    return FileResponse(WEB_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, workers=1)
