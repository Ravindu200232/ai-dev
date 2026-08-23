"""Feature updates receive two bounded unit-test repair rounds."""
from pathlib import Path

import server


def test_feature_update_has_exactly_two_unit_repair_rounds():
    actions = Path("server_modules/agent/feature/actions.py").read_text("utf-8")
    assert "FEATURE_UNIT_REPAIR_ROUNDS = 2" in actions
    call = actions.split("def _feature_tests", 1)[1].split("UNDO_DIR", 1)[0]
    assert "max_repair_rounds=FEATURE_UNIT_REPAIR_ROUNDS" in call
    assert server._unit_repair_budget(2) == (2, 2)


def test_shared_builder_unit_stage_keeps_its_existing_budget():
    assert server._unit_repair_budget(None) == (
        server.MAX_QA_FIX,
        server.QA_ROUND_CEILING,
    )


def test_invalid_feature_budget_falls_back_safely_and_large_values_are_capped():
    assert server._unit_repair_budget("invalid") == (
        server.MAX_QA_FIX,
        server.MAX_QA_FIX,
    )
    assert server._unit_repair_budget(999) == (
        server.MAX_QA_FIX,
        server.QA_ROUND_CEILING,
    )
