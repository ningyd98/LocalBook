from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MODULE_PATH = Path(__file__).parent / "check-ruff-diff.py"
SPEC = spec_from_file_location("check_ruff_diff", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_added_line_ranges_track_insertions_and_deletions():
    patch = """diff --git a/server/sample.py b/server/sample.py
--- a/server/sample.py
+++ b/server/sample.py
@@ -1,3 +1,4 @@
@@ -8,2 +9,0 @@
@@ -12,0 +11,2 @@
"""
    assert MODULE.added_line_ranges(patch) == {"server/sample.py": [(1, 4), (11, 12)]}


def test_test_path_filter_matches_any_segment():
    assert MODULE.is_test_path("server/tests/helper.py")
    assert MODULE.is_test_path("server/reader/tests/test_api.py")
    assert not MODULE.is_test_path("server/reader/service.py")
