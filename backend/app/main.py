import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import Base, SessionLocal, engine
from .ml.model import get_bundle
from .routers import ai, analytics, auth, crud, students, teacher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(engine)
    if settings.SEED_DEMO_DATA:
        from .seed import is_empty, seed

        db = SessionLocal()
        try:
            if is_empty(db):
                logging.getLogger("edupulse").info("Empty database - seeding demo data...")
                seed(db)
        finally:
            db.close()
    get_bundle()  # warm up / train the ML models
    yield


app = FastAPI(title="EduPulse AI API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list({settings.FRONTEND_URL, "http://localhost:3000", "http://127.0.0.1:3000"}),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth.router, crud.router, teacher.router, students.router, analytics.router, ai.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"status": "ok"}
