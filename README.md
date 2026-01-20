# Claude-cycling
A cycling app to be ran from mac os browser - with a few basic workouts to support Push, pull Legs workout.

As i dont need a full featured cycling app, this will keep me on track

-----------------------------------------------------------------------------------------------------------------------


# Indoor Cycling Workout Controller

A local web app to control your smart trainer, run structured power-based workouts, and record your indoor cycling sessions.

This application connects to your Wahoo KICKR (or any FTMS-compatible smart trainer) and heart rate monitor, controls the trainer's resistance (ERG mode) based on a selected workout, and monitors your performance.

## Features

- **Structured Workouts**: Comes with pre-built workout profiles like Zone 2, Sweet Spot, and VO2 Max, all based on your Functional Threshold Power (FTP).
- **ERG Mode Control**: Automatically sets your smart trainer's power target according to the current workout phase.
- **BLE Connectivity**: Connects to smart trainers (FTMS protocol) and heart rate monitors.
- **Live Dashboard**: A web-based UI showing real-time power, heart rate, cadence, and speed, along with a timeline of your workout.
- **Power and HR Zone Monitoring**: Provides visual and audio alerts to help you stay in your target power and heart rate zones.
- **FIT Export**: Saves completed workouts as .fit files, ready for upload to platforms like Strava, COROS, and TrainingPeaks.

## Setup

### 1. Install Python dependencies

```bash
cd ~/claude_cycling
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure your settings

Edit `config.json` to set your FTP and Zone 2 heart rate range. The app calculates power zones automatically based on your FTP.

```json
{
    "user": {
        "ftp": 231,
        "max_hr": 190,
        "zone2_hr_low": 124,
        "zone2_hr_high": 143
    },
    "devices": {
        "trainer_name": "KICKR",
        "hr_monitor_name": "MYZONE"
    }
}
```

You can also adjust your FTP and select different workouts directly in the web UI.

### 3. Run the app

```bash
source venv/bin/activate
python app.py
```

Open http://localhost:8080 in your browser.

## Usage

1. **Power on your devices** - Turn on your smart trainer and heart rate monitor.
2. **Select Workout** - Use the dropdown in the UI to choose a workout type (e.g., "Zone 2 Endurance", "Sweet Spot").
3. **Adjust FTP** - If your FTP has changed, update it in the settings panel.
4. **Scan & Connect Devices** - Click "Scan Devices". The app will find and connect to the trainer and HR monitor specified in your config. You can also select from a list of discovered devices.
5. **Click "Start Workout"** - Recording begins, and the app will start controlling your trainer's resistance.
6. **Ride!** - Follow the on-screen prompts. The app will guide you through warmup, main intervals, and cooldown.
7. **Click "Stop & Save"** - This ends the workout and saves a `.fit` file to the `workouts/` folder.

## Uploading to Strava/COROS

1. Find your workout file in the `workouts/` folder (e.g., `zone2_ride_20240115_180000.fit`).
2. **Strava**: Go to `strava.com` → Upload Activity → Choose File.
3. **COROS**: Use the COROS app or web portal to import the `.fit` file.

## Troubleshooting

### Devices not found
- Make sure Bluetooth is enabled on your computer.
- Ensure devices are powered on and not connected to another app (Zwift, TrainerRoad, etc.).
- Check that the `trainer_name` and `hr_monitor_name` in `config.json` match what your devices broadcast. You can use an app like nRF Connect to find the exact names.

### ERG mode not working
- Your trainer must support the Fitness Machine Service (FTMS) Bluetooth protocol for ERG mode control. Most modern smart trainers (Wahoo KICKR, Tacx Neo, etc.) support this.
- If the trainer is connected but resistance isn't changing, it may not be an FTMS-compatible model.

### Audio alerts not working
- On macOS, the app uses the built-in `say` command. Check your system volume.
- Audio alerts can be disabled in the settings panel.

## File Structure

```
claude_cycling/
├── app.py              # Main Flask application and SocketIO server
├── config.json         # User configuration (FTP, HR zones, device names)
├── requirements.txt    # Python dependencies
├── src/
│   ├── workout_manager.py # Manages structured workouts and power targets
│   ├── ble_manager.py     # Handles BLE connectivity and ERG control
│   ├── zone_analyzer.py   # Analyzes HR data for zone and cardiac drift
│   ├── alert_manager.py   # Manages audio/visual alerts
│   └── fit_exporter.py    # Generates and saves .fit files
├── templates/
│   └── index.html         # The single-page web UI
└── workouts/              # Directory for saved .fit files
```
