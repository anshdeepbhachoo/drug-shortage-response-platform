"""Pick the agents' data source in one place.

    AGENT_DATA_SOURCE=postgres   -> PostgresData (reads as DATABASE_URL_READONLY)
    AGENT_DATA_SOURCE=csv / unset -> CsvData over mock/ (no database needed)

Every agent entry point that isn't handed a data object calls default_data(),
so switching the whole system to the database is one setting.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from services.agents.data.data import CsvData

MOCK_DIR = Path(__file__).resolve().parents[3] / "mock"


def default_data(as_of: date | None = None):
    source = os.getenv("AGENT_DATA_SOURCE", "csv").strip().lower()
    if source == "postgres":
        from services.agents.data.db_data import PostgresData

        return PostgresData.from_env(as_of=as_of)
    if source != "csv":
        raise RuntimeError(f"AGENT_DATA_SOURCE must be 'csv' or 'postgres', not {source!r}")
    return CsvData(MOCK_DIR)
