#!/bin/bash
# Management script for Galahad API services
# Usage: ./manage_services.sh [start|stop|restart|status]

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PATH="$PROJECT_DIR/.venv/bin/activate"

cd "$PROJECT_DIR"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

start_api() {
    echo -e "${GREEN}Starting API server...${NC}"
    if pgrep -f "uvicorn api.main:app" > /dev/null; then
        echo -e "${YELLOW}API server already running${NC}"
    else
        ./run_server.sh &
        sleep 3
        echo -e "${GREEN} API server started on port 8000${NC}"
    fi
}

start_celery() {
    echo -e "${GREEN}Starting Celery worker...${NC}"
    if pgrep -f "celery.*worker" > /dev/null; then
        echo -e "${YELLOW}Celery worker already running${NC}"
    else
        source "$VENV_PATH"
        celery -A api.app.celery_app worker \
            --loglevel=info \
            --concurrency=4 \
            --queues=notifications \
            --max-tasks-per-child=1000 \
            --logfile=logs/celery-worker.log \
            --pidfile=logs/celery-worker.pid \
            --detach
        sleep 2
        echo -e "${GREEN} Celery worker started${NC}"
    fi
}

start_beat() {
    echo -e "${GREEN}Starting Celery Beat...${NC}"
    if pgrep -f "celery.*beat" > /dev/null; then
        echo -e "${YELLOW} Celery Beat already running${NC}"
    else
        source "$VENV_PATH"
        celery -A api.app.celery_app beat \
            --loglevel=info \
            --logfile=logs/celery-beat.log \
            --pidfile=logs/celery-beat.pid \
            --detach
        sleep 2
        echo -e "${GREEN} Celery Beat started${NC}"
    fi
}

start_flower() {
    echo -e "${GREEN} Starting Flower dashboard...${NC}"
    if pgrep -f "flower" > /dev/null; then
        echo -e "${YELLOW} Flower already running${NC}"
    else
        source "$VENV_PATH"
        nohup celery -A api.app.celery_app flower \
            --port=5555 \
            --logfile=logs/flower.log > /dev/null 2>&1 &
        sleep 2
        echo -e "${GREEN} Flower started on http://localhost:5555${NC}"
    fi
}

start_redis() {
    echo -e "${GREEN}Starting Redis...${NC}"
    if redis-cli ping > /dev/null 2>&1; then
        echo -e "${YELLOW}Redis already running${NC}"
    else
        brew services start redis
        sleep 2
        echo -e "${GREEN}Redis started${NC}"
    fi
}

stop_api() {
    echo -e "${YELLOW}Stopping API server...${NC}"
    pkill -f "uvicorn api.main:app" || echo "API not running"
}

stop_celery() {
    echo -e "${YELLOW}Stopping Celery worker...${NC}"
    pkill -f "celery.*worker" || echo "Celery worker not running"
}

stop_beat() {
    echo -e "${YELLOW}Stopping Celery Beat...${NC}"
    pkill -f "celery.*beat" || echo "Celery Beat not running"
}

stop_flower() {
    echo -e "${YELLOW}Stopping Flower...${NC}"
    pkill -f "flower" || echo "Flower not running"
}

status() {
    echo -e "${GREEN}=== Service Status ===${NC}"
    echo ""

    # API
    if pgrep -f "uvicorn api.main:app" > /dev/null; then
        echo -e "${GREEN} API Server: RUNNING${NC} (port 8000)"
    else
        echo -e "${RED}✗ API Server: STOPPED${NC}"
    fi

    # Redis
    if redis-cli ping > /dev/null 2>&1; then
        echo -e "${GREEN} Redis: RUNNING${NC} (port 6379)"
    else
        echo -e "${RED}✗ Redis: STOPPED${NC}"
    fi

    # Celery Worker
    if pgrep -f "celery.*worker" > /dev/null; then
        echo -e "${GREEN} Celery Worker: RUNNING${NC}"
        source "$VENV_PATH"
        celery -A api.app.celery_app inspect ping -t 2 2>&1 | grep -q "pong" && echo "  └─ Status: Healthy" || echo "  └─ Status: Unhealthy"
    else
        echo -e "${RED}✗ Celery Worker: STOPPED${NC}"
    fi

    # Celery Beat
    if pgrep -f "celery.*beat" > /dev/null; then
        echo -e "${GREEN} Celery Beat: RUNNING${NC}"
    else
        echo -e "${YELLOW}⚠ Celery Beat: STOPPED${NC} (optional)"
    fi

    # Flower
    if pgrep -f "flower" > /dev/null; then
        echo -e "${GREEN} Flower: RUNNING${NC} (http://localhost:5555)"
    else
        echo -e "${YELLOW}⚠ Flower: STOPPED${NC} (optional)"
    fi

    echo ""
    echo -e "${GREEN}=== Quick Access ===${NC}"
    echo "API Docs: http://localhost:8000/docs"
    echo "Health: http://localhost:8000/health"
    echo "Flower: http://localhost:5555"
}

case "$1" in
    start)
        echo -e "${GREEN}Starting all services...${NC}"
        mkdir -p logs
        start_redis
        start_api
        start_celery
        start_beat
        start_flower
        echo ""
        status
        ;;
    stop)
        echo -e "${YELLOW}Stopping all services...${NC}"
        stop_flower
        stop_beat
        stop_celery
        stop_api
        echo -e "${GREEN} All services stopped${NC}"
        ;;
    restart)
        $0 stop
        sleep 2
        $0 start
        ;;
    status)
        status
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        echo ""
        echo "Examples:"
        echo "  $0 start    - Start all services"
        echo "  $0 stop     - Stop all services"
        echo "  $0 restart  - Restart all services"
        echo "  $0 status   - Show service status"
        exit 1
        ;;
esac
