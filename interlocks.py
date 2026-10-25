from state import RocketState


class SafetyInterlocks:
    """
    Hardware safety guardrails to prevent catastrophic commands executions
    validates intended operations against current RocketState
    (we check physical state of rocket independently of MissionState enum states - which may
    mistakenly indicate other state than reality)
    """
    def __init__(self, state: RocketState):
        self.state = state

    def can_open_fuel_intake(self):
        # tolerance for numeric errors
        if self.state.telemetry['oxidizer_level'] < 99.5:
            return (False, "Error: filling fuel before having oxidizer filled")

        return (True, "OK")

    def can_deploy_parachute(self):
        if self.state.telemetry['fuel_level'] > 0:
            return (False, "Error: trying to deploy parachute while the engine is still on")

        if abs(self.state.velocity) > 28.0:
            return (False, f"Error: velocity {self.state.velocity} exceeds 30 (including numeric error)")

        return (True, "OK")

    def can_ignite(self):
        if self.state.telemetry['oxidizer_level'] < 99.5 or self.state.telemetry['fuel_level'] < 99.5:
            return (False, "Error: Tanks are not fully loaded for ignition")

        ox_pressure = self.state.telemetry['oxidizer_pressure']

        if ox_pressure < 40.0:
            return (False, f"Error: Oxidizer pressure is too low: {ox_pressure:.1f}")
        if ox_pressure > 64.5:
            return (False, f"Error: Oxidizer pressure is too high for engine to work: {ox_pressure:.1f}")

        return (True, "OK")