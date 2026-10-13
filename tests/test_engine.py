"""Validate fault detection, controlled state and independent command evidence."""

import pytest
from devicetestlab import engine
from devicetestlab.device import VirtualDevice


def without_wall_time(results):
    return [
        {key: value for key, value in result.items() if key != "duration_ms"} for result in results
    ]


def test_healthy_suite_completes_and_every_command_agrees_with_oracle():
    results = engine.run_suite("healthy", 42)
    assert len(results) >= 12
    assert len({result["id"] for result in results}) == len(engine.TESTS)
    assert {result["status"] for result in results} == {"pass"}
    assert all(result["trace"] for result in results)
    assert all(
        step["response"] == step["expected"] for result in results for step in result["trace"]
    )


@pytest.mark.parametrize("scenario", ["corrupt_read", "ignored_write", "stuck_state", "timeout"])
def test_each_fault_has_detected_failure_and_unaffected_checks(scenario):
    results = engine.run_suite(scenario, 3)
    assert any(result["status"] == "fail" for result in results)
    assert any(result["status"] == "pass" for result in results)
    assert all(result["status"] != "error" for result in results)
    assert len(results) == len(engine.TESTS)


def test_a_write_acknowledgement_is_not_proof_that_data_was_stored():
    result = engine.execute_test("single_byte", "ignored_write", 3)
    assert result["trace"][0]["response"] == {"ok": True}
    assert result["status"] == "fail"
    assert result["expected"] == {"ok": True, "value": 90}
    assert result["actual"] == {"ok": True, "value": 0}


def test_reset_state_is_checked_separately_from_clearing_data():
    assert engine.execute_test("reset_clears", "stuck_state", 3)["status"] == "pass"
    result = engine.execute_test("reset_state", "stuck_state", 3)
    assert result["status"] == "fail"
    assert result["actual"]["state"] == "busy"


def test_timeout_is_a_simulated_device_observation_not_a_runner_exception():
    result = engine.execute_test("single_byte", "timeout", 3)
    assert result["status"] == "fail"
    assert result["actual"]["error"] == "TIMEOUT"
    assert result["actual"]["modeled_response_ms"] > result["actual"]["deadline_ms"]
    assert result["modeled_cost_ms"] == 4 + result["actual"]["deadline_ms"]
    assert result["duration_ms"] >= 0


@pytest.mark.parametrize("scenario", [item["id"] for item in engine.SCENARIOS])
def test_reordering_preserves_per_test_outcomes_and_total_modeled_cost(scenario):
    catalog_order = engine.run_suite(scenario, 19)
    reverse_order = engine.run_suite(scenario, 19, [item["id"] for item in reversed(engine.TESTS)])

    def normalize(items):
        return sorted(without_wall_time(items), key=lambda result: result["id"])

    assert normalize(catalog_order) == normalize(reverse_order)
    assert sum(item["modeled_cost_ms"] for item in catalog_order) == sum(
        item["modeled_cost_ms"] for item in reverse_order
    )


def test_seed_is_reproducible_and_changes_the_target_address():
    first = engine.run_suite("ignored_write", 3)
    assert without_wall_time(first) == without_wall_time(engine.run_suite("ignored_write", 3))
    assert engine.execute_test("single_byte", "ignored_write", 4)["status"] == "pass"


@pytest.mark.parametrize("order", [[], ["initial_status"] * 14, ["missing"]])
def test_partial_duplicate_or_unknown_test_orders_are_rejected(order):
    with pytest.raises(ValueError, match="every catalog test exactly once"):
        engine.run_suite("healthy", 0, order)


def test_runner_exceptions_do_not_become_device_failures_or_skip_the_suite(monkeypatch):
    original = VirtualDevice.command

    def damaged_transport(self, request):
        if request["op"] == "STATUS":
            raise OSError("Injected runner transport problem")
        return original(self, request)

    monkeypatch.setattr(VirtualDevice, "command", damaged_transport)
    callbacks = []
    results = engine.run_suite("healthy", 0, on_result=callbacks.append)
    assert results[0]["status"] == "error"
    assert not any(result["status"] == "fail" for result in results)
    assert len(callbacks) == len(results) == len(engine.TESTS)
    assert any(result["status"] == "pass" for result in results)


def test_invalid_commands_leave_existing_data_untouched():
    device = VirtualDevice()
    device.command({"op": "WRITE", "address": 0, "value": 73})
    assert device.command({"op": "WRITE", "address": 0, "value": 256})["ok"] is False
    assert device.command({"op": "WRITE", "address": True, "value": 0})["ok"] is False
    assert device.command({"op": "READ", "address": 0}) == {"ok": True, "value": 73}


def test_snapshot_contains_the_executed_oracle_and_is_an_independent_copy():
    snapshot = engine.test_definition_snapshot()
    assert len(snapshot["sha256"]) == 64
    assert set(snapshot["steps"]) == {item["id"] for item in engine.TESTS}
    snapshot["steps"]["initial_status"][0]["expected"]["state"] = "modified"
    assert engine.execute_test("initial_status", "healthy", 0)["status"] == "pass"
