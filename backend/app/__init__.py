"""Converge backend: ingest research notes, track idea lineage, and run convergence sessions."""

from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

# One .env at the repo root serves every module (Meta API key, Notion token).
load_dotenv(REPO_ROOT / ".env")
