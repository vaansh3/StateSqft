# StateSqft

StateSqft — for-sale inventory tooling (Python ML package + web app).

## Layout

- **`src/bia652p/`** — Data + ML package (`data/load.py`, `features.py`, `evaluate.py`, `scripts/baseline.py`)
- **`backend/`** — FastAPI API: **Supabase JWT** + **OpenAI** only (no legacy cookie login)
- **`frontend/`** — Vite + React: Supabase Auth in the browser, calls the Python API

## Web app: run backend + frontend

### 1. Supabase

1. Create a project at [supabase.com](https://supabase.com).
2. **Authentication → Users** — add a user (email + password).
3. **Settings → API** — copy **Project URL**, **anon public** key, and **JWT Secret**.
4. Optional chat history: run `scripts/supabase_chat_messages.sql` in the SQL editor, then copy the **service_role** key (server only).

### 2. Backend env

Create **`backend/.env`** (not committed):

```env
SUPABASE_JWT_SECRET=paste-jwt-secret-from-supabase
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
OPENAI_API_KEY=sk-...
# optional
# SUPABASE_SERVICE_ROLE_KEY=eyJ...
# OPENAI_MODEL=gpt-4o-mini
# FRONTEND_ORIGINS=http://127.0.0.1:5173,http://localhost:5173
# METRO_CSV=/absolute/path/to/Metro_invt_fs_uc_sfrcondo_sm_month.csv  (default: repo root)
```

Install and run (from repo root):

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

API: `GET /health`, `GET /api/me` (header `Authorization: Bearer <supabase access token>`), `POST /api/chat` with JSON `{"message":"..."}`. Each chat turn **loads your metro inventory CSV**, matches metros mentioned in the message (e.g. “New York”, “last year”, `2024`), and sends those numbers to the model so it can answer from **real data** (metric = monthly **for-sale inventory**, not new-listing permits). Questions like **when to sell** get extra **timing signals**: latest month vs the prior six months, YoY same month, and a short 3‑month direction — still **inventory-only**, with disclaimers in UI and model text.

If chat returns **“Invalid or expired token”** while Supabase login works, your project may use **new JWT signing keys** (asymmetric). The backend then verifies via JWKS and needs a correct **`SUPABASE_URL`** in `backend/.env` (HS256 still uses **`SUPABASE_JWT_SECRET`**).

### 3. Frontend env

Create **`frontend/.env.local`** (see `frontend/.env.example`):

```env
VITE_SUPABASE_URL=https://YOUR_PROJECT.supabase.co
VITE_SUPABASE_ANON_KEY=eyJ...
VITE_API_URL=http://127.0.0.1:8000
```

Run:

```bash
cd frontend
npm install
npm run dev
```

Open **http://127.0.0.1:5173** → sign in with your Supabase user → chat uses OpenAI on the backend. Replies use **Markdown** sections (Summary / charts / takeaways), with **line + bar charts** and metric tiles built from the same CSV numbers the model sees.

### 4. If you used a single root `.env` before

Split it:

- Put **server-only** keys in **`backend/.env`** (`SUPABASE_JWT_SECRET`, `OPENAI_API_KEY`, optional `SUPABASE_SERVICE_ROLE_KEY`).
- Put **`VITE_*`** values in **`frontend/.env.local`**. The Vite app does **not** read the root `.env` automatically.

---

## ML baselines (CSV)

1. **venv + package** (repo root):

   ```bash
   cd /path/to/StateSqft
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -U pip
   pip install -e .
   ```

2. Keep **`Metro_invt_fs_uc_sfrcondo_sm_month.csv`** at the project root.

3. Run:

   ```bash
   python scripts/baseline.py
   ```
