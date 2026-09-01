#!/bin/bash

# Local development launcher: checks the database in DATABASE_URL is reachable,
# runs migrations, and starts the FastAPI backend (:8000) + Vite frontend
# (:5173) together. In deployment these run as a single Databricks App process;
# locally the Vite dev server proxies /v1 and /version to the backend (see
# frontend/vite.config.ts).
#
# You provide the Postgres (via .env's DATABASE_URL) — dev.sh does not provision
# one, so it has no dependency on Docker or any specific database tool.
#
# Usage: ./dev.sh [--debug]
#   --debug   start the backend under debugpy on port 5678

# Don't use `set -e`; we handle errors manually so cleanup always runs.

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

BACKEND_PORT=8000
FRONTEND_PORT=5173

# Parse arguments
DEBUG_MODE=false
for arg in "$@"; do
    if [ "$arg" == "--debug" ]; then
        DEBUG_MODE=true
        echo -e "${YELLOW}Debug mode enabled${NC}"
    fi
done

# Cleanup background processes on exit
cleanup() {
    echo -e "\n${YELLOW}Shutting down services...${NC}"
    [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null || true
    [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null || true
    wait "$FRONTEND_PID" "$BACKEND_PID" 2>/dev/null || true
    echo -e "${GREEN}Services stopped.${NC}"
    exit 0
}
trap cleanup SIGINT SIGTERM

echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  Terramate API Wrapper — Dev Environment ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}\n"

# Clean up old log files
echo -e "${CYAN}Cleaning up old log files...${NC}"
rm -f backend.log frontend.log
echo -e "${GREEN}✓ Log files cleared${NC}"

# Free the dev ports
echo -e "${CYAN}Checking for processes on ports ${BACKEND_PORT} and ${FRONTEND_PORT}...${NC}"
for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
    PORT_PID=$(lsof -ti:"$port" 2>/dev/null || true)
    if [ -n "$PORT_PID" ]; then
        echo -e "${YELLOW}Killing process on port ${port} (PID: ${PORT_PID})...${NC}"
        kill -9 $PORT_PID 2>/dev/null || true
        sleep 1
    fi
done
echo -e "${GREEN}✓ Ports ready${NC}\n"

# Bootstrap .env from the template so a fresh clone runs without a manual copy.
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp ".env.example" ".env"
        echo -e "${GREEN}✓ Created .env from .env.example${NC}"
        echo -e "${YELLOW}  Review .env and fill in any secrets (e.g. GITHUB_PAT) as needed.${NC}\n"
    else
        echo -e "${YELLOW}⚠ No .env or .env.example found. The backend may not start without config.${NC}\n"
    fi
fi

# Load .env into this shell so the backend and alembic inherit DATABASE_URL etc.
# (server/config.py and migrations/env.py read everything from os.environ.)
if [ -f ".env" ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

# Determine Python for the virtualenv (README convention: .venv at repo root).
VENV_DIR=".venv"
if [ -d "$VENV_DIR" ] && [ -x "$VENV_DIR/bin/python" ] && "$VENV_DIR/bin/python" --version >/dev/null 2>&1; then
    echo -e "${CYAN}Using virtual environment (${VENV_DIR})...${NC}"
else
    echo -e "${YELLOW}No valid virtual environment found. Creating ${VENV_DIR}...${NC}"
    rm -rf "$VENV_DIR"
    if command -v python3 >/dev/null 2>&1; then
        python3 -m venv "$VENV_DIR"
    else
        python -m venv "$VENV_DIR"
    fi
    if [ ! -x "$VENV_DIR/bin/python" ]; then
        echo -e "${RED}✗ Failed to create virtual environment${NC}"
        exit 1
    fi
    echo -e "${GREEN}✓ Virtual environment created${NC}"
fi
PYTHON="$VENV_DIR/bin/python"

# Verify key deps; install from requirements-dev.txt if anything is missing.
echo -e "${CYAN}Checking Python dependencies...${NC}"
MISSING=()
for mod in uvicorn fastapi sqlalchemy alembic psycopg2 httpx; do
    "$PYTHON" -c "import $mod" 2>/dev/null || MISSING+=("$mod")
done
if [ "$DEBUG_MODE" = true ]; then
    "$PYTHON" -c "import debugpy" 2>/dev/null || MISSING+=("debugpy")
fi
if [ ${#MISSING[@]} -gt 0 ]; then
    echo -e "${YELLOW}Missing: ${MISSING[*]}. Installing from requirements-dev.txt...${NC}"
    "$PYTHON" -m pip install --upgrade pip >/dev/null 2>&1
    if ! "$PYTHON" -m pip install -r requirements-dev.txt; then
        echo -e "${RED}✗ Failed to install Python dependencies${NC}"
        exit 1
    fi
    if [ "$DEBUG_MODE" = true ]; then
        "$PYTHON" -m pip install debugpy >/dev/null 2>&1
    fi
    echo -e "${GREEN}✓ Dependencies installed${NC}"
else
    echo -e "${GREEN}✓ Dependencies present${NC}"
fi
echo ""

# Returns success when the database named by DATABASE_URL actually accepts a
# connection — verified against the real URL (host, port, AND database name),
# not a proxy like `pg_isready` on an assumed container. Silent; caller logs.
db_reachable() {
    [ -n "$DATABASE_URL" ] || return 1
    "$PYTHON" - "$DATABASE_URL" <<'PY' 2>/dev/null
import sys
from sqlalchemy import create_engine, text
try:
    with create_engine(sys.argv[1]).connect() as c:
        c.execute(text("SELECT 1"))
except Exception:
    sys.exit(1)
PY
}

# The app needs a reachable Postgres (DATABASE_URL). In a deployed app the
# Lakebase resource provides it; locally, point DATABASE_URL at whatever
# Postgres you run (a native install, a managed instance, or a container).
# dev.sh intentionally does NOT provision a database — it only checks the one
# you've configured is up, so there's no hard dependency on any single tool.
echo -e "${CYAN}Checking database connectivity...${NC}"
if db_reachable; then
    echo -e "${GREEN}✓ Database reachable${NC}"
else
    echo -e "${RED}✗ Cannot reach a Postgres at DATABASE_URL:${NC}"
    echo -e "    ${BLUE}${DATABASE_URL:-(unset)}${NC}"
    echo -e "${YELLOW}  Start a Postgres and set DATABASE_URL in .env, then re-run ./dev.sh.${NC}"
    echo -e "${YELLOW}  See README.md → \"Local development\" for a ready-to-paste setup.${NC}"
    exit 1
fi

# Apply migrations.
echo -e "${CYAN}Running database migrations (alembic upgrade head)...${NC}"
if ! "$PYTHON" -m alembic upgrade head; then
    echo -e "${RED}✗ Migrations failed. Check your DATABASE_URL and that Postgres is reachable.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Migrations applied${NC}\n"

# Start backend (FastAPI / uvicorn) from the repo root so `server.main` resolves.
echo -e "${GREEN}→ Starting backend API...${NC}"
echo "=== Backend started at $(date) ===" > backend.log
if [ "$DEBUG_MODE" = true ]; then
    echo -e "${GREEN}→ Backend debugger listening on port 5678${NC}"
    "$PYTHON" -m debugpy --listen 0.0.0.0:5678 -m uvicorn server.main:app --reload --port "$BACKEND_PORT" >> backend.log 2>&1 &
else
    "$PYTHON" -m uvicorn server.main:app --reload --port "$BACKEND_PORT" >> backend.log 2>&1 &
fi
BACKEND_PID=$!

sleep 3
if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo -e "${RED}✗ Backend failed to start. Last lines of backend.log:${NC}"
    tail -n 15 backend.log 2>/dev/null
    exit 1
fi

# Ensure frontend deps, then start the Vite dev server.
if [ ! -d "frontend/node_modules" ] || [ ! -e "frontend/node_modules/.bin/vite" ]; then
    echo -e "${YELLOW}Frontend dependencies not found. Running npm install...${NC}"
    echo -e "${CYAN}(This can take a minute the first time.)${NC}"
    (cd frontend && npm install)
    if [ $? -ne 0 ]; then
        echo -e "${RED}✗ npm install failed.${NC}"
        kill "$BACKEND_PID" 2>/dev/null || true
        exit 1
    fi
    echo -e "${GREEN}✓ Frontend dependencies installed${NC}"
else
    echo -e "${GREEN}✓ Frontend dependencies present${NC}"
fi
echo ""

echo -e "${GREEN}→ Starting frontend...${NC}"
echo "=== Frontend started at $(date) ===" > frontend.log
(cd frontend && npm run dev) >> frontend.log 2>&1 &
FRONTEND_PID=$!

sleep 3
if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
    echo -e "${RED}✗ Frontend failed to start. Last lines of frontend.log:${NC}"
    tail -n 15 frontend.log 2>/dev/null
    kill "$BACKEND_PID" 2>/dev/null || true
    exit 1
fi

echo -e "\n${GREEN}✓ Development environment is running!${NC}\n"
echo -e "${CYAN}Frontend:${NC}    ${BLUE}http://localhost:${FRONTEND_PORT}${NC}"
echo -e "${CYAN}Backend API:${NC} ${BLUE}http://localhost:${BACKEND_PORT}${NC}"
echo -e "${CYAN}API Docs:${NC}    ${BLUE}http://localhost:${BACKEND_PORT}/docs${NC}"
echo -e "\n${YELLOW}Press Ctrl+C to stop the backend and frontend${NC}"
echo -e "${CYAN}────────────────────────────────────────────${NC}"
echo -e "${CYAN}Logs:${NC} ${BLUE}backend.log${NC}  ${GREEN}frontend.log${NC}"
echo -e "${CYAN}Tail both:${NC} ${YELLOW}tail -f backend.log frontend.log${NC}\n"

# Keep running until both processes exit (or Ctrl+C triggers cleanup).
wait "$FRONTEND_PID" "$BACKEND_PID"
