# AlphaBot Ubuntu Migration

This runbook moves the current AlphaBot branch and runtime setup into an Ubuntu laptop environment.

## 1. Pull the Code

Fresh clone:

```bash
cd /your/workspace
git clone git@github.com:rajeshselvam02/alphabot.git
cd alphabot
git checkout feature/next-local-work
```

Existing clone:

```bash
cd /your/workspace/alphabot
git fetch origin
git checkout feature/next-local-work
git pull origin feature/next-local-work
```

## 2. Bootstrap Ubuntu

From the repo root:

```bash
chmod +x scripts/bootstrap_ubuntu.sh
./scripts/bootstrap_ubuntu.sh
```

This script:
- installs Python, venv support, Redis, Node.js, and npm
- creates `.venv`
- installs backend dependencies from `requirements.txt`
- installs frontend dependencies
- builds the frontend bundle

## 3. Copy Runtime State

Git moves code only. If you want the laptop to preserve current local state, also copy:

- `.env`
- `alphabot.db`
- `reports/`
- optionally `dump.rdb`

Example:

```bash
scp user@old-machine:/root/alphabot/.env /your/workspace/alphabot/.env
scp user@old-machine:/root/alphabot/alphabot.db /your/workspace/alphabot/
scp -r user@old-machine:/root/alphabot/reports /your/workspace/alphabot/
```

## 4. Start the Stack

Backend:

```bash
cd /your/workspace/alphabot
source .venv/bin/activate
uvicorn backend.api.main:app --host 0.0.0.0 --port 8000
```

Frontend:

```bash
cd /your/workspace/alphabot/frontend
npm run dev
```

Redis check:

```bash
redis-cli ping
```

## 5. Smoke Checks

```bash
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/xaufx/validation/latest
```

## Notes

- The active branch for this workstream is `feature/next-local-work`.
- The frontend dev server is configured for port `3000`.
- The backend API is expected on port `8000`.
- If you move local DB and report artifacts, validation history and benchmark assets will follow the codebase onto the laptop.
