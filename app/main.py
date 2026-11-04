"""Local API; the polling bot runs as a separate process."""

from fastapi import FastAPI, HTTPException

from app.config import load_settings
from app.database import check_database

app = FastAPI(title="Ace Place", version="0.1.0")


@app.get("/health/live")
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def readiness() -> dict[str, str]:
    try:
        ready = check_database(load_settings())
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    if not ready:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"status": "ok", "database": "ok"}
