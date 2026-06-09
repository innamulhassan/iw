"""DBOps specialist — db-failure worker.

Thin entrypoint; all logic is the shared SpecialistAgent (agents/specialist.py).
Config (model, tools, prompt, A2A binding) is agents/configs/dbops.yaml.

    uv run python -m lunasre.agents.dbops --serve              # A2A server :8003
    uv run python -m lunasre.agents.dbops --debug-investigate  # one-shot, no HTTP
"""

from lunasre.agents.specialist import specialist_main

if __name__ == "__main__":
    specialist_main("dbops-agent")
