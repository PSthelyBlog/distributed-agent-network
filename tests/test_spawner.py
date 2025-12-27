"""
Tests for the spawner module.

Run with: pytest tests/test_spawner.py -v
Uses mocking to avoid requiring Docker daemon during tests.
"""

import os
from unittest.mock import MagicMock, Mock, patch, PropertyMock

import pytest
from docker.errors import DockerException, NotFound, APIError

# Set test environment before imports
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

from lib.spawner import DomainSpawner, DomainConfig, DomainInfo


class TestDomainConfig:
    """Tests for DomainConfig model."""

    def test_create_with_defaults(self):
        config = DomainConfig(domain_type="backend")
        assert config.domain_type == "backend"
        assert config.memory_limit == "1g"
        assert config.cpu_limit == 0.5
        assert "redis://message-broker:6379" in config.redis_url

    def test_create_with_custom_values(self):
        config = DomainConfig(
            domain_type="frontend",
            memory_limit="2g",
            cpu_limit=1.0,
            image="custom-image:latest",
        )
        assert config.domain_type == "frontend"
        assert config.memory_limit == "2g"
        assert config.cpu_limit == 1.0
        assert config.image == "custom-image:latest"


class TestDomainInfo:
    """Tests for DomainInfo model."""

    def test_create_domain_info(self):
        info = DomainInfo(
            domain_id="backend-abc123",
            domain_type="backend",
            container_id="abc123def456",
            container_name="domain-backend-abc123",
            status="running",
        )
        assert info.domain_id == "backend-abc123"
        assert info.domain_type == "backend"
        assert info.status == "running"
        assert info.created_at is not None

    def test_to_dict_excludes_none(self):
        info = DomainInfo(
            domain_id="test-123",
            domain_type="backend",
            container_id="abc123",
            container_name="test-container",
            status="running",
            health=None,
        )
        d = info.to_dict()
        assert "health" not in d
        assert d["domain_id"] == "test-123"

    def test_to_dict_includes_health_when_set(self):
        info = DomainInfo(
            domain_id="test-123",
            domain_type="backend",
            container_id="abc123",
            container_name="test-container",
            status="running",
            health="healthy",
        )
        d = info.to_dict()
        assert d["health"] == "healthy"


class TestDomainSpawnerInit:
    """Tests for DomainSpawner initialization."""

    def test_init_defaults(self):
        spawner = DomainSpawner()
        assert spawner.docker_url is None
        assert spawner.default_image is not None
        assert spawner.default_network is not None
        assert spawner._client is None

    def test_init_with_custom_values(self):
        spawner = DomainSpawner(
            docker_url="tcp://localhost:2375",
            default_image="my-image:latest",
            default_network="my-network",
        )
        assert spawner.docker_url == "tcp://localhost:2375"
        assert spawner.default_image == "my-image:latest"
        assert spawner.default_network == "my-network"

    @patch("lib.spawner.docker.from_env")
    def test_lazy_client_initialization(self, mock_from_env):
        mock_client = MagicMock()
        mock_from_env.return_value = mock_client

        spawner = DomainSpawner()
        assert spawner._client is None

        # Access client property
        client = spawner.client
        assert client == mock_client
        mock_from_env.assert_called_once()

        # Second access should not create new client
        client2 = spawner.client
        assert client2 == mock_client
        assert mock_from_env.call_count == 1


class TestDomainSpawnerPing:
    """Tests for ping functionality."""

    @patch("lib.spawner.docker.from_env")
    def test_ping_success(self, mock_from_env):
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_from_env.return_value = mock_client

        spawner = DomainSpawner()
        assert spawner.ping() is True
        mock_client.ping.assert_called_once()

    @patch("lib.spawner.docker.from_env")
    def test_ping_failure(self, mock_from_env):
        mock_client = MagicMock()
        mock_client.ping.side_effect = DockerException("Connection refused")
        mock_from_env.return_value = mock_client

        spawner = DomainSpawner()
        assert spawner.ping() is False


class TestDomainSpawnerSpawn:
    """Tests for spawn_domain functionality."""

    @patch("lib.spawner.docker.from_env")
    def test_spawn_domain_success(self, mock_from_env):
        mock_container = MagicMock()
        mock_container.id = "container123"
        mock_container.status = "running"

        mock_client = MagicMock()
        mock_client.containers.run.return_value = mock_container
        mock_client.containers.get.return_value = mock_container
        mock_from_env.return_value = mock_client

        spawner = DomainSpawner()
        domain_id = spawner.spawn_domain("backend", wait_for_start=True)

        assert domain_id.startswith("backend-")
        assert len(domain_id) == len("backend-") + 8

        # Verify container.run was called with correct args
        call_kwargs = mock_client.containers.run.call_args.kwargs
        assert call_kwargs["environment"]["AGENT_ROLE"] == "domain"
        assert call_kwargs["environment"]["DOMAIN_TYPE"] == "backend"
        assert "backend" in call_kwargs["labels"][DomainSpawner.DOMAIN_LABEL]

    @patch("lib.spawner.docker.from_env")
    def test_spawn_domain_no_wait(self, mock_from_env):
        mock_container = MagicMock()
        mock_container.id = "container123"

        mock_client = MagicMock()
        mock_client.containers.run.return_value = mock_container
        mock_from_env.return_value = mock_client

        spawner = DomainSpawner()
        domain_id = spawner.spawn_domain("frontend", wait_for_start=False)

        assert domain_id.startswith("frontend-")
        # containers.get should not be called when not waiting
        mock_client.containers.get.assert_not_called()

    @patch("lib.spawner.docker.from_env")
    def test_spawn_domain_with_custom_config(self, mock_from_env):
        mock_container = MagicMock()
        mock_container.id = "container123"
        mock_container.status = "running"

        mock_client = MagicMock()
        mock_client.containers.run.return_value = mock_container
        mock_client.containers.get.return_value = mock_container
        mock_from_env.return_value = mock_client

        config = DomainConfig(
            domain_type="devops",
            memory_limit="2g",
            cpu_limit=1.0,
        )

        spawner = DomainSpawner()
        domain_id = spawner.spawn_domain("devops", config=config)

        call_kwargs = mock_client.containers.run.call_args.kwargs
        assert call_kwargs["mem_limit"] == "2g"
        assert call_kwargs["cpu_quota"] == 100000  # 1.0 * 100000

    @patch("lib.spawner.docker.from_env")
    def test_spawn_domain_container_fails(self, mock_from_env):
        mock_client = MagicMock()
        mock_client.containers.run.side_effect = APIError("Image not found")
        mock_from_env.return_value = mock_client

        spawner = DomainSpawner()
        with pytest.raises(DockerException) as exc_info:
            spawner.spawn_domain("backend")

        assert "Failed to spawn domain backend" in str(exc_info.value)

    @patch("lib.spawner.docker.from_env")
    def test_spawn_domain_container_exits_immediately(self, mock_from_env):
        mock_container = MagicMock()
        mock_container.id = "container123"
        mock_container.status = "exited"
        mock_container.logs.return_value = b"Error: startup failed"

        mock_client = MagicMock()
        mock_client.containers.run.return_value = mock_container
        mock_client.containers.get.return_value = mock_container
        mock_from_env.return_value = mock_client

        spawner = DomainSpawner()
        with pytest.raises(DockerException) as exc_info:
            spawner.spawn_domain("backend", timeout=1)

        assert "exited unexpectedly" in str(exc_info.value)


class TestDomainSpawnerStop:
    """Tests for stop_domain functionality."""

    @patch("lib.spawner.docker.from_env")
    def test_stop_domain_success(self, mock_from_env):
        mock_container = MagicMock()

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [mock_container]
        mock_from_env.return_value = mock_client

        spawner = DomainSpawner()
        result = spawner.stop_domain("backend-abc123")

        assert result is True
        mock_container.stop.assert_called_once()
        mock_container.remove.assert_called_once()

    @patch("lib.spawner.docker.from_env")
    def test_stop_domain_not_found(self, mock_from_env):
        mock_client = MagicMock()
        mock_client.containers.list.return_value = []
        mock_from_env.return_value = mock_client

        spawner = DomainSpawner()
        result = spawner.stop_domain("nonexistent-123")

        assert result is False

    @patch("lib.spawner.docker.from_env")
    def test_stop_domain_already_stopped(self, mock_from_env):
        mock_container = MagicMock()
        mock_container.stop.side_effect = NotFound("Already stopped")

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [mock_container]
        mock_from_env.return_value = mock_client

        spawner = DomainSpawner()
        result = spawner.stop_domain("backend-abc123")

        assert result is False


class TestDomainSpawnerList:
    """Tests for list_domains functionality."""

    @patch("lib.spawner.docker.from_env")
    def test_list_domains_empty(self, mock_from_env):
        mock_client = MagicMock()
        mock_client.containers.list.return_value = []
        mock_from_env.return_value = mock_client

        spawner = DomainSpawner()
        domains = spawner.list_domains()

        assert domains == []

    @patch("lib.spawner.docker.from_env")
    def test_list_domains_with_results(self, mock_from_env):
        mock_container1 = MagicMock()
        mock_container1.id = "abc123"
        mock_container1.name = "domain-backend-abc"
        mock_container1.status = "running"
        mock_container1.labels = {
            DomainSpawner.DOMAIN_LABEL: "backend",
            DomainSpawner.DOMAIN_ID_LABEL: "backend-abc",
        }
        mock_container1.attrs = {"State": {"Health": {"Status": "healthy"}}}
        mock_container1.reload = MagicMock()

        mock_container2 = MagicMock()
        mock_container2.id = "def456"
        mock_container2.name = "domain-frontend-def"
        mock_container2.status = "running"
        mock_container2.labels = {
            DomainSpawner.DOMAIN_LABEL: "frontend",
            DomainSpawner.DOMAIN_ID_LABEL: "frontend-def",
        }
        mock_container2.attrs = {"State": {}}
        mock_container2.reload = MagicMock()

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [mock_container1, mock_container2]
        mock_from_env.return_value = mock_client

        spawner = DomainSpawner()
        domains = spawner.list_domains()

        assert len(domains) == 2
        assert domains[0].domain_type == "backend"
        assert domains[0].health == "healthy"
        assert domains[1].domain_type == "frontend"
        assert domains[1].health is None

    @patch("lib.spawner.docker.from_env")
    def test_list_domains_filter_by_type(self, mock_from_env):
        mock_client = MagicMock()
        mock_client.containers.list.return_value = []
        mock_from_env.return_value = mock_client

        spawner = DomainSpawner()
        spawner.list_domains(domain_type="backend")

        # Verify filter includes domain type
        call_kwargs = mock_client.containers.list.call_args.kwargs
        assert f"{DomainSpawner.DOMAIN_LABEL}=backend" in str(call_kwargs["filters"])


class TestDomainSpawnerHealth:
    """Tests for health check functionality."""

    @patch("lib.spawner.docker.from_env")
    def test_is_domain_healthy_running(self, mock_from_env):
        mock_container = MagicMock()
        mock_container.id = "abc123"
        mock_container.name = "domain-backend-abc"
        mock_container.status = "running"
        mock_container.labels = {
            DomainSpawner.DOMAIN_LABEL: "backend",
            DomainSpawner.DOMAIN_ID_LABEL: "backend-abc",
        }
        mock_container.attrs = {"State": {"Health": {"Status": "healthy"}}}
        mock_container.reload = MagicMock()

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [mock_container]
        mock_from_env.return_value = mock_client

        spawner = DomainSpawner()
        assert spawner.is_domain_healthy("backend-abc") is True

    @patch("lib.spawner.docker.from_env")
    def test_is_domain_healthy_not_running(self, mock_from_env):
        mock_container = MagicMock()
        mock_container.id = "abc123"
        mock_container.name = "domain-backend-abc"
        mock_container.status = "exited"
        mock_container.labels = {
            DomainSpawner.DOMAIN_LABEL: "backend",
            DomainSpawner.DOMAIN_ID_LABEL: "backend-abc",
        }
        mock_container.attrs = {}
        mock_container.reload = MagicMock()

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [mock_container]
        mock_from_env.return_value = mock_client

        spawner = DomainSpawner()
        assert spawner.is_domain_healthy("backend-abc") is False

    @patch("lib.spawner.docker.from_env")
    def test_is_domain_healthy_unhealthy_status(self, mock_from_env):
        mock_container = MagicMock()
        mock_container.id = "abc123"
        mock_container.name = "domain-backend-abc"
        mock_container.status = "running"
        mock_container.labels = {
            DomainSpawner.DOMAIN_LABEL: "backend",
            DomainSpawner.DOMAIN_ID_LABEL: "backend-abc",
        }
        mock_container.attrs = {"State": {"Health": {"Status": "unhealthy"}}}
        mock_container.reload = MagicMock()

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [mock_container]
        mock_from_env.return_value = mock_client

        spawner = DomainSpawner()
        assert spawner.is_domain_healthy("backend-abc") is False

    @patch("lib.spawner.docker.from_env")
    def test_is_domain_healthy_not_found(self, mock_from_env):
        mock_client = MagicMock()
        mock_client.containers.list.return_value = []
        mock_from_env.return_value = mock_client

        spawner = DomainSpawner()
        assert spawner.is_domain_healthy("nonexistent-123") is False

    @patch("lib.spawner.docker.from_env")
    def test_get_healthy_domain_finds_one(self, mock_from_env):
        mock_container = MagicMock()
        mock_container.id = "abc123"
        mock_container.name = "domain-backend-abc"
        mock_container.status = "running"
        mock_container.labels = {
            DomainSpawner.DOMAIN_LABEL: "backend",
            DomainSpawner.DOMAIN_ID_LABEL: "backend-abc",
        }
        mock_container.attrs = {"State": {"Health": {"Status": "healthy"}}}
        mock_container.reload = MagicMock()

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [mock_container]
        mock_from_env.return_value = mock_client

        spawner = DomainSpawner()
        domain = spawner.get_healthy_domain("backend")

        assert domain is not None
        assert domain.domain_id == "backend-abc"

    @patch("lib.spawner.docker.from_env")
    def test_get_healthy_domain_none_available(self, mock_from_env):
        mock_client = MagicMock()
        mock_client.containers.list.return_value = []
        mock_from_env.return_value = mock_client

        spawner = DomainSpawner()
        domain = spawner.get_healthy_domain("backend")

        assert domain is None


class TestDomainSpawnerCleanup:
    """Tests for cleanup functionality."""

    @patch("lib.spawner.docker.from_env")
    def test_cleanup_stopped_removes_exited(self, mock_from_env):
        mock_container = MagicMock()
        mock_container.id = "abc123"
        mock_container.name = "domain-backend-abc"
        mock_container.status = "exited"
        mock_container.labels = {
            DomainSpawner.DOMAIN_LABEL: "backend",
            DomainSpawner.DOMAIN_ID_LABEL: "backend-abc",
        }
        mock_container.attrs = {}
        mock_container.reload = MagicMock()

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [mock_container]
        mock_from_env.return_value = mock_client

        spawner = DomainSpawner()
        removed = spawner.cleanup_stopped()

        assert "backend-abc" in removed
        mock_container.stop.assert_called()
        mock_container.remove.assert_called()

    @patch("lib.spawner.docker.from_env")
    def test_cleanup_stopped_ignores_running(self, mock_from_env):
        mock_container = MagicMock()
        mock_container.id = "abc123"
        mock_container.name = "domain-backend-abc"
        mock_container.status = "running"
        mock_container.labels = {
            DomainSpawner.DOMAIN_LABEL: "backend",
            DomainSpawner.DOMAIN_ID_LABEL: "backend-abc",
        }
        mock_container.attrs = {}
        mock_container.reload = MagicMock()

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [mock_container]
        mock_from_env.return_value = mock_client

        spawner = DomainSpawner()
        removed = spawner.cleanup_stopped()

        assert removed == []

    @patch("lib.spawner.docker.from_env")
    def test_cleanup_all(self, mock_from_env):
        mock_container1 = MagicMock()
        mock_container1.id = "abc123"
        mock_container1.name = "domain-backend-abc"
        mock_container1.status = "running"
        mock_container1.labels = {
            DomainSpawner.DOMAIN_LABEL: "backend",
            DomainSpawner.DOMAIN_ID_LABEL: "backend-abc",
        }
        mock_container1.attrs = {}
        mock_container1.reload = MagicMock()

        mock_container2 = MagicMock()
        mock_container2.id = "def456"
        mock_container2.name = "domain-frontend-def"
        mock_container2.status = "exited"
        mock_container2.labels = {
            DomainSpawner.DOMAIN_LABEL: "frontend",
            DomainSpawner.DOMAIN_ID_LABEL: "frontend-def",
        }
        mock_container2.attrs = {}
        mock_container2.reload = MagicMock()

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [mock_container1, mock_container2]
        mock_from_env.return_value = mock_client

        spawner = DomainSpawner()
        removed = spawner.cleanup_all()

        assert len(removed) == 2
        assert "backend-abc" in removed
        assert "frontend-def" in removed
