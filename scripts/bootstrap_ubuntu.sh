#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"

echo "[alphabot] updating apt metadata"
sudo apt-get update

echo "[alphabot] installing system packages"
sudo apt-get install -y \
  python3 \
  python3-venv \
  python3-pip \
  redis-server \
  nodejs \
  npm

echo "[alphabot] ensuring redis is enabled"
sudo systemctl enable redis-server
sudo systemctl restart redis-server

echo "[alphabot] creating virtualenv"
python3 -m venv "${VENV_DIR}"
source "${VENV_DIR}/bin/activate"

echo "[alphabot] installing python dependencies"
python -m pip install --upgrade pip
pip install -r "${ROOT_DIR}/requirements.txt"

echo "[alphabot] installing frontend dependencies"
pushd "${ROOT_DIR}/frontend" >/dev/null
npm install
npm run build
popd >/dev/null

echo
echo "[alphabot] bootstrap complete"
echo "next:"
echo "  1. copy your .env into ${ROOT_DIR}/.env"
echo "  2. optionally copy alphabot.db and reports/"
echo "  3. run backend:  source ${VENV_DIR}/bin/activate && uvicorn backend.api.main:app --host 0.0.0.0 --port 8000"
echo "  4. run frontend: cd ${ROOT_DIR}/frontend && npm run dev"
