# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Distributed Agent Network is a hierarchical multi-agent system that orchestrates Claude Code CLI instances through Docker containers. A main orchestrator routes tasks to domain orchestrators (backend, frontend, devops), which delegate work to specialized workers using the Task tool.

## Build and Run Commands

```bash
# Development setup
./scripts/setup-dev.sh && source .venv/bin/activate
# Or manually: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt

# Start with domains (production mode)
docker compose build
docker compose --profile backend up -d     # Backend only
docker compose --profile domains up -d     # All domains

# Interactive mode (main orchestrator spawns domains on demand)
docker compose up -d
docker exec -it main-orchestrator claude --dangerously-skip-permissions -p "[request]"

# Submit tasks
./scripts/send-task.sh backend "Create user registration endpoint"
./scripts/send-task.sh frontend "Build login form" --priority high --wait
```

## Testing

```bash
# Requires Redis running
docker run -d --name redis-test -p 6379:6379 redis:7-alpine

pytest tests/ -v                              # All tests
pytest tests/test_messaging.py -v             # Single file
pytest tests/test_messaging.py::test_name -v  # Single test
pytest tests/integration/ -v                  # Integration tests
pytest tests/ --cov=lib --cov-report=term-missing  # With coverage
```

## Architecture

```
Main Orchestrator (routes tasks, spawns domain containers)
        │ Redis pub/sub
        ├── Backend Domain → api-worker, db-worker, auth-worker
        ├── Frontend Domain → react-worker, css-worker, a11y-worker
        └── DevOps Domain → docker-worker, ci-worker, infra-worker
```

**Core libraries** (`lib/`):
- `messaging.py` - Redis pub/sub, task queues (`TaskMessage`, `TaskResult` models)
- `registry.py` - Agent registration, heartbeat, discovery (`AgentInfo` model)
- `spawner.py` - Docker container lifecycle (`DomainConfig`, `DomainInfo` models)
- `domain_runner.py` - Domain orchestrator task queue processor

**Agent instructions** (`agents/`):
- `main-orchestrator/CLAUDE.md` - Task routing, domain spawning
- `domain-templates/{backend,frontend,devops}/CLAUDE.md` - Domain orchestration
- `domain-templates/{domain}/.claude/agents/*.md` - Worker specializations

**Redis key schema**:
- `tasks:pending:{domain}` (LIST) - Task queue
- `tasks:active:{domain}` (SET) - Active tasks
- `results:{task_id}` (HASH) - Status, output, error
- `agents:info:{agent_id}` (HASH) - Agent metadata
- `agents:heartbeat:{agent_id}` (STRING with 30s TTL) - Health

## Development Notes

- All Pydantic models are in `lib/` - use `TaskMessage`, `TaskResult`, `AgentInfo`, `DomainConfig`
- Domain templates in `agents/domain-templates/` are mounted into containers at `/agents`
- Workers are spawned via Claude's Task tool with agent definitions from `.claude/agents/`
- Container resource limits: Main (2GB/1CPU), Domains (1GB/0.5CPU), Redis (256MB LRU)
- Docker socket is mounted in main-orchestrator to enable domain container spawning
