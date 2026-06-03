#!/bin/bash
# ─── Deploy / update: Argentina Legal & Data MCP → VPS ───────────────────────
# Actualiza el CÓDIGO en el VPS (additivo, no toca nginx ni el dataset).
# Uso:  bash scripts/deploy-vps.sh        (desde la raíz del repo, con commit hecho)
#
# Provisión inicial (una sola vez, ya hecha) — referencia:
#   - usuario de sistema `argmcp`, app en /opt/argentina-legal-data-mcp
#   - venv + `pip install -e ".[http]"`  (EDITABLE: así este deploy = extraer + restart)
#   - `python -m arg_legal_mcp build-dataset`  (dataset offline de InfoLEG)
#   - .env (authless, 127.0.0.1:8090), systemd `argentina-legal-data-mcp.service`
#   - nginx vhost mcp.derechointeligente.com.ar → 127.0.0.1:8090  + certbot --nginx
# ──────────────────────────────────────────────────────────────────────────────
set -e
VPS="${VPS:-vps}"                 # alias de ~/.ssh/config (HostName 187.127.8.156)
APP="/opt/argentina-legal-data-mcp"
SVC="argentina-legal-data-mcp.service"

echo "→ Empaquetando HEAD (solo archivos versionados)…"
git archive --format=tar.gz -o /tmp/argmcp.tar.gz HEAD

echo "→ Subiendo al VPS…"
scp -o BatchMode=yes /tmp/argmcp.tar.gz "$VPS:/tmp/argmcp.tar.gz"

echo "→ Extrayendo + reiniciando servicio…"
ssh -o BatchMode=yes "$VPS" "set -e; \
  tar xzf /tmp/argmcp.tar.gz -C $APP; \
  chown -R argmcp:argmcp $APP; \
  systemctl restart $SVC; sleep 3; \
  echo active=\$(systemctl is-active $SVC)"

echo "✓ Deploy OK → https://mcp.derechointeligente.com.ar/mcp"
echo "  (si cambiaron dependencias en pyproject, corré en el VPS: cd $APP && .venv/bin/pip install -e \".[http]\")"
