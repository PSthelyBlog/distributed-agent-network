"""
Tests for the registry module.

Run with: pytest tests/test_registry.py -v
Requires: Redis running on localhost:6379
"""

import os
import time

import pytest

# Set test Redis URL before imports
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

from lib.registry import AgentInfo, AgentRegistry


class TestAgentInfo:
    """Tests for AgentInfo model."""

    def test_create_with_defaults(self):
        info = AgentInfo(agent_id="test-agent", role="worker")
        assert info.agent_id == "test-agent"
        assert info.role == "worker"
        assert info.status == "starting"
        assert info.created_at is not None

    def test_create_domain_agent(self):
        info = AgentInfo(
            agent_id="backend-001",
            role="domain",
            domain_type="backend",
            container_id="abc123",
        )
        assert info.domain_type == "backend"
        assert info.container_id == "abc123"

    def test_dict_serialization(self):
        info = AgentInfo(
            agent_id="test",
            role="worker",
            status="active",
        )
        data = info.to_dict()
        parsed = AgentInfo.from_dict(data)

        assert parsed.agent_id == info.agent_id
        assert parsed.role == info.role
        assert parsed.status == info.status


@pytest.fixture
def registry():
    """Create registry instance for tests."""
    r = AgentRegistry()
    yield r
    r.close()


@pytest.fixture
def clean_registry(registry):
    """Clean up test agents before and after tests."""
    test_patterns = [
        "agents:*test*",
        "agents:info:test-*",
        "agents:heartbeat:test-*",
    ]

    def cleanup():
        for pattern in test_patterns:
            for key in registry.client.keys(pattern):
                registry.client.delete(key)
        # Also clean specific test sets
        registry.client.delete("agents:domains:test-domain")

    cleanup()
    yield
    cleanup()


class TestAgentRegistry:
    """Tests for AgentRegistry class."""

    @pytest.mark.usefixtures("clean_registry")
    def test_register_worker(self, registry):
        """Test registering a worker agent."""
        agent = registry.register(
            agent_id="test-worker-001",
            role="worker",
        )

        assert agent.agent_id == "test-worker-001"
        assert agent.role == "worker"
        assert agent.status == "active"

        # Verify in registry
        retrieved = registry.get_agent("test-worker-001")
        assert retrieved is not None
        assert retrieved.agent_id == "test-worker-001"

    @pytest.mark.usefixtures("clean_registry")
    def test_register_domain(self, registry):
        """Test registering a domain orchestrator."""
        agent = registry.register(
            agent_id="test-backend-001",
            role="domain",
            domain_type="test-domain",
            container_id="container-123",
        )

        assert agent.role == "domain"
        assert agent.domain_type == "test-domain"

        # Verify in domain set
        domains = registry.get_domain_orchestrators("test-domain")
        assert len(domains) == 1
        assert domains[0].agent_id == "test-backend-001"

    @pytest.mark.usefixtures("clean_registry")
    def test_deregister(self, registry):
        """Test deregistering an agent."""
        registry.register(
            agent_id="test-agent-to-remove",
            role="worker",
        )

        # Verify registered
        assert registry.get_agent("test-agent-to-remove") is not None

        # Deregister
        result = registry.deregister("test-agent-to-remove")
        assert result is True

        # Verify removed
        assert registry.get_agent("test-agent-to-remove") is None

    @pytest.mark.usefixtures("clean_registry")
    def test_deregister_nonexistent(self, registry):
        """Test deregistering agent that doesn't exist."""
        result = registry.deregister("nonexistent-agent")
        assert result is False

    @pytest.mark.usefixtures("clean_registry")
    def test_list_agents(self, registry):
        """Test listing all agents."""
        # Register multiple agents
        registry.register("test-worker-1", "worker")
        registry.register("test-worker-2", "worker")
        registry.register("test-domain-1", "domain", "test-domain")

        # List all
        all_agents = registry.list_agents()
        test_agents = [a for a in all_agents if a.agent_id.startswith("test-")]
        assert len(test_agents) == 3

        # List by role
        workers = registry.list_agents(role="worker")
        test_workers = [a for a in workers if a.agent_id.startswith("test-")]
        assert len(test_workers) == 2

        domains = registry.list_agents(role="domain")
        test_domains = [a for a in domains if a.agent_id.startswith("test-")]
        assert len(test_domains) == 1

    @pytest.mark.usefixtures("clean_registry")
    def test_heartbeat(self, registry):
        """Test heartbeat functionality."""
        registry.register("test-heartbeat-agent", "worker")

        # Send heartbeat
        result = registry.heartbeat("test-heartbeat-agent")
        assert result is True

        # Verify healthy
        assert registry.is_healthy("test-heartbeat-agent") is True

    @pytest.mark.usefixtures("clean_registry")
    def test_is_healthy_expired(self, registry):
        """Test health check with expired heartbeat."""
        registry.register("test-expired-agent", "worker")

        # Manually delete heartbeat to simulate expiry
        registry.client.delete("agents:heartbeat:test-expired-agent")

        # Should be unhealthy
        assert registry.is_healthy("test-expired-agent") is False

    @pytest.mark.usefixtures("clean_registry")
    def test_find_available_domain(self, registry):
        """Test finding available domain orchestrator."""
        # Register a domain
        registry.register(
            agent_id="test-backend-available",
            role="domain",
            domain_type="test-domain",
        )

        # Find available
        domain = registry.find_available_domain("test-domain")
        assert domain is not None
        assert domain.agent_id == "test-backend-available"

    @pytest.mark.usefixtures("clean_registry")
    def test_find_available_domain_none(self, registry):
        """Test finding domain when none available."""
        domain = registry.find_available_domain("nonexistent-domain")
        assert domain is None

    @pytest.mark.usefixtures("clean_registry")
    def test_update_status(self, registry):
        """Test updating agent status."""
        registry.register("test-status-agent", "worker")

        # Update to busy
        registry.set_busy("test-status-agent")
        agent = registry.get_agent("test-status-agent")
        assert agent.status == "busy"

        # Update to active
        registry.set_active("test-status-agent")
        agent = registry.get_agent("test-status-agent")
        assert agent.status == "active"

    @pytest.mark.usefixtures("clean_registry")
    def test_cleanup_dead_agents(self, registry):
        """Test cleaning up dead agents."""
        # Register agents
        registry.register("test-alive-agent", "worker")
        registry.register("test-dead-agent", "worker")

        # Kill heartbeat for dead agent
        registry.client.delete("agents:heartbeat:test-dead-agent")

        # Cleanup
        removed = registry.cleanup_dead_agents()
        assert "test-dead-agent" in removed

        # Verify dead agent removed
        assert registry.get_agent("test-dead-agent") is None

        # Verify alive agent still exists
        assert registry.get_agent("test-alive-agent") is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
