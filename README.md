# Distributed Agent Network

A distributed network of AI agents organized by domain and specialization, using Claude Code CLI instances orchestrated through Docker containers.

## Overview

This system implements a hierarchical agent architecture where a main orchestrator routes tasks to specialized domain orchestrators, which in turn delegate work to focused worker agents.

```
┌─────────────────────────────────────────────────────────────┐
│                     MAIN ORCHESTRATOR                       │
│         • Routes tasks to domains                           │
│         • Spawns domain containers via Docker API           │
└─────────────────────────┬───────────────────────────────────┘
                          │ Redis pub/sub
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│   BACKEND     │ │   FRONTEND    │ │    DEVOPS     │
│   DOMAIN      │ │   DOMAIN      │ │    DOMAIN     │
│  (container)  │ │  (container)  │ │  (container)  │
└───────┬───────┘ └───────┬───────┘ └───────┬───────┘
        │                 │                 │
        ▼                 ▼                 ▼
   Task tool          Task tool         Task tool
   subagents          subagents         subagents
```

## Features

- **Domain-based routing**: Tasks automatically route to backend, frontend, or devops domains
- **Dynamic scaling**: Domain containers spawn on demand
- **Worker specialization**: Each domain has focused workers (API, database, React, CI/CD, etc.)
- **Redis messaging**: Pub/sub communication with task queues and result tracking
- **Health monitoring**: Heartbeat-based agent health with automatic cleanup
- **CLI tools**: Easy task submission and log aggregation

## Prerequisites

- Docker and Docker Compose
- Python 3.10+
- Redis (included in docker-compose)
- Anthropic API key

## Quick Start

### 1. Clone and Setup

```bash
git clone <repository-url>
cd distributed-agent-network

# Setup development environment
./scripts/setup-dev.sh
source .venv/bin/activate

# Check prerequisites
./scripts/check-prerequisites.sh
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
```

### 3. Start the Network

```bash
# Build and start containers
docker compose build
docker compose up -d

# Verify services are running
docker compose ps
```

### 4. Submit a Task

```bash
# Simple task
./scripts/send-task.sh backend "Create a REST API endpoint for user registration"

# With options
./scripts/send-task.sh frontend "Build login form" --priority high --wait

# Multi-domain task (routed by main orchestrator)
./scripts/send-task.sh main "Create user registration with API endpoint and form component"
```

### 5. Monitor Progress

```bash
# View all agent logs
./scripts/tail-logs.sh

# View specific container logs
./scripts/tail-logs.sh --main
./scripts/tail-logs.sh --domains

# View task-specific logs
./scripts/tail-logs.sh --task <task-id>

# Redis Commander UI (optional)
docker compose --profile debug up -d redis-ui
# Open http://localhost:8081
```

## Project Structure

```
distributed-agent-network/
├── lib/
│   ├── messaging.py      # Redis pub/sub, task queues
│   ├── registry.py       # Agent registration, health monitoring
│   ├── health_check.py   # Container health checks
│   └── spawner.py        # Docker container management
├── agents/
│   ├── main-orchestrator/
│   │   ├── CLAUDE.md     # Main orchestrator instructions
│   │   └── .claude/settings.json
│   └── domain-templates/
│       ├── backend/      # API, database, auth workers
│       ├── frontend/     # React, CSS, accessibility workers
│       └── devops/       # Docker, CI/CD, infrastructure workers
├── scripts/
│   ├── entrypoint.sh     # Container bootstrap
│   ├── check-prerequisites.sh
│   ├── setup-dev.sh      # Dev environment setup
│   ├── send-task.sh      # CLI for task submission
│   └── tail-logs.sh      # Aggregated log viewing
├── tests/
│   ├── test_messaging.py # 18 tests
│   ├── test_registry.py  # 14 tests
│   ├── test_spawner.py   # 30 tests
│   └── integration/
│       └── test_full_flow.py # 15 tests
├── Dockerfile.agent      # Base agent image
├── docker-compose.yml    # Service orchestration
└── workspace/            # Shared project workspace
```

## Domains and Workers

### Backend Domain
| Worker | Specialization |
|--------|----------------|
| api-worker | REST/GraphQL endpoint implementation |
| db-worker | Database schema, migrations, queries |
| auth-worker | Authentication and authorization |

### Frontend Domain
| Worker | Specialization |
|--------|----------------|
| react-worker | React component development |
| css-worker | Styling and responsive design |
| a11y-worker | Accessibility compliance |

### DevOps Domain
| Worker | Specialization |
|--------|----------------|
| docker-worker | Containerization, Dockerfile configs |
| ci-worker | CI/CD pipeline configuration |
| infra-worker | Infrastructure as code |

## CLI Reference

### send-task.sh

```bash
./scripts/send-task.sh <domain> <description> [options]

Arguments:
    domain          Target domain (backend, frontend, devops)
    description     Task description

Options:
    -p, --priority  Task priority: low, normal, high (default: normal)
    -t, --timeout   Task timeout in seconds (default: 300)
    -w, --wait      Wait for task completion and show result
    -c, --context   JSON context string
    -r, --redis     Redis URL (default: redis://localhost:6379)
```

### tail-logs.sh

```bash
./scripts/tail-logs.sh [options]

Options:
    -m, --main          Show main orchestrator logs only
    -d, --domains       Show domain orchestrator logs only
    -t, --task ID       Show logs for specific task ID
    -g, --grep PATTERN  Filter logs by pattern
    -n, --lines N       Number of lines to show (default: 100)
    --no-follow         Don't follow, just show recent logs
```

## Redis Data Schema

```
# Task Queues
tasks:pending:{domain}     LIST    Pending tasks (FIFO)
tasks:active:{domain}      SET     Currently executing tasks

# Results
results:{task_id}          HASH    {status, output, error, timestamps}
results:{task_id}:logs     LIST    Execution logs

# Agent Registry
agents:all                 SET     All agent IDs
agents:domains             SET     Domain orchestrator IDs
agents:info:{agent_id}     HASH    Agent metadata
agents:heartbeat:{id}      STRING  TTL-based health (30s)
```

## Development

### Running Tests

```bash
# Activate virtual environment
source .venv/bin/activate

# Start Redis (required for tests)
docker run -d --name redis-test -p 6379:6379 redis:7-alpine

# Run all tests
pytest tests/ -v

# Run integration tests only
pytest tests/integration/ -v

# Run with coverage
pytest tests/ --cov=lib --cov-report=term-missing
```

### Test Results

```
74 passed, 3 skipped (Docker tests when daemon unavailable)
```

### Adding a New Domain

1. Create domain template directory:
   ```bash
   mkdir -p agents/domain-templates/newdomain/.claude/agents
   ```

2. Create `CLAUDE.md` with domain orchestrator instructions

3. Create worker definitions in `.claude/agents/`:
   - `worker1.md`
   - `worker2.md`

4. Update routing logic in main orchestrator's `CLAUDE.md`

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Anthropic API key | (required) |
| `REDIS_URL` | Redis connection URL | `redis://localhost:6379` |
| `AGENT_ROLE` | Agent role (main, domain, worker) | - |
| `AGENT_ID` | Unique agent identifier | - |
| `DOMAIN_TYPE` | Domain type for domain orchestrators | - |

### Resource Limits

Default limits in docker-compose.yml:
- Main orchestrator: 2GB RAM, 1 CPU
- Domain containers: 1GB RAM, 0.5 CPU
- Redis: 256MB (with LRU eviction)

## Troubleshooting

### Common Issues

**Redis connection failed**
```bash
# Ensure Redis is running
docker compose up -d message-broker
# Or for development
docker run -d --name redis-test -p 6379:6379 redis:7-alpine
```

**Docker socket permission denied**
```bash
# Add user to docker group
sudo usermod -aG docker $USER
# Log out and back in
```

**Task stuck in pending**
```bash
# Check if domain orchestrator is running
docker compose ps
# Check domain logs
./scripts/tail-logs.sh --domains
```

### Debugging

```bash
# Inspect Redis queues
redis-cli LRANGE tasks:pending:backend 0 -1
redis-cli HGETALL results:<task-id>

# View container logs
docker compose logs main-orchestrator
docker compose logs -f  # Follow all logs

# Redis Commander UI
docker compose --profile debug up -d redis-ui
# Open http://localhost:8081
```

## License

[Add license information]

## Contributing

[Add contribution guidelines]
