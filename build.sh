#!/bin/bash
# build.sh – PresenceService Deploy
# Verwendung: bash build.sh

set -e

echo "=== PresenceService Deploy ==="

# ── Docker Network erstellen ──────────────────────────────────
echo ""
echo "--- Network ---"
if docker network ls | grep -q presence-net; then
    echo "  ✅ Network presence-net existiert bereits"
else
    docker network create presence-net
    echo "  ✅ Network presence-net erstellt"
fi

# ── Redis starten (falls nicht läuft) ────────────────────────
echo ""
echo "--- Redis ---"

docker stop presence-redis 2>/dev/null || true
docker rm   presence-redis 2>/dev/null || true

docker run -d \
  --name presence-redis \
  --network presence-net \
  --restart unless-stopped \
  redis:7-alpine \
  redis-server --save "" --appendonly no

echo "  ✅ Redis gestartet"


# ── Alten Container stoppen ───────────────────────────────────
echo ""
echo "--- Build ---"
docker stop presenceservice 2>/dev/null || true
docker rm   presenceservice 2>/dev/null || true

# ── Neues Image bauen ─────────────────────────────────────────
docker build --no-cache -t presenceservice .
echo "  ✅ Image gebaut"

# ── Neuen Container starten ───────────────────────────────────
echo ""
echo "--- Start ---"
docker stop presenceservice 2>/dev/null || true
docker rm   presenceservice 2>/dev/null || true
docker run -d \
  --name presenceservice \
  --network presence-net \
  -p 8002:8000 \
  --env-file .env \
  --restart unless-stopped \
  presenceservice

docker network disconnect bridge presenceservice 2>/dev/null || true

echo "  ✅ PresenceService gestartet auf Port 8002"

# ── Health Check ──────────────────────────────────────────────
echo ""
echo "--- Health Check ---"
sleep 3
if curl -sf http://localhost:8002/health > /dev/null 2>&1; then
    echo "  ✅ Service antwortet"
    curl -s http://localhost:8002/health
    echo ""
else
    echo "  ⚠️  Noch nicht bereit – prüfe Logs:"
    echo "     docker logs presenceservice"
fi

echo ""
echo "=== Deploy abgeschlossen: $(date) ==="
echo ""
echo "  Logs:      docker logs -f presenceservice"
echo "  Stoppen:   docker stop presenceservice presence-redis"
echo "  Neustart:  docker restart presenceservice"
