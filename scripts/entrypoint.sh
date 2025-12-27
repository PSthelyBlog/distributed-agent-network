#!/bin/bash
set -e

# Colors for logging
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

# Validate required environment variables
check_env() {
    if [ -z "$ANTHROPIC_API_KEY" ]; then
        log_error "ANTHROPIC_API_KEY is not set"
        exit 1
    fi

    if [ -z "$REDIS_URL" ]; then
        log_warn "REDIS_URL not set, using default: redis://message-broker:6379"
        export REDIS_URL="redis://message-broker:6379"
    fi
}

# Wait for Redis to be available
wait_for_redis() {
    log_info "Waiting for Redis connection..."

    local max_attempts=30
    local attempt=1

    while [ $attempt -le $max_attempts ]; do
        if python3 -c "from lib.messaging import AgentMessaging; m = AgentMessaging(); m.ping()" 2>/dev/null; then
            log_info "Redis connection established"
            return 0
        fi

        log_info "Redis not ready, attempt $attempt/$max_attempts..."
        sleep 2
        attempt=$((attempt + 1))
    done

    log_error "Could not connect to Redis after $max_attempts attempts"
    exit 1
}

# Copy agent configuration to workspace
setup_agent_config() {
    log_info "Setting up agent configuration..."

    # Copy CLAUDE.md if exists
    if [ -f "/agent-config/CLAUDE.md" ]; then
        cp /agent-config/CLAUDE.md /workspace/CLAUDE.md
        log_info "Copied CLAUDE.md to workspace"
    fi

    # Copy .claude directory if exists
    if [ -d "/agent-config/.claude" ]; then
        cp -r /agent-config/.claude /workspace/.claude
        log_info "Copied .claude directory to workspace"
    fi
}

# Register agent with the network
register_agent() {
    log_info "Registering agent with network..."

    python3 -c "
from lib.registry import AgentRegistry
import os

registry = AgentRegistry()
agent_id = os.environ.get('AGENT_ID', os.environ.get('HOSTNAME', 'unknown'))
agent_role = os.environ.get('AGENT_ROLE', 'worker')
domain_type = os.environ.get('DOMAIN_TYPE', '')

registry.register(agent_id, agent_role, domain_type)
print(f'Registered agent: {agent_id} as {agent_role}')
"

    if [ $? -eq 0 ]; then
        log_info "Agent registered successfully"
    else
        log_error "Failed to register agent"
        exit 1
    fi
}

# Start heartbeat in background
start_heartbeat() {
    log_info "Starting heartbeat process..."

    python3 -c "
from lib.registry import AgentRegistry
import os
import time

registry = AgentRegistry()
agent_id = os.environ.get('AGENT_ID', os.environ.get('HOSTNAME', 'unknown'))

while True:
    try:
        registry.heartbeat(agent_id)
    except Exception as e:
        print(f'Heartbeat error: {e}')
    time.sleep(10)
" &

    HEARTBEAT_PID=$!
    log_info "Heartbeat started with PID: $HEARTBEAT_PID"
}

# Cleanup on exit
cleanup() {
    log_info "Shutting down agent..."

    # Deregister from network
    python3 -c "
from lib.registry import AgentRegistry
import os

registry = AgentRegistry()
agent_id = os.environ.get('AGENT_ID', os.environ.get('HOSTNAME', 'unknown'))
registry.deregister(agent_id)
" 2>/dev/null || true

    # Kill heartbeat process
    if [ ! -z "$HEARTBEAT_PID" ]; then
        kill $HEARTBEAT_PID 2>/dev/null || true
    fi

    log_info "Agent shutdown complete"
}

trap cleanup EXIT

# Main execution
main() {
    log_info "Starting Distributed Agent Network agent..."
    log_info "Agent Role: ${AGENT_ROLE:-unknown}"
    log_info "Domain Type: ${DOMAIN_TYPE:-N/A}"

    check_env
    wait_for_redis
    setup_agent_config
    register_agent
    start_heartbeat

    log_info "Agent initialization complete"

    case "$AGENT_ROLE" in
        "main")
            log_info "Starting Main Orchestrator..."
            # Main orchestrator runs Claude Code interactively
            exec claude --dangerously-skip-permissions
            ;;
        "domain")
            log_info "Starting Domain Orchestrator for: ${DOMAIN_TYPE}"
            # Domain orchestrator listens for tasks and processes them
            python3 /lib/domain_runner.py
            ;;
        "worker")
            log_info "Starting Worker agent..."
            # Worker processes a single task (spawned by domain orchestrator)
            if [ ! -z "$TASK_PAYLOAD" ]; then
                echo "$TASK_PAYLOAD" | claude --dangerously-skip-permissions
            else
                log_error "No TASK_PAYLOAD provided for worker"
                exit 1
            fi
            ;;
        *)
            log_error "Unknown AGENT_ROLE: $AGENT_ROLE"
            exit 1
            ;;
    esac
}

main "$@"
