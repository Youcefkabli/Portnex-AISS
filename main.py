"""Entrypoint for uvicorn: uvicorn main:app --host 0.0.0.0 --port 8000"""
from app.api.main import app

__all__ = ["app"]
