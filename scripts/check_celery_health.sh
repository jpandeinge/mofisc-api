#!/bin/bash
# Health check script for Celery worker
# Returns 0 if healthy, 1 if unhealthy

cd "$(dirname "$0")/.."
source .venv/bin/activate

# Check if worker is responding
WORKER_PING=$(celery -A api.app.celery_app inspect ping -t 5 2>&1)

if echo "$WORKER_PING" | grep -q "pong"; then
    echo "✓ Celery worker is healthy"

    # Get worker stats
    echo ""
    echo "Worker Statistics:"
    celery -A api.app.celery_app inspect stats | grep -E "total|pool|uptime" | head -10

    # Check Redis connectivity
    echo ""
    echo "Redis Status:"
    redis-cli ping

    # Check task queues
    echo ""
    echo "Task Queues:"
    redis-cli LLEN celery
    redis-cli LLEN notifications

    exit 0
else
    echo "✗ Celery worker is NOT responding"
    echo ""
    echo "Output:"
    echo "$WORKER_PING"
    exit 1
fi
