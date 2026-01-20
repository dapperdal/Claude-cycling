"""
BLE Manager for connecting to cycling devices.
Handles Wahoo KICKR (FTMS protocol) and Myzone HR monitor.
"""

import asyncio
from bleak import BleakScanner, BleakClient
from dataclasses import dataclass
from typing import Callable, Optional
import struct

# Standard BLE UUIDs
HEART_RATE_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"

# FTMS (Fitness Machine Service) UUIDs - used by Wahoo KICKR
FTMS_SERVICE = "00001826-0000-1000-8000-00805f9b34fb"
INDOOR_BIKE_DATA = "00002ad2-0000-1000-8000-00805f9b34fb"
FTMS_CONTROL_POINT = "00002ad9-0000-1000-8000-00805f9b34fb"
FTMS_STATUS = "00002ada-0000-1000-8000-00805f9b34fb"

# FTMS Control Point Op Codes
FTMS_REQUEST_CONTROL = 0x00
FTMS_RESET = 0x01
FTMS_SET_TARGET_POWER = 0x05
FTMS_START_RESUME = 0x07
FTMS_STOP_PAUSE = 0x08

# Cycling Power Service (alternative for some trainers)
CYCLING_POWER_SERVICE = "00001818-0000-1000-8000-00805f9b34fb"
CYCLING_POWER_MEASUREMENT = "00002a63-0000-1000-8000-00805f9b34fb"

# Cycling Speed and Cadence
CSC_SERVICE = "00001816-0000-1000-8000-00805f9b34fb"
CSC_MEASUREMENT = "00002a5b-0000-1000-8000-00805f9b34fb"


@dataclass
class BikeData:
    """Data from the smart trainer."""
    power: int = 0  # Watts
    cadence: int = 0  # RPM
    speed: float = 0.0  # km/h
    timestamp: float = 0.0


@dataclass
class HRData:
    """Data from heart rate monitor."""
    heart_rate: int = 0  # BPM
    timestamp: float = 0.0


class BLEManager:
    """Manages BLE connections to cycling devices."""

    # Known trainer types and their characteristics
    KNOWN_TRAINERS = {
        "KICKR": {"has_erg": True, "protocol": "ftms"},
        "WATTBIKE": {"has_erg": True, "protocol": "ftms"},
        "TACX": {"has_erg": True, "protocol": "ftms"},
        "ELITE": {"has_erg": True, "protocol": "ftms"},
        "SARIS": {"has_erg": True, "protocol": "ftms"},
        "STAGES": {"has_erg": False, "protocol": "power"},  # Power meter only
        "QUARQ": {"has_erg": False, "protocol": "power"},   # Power meter only
        "ASSIOMA": {"has_erg": False, "protocol": "power"}, # Power meter only
    }

    def __init__(self, trainer_name: str = "KICKR", hr_name: str = "MYZONE"):
        self.trainer_name = trainer_name.upper()
        self.hr_name = hr_name.upper()

        self.trainer_client: Optional[BleakClient] = None
        self.hr_client: Optional[BleakClient] = None

        self.trainer_address: Optional[str] = None
        self.hr_address: Optional[str] = None
        self.connected_trainer_name: Optional[str] = None

        self.on_bike_data: Optional[Callable[[BikeData], None]] = None
        self.on_hr_data: Optional[Callable[[HRData], None]] = None

        self._running = False
        self._last_bike_data = BikeData()
        self._last_hr_data = HRData()

        # For wheel speed calculation
        self._last_wheel_event_time = 0
        self._last_wheel_revs = 0
        self._wheel_circumference = 2.105  # meters (700x25c typical)

        # ERG mode state
        self._erg_mode_active = False
        self._current_target_power = 0
        self._has_control = False
        self._trainer_has_erg = True  # Assume ERG capable until proven otherwise

        # Discovered devices cache
        self._discovered_trainers = []

    def set_trainer_name(self, name: str):
        """Change the trainer name to search for."""
        self.trainer_name = name.upper()
        print(f"Trainer filter set to: {self.trainer_name}")

    def set_hr_name(self, name: str):
        """Change the HR monitor name to search for."""
        self.hr_name = name.upper()
        print(f"HR monitor filter set to: {self.hr_name}")

    async def scan_for_devices(self, timeout: float = 10.0) -> dict:
        """Scan for BLE devices and return found cycling devices."""
        print(f"Scanning for BLE devices for {timeout} seconds...")

        devices = await BleakScanner.discover(timeout=timeout)

        found = {
            "trainer": None,
            "hr_monitor": None,
            "all_devices": [],
            "trainers": [],  # All potential trainers found
            "hr_monitors": []  # All potential HR monitors found
        }

        self._discovered_trainers = []

        for device in devices:
            name = device.name or ""
            device_info = {
                "name": name,
                "address": device.address
            }
            found["all_devices"].append(device_info)

            # Check if it's a known trainer type
            name_upper = name.upper()
            is_trainer = False
            has_erg = True

            for trainer_type, info in self.KNOWN_TRAINERS.items():
                if trainer_type in name_upper:
                    is_trainer = True
                    has_erg = info["has_erg"]
                    device_info["has_erg"] = has_erg
                    device_info["type"] = trainer_type
                    break

            # Also check for generic FTMS or power devices
            if not is_trainer and ("BIKE" in name_upper or "TRAINER" in name_upper or "POWER" in name_upper):
                is_trainer = True
                device_info["has_erg"] = True  # Assume FTMS capable
                device_info["type"] = "UNKNOWN"

            if is_trainer:
                found["trainers"].append(device_info)
                self._discovered_trainers.append(device_info)

            # Check for HR monitors
            if "HR" in name_upper or "HEART" in name_upper or "MYZONE" in name_upper or "POLAR" in name_upper or "GARMIN" in name_upper or "WAHOO" in name_upper:
                found["hr_monitors"].append(device_info)

            # Auto-select based on configured names
            if self.trainer_name in name_upper:
                found["trainer"] = device_info
                self.trainer_address = device.address
                self.connected_trainer_name = name
                self._trainer_has_erg = device_info.get("has_erg", True)
                print(f"Found trainer: {name} ({device.address}) - ERG: {self._trainer_has_erg}")

            if self.hr_name in name_upper:
                found["hr_monitor"] = device_info
                self.hr_address = device.address
                print(f"Found HR monitor: {name} ({device.address})")

        return found

    async def connect_to_device(self, address: str, device_name: str = "") -> bool:
        """Connect to a specific trainer by address."""
        # Disconnect existing trainer if connected
        if self.trainer_client and self.trainer_client.is_connected:
            await self.disconnect_trainer()

        self.trainer_address = address
        self.connected_trainer_name = device_name

        # Check if this device has ERG capability
        name_upper = device_name.upper()
        self._trainer_has_erg = True  # Default to true
        for trainer_type, info in self.KNOWN_TRAINERS.items():
            if trainer_type in name_upper:
                self._trainer_has_erg = info["has_erg"]
                break

        return await self.connect_trainer()

    async def disconnect_trainer(self):
        """Disconnect from the current trainer."""
        if self._erg_mode_active:
            await self.stop_erg_mode()

        if self.trainer_client and self.trainer_client.is_connected:
            await self.trainer_client.disconnect()
            print(f"Disconnected from trainer")

        self.trainer_client = None
        self._has_control = False

    def get_discovered_trainers(self) -> list:
        """Get list of trainers found in last scan."""
        return self._discovered_trainers

    @property
    def trainer_supports_erg(self) -> bool:
        """Check if connected trainer supports ERG mode."""
        return self._trainer_has_erg

    async def connect_trainer(self) -> bool:
        """Connect to the smart trainer."""
        if not self.trainer_address:
            print("No trainer address set. Run scan_for_devices first.")
            return False

        try:
            self.trainer_client = BleakClient(self.trainer_address)
            await self.trainer_client.connect()
            print(f"Connected to trainer at {self.trainer_address}")

            # Subscribe to indoor bike data (FTMS)
            services = self.trainer_client.services

            # Try FTMS Indoor Bike Data first
            if FTMS_SERVICE in [str(s.uuid) for s in services]:
                await self.trainer_client.start_notify(
                    INDOOR_BIKE_DATA,
                    self._handle_indoor_bike_data
                )
                print("Subscribed to FTMS Indoor Bike Data")

            # Also try Cycling Power if available
            if CYCLING_POWER_SERVICE in [str(s.uuid) for s in services]:
                await self.trainer_client.start_notify(
                    CYCLING_POWER_MEASUREMENT,
                    self._handle_cycling_power
                )
                print("Subscribed to Cycling Power")

            # Request control for ERG mode
            await self._request_control()

            return True

        except Exception as e:
            print(f"Failed to connect to trainer: {e}")
            return False

    async def connect_hr_monitor(self) -> bool:
        """Connect to the heart rate monitor."""
        if not self.hr_address:
            print("No HR monitor address set. Run scan_for_devices first.")
            return False

        try:
            self.hr_client = BleakClient(self.hr_address)
            await self.hr_client.connect()
            print(f"Connected to HR monitor at {self.hr_address}")

            await self.hr_client.start_notify(
                HEART_RATE_MEASUREMENT,
                self._handle_hr_measurement
            )
            print("Subscribed to Heart Rate Measurement")

            return True

        except Exception as e:
            print(f"Failed to connect to HR monitor: {e}")
            return False

    def _handle_indoor_bike_data(self, sender, data: bytearray):
        """Parse FTMS Indoor Bike Data characteristic."""
        import time

        # FTMS Indoor Bike Data format
        # Flags (2 bytes) determine which fields are present
        flags = struct.unpack('<H', data[0:2])[0]
        offset = 2

        speed = 0.0
        cadence = 0
        power = 0

        # Bit 0: More Data (0 = all data present)
        # Bit 1: Average Speed present
        # Bit 2: Instantaneous Cadence present
        # Bit 3: Average Cadence present
        # Bit 4: Total Distance present
        # Bit 5: Resistance Level present
        # Bit 6: Instantaneous Power present
        # etc.

        # Instantaneous Speed (always present when bit 0 is 0)
        if len(data) > offset + 1:
            speed_raw = struct.unpack('<H', data[offset:offset+2])[0]
            speed = speed_raw / 100.0  # Convert to km/h
            offset += 2

        # Skip average speed if present (bit 1)
        if flags & 0x02:
            offset += 2

        # Instantaneous Cadence (bit 2)
        if flags & 0x04:
            if len(data) > offset + 1:
                cadence_raw = struct.unpack('<H', data[offset:offset+2])[0]
                cadence = cadence_raw // 2  # 0.5 RPM resolution
                offset += 2

        # Skip average cadence if present (bit 3)
        if flags & 0x08:
            offset += 2

        # Skip total distance if present (bit 4)
        if flags & 0x10:
            offset += 3

        # Skip resistance level if present (bit 5)
        if flags & 0x20:
            offset += 2

        # Instantaneous Power (bit 6)
        if flags & 0x40:
            if len(data) > offset + 1:
                power = struct.unpack('<h', data[offset:offset+2])[0]
                offset += 2

        bike_data = BikeData(
            power=max(0, power),
            cadence=cadence,
            speed=speed,
            timestamp=time.time()
        )

        self._last_bike_data = bike_data

        if self.on_bike_data:
            self.on_bike_data(bike_data)

    def _handle_cycling_power(self, sender, data: bytearray):
        """Parse Cycling Power Measurement characteristic."""
        import time

        # Flags (2 bytes)
        flags = struct.unpack('<H', data[0:2])[0]

        # Instantaneous Power (2 bytes, always present)
        power = struct.unpack('<h', data[2:4])[0]

        # Update bike data with power
        self._last_bike_data.power = max(0, power)
        self._last_bike_data.timestamp = time.time()

        if self.on_bike_data:
            self.on_bike_data(self._last_bike_data)

    def _handle_hr_measurement(self, sender, data: bytearray):
        """Parse Heart Rate Measurement characteristic."""
        import time

        # First byte contains flags
        flags = data[0]

        # Bit 0: Heart Rate Value Format
        # 0 = UINT8, 1 = UINT16
        if flags & 0x01:
            hr = struct.unpack('<H', data[1:3])[0]
        else:
            hr = data[1]

        hr_data = HRData(
            heart_rate=hr,
            timestamp=time.time()
        )

        self._last_hr_data = hr_data

        if self.on_hr_data:
            self.on_hr_data(hr_data)

    async def _request_control(self) -> bool:
        """Request control of the trainer for ERG mode."""
        if not self.trainer_client or not self.trainer_client.is_connected:
            return False

        try:
            # Request control
            command = struct.pack('<B', FTMS_REQUEST_CONTROL)
            await self.trainer_client.write_gatt_char(FTMS_CONTROL_POINT, command)
            self._has_control = True
            print("Acquired trainer control for ERG mode")
            return True
        except Exception as e:
            print(f"Failed to request trainer control: {e}")
            return False

    async def set_target_power(self, watts: int) -> bool:
        """Set ERG mode target power in watts."""
        if not self.trainer_client or not self.trainer_client.is_connected:
            print("Trainer not connected")
            return False

        if not self._has_control:
            await self._request_control()

        try:
            # FTMS Set Target Power: Op Code (1 byte) + Power (2 bytes, signed little-endian)
            command = struct.pack('<Bh', FTMS_SET_TARGET_POWER, watts)
            await self.trainer_client.write_gatt_char(FTMS_CONTROL_POINT, command)
            self._current_target_power = watts
            self._erg_mode_active = True
            print(f"ERG mode: Target power set to {watts}W")
            return True
        except Exception as e:
            print(f"Failed to set target power: {e}")
            return False

    async def stop_erg_mode(self) -> bool:
        """Stop ERG mode and return to free ride."""
        if not self.trainer_client or not self.trainer_client.is_connected:
            return False

        try:
            # Send reset command to exit ERG mode
            command = struct.pack('<B', FTMS_RESET)
            await self.trainer_client.write_gatt_char(FTMS_CONTROL_POINT, command)
            self._erg_mode_active = False
            self._current_target_power = 0
            print("ERG mode disabled - free ride")
            return True
        except Exception as e:
            print(f"Failed to stop ERG mode: {e}")
            return False

    @property
    def erg_mode_active(self) -> bool:
        return self._erg_mode_active

    @property
    def current_target_power(self) -> int:
        return self._current_target_power

    async def disconnect(self):
        """Disconnect from all devices."""
        # Stop ERG mode before disconnecting
        if self._erg_mode_active:
            await self.stop_erg_mode()

        if self.trainer_client and self.trainer_client.is_connected:
            await self.trainer_client.disconnect()
            print("Disconnected from trainer")

        if self.hr_client and self.hr_client.is_connected:
            await self.hr_client.disconnect()
            print("Disconnected from HR monitor")

    @property
    def is_trainer_connected(self) -> bool:
        return self.trainer_client is not None and self.trainer_client.is_connected

    @property
    def is_hr_connected(self) -> bool:
        return self.hr_client is not None and self.hr_client.is_connected

    def get_last_bike_data(self) -> BikeData:
        return self._last_bike_data

    def get_last_hr_data(self) -> HRData:
        return self._last_hr_data
