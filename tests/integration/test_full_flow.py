"""
Integration tests for the full distributed agent network flow.

Tests the complete task lifecycle:
1. Agent registration with Redis
2. Task submission and queue routing
3. Domain orchestrator task pickup
4. Result publication and retrieval

Requirements:
- Redis running (docker-compose up message-broker)
- Docker daemon available for spawner tests
"""

import os
import sys
import time
import pytest
import uuid

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from lib.messaging import AgentMessaging, TaskMessage, TaskResult
from lib.registry import AgentRegistry, AgentInfo
from lib.spawner import DomainSpawner, DomainConfig


# ==================== Fixtures ====================

@pytest.fixture
def redis_url():
    """Redis URL for tests."""
    return os.environ.get("REDIS_URL", "redis://localhost:6379")


@pytest.fixture
def messaging(redis_url):
    """AgentMessaging instance with cleanup."""
    msg = AgentMessaging(redis_url)
    yield msg
    msg.close()


@pytest.fixture
def registry(redis_url):
    """AgentRegistry instance with cleanup."""
    reg = AgentRegistry(redis_url)
    yield reg
    reg.close()


@pytest.fixture
def spawner():
    """DomainSpawner instance with cleanup."""
    sp = DomainSpawner()
    yield sp
    sp.close()


@pytest.fixture
def unique_id():
    """Generate unique ID for test isolation."""
    return f"test-{uuid.uuid4().hex[:8]}"


# ==================== Connection Tests ====================

def docker_available():
    """Check if Docker daemon is accessible."""
    try:
        import docker
        client = docker.from_env()
        client.ping()
        client.close()
        return True
    except Exception:
        return False


class TestConnections:
    """Test basic connectivity to required services."""

    def test_redis_ping(self, messaging):
        """Verify Redis is reachable."""
        assert messaging.ping(), "Redis connection failed"

    @pytest.mark.skipif(not docker_available(), reason="Docker not available")
    def test_docker_ping(self, spawner):
        """Verify Docker daemon is reachable."""
        assert spawner.ping(), "Docker connection failed"


# ==================== Registration Flow Tests ====================

class TestRegistrationFlow:
    """Test agent registration and discovery."""

    def test_main_orchestrator_registration(self, registry, unique_id):
        """Main orchestrator can register and be discovered."""
        agent_id = f"main-{unique_id}"

        # Register
        agent = registry.register(agent_id, role="main")
        assert agent.agent_id == agent_id
        assert agent.role == "main"
        assert agent.status == "active"

        # Discover
        main = registry.get_main_orchestrator()
        assert main is not None
        assert main.agent_id == agent_id

        # Cleanup
        registry.deregister(agent_id)
        assert registry.get_main_orchestrator() is None

    def test_domain_orchestrator_registration(self, registry, unique_id):
        """Domain orchestrators register with domain type."""
        domain_id = f"backend-{unique_id}"

        # Register
        agent = registry.register(
            domain_id,
            role="domain",
            domain_type="backend"
        )
        assert agent.role == "domain"
        assert agent.domain_type == "backend"

        # Discover by type
        domains = registry.get_domain_orchestrators("backend")
        assert any(d.agent_id == domain_id for d in domains)

        # Cleanup
        registry.deregister(domain_id)

    def test_health_monitoring(self, registry, unique_id):
        """Heartbeat keeps agent healthy, missing heartbeat marks unhealthy."""
        agent_id = f"health-{unique_id}"

        # Register (sets initial heartbeat)
        registry.register(agent_id, role="worker")
        assert registry.is_healthy(agent_id)

        # Send heartbeat
        registry.heartbeat(agent_id)
        assert registry.is_healthy(agent_id)

        # Cleanup
        registry.deregister(agent_id)


# ==================== Task Queue Flow Tests ====================

class TestTaskQueueFlow:
    """Test task submission, routing, and completion."""

    def test_task_publish_and_receive(self, messaging, unique_id):
        """Tasks can be published and received by domain."""
        domain = f"backend-{unique_id}"

        # Publish task
        task_id = messaging.publish_task(
            domain=domain,
            description="Test task",
            requirements=["req1", "req2"],
            context={"key": "value"},
            source="test-orchestrator"
        )
        assert task_id is not None

        # Check queue
        assert messaging.get_queue_length(domain) == 1

        # Receive task
        task = messaging.get_next_task(domain)
        assert task is not None
        assert task.task_id == task_id
        assert task.payload["description"] == "Test task"
        assert task.payload["requirements"] == ["req1", "req2"]
        assert task.source == "test-orchestrator"

        # Queue should be empty now (task moved to active)
        assert messaging.get_queue_length(domain) == 0

    def test_task_result_publication(self, messaging, unique_id):
        """Results can be published and retrieved."""
        domain = f"frontend-{unique_id}"

        # Publish and receive task
        task_id = messaging.publish_task(domain, "Build component")
        task = messaging.get_next_task(domain)

        # Check initial result status
        result = messaging.get_result(task_id)
        assert result.status == "in_progress"

        # Publish result
        messaging.publish_result(
            task_id,
            output={"component": "Button", "status": "built"},
            status="completed"
        )

        # Retrieve result
        result = messaging.get_result(task_id)
        assert result.status == "completed"
        assert result.output["component"] == "Button"
        assert result.completed_at is not None

    def test_task_failure_handling(self, messaging, unique_id):
        """Failed tasks are tracked correctly."""
        domain = f"devops-{unique_id}"

        # Submit and fail task
        task_id = messaging.publish_task(domain, "Deploy service")
        messaging.get_next_task(domain)

        messaging.publish_result(
            task_id,
            output={},
            status="failed",
            error="Connection refused"
        )

        result = messaging.get_result(task_id)
        assert result.status == "failed"
        assert result.error == "Connection refused"

    def test_task_logging(self, messaging, unique_id):
        """Task logs are recorded and retrievable."""
        domain = f"backend-{unique_id}"

        task_id = messaging.publish_task(domain, "Process data")

        # Add logs
        messaging.add_log(task_id, "Starting processing")
        messaging.add_log(task_id, "Processed 100 records")
        messaging.add_log(task_id, "Completed successfully")

        # Retrieve logs
        logs = messaging.get_logs(task_id)
        assert len(logs) == 3
        assert "Starting processing" in logs[0]
        assert "100 records" in logs[1]

    def test_multi_domain_task_routing(self, messaging, unique_id):
        """Tasks route to correct domain queues."""
        backend_domain = f"backend-{unique_id}"
        frontend_domain = f"frontend-{unique_id}"

        # Publish to different domains
        backend_task = messaging.publish_task(backend_domain, "API endpoint")
        frontend_task = messaging.publish_task(frontend_domain, "UI component")

        # Each domain should have its task
        assert messaging.get_queue_length(backend_domain) == 1
        assert messaging.get_queue_length(frontend_domain) == 1

        # Receive from correct queues
        be_task = messaging.get_next_task(backend_domain)
        fe_task = messaging.get_next_task(frontend_domain)

        assert be_task.task_id == backend_task
        assert fe_task.task_id == frontend_task


# ==================== End-to-End Flow Tests ====================

class TestEndToEndFlow:
    """Test complete task lifecycle simulating full network."""

    def test_orchestrator_to_domain_flow(self, registry, messaging, unique_id):
        """
        Simulate full flow:
        1. Main orchestrator registers
        2. Domain orchestrator registers
        3. Task submitted by main
        4. Task received by domain
        5. Result published by domain
        6. Result received by main
        """
        main_id = f"main-{unique_id}"
        domain_id = f"backend-{unique_id}"
        domain_type = "backend"

        # 1. Main orchestrator registers
        registry.register(main_id, role="main")
        main = registry.get_main_orchestrator()
        assert main.agent_id == main_id

        # 2. Domain orchestrator registers
        registry.register(domain_id, role="domain", domain_type=domain_type)
        domain = registry.find_available_domain(domain_type)
        assert domain is not None
        assert domain.agent_id == domain_id

        # 3. Main submits task
        task_id = messaging.publish_task(
            domain=domain_type,
            description="Create REST endpoint for /users",
            requirements=["GET /users", "POST /users", "validation"],
            context={"framework": "fastapi"},
            source=main_id
        )

        # 4. Domain receives task
        task = messaging.get_next_task(domain_type)
        assert task.task_id == task_id
        assert task.source == main_id

        # Simulate domain processing
        messaging.add_log(task_id, f"Domain {domain_id} processing task")
        registry.set_busy(domain_id)

        # 5. Domain publishes result
        messaging.publish_result(
            task_id,
            output={
                "files_created": ["api/users.py", "models/user.py"],
                "endpoints": ["/users", "/users/{id}"],
                "tests_passed": True
            },
            status="completed"
        )
        registry.set_active(domain_id)

        # 6. Main retrieves result
        result = messaging.get_result(task_id)
        assert result.status == "completed"
        assert "api/users.py" in result.output["files_created"]

        # Cleanup
        registry.deregister(main_id)
        registry.deregister(domain_id)

    def test_multi_domain_coordination(self, registry, messaging, unique_id):
        """
        Test task coordination across multiple domains:
        1. Backend creates API
        2. Frontend consumes API
        """
        main_id = f"main-{unique_id}"
        backend_id = f"backend-{unique_id}"
        frontend_id = f"frontend-{unique_id}"

        # Register all agents
        registry.register(main_id, role="main")
        registry.register(backend_id, role="domain", domain_type="backend")
        registry.register(frontend_id, role="domain", domain_type="frontend")

        # Phase 1: Backend task
        backend_task_id = messaging.publish_task(
            domain="backend",
            description="Create user API",
            source=main_id
        )

        backend_task = messaging.get_next_task("backend")
        messaging.publish_result(
            backend_task_id,
            output={"api_url": "/api/users", "schema": {"id": "int", "name": "str"}},
            status="completed"
        )

        backend_result = messaging.get_result(backend_task_id)

        # Phase 2: Frontend task (uses backend result)
        frontend_task_id = messaging.publish_task(
            domain="frontend",
            description="Create user list component",
            context={
                "api_url": backend_result.output["api_url"],
                "schema": backend_result.output["schema"]
            },
            source=main_id
        )

        frontend_task = messaging.get_next_task("frontend")
        assert frontend_task.payload["context"]["api_url"] == "/api/users"

        messaging.publish_result(
            frontend_task_id,
            output={"component": "UserList.tsx", "uses_api": "/api/users"},
            status="completed"
        )

        frontend_result = messaging.get_result(frontend_task_id)
        assert frontend_result.status == "completed"

        # Cleanup
        registry.deregister(main_id)
        registry.deregister(backend_id)
        registry.deregister(frontend_id)


# ==================== Spawner Integration Tests ====================

@pytest.mark.skipif(
    not docker_available() or os.environ.get("SKIP_DOCKER_TESTS", "").lower() in ("1", "true", "yes"),
    reason="Docker not available or tests skipped via SKIP_DOCKER_TESTS"
)
class TestSpawnerIntegration:
    """Test Docker container spawning (requires Docker daemon)."""

    def test_list_domains_empty(self, spawner):
        """List domains when none are running."""
        # This just verifies the API works
        domains = spawner.list_domains()
        # domains may or may not be empty depending on other tests
        assert isinstance(domains, list)

    def test_cleanup_stopped_containers(self, spawner):
        """Cleanup operation runs without error."""
        removed = spawner.cleanup_stopped()
        assert isinstance(removed, list)


# ==================== Performance Tests ====================

class TestPerformance:
    """Basic performance characteristics."""

    def test_task_throughput(self, messaging, unique_id):
        """Measure task publish/receive throughput."""
        domain = f"perf-{unique_id}"
        num_tasks = 100

        # Publish tasks
        start = time.time()
        task_ids = []
        for i in range(num_tasks):
            task_id = messaging.publish_task(domain, f"Task {i}")
            task_ids.append(task_id)
        publish_time = time.time() - start

        # Receive tasks
        start = time.time()
        for _ in range(num_tasks):
            task = messaging.get_next_task(domain)
            assert task is not None
        receive_time = time.time() - start

        # Performance assertions (adjust thresholds as needed)
        publish_rate = num_tasks / publish_time
        receive_rate = num_tasks / receive_time

        print(f"\nPublish rate: {publish_rate:.1f} tasks/sec")
        print(f"Receive rate: {receive_rate:.1f} tasks/sec")

        # Should handle at least 50 tasks/sec
        assert publish_rate > 50, f"Publish too slow: {publish_rate:.1f}/sec"
        assert receive_rate > 50, f"Receive too slow: {receive_rate:.1f}/sec"


# ==================== CLI Entry Point ====================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
