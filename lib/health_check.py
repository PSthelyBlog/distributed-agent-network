"""
Health check script for container health monitoring.

Used by Docker HEALTHCHECK to verify agent is operational.
"""

import os
import sys

from messaging import AgentMessaging
from registry import AgentRegistry


def check_health() -> bool:
    """
    Perform health checks.

    Checks:
    1. Redis connectivity
    2. Agent registration (if registered)

    Returns:
        True if healthy, False otherwise
    """
    try:
        # Check Redis connection
        messaging = AgentMessaging()
        if not messaging.ping():
            print("UNHEALTHY: Redis connection failed")
            return False

        # Check agent registration if we have an ID
        agent_id = os.environ.get("AGENT_ID", os.environ.get("HOSTNAME"))
        if agent_id:
            registry = AgentRegistry()
            agent = registry.get_agent(agent_id)

            if agent is None:
                print(f"UNHEALTHY: Agent {agent_id} not registered")
                return False

            if agent.status == "stopping":
                print(f"UNHEALTHY: Agent {agent_id} is stopping")
                return False

        print("HEALTHY")
        return True

    except Exception as e:
        print(f"UNHEALTHY: {e}")
        return False


if __name__ == "__main__":
    healthy = check_health()
    sys.exit(0 if healthy else 1)
