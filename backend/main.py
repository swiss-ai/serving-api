import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import create_engine
from backend.config import get_settings
from backend.services.metrics_service import metrics_collector
from backend.middleware.logging import AccessLogMiddleware
from backend.routers import completions, responses, embeddings, models, profile, metrics

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
    )
    _ = metrics_collector
    yield
    app.state.engine = None


app = FastAPI(lifespan=lifespan)

if settings.access_log:
    logging.basicConfig(level=logging.INFO)
    app.add_middleware(AccessLogMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(completions.router)
app.include_router(responses.router)
app.include_router(embeddings.router)
app.include_router(models.router)
app.include_router(profile.router)
app.include_router(metrics.router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
