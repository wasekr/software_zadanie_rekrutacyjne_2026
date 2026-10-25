import time
from state import RocketState
from interlocks import SafetyInterlocks


def test_cannot_open_fuel_intake_before_oxidizer_full():
    state = RocketState()
    interlocks = SafetyInterlocks(state)

    state.update_sensor("oxidizer_level", 50.0)
    safe, msg = interlocks.can_open_fuel_intake()
    assert safe is False
    assert "Error" in msg

    state.update_sensor("oxidizer_level", 100.0)
    safe, msg = interlocks.can_open_fuel_intake()
    assert safe is True
    assert msg == "OK"


def test_can_ignite_pressure_bounds():
    state = RocketState()
    interlocks = SafetyInterlocks(state)

    state.update_sensor("oxidizer_level", 100.0)
    state.update_sensor("fuel_level", 100.0)

    # Too low pressure (< 40 bar)
    state.update_sensor("oxidizer_pressure", 35.0)
    assert interlocks.can_ignite()[0] is False

    # Optimal pressure (55-65 bar)
    state.update_sensor("oxidizer_pressure", 58.0)
    assert interlocks.can_ignite()[0] is True

    # Too high pressure (> 64.5 bar)
    state.update_sensor("oxidizer_pressure", 66.0)
    assert interlocks.can_ignite()[0] is False


def test_parachute_interlocks_engine_and_velocity():
    state = RocketState()
    interlocks = SafetyInterlocks(state)

    # Blocked when fuel > 0 (engine still running)
    state.update_sensor("fuel_level", 15.0)
    assert interlocks.can_deploy_parachute()[0] is False

    # Blocked when fuel == 0, but velocity exceeds safe threshold (> 28 m/s)
    state.update_sensor("fuel_level", 0.0)
    state.velocity = -32.0
    assert interlocks.can_deploy_parachute()[0] is False

    # Allowed when fuel == 0 and velocity is safe
    state.velocity = -15.0
    assert interlocks.can_deploy_parachute()[0] is True


def test_velocity_and_apogee_calculation():
    state = RocketState()

    state.update_sensor("altitude", 100.0)
    time.sleep(0.05)
    state.update_sensor("altitude", 110.0)
    assert state.velocity > 0.0
    assert state.apogee_reached is False

    # Simulate drop of > 0.5m after reaching max_altitude (110.0m)
    time.sleep(0.05)
    state.update_sensor("altitude", 109.0)
    assert state.velocity < 0.0
    assert state.apogee_reached is True


def test_ack_nack_tracking():
    state = RocketState()
    state.update_command_response(0, True)
    state.update_command_response(1, False)

    assert state.command_responses[0] is True
    assert state.command_responses[1] is False
    assert state.ack_count == 1
    assert state.nack_count == 1