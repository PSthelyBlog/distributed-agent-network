#!/bin/bash
#
# CLI tool for submitting tasks to the distributed agent network.
#
# Usage:
#   ./send-task.sh <domain> <description> [options]
#
# Examples:
#   ./send-task.sh backend "Create REST API for users"
#   ./send-task.sh frontend "Build login form component" --wait
#   ./send-task.sh devops "Set up CI pipeline" --priority high --timeout 600
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
REDIS_URL="${REDIS_URL:-redis://localhost:6379}"
PRIORITY="normal"
TIMEOUT=300
WAIT=false
SOURCE="cli"
CONTEXT=""

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

usage() {
    cat << EOF
Usage: $(basename "$0") <domain> <description> [options]

Submit a task to a domain orchestrator.

Arguments:
    domain          Target domain (backend, frontend, devops)
    description     Task description

Options:
    -p, --priority  Task priority: low, normal, high (default: normal)
    -t, --timeout   Task timeout in seconds (default: 300)
    -w, --wait      Wait for task completion and show result
    -s, --source    Source identifier (default: cli)
    -c, --context   JSON context string (e.g., '{"key": "value"}')
    -r, --redis     Redis URL (default: redis://localhost:6379)
    -h, --help      Show this help message

Examples:
    # Simple task
    $(basename "$0") backend "Create user authentication endpoint"

    # High priority with wait
    $(basename "$0") frontend "Fix critical login bug" --priority high --wait

    # With context
    $(basename "$0") backend "Implement feature" -c '{"feature": "oauth", "provider": "google"}'

    # Custom Redis
    $(basename "$0") devops "Deploy to staging" --redis redis://custom:6379

EOF
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

# Check if Redis is available
check_redis() {
    if ! command -v redis-cli &> /dev/null; then
        log_error "redis-cli not found. Please install Redis tools."
        exit 1
    fi

    # Extract host and port from URL
    local redis_host
    local redis_port
    redis_host=$(echo "$REDIS_URL" | sed -E 's|redis://([^:]+):([0-9]+).*|\1|')
    redis_port=$(echo "$REDIS_URL" | sed -E 's|redis://([^:]+):([0-9]+).*|\2|')

    if ! redis-cli -h "$redis_host" -p "$redis_port" ping &> /dev/null; then
        log_error "Cannot connect to Redis at $REDIS_URL"
        log_info "Start Redis with: docker compose up -d message-broker"
        exit 1
    fi
}

# Generate UUID
generate_uuid() {
    if command -v uuidgen &> /dev/null; then
        uuidgen | tr '[:upper:]' '[:lower:]'
    else
        python3 -c "import uuid; print(uuid.uuid4())"
    fi
}

# Publish task to Redis
publish_task() {
    local domain="$1"
    local description="$2"
    local task_id
    local timestamp
    local task_json

    task_id=$(generate_uuid)
    timestamp=$(date -u +"%Y-%m-%dT%H:%M:%S.000Z")

    # Build task JSON
    task_json=$(cat << EOF
{
    "task_id": "$task_id",
    "type": "task_assignment",
    "source": "$SOURCE",
    "destination": "$domain",
    "timestamp": "$timestamp",
    "payload": {
        "description": "$description",
        "requirements": [],
        "context": ${CONTEXT:-{}}
    },
    "metadata": {
        "priority": "$PRIORITY",
        "timeout_seconds": $TIMEOUT
    }
}
EOF
)

    # Extract host and port
    local redis_host
    local redis_port
    redis_host=$(echo "$REDIS_URL" | sed -E 's|redis://([^:]+):([0-9]+).*|\1|')
    redis_port=$(echo "$REDIS_URL" | sed -E 's|redis://([^:]+):([0-9]+).*|\2|')

    # Push to queue
    redis-cli -h "$redis_host" -p "$redis_port" LPUSH "tasks:pending:$domain" "$task_json" > /dev/null

    # Initialize result tracking
    redis-cli -h "$redis_host" -p "$redis_port" HSET "results:$task_id" \
        "task_id" "$task_id" \
        "status" "pending" > /dev/null

    # Publish notification
    redis-cli -h "$redis_host" -p "$redis_port" PUBLISH "notifications:$domain" "$task_json" > /dev/null

    echo "$task_id"
}

# Wait for task completion
wait_for_result() {
    local task_id="$1"
    local timeout="$2"
    local elapsed=0
    local interval=2
    local status

    # Extract host and port
    local redis_host
    local redis_port
    redis_host=$(echo "$REDIS_URL" | sed -E 's|redis://([^:]+):([0-9]+).*|\1|')
    redis_port=$(echo "$REDIS_URL" | sed -E 's|redis://([^:]+):([0-9]+).*|\2|')

    log_info "Waiting for task completion (timeout: ${timeout}s)..."

    while [ $elapsed -lt "$timeout" ]; do
        status=$(redis-cli -h "$redis_host" -p "$redis_port" HGET "results:$task_id" "status" 2>/dev/null)

        case "$status" in
            completed)
                log_success "Task completed!"
                echo ""
                echo "Result:"
                redis-cli -h "$redis_host" -p "$redis_port" HGETALL "results:$task_id" | \
                    while IFS= read -r key; do
                        IFS= read -r value
                        printf "  %s: %s\n" "$key" "$value"
                    done
                return 0
                ;;
            failed)
                log_error "Task failed!"
                echo ""
                echo "Error:"
                redis-cli -h "$redis_host" -p "$redis_port" HGET "results:$task_id" "error"
                return 1
                ;;
            in_progress)
                printf "\r  Status: in_progress (${elapsed}s elapsed)..."
                ;;
            pending)
                printf "\r  Status: pending (${elapsed}s elapsed)..."
                ;;
        esac

        sleep $interval
        elapsed=$((elapsed + interval))
    done

    echo ""
    log_error "Timeout waiting for task completion"
    return 1
}

# Get task status
get_status() {
    local task_id="$1"

    # Extract host and port
    local redis_host
    local redis_port
    redis_host=$(echo "$REDIS_URL" | sed -E 's|redis://([^:]+):([0-9]+).*|\1|')
    redis_port=$(echo "$REDIS_URL" | sed -E 's|redis://([^:]+):([0-9]+).*|\2|')

    echo "Task: $task_id"
    echo "Status:"
    redis-cli -h "$redis_host" -p "$redis_port" HGETALL "results:$task_id" | \
        while IFS= read -r key; do
            IFS= read -r value
            printf "  %s: %s\n" "$key" "$value"
        done
}

# Parse arguments
if [ $# -lt 2 ]; then
    usage
    exit 1
fi

DOMAIN="$1"
DESCRIPTION="$2"
shift 2

# Parse options
while [ $# -gt 0 ]; do
    case "$1" in
        -p|--priority)
            PRIORITY="$2"
            shift 2
            ;;
        -t|--timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        -w|--wait)
            WAIT=true
            shift
            ;;
        -s|--source)
            SOURCE="$2"
            shift 2
            ;;
        -c|--context)
            CONTEXT="$2"
            shift 2
            ;;
        -r|--redis)
            REDIS_URL="$2"
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

# Validate domain
case "$DOMAIN" in
    backend|frontend|devops)
        ;;
    *)
        log_warn "Non-standard domain: $DOMAIN (expected: backend, frontend, devops)"
        ;;
esac

# Validate priority
case "$PRIORITY" in
    low|normal|high)
        ;;
    *)
        log_error "Invalid priority: $PRIORITY (expected: low, normal, high)"
        exit 1
        ;;
esac

# Main execution
check_redis

log_info "Submitting task to $DOMAIN domain..."
log_info "Description: $DESCRIPTION"
log_info "Priority: $PRIORITY, Timeout: ${TIMEOUT}s"

TASK_ID=$(publish_task "$DOMAIN" "$DESCRIPTION")

log_success "Task submitted: $TASK_ID"

if [ "$WAIT" = true ]; then
    echo ""
    wait_for_result "$TASK_ID" "$TIMEOUT"
else
    echo ""
    echo "To check status:"
    echo "  redis-cli HGETALL results:$TASK_ID"
    echo ""
    echo "To wait for completion:"
    echo "  $0 status $TASK_ID --wait"
fi
