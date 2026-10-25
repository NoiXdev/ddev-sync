from pathlib import Path

import pytest

from ddev_sync.composer import requires


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('{"require-dev": {"deployer/deployer": "^7.0"}}', True),
        ('{"require": {"deployer/deployer": "^7.0"}}', True),
        ('{"require-dev": {"phpunit/phpunit": "^11"}}', False),
        ('{"require-dev": ["deployer/deployer"]}', False),
        ("[]", False),
        ("not json", False),
    ],
)
def test_requires_reads_require_and_require_dev(
    content: str, expected: bool, tmp_path: Path
) -> None:
    composer_json = tmp_path / "composer.json"
    composer_json.write_text(content, encoding="utf-8")
    assert requires(composer_json, "deployer/deployer") is expected


def test_requires_is_false_without_a_composer_json(tmp_path: Path) -> None:
    assert requires(tmp_path / "composer.json", "deployer/deployer") is False
