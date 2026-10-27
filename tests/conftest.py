import sys
from pathlib import Path

# Make `src.open_llm_vtuber` importable the same way run_server.py does.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
