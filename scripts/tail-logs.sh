#!/bin/bash
#
# Aggregated log viewer for the distributed agent network.
#
# Usage:
#   ./tail-logs.sh [options]
#
# Examples:
#   ./tail-logs.sh                    # All containers
#   ./tail-logs.sh --main             # Main orchestrator only
#   ./tail-logs.sh --domains          # Domain orchestrators only
#   ./tail-logs.sh --container main-orchestrator
#   ./tail-logs.sh --task <task-id>   # Logs for specific task
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color

# Default values
REDIS_URL="${REDIS_URL:-redis://localhost:6379}"
FOLLOW=true
LINES=100
FILTER=""
TIMESTAMPS=true

# Container filters
SHOW_MAIN=false
SHOW_DOMAINS=false
SHOW_REDIS=false
CONTAINER_NAME=""
TASK_ID=""

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

usage() {
    cat << EOF
Usage: $(basename "$0") [options]

View aggregated logs from the distributed agent network.

Options:
    -f, --follow        Follow log output (default: true)
    -n, --lines N       Number of lines to show (default: 100)
    --no-follow         Don't follow, just show recent logs
    --no-timestamps     Hide timestamps

Filter Options:
    -m, --main          Show main orchestrator logs only
    -d, --domains       Show domain orchestrator logs only
    -r, --redis         Include Redis logs
    -c, --container     Show logs for specific container
    -t, --task ID       Show logs for specific task ID

    -g, --grep PATTERN  Filter logs by pattern

Display Options:
    --json              Output in JSON format
    --raw               Raw output without colors

Examples:
    # Follow all agent logs
    $(basename "$0")

    # Show last 50 lines from main orchestrator
    $(basename "$0") --main -n 50 --no-follow

    # Follow domain orchestrator logs, filter for errors
    $(basename "$0") --domains --grep "ERROR"

    # Show logs for a specific task
    $(basename "$0") --task abc123-def456

    # All containers including Redis
    $(basename "$0") --redis

EOF
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1" >&2
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

# Color code for container names
get_container_color() {
    local name="$1"
    case "$name" in
        main-orchestrator*)
            echo "$GREEN"
            ;;
        domain-backend*|backend*)
            echo "$CYAN"
            ;;
        domain-frontend*|frontend*)
            echo "$MAGENTA"
            ;;
        domain-devops*|devops*)
            echo "$YELLOW"
            ;;
        message-broker*|redis*)
            echo "$RED"
            ;;
        *)
            echo "$BLUE"
            ;;
    esac
}

# Get list of containers to monitor
get_containers() {
    local containers=""

    if [ -n "$CONTAINER_NAME" ]; then
        echo "$CONTAINER_NAME"
        return
    fi

    if [ "$SHOW_MAIN" = true ]; then
        containers="main-orchestrator"
    elif [ "$SHOW_DOMAINS" = true ]; then
        containers=$(docker ps --format '{{.Names}}' | grep -E '^domain-' || true)
    else
        # All agent containers
        containers=$(docker ps --format '{{.Names}}' | grep -E '^(main-orchestrator|domain-)' || true)
    fi

    if [ "$SHOW_REDIS" = true ]; then
        containers="$containers message-broker"
    fi

    echo "$containers" | tr '\n' ' '
}

# Check if Docker is available
check_docker() {
    if ! command -v docker &> /dev/null; then
        log_error "Docker not found. Please install Docker."
        exit 1
    fi

    if ! docker info &> /dev/null; then
        log_error "Cannot connect to Docker daemon."
        exit 1
    fi
}

# Show task-specific logs from Redis
show_task_logs() {
    local task_id="$1"

    # Extract host and port
    local redis_host
    local redis_port
    redis_host=$(echo "$REDIS_URL" | sed -E 's|redis://([^:]+):([0-9]+).*|\1|')
    redis_port=$(echo "$REDIS_URL" | sed -E 's|redis://([^:]+):([0-9]+).*|\2|')

    echo -e "${BLUE}=== Task: $task_id ===${NC}"
    echo ""

    # Task status
    echo -e "${YELLOW}Status:${NC}"
    redis-cli -h "$redis_host" -p "$redis_port" HGETALL "results:$task_id" 2>/dev/null | \
        while IFS= read -r key; do
            IFS= read -r value
            printf "  %s: %s\n" "$key" "$value"
        done

    echo ""

    # Task logs
    echo -e "${YELLOW}Logs:${NC}"
    local logs
    logs=$(redis-cli -h "$redis_host" -p "$redis_port" LRANGE "results:$task_id:logs" 0 -1 2>/dev/null)

    if [ -z "$logs" ]; then
        echo "  (no logs)"
    else
        echo "$logs" | while IFS= read -r line; do
            echo "  $line"
        done
    fi
}

# Format log line with color
format_log_line() {
    local container="$1"
    local line="$2"
    local color
    color=$(get_container_color "$container")

    # Truncate container name for display
    local short_name
    short_name=$(echo "$container" | cut -c1-20)

    printf "${color}%-20s${NC} | %s\n" "$short_name" "$line"
}

# Stream logs from containers
stream_logs() {
    local containers
    containers=$(get_containers)

    if [ -z "$containers" ]; then
        log_error "No matching containers found"
        echo ""
        echo "Running containers:"
        docker ps --format '  {{.Names}}'
        exit 1
    fi

    log_info "Streaming logs from: $containers"
    echo ""

    # Build docker logs command
    local follow_flag=""
    if [ "$FOLLOW" = true ]; then
        follow_flag="-f"
    fi

    local timestamp_flag=""
    if [ "$TIMESTAMPS" = true ]; then
        timestamp_flag="-t"
    fi

    # Use docker compose logs if available
    if [ -f "$PROJECT_ROOT/docker-compose.yml" ]; then
        cd "$PROJECT_ROOT"

        local services=""
        for container in $containers; do
            # Convert container name to service name
            case "$container" in
                main-orchestrator)
                    services="$services main-orchestrator"
                    ;;
                message-broker)
                    services="$services message-broker"
                    ;;
                domain-*)
                    # Domain containers are spawned dynamically, use docker logs directly
                    docker logs $follow_flag $timestamp_flag --tail "$LINES" "$container" 2>&1 | \
                        while IFS= read -r line; do
                            if [ -z "$FILTER" ] || echo "$line" | grep -q "$FILTER"; then
                                format_log_line "$container" "$line"
                            fi
                        done &
                    ;;
            esac
        done

        if [ -n "$services" ]; then
            docker compose logs $follow_flag $timestamp_flag --tail "$LINES" $services 2>&1 | \
                while IFS= read -r line; do
                    # Extract container name from docker compose output
                    local container_name
                    container_name=$(echo "$line" | sed -E 's/^([^ |]+)[ |]+.*/\1/' | tr -d ' ')
                    local log_content
                    log_content=$(echo "$line" | sed -E 's/^[^ |]+[ |]+//')

                    if [ -z "$FILTER" ] || echo "$log_content" | grep -q "$FILTER"; then
                        format_log_line "$container_name" "$log_content"
                    fi
                done
        fi

        wait
    else
        # Fallback to docker logs for each container
        for container in $containers; do
            docker logs $follow_flag $timestamp_flag --tail "$LINES" "$container" 2>&1 | \
                while IFS= read -r line; do
                    if [ -z "$FILTER" ] || echo "$line" | grep -q "$FILTER"; then
                        format_log_line "$container" "$line"
                    fi
                done &
        done
        wait
    fi
}

# Parse arguments
while [ $# -gt 0 ]; do
    case "$1" in
        -f|--follow)
            FOLLOW=true
            shift
            ;;
        --no-follow)
            FOLLOW=false
            shift
            ;;
        -n|--lines)
            LINES="$2"
            shift 2
            ;;
        --no-timestamps)
            TIMESTAMPS=false
            shift
            ;;
        -m|--main)
            SHOW_MAIN=true
            shift
            ;;
        -d|--domains)
            SHOW_DOMAINS=true
            shift
            ;;
        -r|--redis)
            SHOW_REDIS=true
            shift
            ;;
        -c|--container)
            CONTAINER_NAME="$2"
            shift 2
            ;;
        -t|--task)
            TASK_ID="$2"
            shift 2
            ;;
        -g|--grep)
            FILTER="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Main execution
if [ -n "$TASK_ID" ]; then
    show_task_logs "$TASK_ID"
else
    check_docker
    stream_logs
fi
