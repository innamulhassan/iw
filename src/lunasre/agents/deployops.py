"""DeployOps specialist — deploy-regression worker.

Thin entrypoint; logic in agents/specialist.py, config in agents/configs/deployops.yaml.

    uv run python -m lunasre.agents.deployops --serve              # A2A server :8005
    uv run python -m lunasre.agents.deployops --debug-investigate
"""

from lunasre.agents.specialist import specialist_main

if __name__ == "__main__":
    specialist_main("deployops-agent")
