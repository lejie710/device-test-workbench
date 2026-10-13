"""A bounded in-process protocol device, independent of the test assertions.

This models commands and injected behaviour, not electronic timing or physical memory.
The transport deadline uses simulated response latency; it never waits in real time.
"""

from __future__ import annotations

from typing import Any, TypeGuard


class VirtualDevice:
    CAPACITY = 16
    DEADLINE_MS = 50

    def __init__(self, scenario: str = "healthy", seed: int = 0):
        self.scenario = scenario
        self.seed = seed
        self.memory = [0] * self.CAPACITY
        self.state = "ready"
        # Audit ground truth belongs to the device, never to ordering-model inputs.
        self.fault_address = seed % self.CAPACITY

    @staticmethod
    def _integer(value: Any) -> TypeGuard[int]:
        return isinstance(value, int) and not isinstance(value, bool)

    def command(self, request: dict[str, Any]) -> dict[str, Any]:
        """Return the documented JSON response, including modeled deadline failures."""
        operation = request.get("op")
        if operation == "STATUS":
            return {"ok": True, "state": self.state, "capacity": self.CAPACITY}
        if operation == "RESET":
            self.memory = [0] * self.CAPACITY
            self.state = "busy" if self.scenario == "stuck_state" else "ready"
            return {"ok": True}
        if operation not in {"READ", "WRITE"}:
            return {"ok": False, "error": "UNSUPPORTED_COMMAND"}
        address = request.get("address")
        if not self._integer(address) or not 0 <= address < self.CAPACITY:
            return {"ok": False, "error": "INVALID_ADDRESS"}
        if operation == "WRITE":
            value = request.get("value")
            if not self._integer(value) or not 0 <= value <= 255:
                return {"ok": False, "error": "INVALID_VALUE"}
            if not (self.scenario == "ignored_write" and address == self.fault_address):
                self.memory[address] = value
            self.state = "dirty"
            return {"ok": True}
        response_latency_ms = 120 if self.scenario == "timeout" else 5
        if response_latency_ms > self.DEADLINE_MS:
            return {
                "ok": False,
                "error": "TIMEOUT",
                "deadline_ms": self.DEADLINE_MS,
                "modeled_response_ms": response_latency_ms,
            }
        value = self.memory[address]
        if self.scenario == "corrupt_read" and address == self.fault_address:
            value ^= 1
        return {"ok": True, "value": value}
