#!/usr/bin/env python3
"""
Domain Runner - Listens for tasks on a domain queue and processes them with Claude.

This script runs in domain orchestrator containers, listening for tasks
published by the main orchestrator and executing them via Claude Code CLI.
"""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

# Ensure lib is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from messaging import AgentMessaging, TaskMessage
from registry import AgentRegistry


class DomainRunner:
    """Runs a domain orchestrator that processes tasks from Redis queue."""

    def __init__(self):
        self.domain_type = os.environ.get("DOMAIN_TYPE", "unknown")
        self.agent_id = os.environ.get("AGENT_ID", f"{self.domain_type}-runner")
        self.messaging = AgentMessaging()
        self.registry = AgentRegistry()
        self.running = True
        self.current_task = None

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signals gracefully."""
        self.log("Received shutdown signal, finishing current task...")
        self.running = False

    def log(self, message: str):
        """Log a message with timestamp."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] [{self.domain_type}] {message}", flush=True)

    def run(self):
        """Main run loop - listen for and process tasks."""
        self.log(f"Starting domain runner for: {self.domain_type}")
        self.log(f"Agent ID: {self.agent_id}")
        self.log(f"Listening on queue: tasks:pending:{self.domain_type}")

        while self.running:
            try:
                # Wait for next task (blocking with timeout)
                task = self.messaging.get_next_task(self.domain_type, timeout=5)

                if task:
                    self.process_task(task)
                else:
                    # No task available, send heartbeat
                    self.registry.heartbeat(self.agent_id)

            except KeyboardInterrupt:
                self.log("Interrupted, shutting down...")
                break
            except Exception as e:
                self.log(f"Error in main loop: {e}")
                time.sleep(1)

        self.log("Domain runner stopped")

    def process_task(self, task: TaskMessage):
        """Process a single task by running Claude Code."""
        self.current_task = task
        task_id = task.task_id
        description = task.payload.get("description", "No description provided")
        requirements = task.payload.get("requirements", [])
        context = task.payload.get("context", {})

        self.log(f"Processing task: {task_id}")
        self.log(f"Description: {description}")
        self.messaging.add_log(task_id, f"Task received by {self.agent_id}")

        try:
            # Build prompt for Claude
            prompt = self._build_prompt(description, requirements, context)

            # Run Claude Code
            self.messaging.add_log(task_id, "Starting Claude Code execution")
            result = self._run_claude(prompt, task_id)

            # Publish result
            if result["success"]:
                self.messaging.publish_result(
                    task_id=task_id,
                    output={
                        "stdout": result["stdout"],
                        "files_created": result.get("files_created", []),
                        "files_modified": result.get("files_modified", []),
                    },
                    status="completed",
                )
                self.log(f"Task {task_id} completed successfully")
            else:
                self.messaging.publish_result(
                    task_id=task_id,
                    output={"stdout": result["stdout"]},
                    status="failed",
                    error=result["error"],
                )
                self.log(f"Task {task_id} failed: {result['error']}")

        except Exception as e:
            self.log(f"Task {task_id} error: {e}")
            self.messaging.publish_result(
                task_id=task_id,
                output={},
                status="failed",
                error=str(e),
            )

        finally:
            # Remove from active queue
            self.messaging.complete_task(self.domain_type, task)
            self.current_task = None

    def _build_prompt(
        self, description: str, requirements: list, context: dict
    ) -> str:
        """Build a prompt for Claude Code from task details."""
        prompt_parts = [
            f"You are a {self.domain_type} domain specialist.",
            "",
            "## Task",
            description,
        ]

        if requirements:
            prompt_parts.append("")
            prompt_parts.append("## Requirements")
            for req in requirements:
                prompt_parts.append(f"- {req}")

        if context:
            prompt_parts.append("")
            prompt_parts.append("## Context")
            prompt_parts.append(json.dumps(context, indent=2))

        prompt_parts.extend([
            "",
            "## Instructions",
            "1. Analyze the task and requirements",
            "2. Implement the solution in /workspace",
            "3. Create or modify files as needed",
            "4. When done, output a JSON summary of what was created/modified",
            "",
            "Work in /workspace directory. Be thorough and complete the task fully.",
        ])

        return "\n".join(prompt_parts)

    def _run_claude(self, prompt: str, task_id: str) -> dict:
        """Run Claude Code CLI with the given prompt."""
        result = {
            "success": False,
            "stdout": "",
            "error": None,
            "files_created": [],
            "files_modified": [],
        }

        try:
            # Run claude with the prompt via stdin
            process = subprocess.Popen(
                ["claude", "--dangerously-skip-permissions", "-p", prompt],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd="/workspace",
                env={**os.environ, "CLAUDE_CODE_ENTRYPOINT": "domain-runner"},
            )

            # Stream output and log it
            output_lines = []
            for line in iter(process.stdout.readline, b""):
                decoded = line.decode("utf-8", errors="replace")
                output_lines.append(decoded)
                # Log periodically to Redis
                if len(output_lines) % 10 == 0:
                    self.messaging.add_log(task_id, f"... {len(output_lines)} lines processed")

            process.wait()
            result["stdout"] = "".join(output_lines)

            if process.returncode == 0:
                result["success"] = True
            else:
                result["error"] = f"Claude exited with code {process.returncode}"

        except FileNotFoundError:
            result["error"] = "Claude CLI not found"
        except Exception as e:
            result["error"] = str(e)

        return result


def main():
    """Entry point for domain runner."""
    # Validate environment
    domain_type = os.environ.get("DOMAIN_TYPE")
    if not domain_type:
        print("ERROR: DOMAIN_TYPE environment variable not set", file=sys.stderr)
        sys.exit(1)

    # Check Redis connection
    messaging = AgentMessaging()
    if not messaging.ping():
        print("ERROR: Cannot connect to Redis", file=sys.stderr)
        sys.exit(1)

    # Run the domain runner
    runner = DomainRunner()
    runner.run()


if __name__ == "__main__":
    main()
