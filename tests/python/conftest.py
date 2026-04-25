import sys
from pathlib import Path

# Make src/workers importable without installation
sys.path.insert(0, str(Path(__file__).parents[2] / "src" / "workers"))
