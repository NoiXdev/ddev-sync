"""100% coverage measures execution, not reachability.

`EnvRewriteOperation`, `S3SyncOperation` and the sanitize handler classes in
`operations/catalog.py` can each be fully exercised by their own unit tests
and still never run in production, if nothing ever dispatches to them. This
module asserts the thing coverage cannot: every concrete `Operation` and
every `SanitizeOperationHandler` *class* defined somewhere under
`ddev_sync.operations` is an instance actually present in `OPERATIONS`
(`engine/registry.py`) or `SANITIZE_OPERATIONS` (`operations/sanitize.py`) -
the two registries the engine dispatches through. Classes are discovered by
walking the package, not by hand-listing them, so the blind spot this closes
cannot reopen by someone adding a tenth operation and forgetting to add it
here too, the same way they forgot to add it to the real registry.
"""

import importlib
import inspect
import pkgutil
from collections.abc import Iterator

import ddev_sync.operations as operations_package
from ddev_sync.engine.registry import OPERATIONS
from ddev_sync.operations.sanitize import SANITIZE_OPERATIONS

# Fully qualified "module:ClassName" entries for a class that is deliberately
# *not* reachable from either registry. Keep this empty unless something
# genuinely needs to opt out - and if you add an entry, say why right here.
ALLOWED_UNREGISTERED: frozenset[str] = frozenset()


def _discovered_classes() -> Iterator[tuple[str, type]]:
    """Every class defined (not merely imported) in a module under `operations/`."""
    prefix = f"{operations_package.__name__}."
    for module_info in pkgutil.iter_modules(operations_package.__path__, prefix=prefix):
        module = importlib.import_module(module_info.name)
        for name, obj in vars(module).items():
            if not inspect.isclass(obj):
                continue
            if obj.__module__ != module.__name__:
                continue  # imported from elsewhere, not defined here
            if getattr(obj, "_is_protocol", False):
                continue  # `Operation` / `SanitizeOperationHandler` themselves
            yield f"{module.__name__}:{name}", obj


def _is_operation_shaped(cls: type) -> bool:
    return callable(getattr(cls, "execute", None))


def _is_sanitize_handler_shaped(cls: type) -> bool:
    return callable(getattr(cls, "run", None))


def test_every_operation_and_sanitize_handler_class_is_registered() -> None:
    registered_operations = {type(operation) for operation in OPERATIONS.values()}
    registered_handlers = {type(handler) for handler in SANITIZE_OPERATIONS.values()}

    unreachable = [
        qualname
        for qualname, cls in _discovered_classes()
        if qualname not in ALLOWED_UNREGISTERED
        and (_is_operation_shaped(cls) or _is_sanitize_handler_shaped(cls))
        and cls not in registered_operations
        and cls not in registered_handlers
    ]

    assert unreachable == [], (
        "the following classes under ddev_sync.operations implement Operation or "
        "SanitizeOperationHandler but are not reachable from OPERATIONS or "
        "SANITIZE_OPERATIONS, so nothing in production ever calls them: "
        f"{unreachable}. Either wire them in, or add them to ALLOWED_UNREGISTERED "
        "with a comment explaining why they deliberately opt out."
    )


def test_allowed_unregistered_entries_are_still_real_classes() -> None:
    """Guards the allowlist itself: an entry naming a class that was since
    deleted or renamed would silently stop excluding anything, rather than
    raising - the opposite of what an allowlist is for.
    """
    discovered = {qualname for qualname, _cls in _discovered_classes()}
    assert discovered >= ALLOWED_UNREGISTERED
