from ddev_sync.engine.registry import OPERATIONS
from ddev_sync.engine.steps import STEP_ORDER


def test_every_step_kind_has_an_operation() -> None:
    assert set(OPERATIONS) == set(STEP_ORDER)


def test_db_download_is_a_no_op_because_the_dump_is_streamed() -> None:
    from ddev_sync.engine.registry import NoOpOperation
    from ddev_sync.engine.steps import StepKind

    assert isinstance(OPERATIONS[StepKind.DB_DOWNLOAD], NoOpOperation)


def test_the_no_op_accepts_any_params_and_does_nothing() -> None:
    from ddev_sync.engine.registry import NoOpOperation

    NoOpOperation().execute(None, {"anything": True})  # type: ignore[arg-type]
