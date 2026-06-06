"""Allow `python -m devagent` as an entry point (robust across environments / PowerShell)."""
from devagent.cli import app

if __name__ == "__main__":
    app()
