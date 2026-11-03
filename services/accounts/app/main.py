from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .auth import AVATARS, init_accounts, router
from .database import get_connection

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_accounts()
    yield

app = FastAPI(title="Accounts API", version="1.0.0", lifespan=lifespan)
app.include_router(router)
app.mount("/photos/avatars", StaticFiles(directory=AVATARS), name="avatars")

@app.get("/health")
def health():
    return {"status": "ok", "service": "accounts"}

@app.get("/ready")
def ready():
    with get_connection() as connection:
        connection.execute("SELECT 1")
    return {"status": "ready", "service": "accounts"}
