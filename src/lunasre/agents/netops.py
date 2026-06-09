"""NetOps specialist — network-partition worker.

Thin entrypoint; logic in agents/specialist.py, config in agents/configs/netops.yaml.

    uv run python -m lunasre.agents.netops --serve              # A2A server :8004
    uv run python -m lunasre.agents.netops --debug-investigate
"""

from lunasre.agents.specialist import specialist_main

if __name__ == "__main__":
    specialist_main("netops-agent")
