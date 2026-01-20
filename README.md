# Indoor Cycling Workout Controller

A local web app to control your smart trainer, run structured workouts, and record your indoor cycling sessions.

This application connects to your Wahoo KICKR (or any FTMS-compatible smart trainer) and heart rate monitor, controls the trainer's resistance (ERG mode), and monitors your performance.

## Features

- **HR-Targeted Zone 2**: The Zone 2 workout automatically adjusts power to keep your heart rate in zone. No more manual adjustments or annoying alerts - the trainer does the work.
- **Structured Workouts**: Pre-built workout profiles:
  - **Zone 2 (HR Targeted)** (60 min) - Power auto-adjusts to maintain target HR
  - **Sweet Spot** (55 min) - 2x20min intervals at 90% FTP
  - **Tempo/Threshold** (45 min) - 2x15min at 97% FTP
  - **VO2max Intervals** (35 min) - 5x3min at 120% FTP
- **ERG Mode Control**: Automatically sets your smart trainer's power target according to the current workout phase.
- **BLE Connectivity**: Connects to FTMS-compatible smart trainers (Wahoo KICKR, Tacx, Elite, Saris, Wattbike) and heart rate monitors (Myzone, Polar, Garmin).
- **Live Dashboard**: Real-time display of power, heart rate, cadence, and speed with a visual workout timeline.
- **Performance Metrics**: Tracks efficiency factor (power/HR ratio), cardiac drift, and time in zone.
- **FIT Export**: Saves completed workouts as `.fit` files for upload to Strava, COROS, TrainingPeaks, and Garmin Connect.

## How HR-Targeted Zone 2 Works

Traditional Zone 2 training holds a fixed power (e.g., 70% FTP), but your heart rate can drift based on fatigue, heat, hydration, or sleep quality. This app takes a smarter approach:

1. **You set your HR Zone 2 range** (e.g., 124-143 bpm)
2. **The app calculates your target HR** (middle of your zone)
3. **During the main set, power auto-adjusts every 30 seconds**:
   - HR too high → reduce power by 5-10W
   - HR too low → increase power by 5-10W
   - HR in zone → maintain current power
4. **Power stays within safe bounds** (50-80% FTP)

This means you get true Zone 2 training regardless of conditions - the power becomes the output, not the target.

**Note**: Sweet Spot, Tempo, and VO2max workouts use fixed power targets since those workouts are training specific energy systems at specific intensities.

## Setup

### 1. Install Python dependencies

```bash
cd ~/claude_cycling
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure your settings

Edit `config.json` to set your FTP and HR Zone 2 range:

```json
{
    "user": {
        "ftp": 215,
        "max_hr": 190,
        "zone2_hr_low": 124,
        "zone2_hr_high": 143
    },
    "devices": {
        "trainer_name": "KICKR",
        "hr_monitor_name": "MYZONE"
    },
    "alerts": {
        "audio_enabled": true,
        "visual_enabled": true
    }
}
```

- **ftp**: Your Functional Threshold Power in watts
- **zone2_hr_low/high**: Your heart rate Zone 2 boundaries - this is what the Zone 2 workout targets
- **trainer_name**: Substring to match when scanning for trainers (e.g., "KICKR", "TACX")
- **hr_monitor_name**: Substring to match for HR monitors (e.g., "MYZONE", "POLAR")

You can also adjust these settings directly in the web UI.

### 3. Run the app

```bash
source venv/bin/activate
python app.py
```

Open http://localhost:8080 in your browser.

## Usage

1. **Power on your devices** - Turn on your smart trainer and heart rate monitor.
2. **Select Workout** - Use the dropdown to choose a workout type.
3. **Scan & Connect** - Click "Scan Devices". The app auto-connects to matching devices.
4. **Start Workout** - Click "Start Workout". ERG mode activates.
5. **Ride** - For Zone 2, watch the power auto-adjust to keep your HR in target. For other workouts, hold the prescribed power.
6. **Stop & Save** - Click "Stop & Save" to end the workout and export a `.fit` file.

### Workout Types

| Workout | Duration | Intensity | Control Mode |
|---------|----------|-----------|--------------|
| Zone 2 (HR Targeted) | 60 min | Low | Auto-adjusts power to maintain HR |
| Sweet Spot | 55 min | Medium-High | Fixed power (90% FTP) |
| Tempo/Threshold | 45 min | High | Fixed power (97% FTP) |
| VO2max Intervals | 35 min | High | Fixed power (120% FTP) |

## Uploading to Strava/COROS

1. Find your workout in `workouts/` (e.g., `zone2_ride_20240115_180000.fit`)
2. **Strava**: Go to strava.com → Upload Activity → Choose File
3. **COROS**: Use the COROS app or web portal to import the `.fit` file
4. **Garmin Connect**: Upload via connect.garmin.com → Import Data

## Troubleshooting

### Devices not found
- Ensure Bluetooth is enabled on your computer
- Close other apps that might be connected to your trainer (Zwift, TrainerRoad, Wahoo app)
- Check that `trainer_name` and `hr_monitor_name` in `config.json` match your device names

### ERG mode not working
- Your trainer must support FTMS (Fitness Machine Service) protocol
- The UI shows an ERG indicator (⚡) - if crossed out, the device doesn't support ERG
- Power-only devices (Stages, Quarq, Assioma pedals) can display power but cannot control resistance

### HR-targeted mode not adjusting power
- Ensure your HR monitor is connected (HR badge should show green)
- HR adjustments only happen during the main Zone 2 phase, not warmup/cooldown
- The app needs ~10 seconds of HR data before it starts adjusting

### Workout dropdown not responding
- The dropdown is disabled while a workout is active - stop the current workout first
- Refresh the page if the dropdown shows only one option after startup

### Audio alerts not working
- On macOS, the app uses the `say` command - check system volume
- Toggle the mute button in the header or disable in Settings

## File Structure

```
claude_cycling/
├── app.py                 # Flask application and SocketIO server
├── config.json            # User configuration
├── requirements.txt       # Python dependencies
├── src/
│   ├── workout_manager.py # Workout definitions, HR-targeting logic
│   ├── ble_manager.py     # BLE connectivity and FTMS ERG control
│   ├── zone_analyzer.py   # HR zone tracking and cardiac drift detection
│   ├── alert_manager.py   # Audio/visual alert system (macOS TTS)
│   └── fit_exporter.py    # FIT file generation
├── templates/
│   └── index.html         # Single-page web UI (Socket.IO client)
└── workouts/              # Saved .fit files and auto-backups
```

## Technical Details

- **Backend**: Flask + Flask-SocketIO (threading mode)
- **BLE**: Bleak library for cross-platform Bluetooth LE
- **Protocols**: FTMS (ERG control), Cycling Power Service, Heart Rate Service
- **Frontend**: Vanilla JavaScript with Socket.IO for real-time updates
- **Data format**: FIT protocol (compatible with all major fitness platforms)
- **HR-targeting**: Samples HR every second, adjusts power every 30 seconds with 5W increments
