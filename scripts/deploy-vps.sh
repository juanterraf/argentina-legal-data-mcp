#!/bin/bash
# ─── Deploy / update: Argentina Legal & Data MCP → VPS ───────────────────────
# Actualiza el CÓDIGO y las unidades systemd en el VPS. No toca nginx ni el dataset.
# Uso:  bash scripts/deploy-vps.sh        (desde la raíz del repo, con commit hecho)
#
# Qué hace:
#   1. sube HEAD (solo archivos versionados) y lo extrae en $APP
#   2. `pip install -e ".[http]"` SOLO si cambió pyproject.toml
#   3. instala/actualiza servicio + timer del dataset (systemd/) y habilita el timer
#   4. chown al usuario argmcp, nada escribible por "otros", .env en 640
#   5. reinicia el servicio y verifica que quede activo
#
# Provisión inicial (una sola vez, ya hecha) — referencia:
#   - usuario de sistema `argmcp`, app en /opt/argentina-legal-data-mcp
#   - venv + `pip install -e ".[http]"`  (EDITABLE: así este deploy = extraer + restart)
#   - `python -m arg_legal_mcp build-dataset`  (dataset offline de InfoLEG)
#   - .env (authless, 127.0.0.1:8090; data/ y .cache/ dentro de $APP)
#   - nginx vhost mcp.derechointeligente.com.ar → 127.0.0.1:8090  + certbot --nginx
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail
VPS="${VPS:-vps}"                 # alias de tu ~/.ssh/config (Host vps -> tu servidor)
APP="/opt/argentina-legal-data-mcp"
SVC="argentina-legal-data-mcp.service"
DSVC="argentina-legal-data-mcp-dataset.service"
TIMER="argentina-legal-data-mcp-dataset.timer"
TMP_TAR="$(mktemp -t argmcp.XXXXXX).tar.gz"
trap 'rm -f "$TMP_TAR"' EXIT

echo "→ Empaquetando HEAD (solo archivos versionados)…"
git archive --format=tar.gz -o "$TMP_TAR" HEAD

echo "→ Subiendo al VPS…"
scp -o BatchMode=yes "$TMP_TAR" "$VPS:/tmp/argmcp.tar.gz"

echo "→ Extrayendo, instalando unidades y reiniciando…"
ssh -o BatchMode=yes "$VPS" "APP='$APP' SVC='$SVC' DSVC='$DSVC' TIMER='$TIMER' bash -s" <<'REMOTE'
set -euo pipefail
old_hash="$(sha256sum "$APP/pyproject.toml" 2>/dev/null | cut -d' ' -f1 || true)"

tar xzf /tmp/argmcp.tar.gz -C "$APP" --no-same-owner --no-same-permissions
rm -f /tmp/argmcp.tar.gz

# Ownership/permissions: app user, nothing writable by "others", .env not world-readable.
chown -R argmcp:argmcp "$APP"
chmod -R o-w "$APP"
if [ -f "$APP/.env" ]; then chmod 640 "$APP/.env"; fi
mkdir -p "$APP/data" "$APP/.cache"
chown argmcp:argmcp "$APP/data" "$APP/.cache"

new_hash="$(sha256sum "$APP/pyproject.toml" | cut -d' ' -f1)"
if [ "$old_hash" != "$new_hash" ]; then
  echo "  pyproject.toml cambió → pip install -e .[http]"
  (cd "$APP" && runuser -u argmcp -- "$APP/.venv/bin/pip" install --quiet -e ".[http]")
fi

# systemd units (service + dataset timer), only rewritten when they changed.
changed=0
for unit in "$SVC" "$DSVC" "$TIMER"; do
  if ! cmp -s "$APP/systemd/$unit" "/etc/systemd/system/$unit"; then
    install -m 0644 -o root -g root "$APP/systemd/$unit" "/etc/systemd/system/$unit"
    changed=1
    echo "  unidad actualizada: $unit"
  fi
done
if [ "$changed" = 1 ]; then systemctl daemon-reload; fi
systemctl enable --now "$TIMER" >/dev/null 2>&1 || systemctl enable --now "$TIMER"

systemctl restart "$SVC"
sleep 3
state="$(systemctl is-active "$SVC" || true)"
echo "  $SVC: $state"
echo "  timer: $(systemctl is-enabled "$TIMER" 2>/dev/null) / próxima: $(systemctl show -p NextElapseUSecRealtime --value "$TIMER" 2>/dev/null)"
if [ "$state" != "active" ]; then
  journalctl -u "$SVC" -n 40 --no-pager || true
  exit 1
fi
REMOTE

echo "✓ Deploy OK → https://mcp.derechointeligente.com.ar/mcp"
