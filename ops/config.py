"""Paths and defaults. OPS_DB overrides the database location (handy for tests)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
SCHEMA_PATH = ROOT / "ops" / "schema.sql"
DB_PATH = Path(os.environ.get("OPS_DB", str(ROOT / "data" / "ops.db")))
DEFAULT_SEED = 7
# Bump whenever ops/schema.sql changes: app.py rebuilds a database stamped with another version.
SCHEMA_VERSION = "1.4"
