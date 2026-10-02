"""The one definition of where the integration project lives.

Imported by `conftest.py` (which exports it to `docker compose`) and by the
test module (whose `project` fixture populates it). A module rather than a
conftest attribute so neither side depends on pytest's sys.path handling.
"""

from pathlib import Path

PROJECT_DIR = (Path(__file__).parent / ".project").resolve()
