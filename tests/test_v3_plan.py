import pytest

from repofix.harness.state import RunState
from repofix.harness.tools.plan import update_plan


def test_plan_update_and_invalid_does_not_mutate():
    state = RunState()
    update_plan(
        state, [{"step": "read", "status": "completed"}, {"step": "edit", "status": "in_progress"}]
    )
    before = list(state.plan)
    for invalid in [
        None,
        [{}],
        [{"step": "", "status": "pending"}],
        [{"step": "a", "status": "unknown"}],
        [{"step": "a", "status": "in_progress"}] * 2,
    ]:
        with pytest.raises(ValueError):
            update_plan(state, invalid)
        assert state.plan == before
    update_plan(state, [])
    assert state.plan == []
