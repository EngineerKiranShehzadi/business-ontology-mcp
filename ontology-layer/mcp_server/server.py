"""MCP stdio entry point under the requested ontology-layer layout."""

import runpy
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROOT_SERVER = PROJECT_ROOT / "server.py"
sys.path.insert(0, str(PROJECT_ROOT))


if __name__ == "__main__":
    runpy.run_path(str(ROOT_SERVER), run_name="__main__")
