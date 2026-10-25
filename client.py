import threading
from communication_library import ids
from communication_library.communication_manager import CommunicationManager
from communication_library.exceptions import TransportTimeoutError, \
    UnregisteredCallbackError
from software_zadanie_rekrutacyjne_2026.state import RocketState


class TelemetricReceiver(threading.Thread):
    """
    Background thread dedicated to receiving network frames.

    Continuously listens for incoming packets, filtering for FEED
    (sensor data) and ACK/NACK responses, and immediately updates the shared
    RocketState object without blocking the main Mission Control logic.
    The provided CommunicationManager relies on a synchronous, blocking receive() method — it literally halts execution until a packet arrives or times out.
    If this sits in main loop, state machine stops dead waiting for data. By isolating it in a background thread, the network
    listener quietly waits for packets and updates RocketState in the background. This allows main FSM loop to evaluate physics and trigger the
    parachute at a guaranteed high frequency without network-induced latency.
    splited logic (sensors packets handled by making specific frames and registering callbacks and nack or ack handled in if in except error among other undefined packets)
    was worse for multithreaded system,
    now as we deal with all packets in one place (in except error section)  we have guranteed exact order of operations, we dont have to make artificial frames (for sensors)
    we avoid borderplate
    """

    def __init__(self, cm: CommunicationManager, state: RocketState, config: dict):
        super().__init__(daemon=True) # Daemon thread closes automatically when main program exits
        self.state = state
        self.config = config
        self.cm = cm
        self.running = True

    def _get_sensor_name(self, device_id: int):
        """
        Helper tp get sensor name from device_id from YAML file
        :param device_id:
        :return: sensor name | None
        """
        for sensor_name, sensor_data in self.config['devices']['sensor'].items():
            if sensor_data['device_id'] == device_id:
                return sensor_name

        return None

    def run(self):
        print("Starting Telemetric Receiver thread...")
        while self.running:
            try:
                # cm.receive blocks until a frame arrives or timeout occurs
                frame = self.cm.receive()
            except TransportTimeoutError:
                continue
            except UnregisteredCallbackError as e:

                #telemetry (feed)
                if e.frame.action == ids.ActionID.FEED and e.frame.device_type == ids.DeviceID.SENSOR:
                    sensor_name = self._get_sensor_name(e.frame.device_id)
                    if sensor_name:
                        value = e.frame.payload[0]
                        self.state.update_sensor(sensor_name, value)

                # successful confirmations ACK
                elif e.frame.action == ids.ActionID.ACK:
                    self.state.update_command_response(e.frame.device_id, True)

                # unsuccesful NACK
                elif e.frame.action == ids.ActionID.NACK:
                    self.state.update_command_response(e.frame.device_id, False)
                    print(f"Warning: Command for device_id: {e.frame.device_id} was received but rejected (NACK)")


    def stop(self):
        self.running = False