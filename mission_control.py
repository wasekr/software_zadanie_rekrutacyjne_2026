import yaml
from communication_library.communication_manager import CommunicationManager
from communication_library.tcp_transport import TcpSettings
from communication_library.transport import TransportType
from communication_library.frame import Frame
from communication_library import ids
from enum import Enum, auto
from client import TelemetricReceiver
from interlocks import SafetyInterlocks
from state import RocketState
import time


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

        # initialization of Finite State Machine
        self.state = MissionState.INIT

        self.cm = CommunicationManager()
        self.cm.change_transport_type(TransportType.TCP)
        self.cm.connect(TcpSettings("127.0.0.1", 3000))

        self.rocket_state = RocketState()
        self.interlocks = SafetyInterlocks(self.rocket_state)

        self.receiver = TelemetricReceiver(self.cm, self.rocket_state, self.config)

        #opening a thread (and runs "run" method)
        self.receiver.start()

        # short pause so the background thread can receive first feed frames from network before FSM start
        time.sleep(1.5)

    def _build_servo_frame(self, servo_name: str, open_value: bool):
        """helper for building frames without sending them"""
        servo_data = self.config['devices']['servo'][servo_name]

        # 0 may be interpreted as angle where fuel pipe is fully open and 100 as angle where it's totally closed
        # rather than 0% or 100% of flow
        target_position = servo_data['open_pos'] if open_value else servo_data['closed_pos']
        return Frame(
            destination = ids.BoardID.ROCKET,
            priority = ids.PriorityID.HIGH,
            action = ids.ActionID.SERVICE,
            source = ids.BoardID.SOFTWARE,
            device_type = ids.DeviceID.SERVO,
            device_id = servo_data['device_id'],
            data_type = ids.DataTypeID.INT16,
            operation = ids.OperationID.SERVO.value.POSITION,
            payload = (target_position,)
        )

    def _build_relay_frame(self, relay_name: str, open_relay: bool):
        """helper for building frames without sending them"""
        relay_data = self.config['devices']['relay'][relay_name]
        operation = ids.OperationID.RELAY.value.OPEN if open_relay else ids.OperationID.RELAY.value.CLOSE

        # now we use service because it's a command for rocket
        return Frame(
            destination=ids.BoardID.ROCKET,
            priority=ids.PriorityID.HIGH,
            action=ids.ActionID.SERVICE,
            source=ids.BoardID.SOFTWARE,
            device_type=ids.DeviceID.RELAY,
            device_id=relay_data['device_id'],
            data_type=ids.DataTypeID.NO_DATA,
            operation=operation,
            payload=()
        )

    def set_servo_position(self, servo_name: str, open_value: bool):
        """
        Sends a high-priority service command to change a servo's position.

        :param servo_name: Name of the servo in the YAML config.
        :param open_value: True to open the valve, False to close it.
        """
        if open_value and servo_name == 'fuel_intake':
            safe, msg = self.interlocks.can_open_fuel_intake()
            if not safe:
                print(msg)
                return

        command_frame = self._build_servo_frame(servo_name, open_value)

        # enque
        self.cm.push(command_frame)

        # deque
        self.cm.send()

        print(f">>> Sent a command: {servo_name} -> {'OPEN' if open_value else 'CLOSE'}")

    def set_relay_state(self, relay_name: str, open_relay: bool):
        """
        Sends a high-priority service command to toggle a relay state.

        :param relay_name: Name of the relay in the YAML config.
        :param open_relay: True to open the relay, False to close it.
        """

        if open_relay and relay_name == 'parachute':
            safe, msg = self.interlocks.can_deploy_parachute()
            if not safe:
                print(msg)
                return

        command_frame = self._build_relay_frame(relay_name, open_relay)
        self.cm.push(command_frame)
        self.cm.send()
        print(f">>> Command: Relay {relay_name} -> {'OPEN' if open_relay else 'CLOSE'}")


    def trigger_ignition_sequence(self):
        """
        Sending 3 commands at once so to perform operations under 1 second window
        """
        safe, msg = self.interlocks.can_ignite()
        if not safe:
            self.trigger_abort(f"IGNITION BLOCKED BY INTERBLOCKS: {msg}")
            return

        print("\n\n>>> INITIATING RAPID IGNITION SEQUENCE...")

        f_main = self._build_servo_frame('fuel_main', True)
        o_main = self._build_servo_frame('oxidizer_main', True)
        igniter = self._build_relay_frame('igniter', True)

        self.cm.push(f_main)
        self.cm.push(o_main)
        self.cm.push(igniter)
        self.cm.send()

        self.state = MissionState.IN_FLIGHT
        print(f"\n\n>>> Change of state: {self.state}\nLIFT-OFF !!!\n")


    def trigger_abort(self, reason: str):
        """
        Immediate mission abort and closing all valves
        :param reason: What was the reason for the abort
        """
        self.state = MissionState.ABORT
        print(f"\n\n>>>MISSION ABORTED ! ! !\nREASON: {reason}\n\n")

        # direct sending avoiding standard logic (set_relay or servo functions as we dont have to check interlocks cases for these)
        self.cm.push(self._build_relay_frame('oxidizer_heater', False))
        self.cm.push(self._build_relay_frame('igniter', False))
        self.cm.push(self._build_servo_frame('oxidizer_intake', False))
        self.cm.push(self._build_servo_frame('fuel_intake', False))
        self.cm.push(self._build_servo_frame('oxidizer_main', False))
        self.cm.push(self._build_servo_frame('fuel_main', False))
        self.cm.send()
        print("ALL SYSTEMS SECURED\n\n")

    def autopilot(self):
        """
        Analyzes telemetric data and drives the state machine to fill the oxidizer and fuel properly
        """

        # downloading telemetry directly from physical object
        ox_level = self.rocket_state.telemetry.get('oxidizer_level', 0.0)
        fuel_level = self.rocket_state.telemetry.get('fuel_level', 0.0)
        ox_pressure = self.rocket_state.telemetry.get('oxidizer_pressure', 0.0)

        # global safety check: aborting mission (before launch) if critical conditions
        if self.state in (MissionState.PRESSURIZING, MissionState.READY):
            # oxidizer tank pressure cannot exceed 90
            if ox_pressure >= 85.0:
                self.trigger_abort("CRITICAL OVERPRESSURE DETECTED")
                return
            # sudden fueal leakage right before the start
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
            self.trigger_ignition_sequence()

        elif self.state == MissionState.IN_FLIGHT:
            if self.rocket_state.apogee_reached:
                self.set_relay_state('parachute', open_relay=True)

                # Safing - if we have deployed a parachute turning off igniter will save battery
                # and closing valves prevents leakage of residual fuel (but simulation doesnt expect that)
                self.set_relay_state('igniter', open_relay=False)
                self.set_servo_position('fuel_main', open_value=False)
                self.set_servo_position('oxidizer_main', open_value=False)

                self.state = MissionState.RECOVERY
                print(f"\n\nAPOGEE DETECTED (Max altitude {self.rocket_state.max_altitude:.2f})\nParachute deployed!\n>>> Change of state: {self.state}\n\n")


    def run(self):
        """Main operational loop handling incoming telemetry and state machine logic."""
        print("Mission Control is on. Listening for telemetry..")

        try:
            while True:
                self.autopilot()

                # guaranteed frequency and background thread (Telemetric receiver) can use cpu in this time
                time.sleep(0.1)

        # as ctrl + C
        except KeyboardInterrupt:
            print("\nManual interrupt detected.")

            #closing all valves
            self.trigger_abort("MANUAL SHUTDOWN")

            #killing the background thread
            self.receiver.stop()

if __name__ == "__main__":
    mc = MissionControl()
    mc.run()
