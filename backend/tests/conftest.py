import os
from pathlib import Path

SPECS = Path(os.environ.get("SPECS_DIR", Path(__file__).resolve().parents[2] / "specs"))
