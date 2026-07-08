"""Compat shim — use arabesque.execution.live instead."""
from arabesque.execution.live import *  # noqa: F401, F403

if __name__ == "__main__":
    from arabesque.execution.live import main
    main()
