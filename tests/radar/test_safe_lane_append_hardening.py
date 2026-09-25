[Reading 30 lines from start (total: 30 lines, 0 remaining)]

import pytest

from radar.safe_lane_append import SafeLaneAppender

class FakeGit:
    def __init__(self, result):
        self.result = result

    def is_ancestor(self, candidate_sha, head_sha):
        return self.result

@pytest.mark.parametrize(
    ("backend_result", "expected"),
    [
        (True, True),
        (False, False),
        ("false", None),
        (1, None),
        (None, None),
    ],
)
def test_candidate_reachability_requires_exact_boolean_backend_result(
    backend_result, expected
):
    appender = SafeLaneAppender(
        control_source=None,
        history_source=None,
        git_backend=FakeGit(backend_result),
    )
    assert appender._candidate_reachability("a" * 40, "b" * 40) is expected