import yaml
from communication_library.communication_manager import CommunicationManager
from communication_library.tcp_transport import TcpSettings
from communication_library.transport import TransportType
from communication_library.frame import Frame
from communication_library import ids
from communication_library.exceptions import TransportTimeoutError, \
    UnregisteredCallbackError
from enum import Enum, auto

class MissionState(Enum):
    INIT = auto() # = 1 because automatically assign ascending values
    FILLING_OXIDIZER = auto()
    FILLING_FUEL = auto()
    READY = auto()
    PRESSURIZING = auto()
    IN_FLIGHT = auto()
    RECOVERY = auto()
    ABORT = auto() # emergency state


class MissionControl:
    def __init__(self, config_path="simulator_config.yaml"):
        with open(config_path, "r") as f:
            self.config = yaml.safe_load(f)

        # stores current sensor readings for FSM logic
        self.telemetry = {}

        # initialization of Finite State Machine
        self.state = MissionState.INIT

        self.max_altitude = 0.0

        self.cm = CommunicationManager()
        self.cm.change_transport_type(TransportType.TCP)
        self.cm.connect(TcpSettings("127.0.0.1", 3000))

        self.setup_telemetry_listeners()

    def on_sensor_feed(self, frame: Frame):
        """Callback executed automatically after receiving right sensor telemetry"""
        sensor_id = frame.device_id

        for sensor_name, sensor_data in self.config['devices']['sensor'].items():
            if sensor_data['device_id'] == sensor_id:
                self.telemetry[sensor_name] = frame.payload[0]
                print(f"[{sensor_name.upper()}]: {self.telemetry[sensor_name]}")
                break


    def setup_telemetry_listeners(self):
        """
        Registers listener callbacks to filter and catch specific sensor frames from sensors (other "noise" are filtered)
        (unnecessary packages received from the network are ignored,
         ACK confirmations are being let through in exception section
         and sensor measurements packages are recognised and make it immediately to telemetry dictionary (updating it)
        """
        for sensor_name, sensor_data in self.config['devices']['sensor'].items():
            data_type = sensor_data['data_type'].upper()
            enum_data_type = ids.DataTypeID[data_type]

            pattern_frame = Frame(
                destination = ids.BoardID.SOFTWARE,
                priority= ids.PriorityID.LOW,
                action = ids.ActionID.FEED,
                source = ids.BoardID.ROCKET,
                device_type = ids.DeviceID.SENSOR,
                device_id = sensor_data['device_id'],
                data_type = enum_data_type,
                operation=ids.OperationID.SENSOR.value.READ,
                payload=()
            )

            self.cm.register_callback(self.on_sensor_feed, pattern_frame)
            self.telemetry[sensor_name] = 0.0

    def set_servo_position(self, servo_name: str, open_value: bool):
        """
        Sends a high-priority service command to change a servo's position.

        :param servo_name: Name of the servo in the YAML config.
        :param open_value: True to open the valve, False to close it.
        """

        servo_data = self.config['devices']['servo'][servo_name]

        # 0 may be interpreted as angle where fuel pipe is fully open and 100 as angle where it's totally closed
        # rather than 0% or 100% of flow
        target_position = servo_data['open_pos'] if open_value else servo_data['closed_pos']

        # now we use service because it's a command for rocket
        command_frame = Frame(
            destination = ids.BoardID.ROCKET,
            priority= ids.PriorityID.HIGH,
            action = ids.ActionID.SERVICE,
            source = ids.BoardID.SOFTWARE,
            device_type = ids.DeviceID.SERVO,
            device_id = servo_data['device_id'],
            data_type = ids.DataTypeID.INT16,
            operation = ids.OperationID.SERVO.value.POSITION,
            payload = (target_position,)
        )

        # enque
        self.cm.push(command_frame)

        # deque
        self.cm.send()

        print(f">>> Sent a command setting position {servo_name} -> {target_position}")

    def set_relay_state(self, relay_name: str, open_relay: bool):
        """
        Sends a high-priority service command to toggle a relay state.

        :param relay_name: Name of the relay in the YAML config.
        :param open_relay: True to open the relay, False to close it.
        """
        relay_data = self.config['devices']['relay'][relay_name]
        operation = ids.OperationID.RELAY.value.OPEN if open_relay else ids.OperationID.RELAY.value.CLOSE

        command_frame = Frame(
            destination = ids.BoardID.ROCKET,
            priority= ids.PriorityID.HIGH,
            action = ids.ActionID.SERVICE,
            source = ids.BoardID.SOFTWARE,
            device_type = ids.DeviceID.RELAY,
            device_id = relay_data['device_id'],
            data_type = ids.DataTypeID.NO_DATA,
            operation = operation,
            payload = ()
        )

        self.cm.push(command_frame)
        self.cm.send()
        print(f">>> Command: Relay {relay_name} -> {'OPEN' if open_relay else 'CLOSE'}")

    def trigger_abort(self, reason: str):
        """
        Immediate mission abort and closing all valves
        :param reason: What was the reason for the abort
        """
        self.state = MissionState.ABORT
        print(f"\n\n>>>MISSION ABORTED ! ! !\nREASON: {reason}\n\n")

        self.set_relay_state('oxidizer_heater', open_relay=False)
        self.set_relay_state('igniter', open_relay=False)

        self.set_servo_position('oxidizer_intake', open_value=False)
        self.set_servo_position('fuel_intake', open_value=False)
        self.set_servo_position('oxidizer_main', open_value=False)
        self.set_servo_position('fuel_main', open_value=False)

        print("ALL SYSTEMS SECURED\n\n")

    def autopilot(self):
        """
        Analyzes telemetric data and drives the state machine to fill the oxidizer and fuel properly
        """

        # check if we already have any telemetric data (from sensors)
        if not self.telemetry:
            return

        ox_level = self.telemetry.get('oxidizer_level', 0.0)
        fuel_level = self.telemetry.get('fuel_level', 0.0)
        ox_pressure = self.telemetry.get('oxidizer_pressure', 0.0)
        altitude = self.telemetry.get('altitude', 0.0)

        # global safety check: aborting mission (before launch) if critical overpressure (risk of explosion)
        if self.state in (MissionState.PRESSURIZING, MissionState.READY):
            if ox_pressure >= 68.0:
                self.trigger_abort("CRITICAL OVERPRESSURE DETECTED")
                return

        # global safety check: aborting mission after detecting sudden fuel leakage right before start
        if self.state in (MissionState.PRESSURIZING, MissionState.READY):
            if fuel_level < 97.0:
                self.trigger_abort("FUEL LEAKAGE DETECTED")
                return


        if self.state == MissionState.INIT:
            if ox_level < 100.0:
                self.set_servo_position('oxidizer_intake', open_value=True)
                self.state = MissionState.FILLING_OXIDIZER
                print(f"\n\n>>> Change of state: {self.state}\n\n")

            # if oxidizer tank was full we "jump" to fuel filling
            elif ox_level >= 100.0:
                self.set_servo_position('fuel_intake', open_value=True)
                self.state = MissionState.FILLING_FUEL
                print(f"\n\n>>> Change of state: {self.state}\n\n")

        elif self.state == MissionState.FILLING_OXIDIZER:
            # waiting until oxidizer tank is full   (>= instead of == because of numeric errors)
            if ox_level >= 100.0:
                self.set_servo_position('oxidizer_intake', open_value=False)
                self.set_servo_position('fuel_intake', open_value=True)
                self.state = MissionState.FILLING_FUEL
                print(f"\n\n>>> Change of state: {self.state}\n\n")

        elif self.state == MissionState.FILLING_FUEL:
            if fuel_level >= 100.0:
                self.set_servo_position('fuel_intake', open_value=False)

                # increasing oxidizer pressure before launch
                self.set_relay_state('oxidizer_heater', open_relay=True)
                self.state = MissionState.PRESSURIZING
                print(f"\n\n>>> Change of state: {self.state}\n\n")

        elif self.state == MissionState.PRESSURIZING:
            if ox_pressure >= 55.0:
                self.set_relay_state('oxidizer_heater', open_relay=False)
                self.state = MissionState.READY
                print(f"\n\n>>> Change of state: {self.state} (for launch)\n\n")

        elif self.state == MissionState.READY:
            self.set_servo_position('oxidizer_main', open_value=True)
            self.set_servo_position('fuel_main', open_value=True)
            self.set_relay_state('igniter', open_relay=True)
            self.state = MissionState.IN_FLIGHT
            print(f"\n\n>>> Change of state: {self.state}\nLIFT-OFF !!!\n")

        elif self.state == MissionState.IN_FLIGHT:
            if altitude >= self.max_altitude:
                self.max_altitude = altitude

            # if the rocket will start falling
            # -0.5m is epsilon of error to avoid premature parachute opening
            if altitude < self.max_altitude - 0.5:
                self.set_relay_state('parachute', open_relay=True)

                # Safing - if we have deployed a parachute turning off igniter will save battery
                # and closing valves prevents leakage of residual fuel (but simulation doesnt expect that)
                self.set_relay_state('igniter', open_relay=False)
                self.set_servo_position('fuel_main', open_value=False)
                self.set_servo_position('oxidizer_main', open_value=False)

                self.state = MissionState.RECOVERY
                print(f"\n\nAPOGEE DETECTED (Max altitude {self.max_altitude:.2f})\nParachute deployed!\n>>> Change of state: {self.state}\n\n")



    def run(self):
        """Main operational loop handling incoming telemetry and state machine logic."""
        print("Mission Control is on. Listening for telemetry..")

        while True:
            try:
                self.autopilot()
                frame = self.cm.receive()

            # to avoid waiting too long for any frame if the channel is currently empty
            except TransportTimeoutError:
                pass

            # if we havent registered callback for received frame yet
            except UnregisteredCallbackError as e:
                if e.frame.device_type != ids.DeviceID.SENSOR:
                    if e.frame.action == ids.ActionID.ACK:
                        print(f"Received ACK operation = {hex(e.frame.operation)}")
                    continue


if __name__ == "__main__":
    mc = MissionControl()
    mc.run()
