import re
import subprocess
from pathlib import Path

# Anchored with `\b` so hex and base64 blobs in lockfiles cannot false-positive
# (e.g. a stray "a3b" inside a package hash in uv.lock must not trip this).
BANNED = re.compile(
    r"\b(a3b|a3better|a3bsafe|beijer|ddevsync\.pyz|A3BETTER_CREDENTIALS)",
    re.IGNORECASE,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [REPO_ROOT / name for name in result.stdout.split("\0") if name]


def test_no_agency_specific_terms_remain() -> None:
    offenders: list[str] = []
    for path in _tracked_files():
        if path.name == "test_no_legacy_terms.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if BANNED.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}")
    assert offenders == [], "Agency-specific terms found:\n" + "\n".join(offenders)
