#!/usr/bin/env python3
"""Stable entrypoint for the modular AgentForge idea-file pipeline."""
from server_modules.agent.pipeline import *  # noqa: F401,F403
from server_modules.agent.pipeline.watcher import main


if __name__ == "__main__":
    main()
