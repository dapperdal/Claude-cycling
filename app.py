"""
Zone 2 Indoor Cycling App
Main application with web UI, BLE connectivity, ERG mode, and workout recording.
"""

import asyncio
import json
import os
import time
import threading
from datetime import datetime
from flask import Flask, render_template
from flask_socketio import SocketIO

from src.ble_manager import BLEManager, BikeData, HRData
from src.zone_analyzer import ZoneAnalyzer
from src.alert_manager import AlertManager, AlertConfig
from src.fit_exporter import FitExporter
from src.workout_manager import WorkoutManager, WorkoutPhase

# Initialize Flask app - use absolute path to ensure correct templates
import pathlib
BASE_DIR = pathlib.Path(__file__).parent.absolute()
app = Flask(__name__, template_folder=str(BASE_DIR / 'templates'))
app.config['SECRET_KEY'] = 'zone2cycling'
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins="*")

# Load config
CONFIG_FILE = 'config.json'


def load_config():
    """Load configuration from file."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {
        "user": {"ftp": 215, "max_hr": 190, "zone2_hr_low": 124, "zone2_hr_high": 143},
        "workout": {"warmup_minutes": 5, "main_minutes": 50, "cooldown_minutes": 5, "zone2_percent_ftp": 65},
        "alerts": {"audio_enabled": True, "visual_enabled": True},
        "devices": {"trainer_name": "KICKR", "hr_monitor_name": "MYZONE"}
    }


def save_config(config):
    """Save configuration to file."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)


config = load_config()

# Initialize components
ble_manager = BLEManager(
    trainer_name=config['devices']['trainer_name'],
    hr_name=config['devices']['hr_monitor_name']
)

# Initialize workout manager with FTP
workout_manager = WorkoutManager(ftp=config['user']['ftp'])

# Zone analyzer for heart rate monitoring
zone_analyzer = ZoneAnalyzer(
    zone2_low=config['user']['zone2_hr_low'],
    zone2_high=config['user']['zone2_hr_high']
)

alert_manager = AlertManager(AlertConfig(
    audio_enabled=config['alerts']['audio_enabled'],
    visual_enabled=config['alerts']['visual_enabled']
))

fit_exporter = FitExporter()

# State
workout_active = False
ble_loop = None
ble_thread = None
workout_update_thread = None


def run_async(coro):
    """Run an async coroutine in the BLE event loop."""
    if ble_loop:
        future = asyncio.run_coroutine_threadsafe(coro, ble_loop)
        return future.result(timeout=30)
    return None


def ble_thread_func():
    """Background thread for BLE operations."""
    global ble_loop
    ble_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(ble_loop)
    ble_loop.run_forever()


def start_ble_thread():
    """Start the BLE background thread."""
    global ble_thread
    if ble_thread is None or not ble_thread.is_alive():
        ble_thread = threading.Thread(target=ble_thread_func, daemon=True)
        ble_thread.start()
        time.sleep(0.5)  # Give loop time to start


def workout_update_loop():
    """Background loop to update workout state and ERG power."""
    global workout_active
    while workout_active and workout_manager.is_running:
        # Update workout manager (handles phase transitions and power ramps)
        new_power = workout_manager.update()

        if new_power is not None:
            # Power target changed, update ERG mode
            run_async(ble_manager.set_target_power(new_power))

        # Send workout status to UI
        socketio.emit('workout_status', {
            'phase': workout_manager.current_phase.value,
            'segment_name': workout_manager.current_segment_name,
            'target_power': workout_manager.target_power,
            'elapsed_seconds': int(workout_manager.elapsed_seconds),
            'remaining_seconds': int(workout_manager.remaining_seconds),
            'segment_remaining': int(workout_manager.segment_remaining_seconds),
            'total_duration': workout_manager.total_duration_seconds
        })

        time.sleep(1.0)

    # Workout completed naturally
    if not workout_manager.is_running and workout_active:
        handle_workout_complete()


def handle_workout_complete():
    """Handle workout completion."""
    global workout_active
    workout_active = False

    # Stop ERG mode
    run_async(ble_manager.stop_erg_mode())

    alert_manager.play_sound('stop')
    alert_manager.announce("Workout complete! Great job. Saving your ride.")

    # Save FIT file
    if fit_exporter.record_count > 0:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'workouts/zone2_ride_{timestamp}.fit'
        os.makedirs('workouts', exist_ok=True)
        filepath = fit_exporter.export(filename)
        print(f'Workout saved to: {filepath}')
        socketio.emit('workout_saved', {'filename': filepath})

    socketio.emit('workout_complete', {})
    alert_manager.stop()


# Workout manager callbacks
def on_power_change(power: int):
    """Called when workout manager wants to change ERG power."""
    print(f"ERG power target: {power}W")
    socketio.emit('erg_power', {'target': power})


def on_phase_change(phase: WorkoutPhase, name: str):
    """Called when workout phase changes."""
    print(f"Workout phase: {name}")
    messages = {
        WorkoutPhase.WARMUP: f"Starting warmup. Ramping to {workout_manager.config.zone2_power} watts.",
        WorkoutPhase.MAIN: f"Warmup complete. Main set: {workout_manager.config.zone2_power} watts for 50 minutes.",
        WorkoutPhase.COOLDOWN: "Starting cooldown. Ramping down.",
        WorkoutPhase.COMPLETED: "Workout complete!"
    }
    alert_manager.announce(messages.get(phase, name))
    socketio.emit('phase_change', {'phase': phase.value, 'name': name})


workout_manager.on_power_change = on_power_change
workout_manager.on_phase_change = on_phase_change


# BLE data callbacks
def on_bike_data(data: BikeData):
    """Called when new bike data is received."""
    socketio.emit('bike_data', {
        'power': data.power,
        'cadence': data.cadence,
        'speed': round(data.speed, 1),
        'timestamp': data.timestamp,
        'target_power': workout_manager.target_power if workout_active else 0
    })

    if workout_active:
        # Get current HR for the record
        hr_data = ble_manager.get_last_hr_data()
        fit_exporter.add_record(
            timestamp=data.timestamp,
            heart_rate=hr_data.heart_rate,
            power=data.power,
            cadence=data.cadence,
            speed=data.speed / 3.6  # Convert km/h to m/s
        )

        # Check power deviation from target
        target = workout_manager.target_power
        if target > 0 and abs(data.power - target) > target * 0.15:
            # Power is >15% off target (but don't alert, ERG should handle it)
            pass


def on_hr_data(data: HRData):
    """Called when new HR data is received."""
    socketio.emit('hr_data', {
        'heart_rate': data.heart_rate,
        'timestamp': data.timestamp
    })

    if workout_active:
        bike_data = ble_manager.get_last_bike_data()

        # Update analyzer (now tracking power zones)
        # Only trigger HR zone alerts during main phase, not warmup/cooldown
        current_phase = workout_manager.current_phase.value
        alerts = zone_analyzer.update(
            hr=data.heart_rate,
            power=bike_data.power,
            cadence=bike_data.cadence,
            phase=current_phase
        )

        # Send alerts
        for alert in alerts:
            alert_manager.alert(alert.type, alert.message, alert.severity)
            socketio.emit('alert', {
                'type': alert.type,
                'message': alert.message,
                'severity': alert.severity
            })

        # Send updated stats
        stats = zone_analyzer.get_stats()
        socketio.emit('stats', {
            'avg_hr': round(stats.avg_hr, 1),
            'avg_power': round(stats.avg_power, 1),
            'avg_cadence': round(stats.avg_cadence, 1),
            'time_in_zone': round(stats.time_in_zone, 0),
            'efficiency_factor': round(stats.efficiency_factor, 2),
            'cardiac_drift_percent': round(stats.cardiac_drift_percent, 1)
        })

        # Add HR-only record if no bike data
        if bike_data.power == 0:
            fit_exporter.add_record(
                timestamp=data.timestamp,
                heart_rate=data.heart_rate,
                power=0,
                cadence=0,
                speed=0
            )


# Set callbacks
ble_manager.on_bike_data = on_bike_data
ble_manager.on_hr_data = on_hr_data


# Routes
@app.route('/')
def index():
    return render_template('index.html')


# Socket events
@socketio.on('connect')
def handle_connect():
    """Handle client connection."""
    print('Client connected')
    emit_device_status()


@socketio.on('get_config')
def handle_get_config():
    """Send current configuration to client."""
    summary = workout_manager.get_workout_summary()
    socketio.emit('config', {
        'ftp': config['user']['ftp'],
        'zone2_power': summary['zone2_power'],
        'zone2_range': summary['zone2_range'],
        'warmup_power_start': workout_manager.config.warmup_start_power,
        'warmup_power_end': summary['zone2_power'],
        'workout_duration_minutes': summary['total_duration_minutes'],
        'audio_enabled': config['alerts']['audio_enabled'],
        'segments': summary['segments'],
        'workout_types': workout_manager.get_workout_types(),
        'current_workout_type': workout_manager.current_workout_type
    })


@socketio.on('set_workout_type')
def handle_set_workout_type(data):
    """Change the workout type."""
    workout_type = data.get('workout_type', 'zone2')
    workout_manager.set_workout_type(workout_type)
    print(f'Workout type changed to: {workout_type}')

    # Send updated config
    handle_get_config()


@socketio.on('scan_devices')
def handle_scan():
    """Scan for BLE devices."""
    print('Scanning for devices...')
    start_ble_thread()

    async def do_scan():
        result = await ble_manager.scan_for_devices(timeout=10.0)

        # Auto-connect if devices found
        if result['trainer']:
            await ble_manager.connect_trainer()
        if result['hr_monitor']:
            await ble_manager.connect_hr_monitor()

        return result

    result = run_async(do_scan())
    socketio.emit('scan_result', result)
    emit_device_status()


@socketio.on('get_discovered_devices')
def handle_get_discovered():
    """Get list of discovered trainers from last scan."""
    trainers = ble_manager.get_discovered_trainers()
    socketio.emit('discovered_devices', {
        'trainers': trainers,
        'current_trainer': ble_manager.connected_trainer_name,
        'trainer_has_erg': ble_manager.trainer_supports_erg
    })


@socketio.on('connect_trainer')
def handle_connect_trainer(data):
    """Connect to a specific trainer by address."""
    address = data.get('address')
    name = data.get('name', '')

    if not address:
        socketio.emit('alert', {'type': 'error', 'message': 'No device address provided', 'severity': 'warning'})
        return

    print(f'Connecting to trainer: {name} ({address})')
    start_ble_thread()

    async def do_connect():
        success = await ble_manager.connect_to_device(address, name)
        return success

    success = run_async(do_connect())

    if success:
        # Update config with new trainer name
        config['devices']['trainer_name'] = name.split()[0].upper() if name else 'TRAINER'
        save_config(config)
        socketio.emit('alert', {'type': 'success', 'message': f'Connected to {name}', 'severity': 'success'})
    else:
        socketio.emit('alert', {'type': 'error', 'message': f'Failed to connect to {name}', 'severity': 'warning'})

    emit_device_status()


@socketio.on('disconnect_trainer')
def handle_disconnect_trainer():
    """Disconnect from current trainer."""
    print('Disconnecting trainer...')

    async def do_disconnect():
        await ble_manager.disconnect_trainer()

    run_async(do_disconnect())
    emit_device_status()


@socketio.on('set_device_filter')
def handle_set_device_filter(data):
    """Update device name filters for scanning."""
    if 'trainer_name' in data:
        ble_manager.set_trainer_name(data['trainer_name'])
        config['devices']['trainer_name'] = data['trainer_name']
        save_config(config)

    if 'hr_name' in data:
        ble_manager.set_hr_name(data['hr_name'])
        config['devices']['hr_monitor_name'] = data['hr_name']
        save_config(config)


@socketio.on('start_workout')
def handle_start_workout():
    """Start a new workout with ERG mode."""
    global workout_active, workout_update_thread

    print('Starting workout...')
    workout_active = True

    # Reset components
    zone_analyzer.reset()
    fit_exporter.clear()

    # Start alert manager
    alert_manager.start()
    alert_manager.play_sound('start')

    # Start workout manager
    workout_manager.start()

    # Set initial ERG power
    initial_power = workout_manager.target_power
    run_async(ble_manager.set_target_power(initial_power))

    # Start workout update thread
    workout_update_thread = threading.Thread(target=workout_update_loop, daemon=True)
    workout_update_thread.start()


@socketio.on('stop_workout')
def handle_stop_workout():
    """Stop workout and save FIT file."""
    global workout_active

    print('Stopping workout...')
    workout_active = False
    workout_manager.stop()

    # Stop ERG mode
    run_async(ble_manager.stop_erg_mode())

    alert_manager.play_sound('stop')
    alert_manager.announce("Workout stopped. Saving file.")

    # Save FIT file
    if fit_exporter.record_count > 0:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'workouts/zone2_ride_{timestamp}.fit'
        os.makedirs('workouts', exist_ok=True)
        filepath = fit_exporter.export(filename)
        print(f'Workout saved to: {filepath}')
        socketio.emit('workout_saved', {'filename': filepath})
        alert_manager.announce(f"Workout saved.")
    else:
        socketio.emit('alert', {
            'type': 'info',
            'message': 'No data recorded.',
            'severity': 'warning'
        })

    alert_manager.stop()


@socketio.on('update_ftp')
def handle_update_ftp(data):
    """Update FTP and recalculate power zones."""
    ftp = data.get('ftp', 215)
    config['user']['ftp'] = ftp
    save_config(config)

    workout_manager.set_ftp(ftp)
    
    print(f'FTP updated: {ftp}W, Zone 2 Power: {workout_manager.config.zone2_low}-{workout_manager.config.zone2_high}W')

    # Send updated config
    handle_get_config()


@socketio.on('update_hr_zones')
def handle_update_hr_zones(data):
    """Update HR zones."""
    if 'zone2_hr_low' in data and 'zone2_hr_high' in data:
        config['user']['zone2_hr_low'] = data['zone2_hr_low']
        config['user']['zone2_hr_high'] = data['zone2_hr_high']
        save_config(config)

        zone_analyzer.update_zones(
            low=data['zone2_hr_low'],
            high=data['zone2_hr_high']
        )
        print(f"HR zones updated: {data['zone2_hr_low']}-{data['zone2_hr_high']} BPM")
        # Optionally, send updated config or a confirmation
        socketio.emit('hr_zones_updated', {
            'zone2_hr_low': data['zone2_hr_low'],
            'zone2_hr_high': data['zone2_hr_high']
        })


@socketio.on('update_settings')
def handle_update_settings(data):
    """Update app settings."""
    if 'audio_enabled' in data:
        config['alerts']['audio_enabled'] = data['audio_enabled']
        alert_manager.config.audio_enabled = data['audio_enabled']
        save_config(config)
        print(f'Audio alerts: {data["audio_enabled"]}')


@socketio.on('set_erg_power')
def handle_set_erg_power(data):
    """Manually set ERG power (for testing/override)."""
    power = data.get('power', 100)
    run_async(ble_manager.set_target_power(power))
    socketio.emit('erg_power', {'target': power})


@socketio.on('stop_erg')
def handle_stop_erg():
    """Stop ERG mode (free ride)."""
    run_async(ble_manager.stop_erg_mode())
    socketio.emit('erg_power', {'target': 0})


def emit_device_status():
    """Emit current device connection status."""
    socketio.emit('device_status', {
        'trainer': ble_manager.is_trainer_connected,
        'trainer_name': ble_manager.connected_trainer_name or config['devices']['trainer_name'],
        'trainer_has_erg': ble_manager.trainer_supports_erg,
        'hr_monitor': ble_manager.is_hr_connected,
        'erg_active': ble_manager.erg_mode_active,
        'erg_power': ble_manager.current_target_power
    })


if __name__ == '__main__':
    ftp = config['user']['ftp']
    z2_power = workout_manager.config.zone2_power
    z2_low = workout_manager.config.zone2_low
    z2_high = workout_manager.config.zone2_high

    print("\n" + "="*50)
    print("Zone 2 Indoor Cycling App (Power-Based)")
    print("="*50)
    print(f"\nFTP: {ftp}W")
    print(f"Zone 2 Power: {z2_power}W (range: {z2_low}-{z2_high}W)")
    print(f"\nWorkout Structure:")
    print(f"  - Warmup: 5 min (ramp {workout_manager.config.warmup_start_power}W -> {z2_power}W)")
    print(f"  - Main:   50 min @ {z2_power}W")
    print(f"  - Cooldown: 5 min (ramp {z2_power}W -> {workout_manager.config.cooldown_end_power}W)")
    print(f"\nLooking for: {config['devices']['trainer_name']} trainer, {config['devices']['hr_monitor_name']} HR")
    print("\nStarting web server...")
    print("Open http://localhost:8080 in your browser")
    print("="*50 + "\n")

    # Start BLE thread
    start_ble_thread()

    # Run Flask app
    socketio.run(app, host='0.0.0.0', port=8080, debug=False, allow_unsafe_werkzeug=True)
