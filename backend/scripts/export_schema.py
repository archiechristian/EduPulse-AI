"""Regenerate supabase/schema.sql from the SQLAlchemy models.

    python scripts/export_schema.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.dialects import postgresql  # noqa: E402
from sqlalchemy.schema import CreateIndex, CreateTable  # noqa: E402

from app.database import Base  # noqa: E402
from app import models  # noqa: E402,F401

OUT = Path(__file__).resolve().parents[2] / "supabase" / "schema.sql"

parts = [
    "-- EduPulse AI schema for Supabase (Postgres).",
    "-- Generated from backend/app/models.py by backend/scripts/export_schema.py.",
    "-- Run in the Supabase SQL editor, or simply point DATABASE_URL at Supabase: the API creates",
    "-- these tables automatically on startup.",
    "",
]
dialect = postgresql.dialect()
for table in Base.metadata.sorted_tables:
    parts.append(str(CreateTable(table, if_not_exists=True).compile(dialect=dialect)).strip() + ";")
    for idx in table.indexes:
        parts.append(str(CreateIndex(idx, if_not_exists=True).compile(dialect=dialect)).strip() + ";")
    parts.append("")

parts += [
    "-- The FastAPI backend connects with the service connection string and enforces role-based",
    "-- access itself. Enable RLS so the tables are not exposed through Supabase's public REST API.",
]
parts += [f"ALTER TABLE {t.name} ENABLE ROW LEVEL SECURITY;" for t in Base.metadata.sorted_tables]

OUT.parent.mkdir(exist_ok=True)
OUT.write_text("\n".join(parts) + "\n", encoding="utf-8")
print(f"Wrote {OUT}")
