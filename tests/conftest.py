import os
from pathlib import Path

os.environ["TIMELINE_DATA"] = str(Path(__file__).parent / ".data")
os.environ["OPTIONS_FILE"] = str(Path(__file__).parent / "fixtures" / "options.json")

