"""
Agent registry for tracking active agents in the network.

Provides registration, discovery, and health monitoring via Redis.
"""

import os
import time
from datetime import datetime, timezone
from typing import Optional

import redis
from pydantic import BaseModel, Field


class AgentInfo(BaseModel):
    """Information about a registered agent."""

    agent_id: str
    role: str  # main, domain, worker
    domain_type: Optional[str] = None
    status: str = "starting"  # starting, active, busy, stopping
    container_id: Optional[str] = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_heartbeat: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "AgentInfo":
        return cls.model_validate(data)


class AgentRegistry:
    """
    Redis-based agent registry for discovery and health monitoring.

    Key structure:
        agents:all                  SET of all agent IDs
        agents:domains              SET of domain orchestrator IDs
        agents:workers              SET of worker IDs
        agents:info:{agent_id}      HASH with agent info
        agents:heartbeat:{agent_id} STRING with TTL for health check
    """

    HEARTBEAT_TTL = 30  # seconds

    def __init__(self, redis_url: Optional[str] = None):
        """
        Initialize registry with Redis connection.

        Args:
            redis_url: Redis connection URL. Defaults to REDIS_URL env var.
        """
        self.redis_url = redis_url or os.environ.get(
            "REDIS_URL", "redis://localhost:6379"
        )
        self._client: Optional[redis.Redis] = None

    @property
    def client(self) -> redis.Redis:
        """Lazy Redis client initialization."""
        if self._client is None:
            self._client = redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5,
            )
        return self._client

    def close(self) -> None:
        """Close Redis connection."""
        if self._client:
            self._client.close()

    # ==================== Registration ====================

    def register(
        self,
        agent_id: str,
        role: str,
        domain_type: Optional[str] = None,
        container_id: Optional[str] = None,
    ) -> AgentInfo:
        """
        Register an agent with the network.

        Args:
            agent_id: Unique agent identifier
            role: Agent role (main, domain, worker)
            domain_type: Domain type for domain orchestrators
            container_id: Docker container ID if applicable

        Returns:
            AgentInfo for the registered agent
        """
        agent = AgentInfo(
            agent_id=agent_id,
            role=role,
            domain_type=domain_type,
            container_id=container_id,
            status="active",
        )

        pipe = self.client.pipeline()

        # Store agent info
        pipe.hset(f"agents:info:{agent_id}", mapping=agent.to_dict())

        # Add to appropriate sets
        pipe.sadd("agents:all", agent_id)

        if role == "main":
            pipe.set("agents:main", agent_id)
        elif role == "domain":
            pipe.sadd("agents:domains", agent_id)
            if domain_type:
                pipe.sadd(f"agents:domains:{domain_type}", agent_id)
        elif role == "worker":
            pipe.sadd("agents:workers", agent_id)

        # Set initial heartbeat
        pipe.setex(
            f"agents:heartbeat:{agent_id}",
            self.HEARTBEAT_TTL,
            datetime.now(timezone.utc).isoformat(),
        )

        pipe.execute()

        return agent

    def deregister(self, agent_id: str) -> bool:
        """
        Remove an agent from the registry.

        Args:
            agent_id: Agent identifier to remove

        Returns:
            True if agent was removed, False if not found
        """
        # Get agent info first
        info = self.get_agent(agent_id)
        if not info:
            return False

        pipe = self.client.pipeline()

        # Remove from sets
        pipe.srem("agents:all", agent_id)

        if info.role == "main":
            pipe.delete("agents:main")
        elif info.role == "domain":
            pipe.srem("agents:domains", agent_id)
            if info.domain_type:
                pipe.srem(f"agents:domains:{info.domain_type}", agent_id)
        elif info.role == "worker":
            pipe.srem("agents:workers", agent_id)

        # Remove info and heartbeat
        pipe.delete(f"agents:info:{agent_id}")
        pipe.delete(f"agents:heartbeat:{agent_id}")

        pipe.execute()

        return True

    # ==================== Discovery ====================

    def get_agent(self, agent_id: str) -> Optional[AgentInfo]:
        """
        Get information about a specific agent.

        Args:
            agent_id: Agent identifier

        Returns:
            AgentInfo or None if not found
        """
        data = self.client.hgetall(f"agents:info:{agent_id}")
        if data:
            return AgentInfo.from_dict(data)
        return None

    def list_agents(self, role: Optional[str] = None) -> list[AgentInfo]:
        """
        List all registered agents.

        Args:
            role: Optional filter by role

        Returns:
            List of AgentInfo objects
        """
        if role == "domain":
            agent_ids = self.client.smembers("agents:domains")
        elif role == "worker":
            agent_ids = self.client.smembers("agents:workers")
        else:
            agent_ids = self.client.smembers("agents:all")

        agents = []
        for agent_id in agent_ids:
            agent = self.get_agent(agent_id)
            if agent:
                agents.append(agent)

        return agents

    def get_main_orchestrator(self) -> Optional[AgentInfo]:
        """Get the main orchestrator agent."""
        agent_id = self.client.get("agents:main")
        if agent_id:
            return self.get_agent(agent_id)
        return None

    def get_domain_orchestrators(
        self, domain_type: Optional[str] = None
    ) -> list[AgentInfo]:
        """
        Get domain orchestrator agents.

        Args:
            domain_type: Optional filter by domain type

        Returns:
            List of domain orchestrator AgentInfo
        """
        if domain_type:
            agent_ids = self.client.smembers(f"agents:domains:{domain_type}")
        else:
            agent_ids = self.client.smembers("agents:domains")

        agents = []
        for agent_id in agent_ids:
            agent = self.get_agent(agent_id)
            if agent:
                agents.append(agent)

        return agents

    def find_available_domain(self, domain_type: str) -> Optional[AgentInfo]:
        """
        Find an available domain orchestrator for a given type.

        Args:
            domain_type: Type of domain needed

        Returns:
            AgentInfo for an available domain or None
        """
        domains = self.get_domain_orchestrators(domain_type)

        # Filter to healthy, active domains
        available = [
            d for d in domains if d.status == "active" and self.is_healthy(d.agent_id)
        ]

        if available:
            # Return the first available (could be enhanced with load balancing)
            return available[0]

        return None

    # ==================== Health Monitoring ====================

    def heartbeat(self, agent_id: str) -> bool:
        """
        Send heartbeat for an agent.

        Args:
            agent_id: Agent identifier

        Returns:
            True if heartbeat was recorded
        """
        now = datetime.now(timezone.utc).isoformat()

        pipe = self.client.pipeline()
        pipe.setex(f"agents:heartbeat:{agent_id}", self.HEARTBEAT_TTL, now)
        pipe.hset(f"agents:info:{agent_id}", "last_heartbeat", now)
        pipe.execute()

        return True

    def is_healthy(self, agent_id: str) -> bool:
        """
        Check if an agent is healthy (has recent heartbeat).

        Args:
            agent_id: Agent identifier

        Returns:
            True if agent is healthy
        """
        return self.client.exists(f"agents:heartbeat:{agent_id}") > 0

    def get_unhealthy_agents(self) -> list[str]:
        """
        Get list of agents that have missed heartbeats.

        Returns:
            List of unhealthy agent IDs
        """
        all_agents = self.client.smembers("agents:all")
        unhealthy = []

        for agent_id in all_agents:
            if not self.is_healthy(agent_id):
                unhealthy.append(agent_id)

        return unhealthy

    def cleanup_dead_agents(self) -> list[str]:
        """
        Remove agents that have been unhealthy.

        Returns:
            List of removed agent IDs
        """
        removed = []

        for agent_id in self.get_unhealthy_agents():
            if self.deregister(agent_id):
                removed.append(agent_id)

        return removed

    # ==================== Status Updates ====================

    def update_status(self, agent_id: str, status: str) -> bool:
        """
        Update an agent's status.

        Args:
            agent_id: Agent identifier
            status: New status (active, busy, stopping)

        Returns:
            True if updated successfully
        """
        return self.client.hset(f"agents:info:{agent_id}", "status", status) >= 0

    def set_busy(self, agent_id: str) -> bool:
        """Mark an agent as busy."""
        return self.update_status(agent_id, "busy")

    def set_active(self, agent_id: str) -> bool:
        """Mark an agent as active/available."""
        return self.update_status(agent_id, "active")


# CLI interface
if __name__ == "__main__":
    import sys

    registry = AgentRegistry()

    if len(sys.argv) < 2:
        print("Usage: python registry.py <command> [args]")
        print("Commands: register, deregister, list, heartbeat, cleanup")
        sys.exit(1)

    command = sys.argv[1]

    if command == "register":
        agent_id = os.environ.get("AGENT_ID", os.environ.get("HOSTNAME", "test-agent"))
        role = os.environ.get("AGENT_ROLE", "worker")
        domain_type = os.environ.get("DOMAIN_TYPE", "")

        agent = registry.register(agent_id, role, domain_type or None)
        print(f"Registered: {agent.agent_id} as {agent.role}")

    elif command == "deregister":
        agent_id = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("AGENT_ID")
        if registry.deregister(agent_id):
            print(f"Deregistered: {agent_id}")
        else:
            print(f"Agent not found: {agent_id}")

    elif command == "list":
        role = sys.argv[2] if len(sys.argv) > 2 else None
        agents = registry.list_agents(role)
        print(f"Registered agents ({len(agents)}):")
        for agent in agents:
            health = "healthy" if registry.is_healthy(agent.agent_id) else "unhealthy"
            print(f"  - {agent.agent_id}: {agent.role} ({agent.status}, {health})")

    elif command == "heartbeat":
        agent_id = os.environ.get("AGENT_ID", os.environ.get("HOSTNAME"))
        print(f"Starting heartbeat for {agent_id}")

        while True:
            registry.heartbeat(agent_id)
            print(f"Heartbeat sent: {datetime.now(timezone.utc).isoformat()}")
            time.sleep(10)

    elif command == "cleanup":
        removed = registry.cleanup_dead_agents()
        print(f"Cleaned up {len(removed)} dead agents: {removed}")

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
