from nicegui import ui, app
from mission_control import MissionControl
import sys
import time

# backend initialization
mc = MissionControl()

with ui.card().classes('w-full items-center p-4 bg-slate-50 min-h-screen'):
    ui.label('🚀 MISSION CONTROL DASHBOARD 🚀').classes('text-3xl font-bold mb-2')

    # elements dynamically updated
    state_label = ui.label(f"State: {mc.state.name}").classes('text-xl font-mono bg-blue-100 px-4 py-2 rounded-lg mb-6 shadow-sm')

    # Initialize data trackers at the top of file
    chart_data = {'time': [], 'alt': []}
    tick = [0]  # List used to easily modify the counter inside update_ui

    # two-column layout
    with ui.row().classes('w-full max-w-5xl gap-6'):
        # flight dynamics panel
        with ui.card().classes('flex-1 p-6'):
            ui.label('Flight Dynamics').classes('text-xl font-bold border-b w-full pb-2 mb-4 text-gray-700')
            alt_label = ui.label('Altitude: 0.0 m').classes('text-lg')
            vel_label = ui.label('Velocity: 0.0 m/s').classes('text-lg')
            max_alt_label = ui.label('Max Altitude: 0.0 m').classes('text-lg text-gray-500 mt-2')

        # propulsion and tanks
        with ui.card().classes('flex-1 p-6'):
            ui.label('Propulsion & Systems').classes('text-xl font-bold border-b w-full pb-2 mb-4 text-gray-700')
            press_label = ui.label('Oxidizer Pressure: 0.0 bar').classes('text-lg font-bold mb-4')

            ui.label('Oxidizer Level')
            ox_progress = ui.linear_progress(value=0.0).props('color="blue" size="20px" rounded')
            ox_label = ui.label('0.0%').classes('text-sm mb-4 text-right w-full')

            ui.label('Fuel Level')
            fuel_progress = ui.linear_progress(value=0.0).props('color="orange" size="20px" rounded')
            fuel_label = ui.label('0.0%').classes('text-sm text-right w-full')

    # mission clock and hardware status
    launch_time = [None]
    with ui.card().classes('w-full max-w-5xl mt-6 p-4'):
        with ui.row().classes('w-full justify-between items-center'):
            t_time_label = ui.label('T- 00:00.0').classes('text-2xl font-mono font-bold text-blue-600')

            with ui.row().classes('gap-4'):
                ui.label('Hardware:').classes('font-bold self-center')
                heater_badge = ui.badge('OX. HEATER', color='red')
                ox_main_badge = ui.badge('OXIDIZER MAIN', color='red')
                fuel_main_badge = ui.badge('FUEL MAIN', color='red')
                igniter_badge = ui.badge('IGNITER', color='red')
                chute_badge = ui.badge('PARACHUTE', color='red')

    # chart
    with ui.card().classes('w-full max-w-5xl mt-6 p-6'):
        ui.label('Live Altitude Tracking').classes('text-xl font-bold border-b w-full pb-2 mb-4 text-gray-700')
        flight_chart = ui.echart({
            'xAxis': {'type': 'category', 'data': []},
            'yAxis': {'type': 'value', 'name': 'Altitude (m)'},
            'series': [{'data': [], 'type': 'line', 'smooth': True, 'color': '#3b82f6'}],
            'animation': False  # Disabled for better performance on rapid updates
        }).classes('w-full h-64')

    # logs
    with ui.card().classes('w-full max-w-5xl mt-6 p-4 bg-gray-900'):
        ui.label('SYSTEM LOGS').classes('text-white font-bold mb-2')
        system_log = ui.log(max_lines=50).classes('w-full h-48 bg-black text-green-400 font-mono text-sm')


    class UIConsole:
        def write(self, message):
            if message.strip():
                system_log.push(message.strip())

        def flush(self):
            pass

        def isatty(self):
            # Tells standard libraries this is not a real terminal
            return False


    # Redirect terminal output to the GUI
    sys.stdout = UIConsole()

    # manual abort button
    ui.button("EMERGENCY ABORT", color='red', on_click = lambda: mc.trigger_abort("MANUAL GUI OVERRIDE"))

#update loop
def update_gui():
    """Drives the state machine and refreshes UI elements"""
    mc.autopilot()

    alt = mc.rocket_state.telemetry.get('altitude', 0.0)
    vel = mc.rocket_state.velocity
    max_alt = mc.rocket_state.max_altitude
    ox_lvl = mc.rocket_state.telemetry.get('oxidizer_level', 0.0)
    fuel_lvl = mc.rocket_state.telemetry.get('fuel_level', 0.0)
    press = mc.rocket_state.telemetry.get('oxidizer_pressure', 0.0)

    # manual override for touchdown
    if mc.state.name == 'RECOVERY' and alt < 5.0:
        alt = 0.0
        vel = 0.0
        state_label.set_text('State: TOUCHDOWN')
    else:
        state_label.set_text(f"State: {mc.state.name}")

    #updating  UI elements with fresh data
    alt_label.set_text(f'Altitude: {alt:.1f} m')
    vel_label.set_text(f'Velocity: {vel:.1f} m/s')
    max_alt_label.set_text(f'Max Altitude: {max_alt:.1f} m')
    press_label.set_text(f"Pressure: {press:.1f} bar")

    # Progress bar (require a normalized float between 0.0 and 1.0)
    ox_progress.set_value(ox_lvl / 100.0)
    ox_label.set_text(f'{ox_lvl:.1f}%')

    fuel_progress.set_value(fuel_lvl / 100.0)
    fuel_label.set_text(f'{fuel_lvl:.1f}%')

    if mc.state.name in ['IN_FLIGHT', 'RECOVERY', 'ABORT']:
        chart_data['time'].append(str(tick[0]))
        chart_data['alt'].append(round(alt, 1))
        tick[0] += 1

        flight_chart.options['xAxis']['data'] = chart_data['time']
        flight_chart.options['series'][0]['data'] = chart_data['alt']
        flight_chart.update()

    # Mission Clock (Freezes when no longer IN_FLIGHT)
    if mc.state.name == 'IN_FLIGHT' and launch_time[0] is None:
        launch_time[0] = time.time()

    if launch_time[0] is not None:
        # Continue ticking unless we have landed (alt == 0.0 from manual override)
        if not (mc.state.name == 'RECOVERY' and alt == 0.0):
            elapsed = time.time() - launch_time[0]
            t_time_label.set_text(f"T+ {elapsed:05.1f}s")

    # Hardware Status Mapping (Green = OPEN/ON, Red = CLOSED/OFF)
    heater_badge.props(f'color={"green" if mc.state.name in ["PRESSURIZING", "READY"] else "red"}')
    ox_main_badge.props(f'color={"green" if mc.state.name == "IN_FLIGHT" else "red"}')
    fuel_main_badge.props(f'color={"green" if mc.state.name == "IN_FLIGHT" else "red"}')
    igniter_badge.props(f'color={"green" if mc.state.name == "READY" else "red"}')
    chute_badge.props(f'color={"green" if mc.state.name == "RECOVERY" else "red"}')


# every 0.1s dynamic labels will be refreshed
ui.timer(0.1, update_gui)

# closing GUI (ctrl+c in terminal) will also automatically close the network thread
app.on_shutdown(mc.receiver.stop)

ui.run(title="AGH Space Systems", port=8000)