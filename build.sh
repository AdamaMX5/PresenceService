#!/bin/bash
# build.sh – PresenceService Deploy
# Verwendung: bash build.sh

set -e

echo "=== PresenceService Deploy ==="

# ── Redis starten (falls nicht läuft) ────────────────────────
echo ""
echo "--- Redis ---"
if docker ps --filter "name=presence-redis" --filter "status=running" | grep -q presence-redis; then
    echo "  ✅ Redis läuft bereits"
else
    docker stop presence-redis 2>/dev/null || true
    docker rm   presence-redis 2>/dev/null || true

    docker run -d \
      --name presence-redis \
      --restart unless-stopped \
      redis:7-alpine \
      redis-server --save "" --appendonly no

    echo "  ✅ Redis gestartet"
fi

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
docker run -d \
  --name presenceservice \
  -p 8002:8000 \
  --link presence-redis:redis \
  --env-file .env \
  --restart unless-stopped \
  presenceservice

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
