#!/bin/bash
#
# Prerequisites Check Script for Distributed Agent Network
#
# This script checks if all required dependencies are installed
# and properly configured before running the agent network.
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Counters
PASSED=0
FAILED=0
WARNINGS=0

# Print functions
print_header() {
    echo ""
    echo -e "${BLUE}══════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  Distributed Agent Network - Prerequisites Check${NC}"
    echo -e "${BLUE}══════════════════════════════════════════════════════════════${NC}"
    echo ""
}

print_section() {
    echo ""
    echo -e "${BLUE}── $1 ──${NC}"
}

print_check() {
    printf "  %-40s" "$1"
}

print_pass() {
    echo -e "${GREEN}[PASS]${NC} $1"
    PASSED=$((PASSED + 1))
}

print_fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    FAILED=$((FAILED + 1))
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
    WARNINGS=$((WARNINGS + 1))
}

print_info() {
    echo -e "        ${BLUE}->${NC} $1"
}

# Check if command exists
check_command() {
    local cmd=$1
    local name=${2:-$1}
    local required=${3:-true}

    print_check "Checking $name..."

    if command -v "$cmd" &> /dev/null; then
        local version=$($cmd --version 2>&1 | head -1)
        print_pass ""
        print_info "$version"
        return 0
    else
        if [ "$required" = true ]; then
            print_fail "Not installed"
            return 1
        else
            print_warn "Not installed (optional)"
            return 0
        fi
    fi
}

# Check Docker
check_docker() {
    print_section "Docker"

    print_check "Checking Docker CLI..."
    if command -v docker &> /dev/null; then
        local version=$(docker --version 2>&1)
        print_pass ""
        print_info "$version"
    else
        print_fail "Docker CLI not installed"
        print_info "Install: https://docs.docker.com/get-docker/"
        return
    fi

    print_check "Checking Docker daemon..."
    if timeout 5 docker info &> /dev/null; then
        print_pass "Running"
    else
        print_fail "Not running or not accessible"
        print_info "Start Docker daemon or check permissions"
        print_info "Run: sudo systemctl start docker"
        print_info "Or add user to docker group: sudo usermod -aG docker \$USER"
    fi

    print_check "Checking Docker Compose..."
    if timeout 3 docker compose version &> /dev/null; then
        local version=$(docker compose version 2>&1)
        print_pass ""
        print_info "$version"
    elif command -v docker-compose &> /dev/null; then
        local version=$(docker-compose --version 2>&1)
        print_pass ""
        print_info "$version (legacy)"
    else
        print_fail "Docker Compose not installed"
        print_info "Install: https://docs.docker.com/compose/install/"
    fi
}

# Check Python
check_python() {
    print_section "Python"

    print_check "Checking Python 3..."
    if command -v python3 &> /dev/null; then
        local version=$(python3 --version 2>&1)
        print_pass ""
        print_info "$version"

        # Check minimum version (3.10+)
        local py_version=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        local py_major=$(echo $py_version | cut -d. -f1)
        local py_minor=$(echo $py_version | cut -d. -f2)

        if [ "$py_major" -ge 3 ] && [ "$py_minor" -ge 10 ]; then
            print_info "Version OK (>= 3.10 required)"
        else
            print_warn "Python 3.10+ recommended, found $py_version"
        fi
    else
        print_fail "Python 3 not installed"
        print_info "Install: sudo apt install python3 python3-pip python3-venv"
    fi

    print_check "Checking pip..."
    if command -v pip3 &> /dev/null; then
        local version=$(pip3 --version 2>&1)
        print_pass ""
        print_info "$version"
    else
        print_fail "pip3 not installed"
        print_info "Install: sudo apt install python3-pip"
    fi

    print_check "Checking venv module..."
    if python3 -m venv --help &> /dev/null; then
        print_pass "Available"
    else
        print_fail "venv not available (ensurepip missing)"
        local py_version=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        print_info "Install: sudo apt install python${py_version}-venv"
    fi
}

# Check Node.js
check_nodejs() {
    print_section "Node.js"

    print_check "Checking Node.js..."
    if command -v node &> /dev/null; then
        local version=$(node --version 2>&1)
        print_pass ""
        print_info "Node.js $version"

        # Check minimum version (18+)
        local node_major=$(echo $version | sed 's/v//' | cut -d. -f1)
        if [ "$node_major" -ge 18 ]; then
            print_info "Version OK (>= 18 required)"
        else
            print_warn "Node.js 18+ recommended, found $version"
        fi
    else
        print_fail "Node.js not installed"
        print_info "Install: https://nodejs.org/ or use nvm"
    fi

    print_check "Checking npm..."
    if command -v npm &> /dev/null; then
        local version=$(npm --version 2>&1)
        print_pass ""
        print_info "npm $version"
    else
        print_fail "npm not installed"
    fi
}

# Check Claude Code CLI
check_claude_code() {
    print_section "Claude Code CLI"

    print_check "Checking Claude Code CLI..."
    if command -v claude &> /dev/null; then
        local version=$(claude --version 2>&1 || echo "installed")
        print_pass ""
        print_info "$version"
    else
        print_fail "Claude Code CLI not installed"
        print_info "Install: npm install -g @anthropic-ai/claude-code"
    fi
}

# Check Redis (optional for local testing)
check_redis() {
    print_section "Redis (for local testing without Docker)"

    print_check "Checking Redis server..."
    if command -v redis-server &> /dev/null; then
        local version=$(redis-server --version 2>&1)
        print_pass ""
        print_info "$version"
    else
        print_warn "Not installed locally"
        print_info "Redis will run in Docker container"
    fi

    print_check "Checking Redis CLI..."
    if command -v redis-cli &> /dev/null; then
        local version=$(redis-cli --version 2>&1)
        print_pass ""
        print_info "$version"
    else
        print_warn "Not installed locally"
        print_info "Install for debugging: sudo apt install redis-tools"
    fi
}

# Check environment variables
check_environment() {
    print_section "Environment Variables"

    print_check "Checking ANTHROPIC_API_KEY..."
    if [ -n "$ANTHROPIC_API_KEY" ]; then
        local masked="${ANTHROPIC_API_KEY:0:8}...${ANTHROPIC_API_KEY: -4}"
        print_pass "Set"
        print_info "Key: $masked"
    else
        print_fail "Not set"
        print_info "Set: export ANTHROPIC_API_KEY=your-api-key"
        print_info "Or create .env file from .env.example"
    fi
}

# Check project files
check_project_files() {
    print_section "Project Files"

    local project_dir=$(dirname $(dirname $(realpath $0)))

    local required_files=(
        "docker-compose.yml"
        "Dockerfile.agent"
        "requirements.txt"
        "lib/messaging.py"
        "lib/registry.py"
        "scripts/entrypoint.sh"
    )

    for file in "${required_files[@]}"; do
        print_check "Checking $file..."
        if [ -f "$project_dir/$file" ]; then
            print_pass "Exists"
        else
            print_fail "Missing"
        fi
    done
}

# Check network ports
check_ports() {
    print_section "Network Ports"

    local ports=("6379:Redis" "8081:Redis UI")

    for port_info in "${ports[@]}"; do
        local port=$(echo $port_info | cut -d: -f1)
        local name=$(echo $port_info | cut -d: -f2)

        print_check "Checking port $port ($name)..."
        if command -v ss &> /dev/null; then
            if ss -tuln | grep -q ":$port "; then
                print_warn "In use"
                print_info "Another service is using port $port"
            else
                print_pass "Available"
            fi
        elif command -v netstat &> /dev/null; then
            if netstat -tuln | grep -q ":$port "; then
                print_warn "In use"
            else
                print_pass "Available"
            fi
        else
            print_warn "Cannot check (ss/netstat not available)"
        fi
    done
}

# Print summary
print_summary() {
    echo ""
    echo -e "${BLUE}══════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  Summary${NC}"
    echo -e "${BLUE}══════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "  ${GREEN}Passed:${NC}   $PASSED"
    echo -e "  ${YELLOW}Warnings:${NC} $WARNINGS"
    echo -e "  ${RED}Failed:${NC}   $FAILED"
    echo ""

    if [ $FAILED -eq 0 ]; then
        echo -e "${GREEN}✓ All required prerequisites are met!${NC}"
        echo ""
        echo "  Next steps:"
        echo "    1. Copy .env.example to .env and set ANTHROPIC_API_KEY"
        echo "    2. Run: docker compose up -d"
        echo "    3. Run tests: pip install -r requirements.txt && pytest tests/ -v"
        echo ""
        return 0
    else
        echo -e "${RED}✗ Some prerequisites are missing. Please install them before proceeding.${NC}"
        echo ""
        return 1
    fi
}

# Main
main() {
    print_header
    check_docker
    check_python
    check_nodejs
    check_claude_code
    check_redis
    check_environment
    check_project_files
    check_ports
    print_summary
}

main "$@"
