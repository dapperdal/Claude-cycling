"""
FIT File Exporter - generates .fit files compatible with Strava and COROS.
"""

import datetime
import struct
from dataclasses import dataclass
from typing import List, Optional
import os


@dataclass
class FitRecord:
    """A single data record for the FIT file."""
    timestamp: float  # Unix timestamp
    heart_rate: int  # bpm
    power: int  # watts
    cadence: int  # rpm
    speed: float  # m/s


class FitExporter:
    """
    Exports workout data to FIT format.
    FIT is the standard format for fitness devices, supported by Strava, COROS, Garmin, etc.
    """

    # FIT Protocol constants
    FIT_PROTOCOL_VERSION = 0x20  # 2.0
    FIT_PROFILE_VERSION = 0x0814  # 20.84

    # Message types
    MSG_FILE_ID = 0
    MSG_FILE_CREATOR = 1
    MSG_EVENT = 21
    MSG_RECORD = 20
    MSG_LAP = 19
    MSG_SESSION = 18
    MSG_ACTIVITY = 34

    # Field types
    TYPE_ENUM = 0
    TYPE_SINT8 = 1
    TYPE_UINT8 = 2
    TYPE_SINT16 = 131
    TYPE_UINT16 = 132
    TYPE_SINT32 = 133
    TYPE_UINT32 = 134
    TYPE_STRING = 7
    TYPE_FLOAT32 = 136
    TYPE_FLOAT64 = 137
    TYPE_UINT8Z = 10
    TYPE_UINT16Z = 139
    TYPE_UINT32Z = 140

    def __init__(self, backup_dir: str = 'workouts'):
        self._records: List[FitRecord] = []
        self._start_time: Optional[float] = None
        self._end_time: Optional[float] = None
        self._backup_dir = backup_dir
        self._backup_file: Optional[str] = None
        self._last_backup_count = 0

    def add_record(self, timestamp: float, heart_rate: int, power: int,
                   cadence: int, speed: float):
        """Add a data record to the workout."""
        if self._start_time is None:
            self._start_time = timestamp
            # Create backup file path when workout starts
            os.makedirs(self._backup_dir, exist_ok=True)
            ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            self._backup_file = os.path.join(self._backup_dir, f'.backup_{ts}.json')

        self._end_time = timestamp
        self._records.append(FitRecord(
            timestamp=timestamp,
            heart_rate=heart_rate,
            power=power,
            cadence=cadence,
            speed=speed
        ))

        # Auto-save every 30 records (~30 seconds of data)
        if len(self._records) - self._last_backup_count >= 30:
            self._save_backup()

    def _save_backup(self):
        """Save backup of current records to JSON file."""
        if not self._backup_file or not self._records:
            return

        import json
        backup_data = {
            'start_time': self._start_time,
            'end_time': self._end_time,
            'records': [
                {
                    'timestamp': r.timestamp,
                    'heart_rate': r.heart_rate,
                    'power': r.power,
                    'cadence': r.cadence,
                    'speed': r.speed
                }
                for r in self._records
            ]
        }

        try:
            with open(self._backup_file, 'w') as f:
                json.dump(backup_data, f)
            self._last_backup_count = len(self._records)
        except Exception as e:
            print(f"Backup save failed: {e}")

    def load_backup(self, backup_file: str) -> bool:
        """Load records from a backup file."""
        import json
        try:
            with open(backup_file, 'r') as f:
                data = json.load(f)

            self._start_time = data['start_time']
            self._end_time = data['end_time']
            self._records = [
                FitRecord(**r) for r in data['records']
            ]
            return True
        except Exception as e:
            print(f"Failed to load backup: {e}")
            return False

    def cleanup_backup(self):
        """Remove backup file after successful export."""
        if self._backup_file and os.path.exists(self._backup_file):
            try:
                os.remove(self._backup_file)
            except Exception:
                pass
        self._backup_file = None
        self._last_backup_count = 0

    def clear(self):
        """Clear all records."""
        self._records.clear()
        self._start_time = None
        self._end_time = None
        self.cleanup_backup()

    def export(self, filename: str) -> str:
        """
        Export the workout to a FIT file.
        Returns the full path to the created file.
        """
        if not self._records:
            raise ValueError("No records to export")

        # Ensure .fit extension
        if not filename.endswith('.fit'):
            filename += '.fit'

        # Build FIT file content
        data = self._build_fit_file()

        # Write to file
        with open(filename, 'wb') as f:
            f.write(data)

        # Clean up backup after successful export
        self.cleanup_backup()

        return os.path.abspath(filename)

    def _build_fit_file(self) -> bytes:
        """Build the complete FIT file as bytes."""
        # FIT file structure:
        # 1. File Header (14 bytes)
        # 2. Data Records
        # 3. CRC (2 bytes)

        data_records = self._build_data_records()

        # File header
        header = self._build_header(len(data_records))

        # Calculate CRC
        full_data = header + data_records
        crc = self._calculate_crc(full_data)

        return full_data + struct.pack('<H', crc)

    def _build_header(self, data_size: int) -> bytes:
        """Build 14-byte FIT file header."""
        header_size = 14
        protocol_version = self.FIT_PROTOCOL_VERSION
        profile_version = self.FIT_PROFILE_VERSION

        # Build header without CRC first (12 bytes)
        header_no_crc = struct.pack('<B', header_size)  # Header size (1 byte)
        header_no_crc += struct.pack('<B', protocol_version)  # Protocol version (1 byte)
        header_no_crc += struct.pack('<H', profile_version)  # Profile version (2 bytes)
        header_no_crc += struct.pack('<I', data_size)  # Data size (4 bytes)
        header_no_crc += b'.FIT'  # Data type (4 bytes)

        # Add header CRC (2 bytes)
        header = header_no_crc + struct.pack('<H', self._calculate_crc(header_no_crc))

        return header

    def _build_data_records(self) -> bytes:
        """Build all data records."""
        data = b''

        # Definition messages (local message types)
        # File ID definition and data
        data += self._build_file_id()

        # Event (start) definition and data
        data += self._build_event(event_type=0, event=0)  # Timer start

        # Record definition
        data += self._build_record_definition()

        # Record data for each sample
        for record in self._records:
            data += self._build_record_data(record)

        # Event (stop)
        data += self._build_event(event_type=1, event=0)  # Timer stop

        # Lap
        data += self._build_lap()

        # Session
        data += self._build_session()

        # Activity
        data += self._build_activity()

        return data

    def _build_file_id(self) -> bytes:
        """Build File ID message (required first message)."""
        # Definition message
        definition = struct.pack('<B', 0x40)  # Definition message, local type 0
        definition += struct.pack('<B', 0)  # Reserved
        definition += struct.pack('<B', 0)  # Architecture (0 = little endian)
        definition += struct.pack('<H', self.MSG_FILE_ID)  # Global message number
        definition += struct.pack('<B', 4)  # Number of fields

        # Fields: type, manufacturer, product, serial_number
        definition += struct.pack('<BBB', 0, 1, self.TYPE_ENUM)  # type
        definition += struct.pack('<BBB', 1, 2, self.TYPE_UINT16)  # manufacturer
        definition += struct.pack('<BBB', 2, 2, self.TYPE_UINT16)  # product
        definition += struct.pack('<BBB', 3, 4, self.TYPE_UINT32Z)  # serial_number

        # Data message
        data = struct.pack('<B', 0x00)  # Data message, local type 0
        data += struct.pack('<B', 4)  # type = activity
        data += struct.pack('<H', 1)  # manufacturer = Garmin (for compatibility)
        data += struct.pack('<H', 1)  # product
        data += struct.pack('<I', 12345)  # serial number

        return definition + data

    def _build_record_definition(self) -> bytes:
        """Build Record definition message."""
        definition = struct.pack('<B', 0x41)  # Definition message, local type 1
        definition += struct.pack('<B', 0)  # Reserved
        definition += struct.pack('<B', 0)  # Architecture (little endian)
        definition += struct.pack('<H', self.MSG_RECORD)  # Global message number
        definition += struct.pack('<B', 5)  # Number of fields

        # Fields: timestamp, heart_rate, cadence, power, speed
        definition += struct.pack('<BBB', 253, 4, self.TYPE_UINT32)  # timestamp
        definition += struct.pack('<BBB', 3, 1, self.TYPE_UINT8)  # heart_rate
        definition += struct.pack('<BBB', 4, 1, self.TYPE_UINT8)  # cadence
        definition += struct.pack('<BBB', 7, 2, self.TYPE_UINT16)  # power
        definition += struct.pack('<BBB', 6, 2, self.TYPE_UINT16)  # speed (enhanced)

        return definition

    def _build_record_data(self, record: FitRecord) -> bytes:
        """Build a single record data message."""
        # Convert timestamp to FIT timestamp (seconds since 1989-12-31)
        fit_epoch = datetime.datetime(1989, 12, 31, tzinfo=datetime.timezone.utc)
        record_time = datetime.datetime.fromtimestamp(record.timestamp, tz=datetime.timezone.utc)
        fit_timestamp = int((record_time - fit_epoch).total_seconds())

        # Speed in mm/s (FIT uses enhanced speed in 1/1000 m/s)
        speed_mms = int(record.speed * 1000)

        data = struct.pack('<B', 0x01)  # Data message, local type 1
        data += struct.pack('<I', fit_timestamp)  # timestamp
        data += struct.pack('<B', min(255, record.heart_rate))  # heart_rate
        data += struct.pack('<B', min(255, record.cadence))  # cadence
        data += struct.pack('<H', min(65535, record.power))  # power
        data += struct.pack('<H', min(65535, speed_mms))  # speed

        return data

    def _build_event(self, event_type: int, event: int) -> bytes:
        """Build Event message (start/stop)."""
        # Definition
        definition = struct.pack('<B', 0x42)  # Definition message, local type 2
        definition += struct.pack('<B', 0)
        definition += struct.pack('<B', 0)
        definition += struct.pack('<H', self.MSG_EVENT)
        definition += struct.pack('<B', 3)

        definition += struct.pack('<BBB', 253, 4, self.TYPE_UINT32)  # timestamp
        definition += struct.pack('<BBB', 0, 1, self.TYPE_ENUM)  # event
        definition += struct.pack('<BBB', 1, 1, self.TYPE_ENUM)  # event_type

        # Data
        fit_epoch = datetime.datetime(1989, 12, 31, tzinfo=datetime.timezone.utc)
        ts = self._start_time if event_type == 0 else self._end_time
        record_time = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        fit_timestamp = int((record_time - fit_epoch).total_seconds())

        data = struct.pack('<B', 0x02)
        data += struct.pack('<I', fit_timestamp)
        data += struct.pack('<B', event)  # event (0 = timer)
        data += struct.pack('<B', event_type)  # event_type (0 = start, 1 = stop)

        return definition + data

    def _build_lap(self) -> bytes:
        """Build Lap message."""
        definition = struct.pack('<B', 0x43)  # Definition message, local type 3
        definition += struct.pack('<B', 0)
        definition += struct.pack('<B', 0)
        definition += struct.pack('<H', self.MSG_LAP)
        definition += struct.pack('<B', 6)

        definition += struct.pack('<BBB', 253, 4, self.TYPE_UINT32)  # timestamp
        definition += struct.pack('<BBB', 2, 4, self.TYPE_UINT32)  # start_time
        definition += struct.pack('<BBB', 7, 4, self.TYPE_UINT32)  # total_elapsed_time
        definition += struct.pack('<BBB', 8, 4, self.TYPE_UINT32)  # total_timer_time
        definition += struct.pack('<BBB', 15, 1, self.TYPE_UINT8)  # avg_heart_rate
        definition += struct.pack('<BBB', 19, 2, self.TYPE_UINT16)  # avg_power

        # Calculate stats
        fit_epoch = datetime.datetime(1989, 12, 31, tzinfo=datetime.timezone.utc)
        start_time = datetime.datetime.fromtimestamp(self._start_time, tz=datetime.timezone.utc)
        end_time = datetime.datetime.fromtimestamp(self._end_time, tz=datetime.timezone.utc)
        fit_start = int((start_time - fit_epoch).total_seconds())
        fit_end = int((end_time - fit_epoch).total_seconds())
        elapsed_ms = int((self._end_time - self._start_time) * 1000)

        avg_hr = int(sum(r.heart_rate for r in self._records) / len(self._records)) if self._records else 0
        avg_power = int(sum(r.power for r in self._records) / len(self._records)) if self._records else 0

        data = struct.pack('<B', 0x03)
        data += struct.pack('<I', fit_end)
        data += struct.pack('<I', fit_start)
        data += struct.pack('<I', elapsed_ms)
        data += struct.pack('<I', elapsed_ms)
        data += struct.pack('<B', min(255, avg_hr))
        data += struct.pack('<H', min(65535, avg_power))

        return definition + data

    def _build_session(self) -> bytes:
        """Build Session message."""
        definition = struct.pack('<B', 0x44)  # Definition message, local type 4
        definition += struct.pack('<B', 0)
        definition += struct.pack('<B', 0)
        definition += struct.pack('<H', self.MSG_SESSION)
        definition += struct.pack('<B', 7)

        definition += struct.pack('<BBB', 253, 4, self.TYPE_UINT32)  # timestamp
        definition += struct.pack('<BBB', 2, 4, self.TYPE_UINT32)  # start_time
        definition += struct.pack('<BBB', 7, 4, self.TYPE_UINT32)  # total_elapsed_time
        definition += struct.pack('<BBB', 8, 4, self.TYPE_UINT32)  # total_timer_time
        definition += struct.pack('<BBB', 5, 1, self.TYPE_ENUM)  # sport
        definition += struct.pack('<BBB', 16, 1, self.TYPE_UINT8)  # avg_heart_rate
        definition += struct.pack('<BBB', 20, 2, self.TYPE_UINT16)  # avg_power

        fit_epoch = datetime.datetime(1989, 12, 31, tzinfo=datetime.timezone.utc)
        start_time = datetime.datetime.fromtimestamp(self._start_time, tz=datetime.timezone.utc)
        end_time = datetime.datetime.fromtimestamp(self._end_time, tz=datetime.timezone.utc)
        fit_start = int((start_time - fit_epoch).total_seconds())
        fit_end = int((end_time - fit_epoch).total_seconds())
        elapsed_ms = int((self._end_time - self._start_time) * 1000)

        avg_hr = int(sum(r.heart_rate for r in self._records) / len(self._records)) if self._records else 0
        avg_power = int(sum(r.power for r in self._records) / len(self._records)) if self._records else 0

        data = struct.pack('<B', 0x04)
        data += struct.pack('<I', fit_end)
        data += struct.pack('<I', fit_start)
        data += struct.pack('<I', elapsed_ms)
        data += struct.pack('<I', elapsed_ms)
        data += struct.pack('<B', 2)  # sport = cycling
        data += struct.pack('<B', min(255, avg_hr))
        data += struct.pack('<H', min(65535, avg_power))

        return definition + data

    def _build_activity(self) -> bytes:
        """Build Activity message."""
        definition = struct.pack('<B', 0x45)  # Definition message, local type 5
        definition += struct.pack('<B', 0)
        definition += struct.pack('<B', 0)
        definition += struct.pack('<H', self.MSG_ACTIVITY)
        definition += struct.pack('<B', 4)

        definition += struct.pack('<BBB', 253, 4, self.TYPE_UINT32)  # timestamp
        definition += struct.pack('<BBB', 0, 4, self.TYPE_UINT32)  # total_timer_time
        definition += struct.pack('<BBB', 1, 2, self.TYPE_UINT16)  # num_sessions
        definition += struct.pack('<BBB', 2, 1, self.TYPE_ENUM)  # type

        fit_epoch = datetime.datetime(1989, 12, 31, tzinfo=datetime.timezone.utc)
        end_time = datetime.datetime.fromtimestamp(self._end_time, tz=datetime.timezone.utc)
        fit_end = int((end_time - fit_epoch).total_seconds())
        elapsed_ms = int((self._end_time - self._start_time) * 1000)

        data = struct.pack('<B', 0x05)
        data += struct.pack('<I', fit_end)
        data += struct.pack('<I', elapsed_ms)
        data += struct.pack('<H', 1)  # num_sessions
        data += struct.pack('<B', 0)  # type = manual

        return definition + data

    def _calculate_crc(self, data: bytes) -> int:
        """Calculate FIT CRC-16."""
        crc_table = [
            0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
            0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400
        ]

        crc = 0
        for byte in data:
            tmp = crc_table[crc & 0xF]
            crc = (crc >> 4) & 0x0FFF
            crc = crc ^ tmp ^ crc_table[byte & 0xF]

            tmp = crc_table[crc & 0xF]
            crc = (crc >> 4) & 0x0FFF
            crc = crc ^ tmp ^ crc_table[(byte >> 4) & 0xF]

        return crc

    @property
    def record_count(self) -> int:
        """Get number of recorded data points."""
        return len(self._records)

    @property
    def duration_seconds(self) -> float:
        """Get workout duration in seconds."""
        if self._start_time and self._end_time:
            return self._end_time - self._start_time
        return 0.0
