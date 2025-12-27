"""
Redis-based messaging system for inter-agent communication.

Provides pub/sub messaging, task queues, and result storage.
"""

import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import redis
from pydantic import BaseModel, Field


class TaskMessage(BaseModel):
    """Schema for task messages between agents."""

    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str = "task_assignment"
    source: str = ""
    destination: str = ""
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    payload: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str) -> "TaskMessage":
        return cls.model_validate_json(data)


class TaskResult(BaseModel):
    """Schema for task results."""

    task_id: str
    status: str = "pending"  # pending, in_progress, completed, failed
    output: Optional[dict] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "TaskResult":
        return cls.model_validate(data)


class AgentMessaging:
    """
    Redis-based messaging system for agent communication.

    Handles:
    - Task queues (FIFO per domain)
    - Result storage
    - Pub/sub for real-time notifications
    """

    def __init__(self, redis_url: Optional[str] = None):
        """
        Initialize messaging with Redis connection.

        Args:
            redis_url: Redis connection URL. Defaults to REDIS_URL env var.
        """
        self.redis_url = redis_url or os.environ.get(
            "REDIS_URL", "redis://localhost:6379"
        )
        self._client: Optional[redis.Redis] = None
        self._pubsub: Optional[redis.client.PubSub] = None

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

    def ping(self) -> bool:
        """Check Redis connection."""
        try:
            return self.client.ping()
        except redis.ConnectionError:
            return False

    def close(self) -> None:
        """Close Redis connection."""
        if self._pubsub:
            self._pubsub.close()
        if self._client:
            self._client.close()

    # ==================== Task Queue Operations ====================

    def publish_task(
        self,
        domain: str,
        description: str,
        requirements: Optional[list] = None,
        context: Optional[dict] = None,
        priority: str = "normal",
        timeout_seconds: int = 300,
        source: str = "",
    ) -> str:
        """
        Publish a task to a domain's queue.

        Args:
            domain: Target domain (e.g., "backend", "frontend")
            description: Task description
            requirements: List of specific requirements
            context: Additional context dict
            priority: Task priority (low, normal, high)
            timeout_seconds: Task timeout
            source: Source agent ID

        Returns:
            task_id: Unique task identifier
        """
        task = TaskMessage(
            source=source,
            destination=domain,
            payload={
                "description": description,
                "requirements": requirements or [],
                "context": context or {},
            },
            metadata={
                "priority": priority,
                "timeout_seconds": timeout_seconds,
            },
        )

        # Add to domain's task queue
        queue_key = f"tasks:pending:{domain}"
        self.client.lpush(queue_key, task.to_json())

        # Initialize result tracking
        self._init_result(task.task_id)

        # Publish notification for real-time subscribers
        self.client.publish(f"notifications:{domain}", task.to_json())

        return task.task_id

    def get_next_task(self, domain: str, timeout: int = 0) -> Optional[TaskMessage]:
        """
        Get the next task from a domain's queue.

        Args:
            domain: Domain to get task for
            timeout: Blocking timeout in seconds (0 = non-blocking)

        Returns:
            TaskMessage or None if no task available
        """
        queue_key = f"tasks:pending:{domain}"
        active_key = f"tasks:active:{domain}"

        if timeout > 0:
            # Blocking pop with timeout
            result = self.client.brpoplpush(queue_key, active_key, timeout)
        else:
            # Non-blocking
            result = self.client.rpoplpush(queue_key, active_key)

        if result:
            task = TaskMessage.from_json(result)
            self._update_result(task.task_id, status="in_progress")
            return task

        return None

    def complete_task(self, domain: str, task: TaskMessage) -> None:
        """Remove task from active queue after completion."""
        active_key = f"tasks:active:{domain}"
        self.client.lrem(active_key, 1, task.to_json())

    def get_queue_length(self, domain: str) -> int:
        """Get number of pending tasks for a domain."""
        return self.client.llen(f"tasks:pending:{domain}")

    # ==================== Result Operations ====================

    def _init_result(self, task_id: str) -> None:
        """Initialize result tracking for a task."""
        result = TaskResult(task_id=task_id, status="pending")
        self.client.hset(f"results:{task_id}", mapping=result.to_dict())

    def _update_result(self, task_id: str, **kwargs) -> None:
        """Update result fields."""
        if "status" in kwargs and kwargs["status"] == "in_progress":
            kwargs["started_at"] = datetime.now(timezone.utc).isoformat()
        self.client.hset(f"results:{task_id}", mapping=kwargs)

    def publish_result(
        self,
        task_id: str,
        output: dict,
        status: str = "completed",
        error: Optional[str] = None,
    ) -> None:
        """
        Publish result for a completed task.

        Args:
            task_id: Task identifier
            output: Result data dict
            status: Final status (completed, failed)
            error: Error message if failed
        """
        result_data = {
            "status": status,
            "output": json.dumps(output),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        if error:
            result_data["error"] = error

        self.client.hset(f"results:{task_id}", mapping=result_data)

        # Publish notification
        self.client.publish(f"results:{task_id}", json.dumps(result_data))

    def get_result(
        self, task_id: str, timeout: int = 0
    ) -> Optional[TaskResult]:
        """
        Get result for a task.

        Args:
            task_id: Task identifier
            timeout: Wait timeout in seconds (0 = no wait)

        Returns:
            TaskResult or None
        """
        result_key = f"results:{task_id}"

        if timeout > 0:
            # Subscribe and wait for result
            pubsub = self.client.pubsub()
            pubsub.subscribe(result_key)

            deadline = time.time() + timeout
            while time.time() < deadline:
                message = pubsub.get_message(timeout=1)
                if message and message["type"] == "message":
                    break

                # Check if result already exists
                data = self.client.hgetall(result_key)
                if data and data.get("status") in ("completed", "failed"):
                    break

            pubsub.close()

        data = self.client.hgetall(result_key)
        if data:
            # Parse output JSON if present
            if "output" in data and data["output"]:
                try:
                    data["output"] = json.loads(data["output"])
                except json.JSONDecodeError:
                    pass
            return TaskResult.from_dict(data)

        return None

    def wait_for_result(
        self, task_id: str, timeout: int = 300, poll_interval: float = 1.0
    ) -> Optional[TaskResult]:
        """
        Wait for a task to complete.

        Args:
            task_id: Task identifier
            timeout: Maximum wait time in seconds
            poll_interval: Polling interval in seconds

        Returns:
            TaskResult or None if timeout
        """
        deadline = time.time() + timeout

        while time.time() < deadline:
            result = self.get_result(task_id)
            if result and result.status in ("completed", "failed"):
                return result
            time.sleep(poll_interval)

        return None

    # ==================== Pub/Sub Operations ====================

    def subscribe(
        self, channels: list[str], callback: Callable[[str, dict], None]
    ) -> None:
        """
        Subscribe to channels and process messages.

        Args:
            channels: List of channel names
            callback: Function(channel, message) to call on message
        """
        self._pubsub = self.client.pubsub()
        self._pubsub.subscribe(*channels)

        for message in self._pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    callback(message["channel"], data)
                except json.JSONDecodeError:
                    callback(message["channel"], {"raw": message["data"]})

    def publish(self, channel: str, message: dict) -> int:
        """
        Publish message to a channel.

        Args:
            channel: Channel name
            message: Message dict

        Returns:
            Number of subscribers that received the message
        """
        return self.client.publish(channel, json.dumps(message))

    # ==================== Utility Operations ====================

    def add_log(self, task_id: str, log_entry: str) -> None:
        """Add a log entry for a task."""
        timestamp = datetime.now(timezone.utc).isoformat()
        self.client.rpush(f"results:{task_id}:logs", f"[{timestamp}] {log_entry}")

    def get_logs(self, task_id: str) -> list[str]:
        """Get all log entries for a task."""
        return self.client.lrange(f"results:{task_id}:logs", 0, -1)


# CLI interface for testing
if __name__ == "__main__":
    import sys

    messaging = AgentMessaging()

    if len(sys.argv) < 2:
        print("Usage: python messaging.py <command> [args]")
        print("Commands: ping, publish, listen")
        sys.exit(1)

    command = sys.argv[1]

    if command == "ping":
        if messaging.ping():
            print("Redis connection OK")
        else:
            print("Redis connection FAILED")
            sys.exit(1)

    elif command == "publish":
        if len(sys.argv) < 4:
            print("Usage: python messaging.py publish <domain> <description>")
            sys.exit(1)
        domain = sys.argv[2]
        description = sys.argv[3]
        task_id = messaging.publish_task(domain, description, source="cli")
        print(f"Published task: {task_id}")

    elif command == "listen":
        if len(sys.argv) < 3:
            print("Usage: python messaging.py listen <domain>")
            sys.exit(1)
        domain = sys.argv[2]
        print(f"Listening for tasks on domain: {domain}")

        while True:
            task = messaging.get_next_task(domain, timeout=5)
            if task:
                print(f"Received task: {task.task_id}")
                print(f"  Description: {task.payload.get('description')}")
                print(json.dumps(task.payload, indent=2))
