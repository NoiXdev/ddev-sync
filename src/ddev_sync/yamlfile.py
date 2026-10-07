"""Read a YAML document, refusing one that defines the same key twice.

PyYAML's `safe_load` keeps the **last** of two equal mapping keys and says
nothing. A configuration with two `targets:` blocks - or two `database:`,
`sanitize:` or `files:` blocks - therefore reads back as though half of it had
never been written, which is the one failure mode this engine otherwise works
hard against: every ambiguity in the merge is reported together with the layer
it came from.

Measured before this existed: a file with a flow-style `targets: {...}` and a
block `targets:` below it loaded as one target, the first silently gone.

The keys are compared in one pass before the base constructor runs, so the
first repeat can be named with the line it recurs on. A key that is not
hashable - `? [a, b]` - cannot be compared and is left to the base, which
rejects it with its own error; checking it here would raise `TypeError` from
the set lookup instead.
"""

from collections.abc import Hashable
from typing import Any

import yaml


class _NoDuplicateKeys(yaml.SafeLoader):
    """`SafeLoader`, except that a repeated mapping key is an error."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        """Refuse a mapping that names a key twice, then build it as usual."""
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            if not isinstance(key, Hashable):
                break
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    "while reading a mapping",
                    node.start_mark,
                    f"found the key {key!r} more than once",
                    key_node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def load_yaml(text: str) -> Any:
    """`yaml.safe_load`, but a document defining a key twice is an error.

    Raises `yaml.YAMLError`, which every caller already wraps with the name of
    the file it was reading.
    """
    return yaml.load(text, Loader=_NoDuplicateKeys)
