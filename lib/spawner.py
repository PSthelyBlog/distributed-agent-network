"""
Docker-based domain spawner for creating domain orchestrator containers.

Provides container lifecycle management for domain orchestrators.
"""

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import docker
from docker.errors import DockerException, NotFound, APIError
from pydantic import BaseModel, Field


class DomainConfig(BaseModel):
    """Configuration for a domain container."""

    domain_type: str
    image: str = "distributed-agent-network-main-orchestrator"
    memory_limit: str = "1g"
    cpu_limit: float = 0.5
    network: str = "distributed-agent-network_agent-network"
    redis_url: str = "redis://message-broker:6379"


class DomainInfo(BaseModel):
    """Information about a spawned domain container."""

    domain_id: str
    domain_type: str
    container_id: str
    container_name: str
    status: str  # created, running, exited, dead
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    health: Optional[str] = None  # healthy, unhealthy, starting, none

    def to_dict(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class DomainSpawner:
    """
    Docker-based spawner for domain orchestrator containers.

    Handles container lifecycle: creation, monitoring, and cleanup.
    """

    # Container label for identifying domain containers
    LABEL_PREFIX = "distributed-agent-network"
    DOMAIN_LABEL = f"{LABEL_PREFIX}.domain"
    DOMAIN_ID_LABEL = f"{LABEL_PREFIX}.domain-id"

    def __init__(
        self,
        docker_url: Optional[str] = None,
        default_image: Optional[str] = None,
        default_network: Optional[str] = None,
    ):
        """
        Initialize spawner with Docker connection.

        Args:
            docker_url: Docker daemon URL. Defaults to DOCKER_HOST env var or socket.
            default_image: Default image for domain containers.
            default_network: Default Docker network to attach containers.
        """
        self.docker_url = docker_url or os.environ.get("DOCKER_HOST")
        self.default_image = default_image or os.environ.get(
            "DOMAIN_IMAGE", "distributed-agent-network-main-orchestrator"
        )
        self.default_network = default_network or os.environ.get(
            "DOCKER_NETWORK", "distributed-agent-network_agent-network"
        )
        self._client: Optional[docker.DockerClient] = None

    @property
    def client(self) -> docker.DockerClient:
        """Lazy Docker client initialization."""
        if self._client is None:
            if self.docker_url:
                self._client = docker.DockerClient(base_url=self.docker_url)
            else:
                self._client = docker.from_env()
        return self._client

    def ping(self) -> bool:
        """Check Docker daemon connection."""
        try:
            return self.client.ping()
        except DockerException:
            return False

    def close(self) -> None:
        """Close Docker client connection."""
        if self._client:
            self._client.close()

    # ==================== Domain Lifecycle ====================

    def spawn_domain(
        self,
        domain_type: str,
        config: Optional[DomainConfig] = None,
        wait_for_start: bool = True,
        timeout: int = 30,
    ) -> str:
        """
        Spawn a new domain orchestrator container.

        Args:
            domain_type: Type of domain (backend, frontend, devops)
            config: Optional custom configuration
            wait_for_start: Wait for container to be running
            timeout: Timeout in seconds for container start

        Returns:
            domain_id: Unique identifier for the domain

        Raises:
            DockerException: If container creation fails
        """
        if config is None:
            config = DomainConfig(
                domain_type=domain_type,
                image=self.default_image,
                network=self.default_network,
            )

        domain_id = f"{domain_type}-{uuid.uuid4().hex[:8]}"
        container_name = f"domain-{domain_id}"

        # Environment variables for the domain container
        environment = {
            "AGENT_ROLE": "domain",
            "AGENT_ID": domain_id,
            "DOMAIN_TYPE": domain_type,
            "REDIS_URL": config.redis_url,
            "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
        }

        # Labels for container identification
        labels = {
            self.DOMAIN_LABEL: domain_type,
            self.DOMAIN_ID_LABEL: domain_id,
            f"{self.LABEL_PREFIX}.managed": "true",
        }

        # Volume mounts
        volumes = self._get_domain_volumes(domain_type)

        try:
            container = self.client.containers.run(
                config.image,
                name=container_name,
                hostname=domain_id,
                detach=True,
                environment=environment,
                labels=labels,
                volumes=volumes,
                network=config.network,
                mem_limit=config.memory_limit,
                cpu_quota=int(config.cpu_limit * 100000),
                restart_policy={"Name": "unless-stopped"},
            )

            if wait_for_start:
                self._wait_for_container(container.id, timeout)

            return domain_id

        except APIError as e:
            raise DockerException(f"Failed to spawn domain {domain_type}: {e}")

    def _get_domain_volumes(self, domain_type: str) -> dict:
        """Get volume mounts for a domain container."""
        # Base paths - these are relative to the project root
        project_root = os.environ.get(
            "PROJECT_ROOT",
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )

        volumes = {
            # Shared workspace
            os.path.join(project_root, "workspace"): {
                "bind": "/workspace",
                "mode": "rw",
            },
            # Library code
            os.path.join(project_root, "lib"): {
                "bind": "/lib",
                "mode": "ro",
            },
        }

        # Domain-specific config if available
        domain_config_path = os.path.join(
            project_root, "agents", "domain-templates", domain_type
        )
        if os.path.exists(domain_config_path):
            volumes[domain_config_path] = {
                "bind": "/agent-config",
                "mode": "ro",
            }

        return volumes

    def _wait_for_container(self, container_id: str, timeout: int) -> None:
        """Wait for container to reach running state."""
        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                container = self.client.containers.get(container_id)
                if container.status == "running":
                    return
                elif container.status in ("exited", "dead"):
                    logs = container.logs(tail=50).decode("utf-8")
                    raise DockerException(
                        f"Container exited unexpectedly. Logs:\n{logs}"
                    )
            except NotFound:
                raise DockerException(f"Container {container_id} not found")

            time.sleep(0.5)

        raise DockerException(f"Container did not start within {timeout}s")

    def stop_domain(self, domain_id: str, timeout: int = 10) -> bool:
        """
        Stop and remove a domain container.

        Args:
            domain_id: Domain identifier
            timeout: Timeout in seconds for graceful stop

        Returns:
            True if stopped successfully, False if not found
        """
        container = self._find_container_by_domain_id(domain_id)
        if not container:
            return False

        try:
            container.stop(timeout=timeout)
            container.remove()
            return True
        except NotFound:
            return False
        except APIError as e:
            raise DockerException(f"Failed to stop domain {domain_id}: {e}")

    def _find_container_by_domain_id(self, domain_id: str):
        """Find container by domain ID label."""
        containers = self.client.containers.list(
            all=True,
            filters={"label": f"{self.DOMAIN_ID_LABEL}={domain_id}"},
        )
        return containers[0] if containers else None

    # ==================== Domain Discovery ====================

    def list_domains(self, domain_type: Optional[str] = None) -> list[DomainInfo]:
        """
        List active domain containers.

        Args:
            domain_type: Optional filter by domain type

        Returns:
            List of DomainInfo objects
        """
        filters = {"label": f"{self.LABEL_PREFIX}.managed=true"}
        if domain_type:
            filters["label"] = [
                f"{self.LABEL_PREFIX}.managed=true",
                f"{self.DOMAIN_LABEL}={domain_type}",
            ]

        containers = self.client.containers.list(all=True, filters=filters)

        domains = []
        for container in containers:
            labels = container.labels
            health = self._get_container_health(container)

            domain = DomainInfo(
                domain_id=labels.get(self.DOMAIN_ID_LABEL, "unknown"),
                domain_type=labels.get(self.DOMAIN_LABEL, "unknown"),
                container_id=container.id[:12],
                container_name=container.name,
                status=container.status,
                health=health,
            )
            domains.append(domain)

        return domains

    def get_domain(self, domain_id: str) -> Optional[DomainInfo]:
        """
        Get information about a specific domain.

        Args:
            domain_id: Domain identifier

        Returns:
            DomainInfo or None if not found
        """
        container = self._find_container_by_domain_id(domain_id)
        if not container:
            return None

        labels = container.labels
        health = self._get_container_health(container)

        return DomainInfo(
            domain_id=domain_id,
            domain_type=labels.get(self.DOMAIN_LABEL, "unknown"),
            container_id=container.id[:12],
            container_name=container.name,
            status=container.status,
            health=health,
        )

    def _get_container_health(self, container) -> Optional[str]:
        """Get health status from container."""
        try:
            container.reload()
            health = container.attrs.get("State", {}).get("Health", {})
            return health.get("Status") if health else None
        except (NotFound, APIError):
            return None

    # ==================== Health Checks ====================

    def is_domain_healthy(self, domain_id: str) -> bool:
        """
        Check if a domain container is healthy.

        Args:
            domain_id: Domain identifier

        Returns:
            True if container is running and healthy
        """
        domain = self.get_domain(domain_id)
        if not domain:
            return False

        # Container must be running
        if domain.status != "running":
            return False

        # If health check is configured, it must be healthy
        if domain.health is not None:
            return domain.health == "healthy"

        # No health check configured, running is good enough
        return True

    def get_healthy_domain(self, domain_type: str) -> Optional[DomainInfo]:
        """
        Find a healthy domain of the specified type.

        Args:
            domain_type: Type of domain needed

        Returns:
            DomainInfo for a healthy domain or None
        """
        domains = self.list_domains(domain_type)

        for domain in domains:
            if self.is_domain_healthy(domain.domain_id):
                return domain

        return None

    # ==================== Cleanup ====================

    def cleanup_stopped(self) -> list[str]:
        """
        Remove all stopped domain containers.

        Returns:
            List of removed domain IDs
        """
        removed = []

        for domain in self.list_domains():
            if domain.status in ("exited", "dead"):
                if self.stop_domain(domain.domain_id):
                    removed.append(domain.domain_id)

        return removed

    def cleanup_all(self, timeout: int = 10) -> list[str]:
        """
        Stop and remove all domain containers.

        Args:
            timeout: Timeout for each container stop

        Returns:
            List of removed domain IDs
        """
        removed = []

        for domain in self.list_domains():
            if self.stop_domain(domain.domain_id, timeout):
                removed.append(domain.domain_id)

        return removed


# CLI interface
if __name__ == "__main__":
    import sys

    spawner = DomainSpawner()

    if len(sys.argv) < 2:
        print("Usage: python spawner.py <command> [args]")
        print("Commands: ping, spawn, stop, list, health, cleanup")
        sys.exit(1)

    command = sys.argv[1]

    if command == "ping":
        if spawner.ping():
            print("Docker connection OK")
        else:
            print("Docker connection FAILED")
            sys.exit(1)

    elif command == "spawn":
        if len(sys.argv) < 3:
            print("Usage: python spawner.py spawn <domain_type>")
            print("Domain types: backend, frontend, devops")
            sys.exit(1)

        domain_type = sys.argv[2]
        print(f"Spawning {domain_type} domain...")
        try:
            domain_id = spawner.spawn_domain(domain_type)
            print(f"Spawned domain: {domain_id}")
        except DockerException as e:
            print(f"Failed to spawn: {e}")
            sys.exit(1)

    elif command == "stop":
        if len(sys.argv) < 3:
            print("Usage: python spawner.py stop <domain_id>")
            sys.exit(1)

        domain_id = sys.argv[2]
        if spawner.stop_domain(domain_id):
            print(f"Stopped domain: {domain_id}")
        else:
            print(f"Domain not found: {domain_id}")
            sys.exit(1)

    elif command == "list":
        domain_type = sys.argv[2] if len(sys.argv) > 2 else None
        domains = spawner.list_domains(domain_type)

        if not domains:
            print("No active domains")
        else:
            print(f"Active domains ({len(domains)}):")
            for d in domains:
                health_str = f", {d.health}" if d.health else ""
                print(f"  - {d.domain_id}: {d.domain_type} ({d.status}{health_str})")

    elif command == "health":
        if len(sys.argv) < 3:
            print("Usage: python spawner.py health <domain_id>")
            sys.exit(1)

        domain_id = sys.argv[2]
        if spawner.is_domain_healthy(domain_id):
            print(f"Domain {domain_id} is healthy")
        else:
            print(f"Domain {domain_id} is NOT healthy")
            sys.exit(1)

    elif command == "cleanup":
        if len(sys.argv) > 2 and sys.argv[2] == "--all":
            removed = spawner.cleanup_all()
            print(f"Removed all domains: {removed}")
        else:
            removed = spawner.cleanup_stopped()
            print(f"Removed stopped domains: {removed}")

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
