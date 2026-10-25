import time

class RocketState:
    """
        Central data store for telemetry and derived physical parameters.

        Split the Finite State Machine from network latency by providing
        instant access to the latest sensor readings, calculated vertical velocity (difference in altitude / difference in time),
        and apogee status.
        not in main while true loop with cm.receive() (to prevent blocking it) if network has latency (e.g throws TransportTimeoutError) the whole
        logic would be frozen and now we enabled asynchronic frames receiving in the background independently of network
        """
    def __init__(self):
        # stores current sensor readings for FSM logic
        self.telemetry = {
            'fuel_level' : 0.0,
            'oxidizer_level' : 0.0,
            'altitude' : 0.0,
            'oxidizer_pressure' : 0.0,
            'angle' : 0.0
        }
        self.velocity = 0.0
        self.max_altitude = 0.0
        self.last_time = time.perf_counter()    #high precision system time
        self.apogee_reached = False

        # for keeping ACE / NACK responses
        # format {device_id: True/False}
        self.command_responses = {}


    def update_sensor(self, sensor_name: str, value: float):
        """
        Updates sensor telemetry and calculates real-time flight metrics.

        Args:
            sensor_name: (e.g., 'altitude').
            value: The current reading from the sensor.
        """
        if sensor_name == "altitude":
            current_time = time.perf_counter()
            dt = current_time - self.last_time

            # Prevent division by zero if multiple frames arrive in the same tick
            if dt > 0:
                self.velocity = (value - self.telemetry['altitude']) / dt

            if value > self.max_altitude:
                self.max_altitude = value

            # Apogee verification: we require 0.5m drop and negative velocity
            # -0.5m is epsilon of error to avoid premature parachute opening
            elif value < self.max_altitude - 0.5 and self.velocity < 0.0:
                self.apogee_reached = True

            self.last_time = current_time

        self.telemetry[sensor_name] = value

    def update_command_response(self, device_id: int, success: bool):
        """
        Register if a command were finished as a success (ACE) or failure (NACK)
        """
        self.command_responses[device_id] = success
