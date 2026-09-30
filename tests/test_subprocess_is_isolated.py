from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src" / "ddev_sync"
ALLOWED = {"transport/runner.py"}


def test_only_the_runner_imports_subprocess() -> None:
    offenders: list[str] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        relative = path.relative_to(SOURCE_ROOT).as_posix()
        if relative in ALLOWED:
            continue
        text = path.read_text(encoding="utf-8")
        if "import subprocess" in text:
            offenders.append(relative)
    assert offenders == [], (
        "subprocess must only be imported by transport/runner.py, found in: " + ", ".join(offenders)
    )
