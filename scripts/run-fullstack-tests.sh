#!/bin/bash
# Full-stack integration test runner for genro-mail-proxy
# This script starts the Docker infrastructure, runs tests, and cleans up

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_ROOT/tests/docker/docker-compose.fulltest.yml"

# Default options
KEEP_RUNNING=false
REBUILD=false
TEST_FILTER=""
VERBOSE=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --keep|-k)
            KEEP_RUNNING=true
            shift
            ;;
        --rebuild|-r)
            REBUILD=true
            shift
            ;;
        --filter|-f)
            TEST_FILTER="$2"
            shift 2
            ;;
        --verbose|-v)
            VERBOSE="-v -s"
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  -k, --keep       Keep containers running after tests"
            echo "  -r, --rebuild    Force rebuild of Docker images"
            echo "  -f, --filter     Filter tests by keyword (passed to pytest -k)"
            echo "  -v, --verbose    Verbose output"
            echo "  -h, --help       Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

echo -e "${GREEN}=======================================${NC}"
echo -e "${GREEN}  genro-mail-proxy Full-Stack Tests   ${NC}"
echo -e "${GREEN}=======================================${NC}"
echo ""

# Check Docker
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Error: Docker is not installed${NC}"
    exit 1
fi

if ! docker compose version &> /dev/null; then
    echo -e "${RED}Error: Docker Compose is not available${NC}"
    exit 1
fi

# Change to project root
cd "$PROJECT_ROOT"

# Cleanup function
cleanup() {
    if [ "$KEEP_RUNNING" = false ]; then
        echo ""
        echo -e "${YELLOW}Cleaning up...${NC}"
        docker compose -f "$COMPOSE_FILE" down -v --remove-orphans 2>/dev/null || true
    else
        echo ""
        echo -e "${YELLOW}Containers kept running. To stop:${NC}"
        echo "docker compose -f $COMPOSE_FILE down -v"
    fi
}

# Set trap for cleanup on exit
trap cleanup EXIT

# Stop any existing containers
echo -e "${YELLOW}Stopping any existing test containers...${NC}"
docker compose -f "$COMPOSE_FILE" down -v --remove-orphans 2>/dev/null || true

# Build options
BUILD_OPTS=""
if [ "$REBUILD" = true ]; then
    BUILD_OPTS="--build --no-cache"
else
    BUILD_OPTS="--build"
fi

# Start infrastructure
echo ""
echo -e "${YELLOW}Starting test infrastructure...${NC}"
docker compose -f "$COMPOSE_FILE" up -d $BUILD_OPTS

# Wait for services to be healthy
echo ""
echo -e "${YELLOW}Waiting for services to be healthy...${NC}"

max_wait=120
waited=0
while [ $waited -lt $max_wait ]; do
    # Check if mailproxy is healthy
    if docker compose -f "$COMPOSE_FILE" ps --format json 2>/dev/null | grep -q '"Health":"healthy".*mailproxy' || \
       docker compose -f "$COMPOSE_FILE" ps 2>/dev/null | grep -q "mailproxy.*healthy"; then
        echo -e "${GREEN}All services are healthy!${NC}"
        break
    fi

    # Alternative check
    if docker compose -f "$COMPOSE_FILE" exec -T mailproxy curl -sf http://localhost:8000/health &>/dev/null; then
        echo -e "${GREEN}Mail proxy service is ready!${NC}"
        break
    fi

    sleep 2
    waited=$((waited + 2))
    echo -n "."
done

if [ $waited -ge $max_wait ]; then
    echo ""
    echo -e "${RED}Timeout waiting for services${NC}"
    echo ""
    echo "Service status:"
    docker compose -f "$COMPOSE_FILE" ps
    echo ""
    echo "Mail proxy logs:"
    docker compose -f "$COMPOSE_FILE" logs mailproxy --tail=50
    exit 1
fi

# Show service status
echo ""
echo -e "${YELLOW}Service status:${NC}"
docker compose -f "$COMPOSE_FILE" ps

# Run tests
echo ""
echo -e "${YELLOW}Running tests...${NC}"
echo ""

PYTEST_OPTS="-v $VERBOSE -m fullstack"
if [ -n "$TEST_FILTER" ]; then
    PYTEST_OPTS="$PYTEST_OPTS -k '$TEST_FILTER'"
fi

# Run pytest
cd "$PROJECT_ROOT"
if eval "python -m pytest tests/test_fullstack_integration.py $PYTEST_OPTS"; then
    echo ""
    echo -e "${GREEN}=======================================${NC}"
    echo -e "${GREEN}  All tests passed!                    ${NC}"
    echo -e "${GREEN}=======================================${NC}"
    exit 0
else
    echo ""
    echo -e "${RED}=======================================${NC}"
    echo -e "${RED}  Some tests failed                    ${NC}"
    echo -e "${RED}=======================================${NC}"
    exit 1
fi
