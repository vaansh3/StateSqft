from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
load_dotenv(BACKEND_ROOT / ".env")

_default_csv = REPO_ROOT / "Metro_invt_fs_uc_sfrcondo_sm_month.csv"
METRO_CSV = Path(
    os.environ.get("METRO_CSV", str(_default_csv)),
).expanduser().resolve()

# Verify Supabase user JWTs (Dashboard → Settings → API → JWT Secret)
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "").strip()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()

SUPABASE_DB_ENABLED = bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()

# Comma-separated origins for the Vite dev server (and production URL when you deploy)
_raw_origins = os.environ.get(
    "FRONTEND_ORIGINS",
    "http://127.0.0.1:5173,http://localhost:5173",
)
FRONTEND_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]
