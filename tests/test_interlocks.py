import time
from interlocks import SafetyInterlocks
from state import RocketState


def test_fuel_intake_blocked_when_oxidizer_empty():
    state = RocketState()
    interlocks = SafetyInterlocks(state)

    state.update_sensor("oxidizer_level", 50.0)
    allowed, msg = interlocks.can_open_fuel_intake()
    assert not allowed
    assert "INTERLOCK BLOCKED" in msg

    state.update_sensor("oxidizer_level", 100.0)
    allowed, _ = interlocks.can_open_fuel_intake()
    assert allowed


def test_ignition_interlocks_pressure_and_intakes():
    state = RocketState()
    interlocks = SafetyInterlocks(state)

    # Low pressure blocked
    state.update_sensor("oxidizer_pressure", 35.0)
    assert not interlocks.can_ignite()[0]

    # Optimal pressure allowed when intakes are closed
    state.update_sensor("oxidizer_pressure", 58.0)
    assert interlocks.can_ignite()[0]

    # Blocked if intake valve left open
    state.update_servo_state("fuel_intake", True)
    assert not interlocks.can_ignite()[0]


def test_parachute_blocked_at_high_velocity_or_engine_on():
    state = RocketState()
    interlocks = SafetyInterlocks(state)

    # Engine still burning fuel
    state.update_sensor("fuel_level", 25.0)
    assert not interlocks.can_deploy_parachute()[0]

    # Fuel depleted, but speed > 28 m/s
    state.update_sensor("fuel_level", 0.0)
    state.velocity = -35.0
    assert not interlocks.can_deploy_parachute()[0]

    # Safe speed after burnout
    state.velocity = -12.0
    assert interlocks.can_deploy_parachute()[0]


def test_apogee_and_velocity_calculation():
    state = RocketState()
    state.update_sensor("altitude", 100.0)
    time.sleep(0.05)
    state.update_sensor("altitude", 110.0)
    assert state.velocity > 0
    assert not state.apogee_reached

    time.sleep(0.05)
    state.update_sensor("altitude", 108.0)
    assert state.velocity < 0
    assert state.apogee_reached