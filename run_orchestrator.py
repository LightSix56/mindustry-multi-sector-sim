"""Launcher for Mindustry Multi-Sector Simulator Orchestrator."""
import os
import sys
from pathlib import Path

# Add project root to sys.path
root = Path(__file__).resolve().parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from mindustry_sim.server import run_orchestrator

if __name__ == "__main__":
    host = os.environ.get("SIM_HOST", "0.0.0.0")
    port = int(os.environ.get("SIM_PORT", "8765"))
    run_orchestrator(host=host, port=port)

