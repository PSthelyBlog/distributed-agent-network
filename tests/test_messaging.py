"""
Tests for the messaging module.

Run with: pytest tests/test_messaging.py -v
Requires: Redis running on localhost:6379
"""

import json
import os
import time
from threading import Thread
from unittest.mock import MagicMock, patch

import pytest

# Set test Redis URL before imports
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

from lib.messaging import AgentMessaging, TaskMessage, TaskResult


class TestTaskMessage:
    """Tests for TaskMessage model."""

    def test_create_with_defaults(self):
        msg = TaskMessage()
        assert msg.task_id is not None
        assert msg.type == "task_assignment"
        assert msg.timestamp is not None
        assert msg.payload == {}
        assert msg.metadata == {}

    def test_create_with_values(self):
        msg = TaskMessage(
            source="main-orchestrator",
            destination="backend",
            payload={"description": "Test task"},
            metadata={"priority": "high"},
        )
        assert msg.source == "main-orchestrator"
        assert msg.destination == "backend"
        assert msg.payload["description"] == "Test task"
        assert msg.metadata["priority"] == "high"

    def test_json_serialization(self):
        msg = TaskMessage(
            source="test",
            payload={"key": "value"},
        )
        json_str = msg.to_json()
        parsed = TaskMessage.from_json(json_str)

        assert parsed.source == msg.source
        assert parsed.payload == msg.payload
        assert parsed.task_id == msg.task_id


class TestTaskResult:
    """Tests for TaskResult model."""

    def test_create_with_defaults(self):
        result = TaskResult(task_id="test-123")
        assert result.task_id == "test-123"
        assert result.status == "pending"
        assert result.output is None
        assert result.error is None

    def test_create_completed_result(self):
        result = TaskResult(
            task_id="test-123",
            status="completed",
            output={"files_created": ["test.py"]},
        )
        assert result.status == "completed"
        assert result.output["files_created"] == ["test.py"]

    def test_dict_serialization(self):
        result = TaskResult(
            task_id="test-123",
            status="failed",
            error="Something went wrong",
        )
        data = result.to_dict()
        parsed = TaskResult.from_dict(data)

        assert parsed.task_id == result.task_id
        assert parsed.status == result.status
        assert parsed.error == result.error


@pytest.fixture
def messaging():
    """Create messaging instance for tests."""
    m = AgentMessaging()
    yield m
    m.close()


@pytest.fixture
def clean_redis(messaging):
    """Clean up test keys before and after tests."""
    test_keys = [
        "tasks:pending:test-domain",
        "tasks:active:test-domain",
        "results:*",
    ]

    # Cleanup before test
    for pattern in test_keys:
        for key in messaging.client.keys(pattern):
            messaging.client.delete(key)

    yield

    # Cleanup after test
    for pattern in test_keys:
        for key in messaging.client.keys(pattern):
            messaging.client.delete(key)


class TestAgentMessaging:
    """Tests for AgentMessaging class."""

    def test_ping(self, messaging):
        """Test Redis connection check."""
        assert messaging.ping() is True

    def test_ping_failure(self):
        """Test ping with bad connection."""
        m = AgentMessaging(redis_url="redis://nonexistent:6379")
        assert m.ping() is False

    @pytest.mark.usefixtures("clean_redis")
    def test_publish_task(self, messaging):
        """Test publishing a task to queue."""
        task_id = messaging.publish_task(
            domain="test-domain",
            description="Test task description",
            requirements=["req1", "req2"],
            context={"project": "test"},
            source="test-source",
        )

        assert task_id is not None

        # Verify task is in queue
        queue_len = messaging.get_queue_length("test-domain")
        assert queue_len == 1

        # Verify result tracking initialized
        result = messaging.get_result(task_id)
        assert result is not None
        assert result.status == "pending"

    @pytest.mark.usefixtures("clean_redis")
    def test_get_next_task(self, messaging):
        """Test retrieving task from queue."""
        # Publish a task
        task_id = messaging.publish_task(
            domain="test-domain",
            description="Test task",
            source="test",
        )

        # Get the task
        task = messaging.get_next_task("test-domain")

        assert task is not None
        assert task.task_id == task_id
        assert task.payload["description"] == "Test task"

        # Verify queue is empty
        assert messaging.get_queue_length("test-domain") == 0

        # Verify result updated to in_progress
        result = messaging.get_result(task_id)
        assert result.status == "in_progress"

    @pytest.mark.usefixtures("clean_redis")
    def test_get_next_task_empty_queue(self, messaging):
        """Test getting task from empty queue."""
        task = messaging.get_next_task("test-domain")
        assert task is None

    @pytest.mark.usefixtures("clean_redis")
    def test_publish_result(self, messaging):
        """Test publishing task result."""
        # Publish a task first
        task_id = messaging.publish_task(
            domain="test-domain",
            description="Test",
            source="test",
        )

        # Publish result
        messaging.publish_result(
            task_id=task_id,
            output={"files_created": ["new_file.py"]},
            status="completed",
        )

        # Verify result
        result = messaging.get_result(task_id)
        assert result.status == "completed"
        assert result.output["files_created"] == ["new_file.py"]
        assert result.completed_at is not None

    @pytest.mark.usefixtures("clean_redis")
    def test_publish_result_failed(self, messaging):
        """Test publishing failed result."""
        task_id = messaging.publish_task(
            domain="test-domain",
            description="Test",
            source="test",
        )

        messaging.publish_result(
            task_id=task_id,
            output={},
            status="failed",
            error="Task execution failed",
        )

        result = messaging.get_result(task_id)
        assert result.status == "failed"
        assert result.error == "Task execution failed"

    @pytest.mark.usefixtures("clean_redis")
    def test_task_fifo_order(self, messaging):
        """Test that tasks are processed in FIFO order."""
        # Publish multiple tasks
        task_ids = []
        for i in range(3):
            task_id = messaging.publish_task(
                domain="test-domain",
                description=f"Task {i}",
                source="test",
            )
            task_ids.append(task_id)

        # Retrieve tasks and verify order
        for i in range(3):
            task = messaging.get_next_task("test-domain")
            assert task.task_id == task_ids[i]
            assert task.payload["description"] == f"Task {i}"

    @pytest.mark.usefixtures("clean_redis")
    def test_add_and_get_logs(self, messaging):
        """Test adding and retrieving task logs."""
        task_id = messaging.publish_task(
            domain="test-domain",
            description="Test",
            source="test",
        )

        messaging.add_log(task_id, "Starting task")
        messaging.add_log(task_id, "Processing step 1")
        messaging.add_log(task_id, "Task completed")

        logs = messaging.get_logs(task_id)
        assert len(logs) == 3
        assert "Starting task" in logs[0]
        assert "Processing step 1" in logs[1]
        assert "Task completed" in logs[2]

    @pytest.mark.usefixtures("clean_redis")
    def test_wait_for_result(self, messaging):
        """Test waiting for task result."""
        task_id = messaging.publish_task(
            domain="test-domain",
            description="Test",
            source="test",
        )

        # Simulate async result publication
        def publish_later():
            time.sleep(0.5)
            messaging.publish_result(
                task_id=task_id,
                output={"done": True},
                status="completed",
            )

        thread = Thread(target=publish_later)
        thread.start()

        # Wait for result
        result = messaging.wait_for_result(task_id, timeout=5, poll_interval=0.2)

        assert result is not None
        assert result.status == "completed"
        assert result.output["done"] is True

        thread.join()

    @pytest.mark.usefixtures("clean_redis")
    def test_wait_for_result_timeout(self, messaging):
        """Test timeout when waiting for result."""
        task_id = messaging.publish_task(
            domain="test-domain",
            description="Test",
            source="test",
        )

        # Wait with short timeout (result never published as completed)
        result = messaging.wait_for_result(task_id, timeout=1, poll_interval=0.2)

        # Should return None on timeout (task never completed)
        assert result is None

        # But the task should still exist with pending status
        stored_result = messaging.get_result(task_id)
        assert stored_result is not None
        assert stored_result.status == "pending"


class TestAgentMessagingPubSub:
    """Tests for pub/sub functionality."""

    @pytest.mark.usefixtures("clean_redis")
    def test_publish_to_channel(self, messaging):
        """Test publishing to a channel."""
        count = messaging.publish(
            channel="test-channel",
            message={"event": "test", "data": {"key": "value"}},
        )
        # Returns 0 if no subscribers (expected in this test)
        assert count >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
