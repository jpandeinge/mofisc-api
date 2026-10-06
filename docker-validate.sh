#!/bin/bash
# Docker Setup Validation Script
# This script validates the Docker configuration before building

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Docker Configuration Validation ===${NC}"
echo ""

# Check Docker installation
echo -e "${YELLOW}Checking Docker installation...${NC}"
if ! command -v docker &> /dev/null; then
    echo -e "${RED}✗ Docker is not installed${NC}"
    echo "Please install Docker Desktop from https://www.docker.com/products/docker-desktop"
    exit 1
else
    DOCKER_VERSION=$(docker --version)
    echo -e "${GREEN}✓ Docker installed: ${DOCKER_VERSION}${NC}"
fi

# Check Docker Compose
echo -e "${YELLOW}Checking Docker Compose...${NC}"
if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo -e "${RED}✗ Docker Compose is not installed${NC}"
    exit 1
else
    if command -v docker-compose &> /dev/null; then
        COMPOSE_VERSION=$(docker-compose --version)
    else
        COMPOSE_VERSION=$(docker compose version)
    fi
    echo -e "${GREEN}✓ Docker Compose available: ${COMPOSE_VERSION}${NC}"
fi

# Check if Docker daemon is running
echo -e "${YELLOW}Checking Docker daemon...${NC}"
if ! docker info &> /dev/null; then
    echo -e "${RED}✗ Docker daemon is not running${NC}"
    echo "Please start Docker Desktop"
    exit 1
else
    echo -e "${GREEN}✓ Docker daemon is running${NC}"
fi

# Check .env file
echo -e "${YELLOW}Checking environment file...${NC}"
if [ ! -f .env ]; then
    echo -e "${RED}✗ .env file not found${NC}"
    echo "Please create .env file from .env.example"
    exit 1
else
    echo -e "${GREEN}✓ .env file exists${NC}"
fi

# Check Firebase credentials
echo -e "${YELLOW}Checking Firebase credentials...${NC}"
if [ ! -f .secrets/firebase-adminsdk.json ]; then
    echo -e "${RED}✗ Firebase credentials not found at .secrets/firebase-adminsdk.json${NC}"
    echo "Please ensure Firebase credentials are in .secrets/firebase-adminsdk.json"
    exit 1
else
    echo -e "${GREEN}✓ Firebase credentials found${NC}"
fi

# Check required files
echo -e "${YELLOW}Checking required files...${NC}"
REQUIRED_FILES=(
    "Dockerfile"
    "docker-compose.yml"
    "requirements.txt"
    "alembic.ini"
)

for file in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$file" ]; then
        echo -e "${RED}✗ Missing required file: $file${NC}"
        exit 1
    fi
done
echo -e "${GREEN}✓ All required files present${NC}"

# Validate docker-compose.yml
echo -e "${YELLOW}Validating docker-compose.yml...${NC}"
if docker-compose config &> /dev/null || docker compose config &> /dev/null; then
    echo -e "${GREEN}✓ docker-compose.yml is valid${NC}"
else
    echo -e "${RED}✗ docker-compose.yml has syntax errors${NC}"
    exit 1
fi

# Check for required environment variables
echo -e "${YELLOW}Checking required environment variables...${NC}"
REQUIRED_VARS=(
    "DATABASE_URL"
    "SECRET_KEY"
    "FIREBASE_WEB_API_KEY"
    "SENDGRID_API_KEY"
)

MISSING_VARS=()
for var in "${REQUIRED_VARS[@]}"; do
    if ! grep -q "^${var}=" .env; then
        MISSING_VARS+=("$var")
    fi
done

if [ ${#MISSING_VARS[@]} -gt 0 ]; then
    echo -e "${YELLOW}⚠ Missing environment variables in .env:${NC}"
    for var in "${MISSING_VARS[@]}"; do
        echo "  - $var"
    done
    echo "Please update your .env file"
else
    echo -e "${GREEN}✓ All required environment variables present${NC}"
fi

echo ""
echo -e "${GREEN}=== Validation Complete ===${NC}"
echo ""
echo "To build and start the Docker containers, run:"
echo -e "${YELLOW}  docker-compose up --build${NC}"
echo ""
echo "To run in detached mode:"
echo -e "${YELLOW}  docker-compose up -d --build${NC}"
echo ""
echo "To view logs:"
echo -e "${YELLOW}  docker-compose logs -f${NC}"
echo ""
echo "To stop all services:"
echo -e "${YELLOW}  docker-compose down${NC}"
