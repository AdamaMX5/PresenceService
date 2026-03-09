#!/bin/bash
# ─────────────────────────────────────────────────────────────
# deploy.sh – PresenceService Erstinstallation
# Verwendung: sudo bash deploy.sh
# ─────────────────────────────────────────────────────────────
set -e  # Abbruch bei Fehler

# ── Konfiguration ────────────────────────────────────────────
SERVICE_NAME="presence-service"
COMPOSE_FILE="docker-compose-presence.yml"
WORK_DIR="$(cd "$(dirname "$0")" && pwd)"   # Ordner wo das Script liegt

# ── Farben ───────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()    { echo -e "${GREEN}[✓]${NC} $1"; }
warning() { echo -e "${YELLOW}[!]${NC} $1"; }
error()   { echo -e "${RED}[✗]${NC} $1"; exit 1; }

# ── Root-Check ───────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
  error "Bitte als root ausführen: sudo bash deploy.sh"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  PresenceService Deployment"
echo "  Arbeitsverzeichnis: $WORK_DIR"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── 1. Docker prüfen ─────────────────────────────────────────
info "Prüfe Docker..."
if ! command -v docker &> /dev/null; then
  error "Docker nicht gefunden. Bitte zuerst Docker installieren: https://docs.docker.com/engine/install/"
fi

if ! docker compose version &> /dev/null; then
  error "Docker Compose Plugin nicht gefunden. Bitte 'docker compose' (v2) installieren."
fi

DOCKER_VERSION=$(docker --version)
info "Docker gefunden: $DOCKER_VERSION"

# ── 2. Compose-Datei prüfen ──────────────────────────────────
info "Prüfe Compose-Datei..."
if [ ! -f "$WORK_DIR/$COMPOSE_FILE" ]; then
  error "Compose-Datei nicht gefunden: $WORK_DIR/$COMPOSE_FILE"
fi

# ── 3. .env prüfen ───────────────────────────────────────────
info "Prüfe Umgebungsvariablen..."
if [ ! -f "$WORK_DIR/.env" ]; then
  warning ".env nicht gefunden – erstelle Vorlage..."
  cat > "$WORK_DIR/.env" << EOF
# PresenceService Konfiguration
AUTH_SERVICE_URL=https://auth.freischule.info
REDIS_URL=redis://redis:6379
EOF
  warning ".env erstellt – bitte Werte prüfen: $WORK_DIR/.env"
else
  info ".env gefunden"
fi

# ── 4. Docker Image bauen ─────────────────────────────────────
info "Baue Docker Image..."
docker compose -f "$WORK_DIR/$COMPOSE_FILE" build --no-cache
info "Image erfolgreich gebaut"

# ── 5. Container starten ──────────────────────────────────────
info "Starte Container..."
docker compose -f "$WORK_DIR/$COMPOSE_FILE" up -d
info "Container gestartet"

# ── 6. Kurz warten und Health Check ──────────────────────────
info "Warte auf Service-Start..."
sleep 3

if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
  info "Health Check OK – Service läuft"
else
  warning "Health Check fehlgeschlagen – Service startet möglicherweise noch"
  warning "Prüfe mit: docker compose -f $COMPOSE_FILE logs presence"
fi

# ── 7. systemd Service einrichten ────────────────────────────
info "Richte systemd Service ein..."

cat > /etc/systemd/system/${SERVICE_NAME}.service << EOF
[Unit]
Description=PresenceService (Docker Compose)
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${WORK_DIR}
ExecStart=/usr/bin/docker compose -f ${WORK_DIR}/${COMPOSE_FILE} up -d
ExecStop=/usr/bin/docker compose -f ${WORK_DIR}/${COMPOSE_FILE} down
ExecReload=/usr/bin/docker compose -f ${WORK_DIR}/${COMPOSE_FILE} restart
TimeoutStartSec=120
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
info "systemd Service '${SERVICE_NAME}' aktiviert"

# ── 8. Zusammenfassung ────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}  Deployment abgeschlossen!${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  Nützliche Befehle:"
echo ""
echo "  Status prüfen:    systemctl status ${SERVICE_NAME}"
echo "  Logs anzeigen:    docker compose -f ${COMPOSE_FILE} logs -f"
echo "  Neu starten:      systemctl restart ${SERVICE_NAME}"
echo "  Stoppen:          systemctl stop ${SERVICE_NAME}"
echo "  Health Check:     curl http://localhost:8000/health"
echo ""
echo "  Nächster Schritt: Cloudflare Route einrichten"
echo "  → presence.freischule.info → http://<server-ip>:8000"
echo ""