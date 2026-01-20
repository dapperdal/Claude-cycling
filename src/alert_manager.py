"""
Alert Manager - handles audio and visual alerts.
"""

import threading
import queue
from typing import Optional
from dataclasses import dataclass
import subprocess
import sys


@dataclass
class AlertConfig:
    """Configuration for alerts."""
    audio_enabled: bool = True
    visual_enabled: bool = True
    volume: float = 1.0


class AlertManager:
    """Manages audio and visual alerts for the workout."""

    def __init__(self, config: Optional[AlertConfig] = None):
        self.config = config or AlertConfig()
        self._speech_queue: queue.Queue = queue.Queue()
        self._speech_thread: Optional[threading.Thread] = None
        self._running = False
        self._tts_engine = None
        self._use_system_say = sys.platform == 'darwin'  # macOS has 'say' command

    def start(self):
        """Start the alert manager."""
        self._running = True
        self._speech_thread = threading.Thread(target=self._speech_worker, daemon=True)
        self._speech_thread.start()

    def stop(self):
        """Stop the alert manager."""
        self._running = False
        if self._speech_thread:
            self._speech_queue.put(None)  # Signal to stop
            self._speech_thread.join(timeout=2.0)

    def _speech_worker(self):
        """Background thread for text-to-speech."""
        # Initialize TTS engine in this thread
        if not self._use_system_say:
            try:
                import pyttsx3
                self._tts_engine = pyttsx3.init()
                self._tts_engine.setProperty('rate', 150)
            except Exception as e:
                print(f"Could not initialize TTS engine: {e}")
                self._tts_engine = None

        while self._running:
            try:
                message = self._speech_queue.get(timeout=1.0)
                if message is None:
                    break

                if self.config.audio_enabled:
                    self._speak(message)

            except queue.Empty:
                continue
            except Exception as e:
                print(f"Speech error: {e}")

    def _speak(self, message: str):
        """Speak a message using TTS."""
        try:
            if self._use_system_say:
                # Use macOS 'say' command - more reliable
                subprocess.run(
                    ['say', '-v', 'Samantha', message],
                    capture_output=True,
                    timeout=10
                )
            elif self._tts_engine:
                self._tts_engine.say(message)
                self._tts_engine.runAndWait()
        except Exception as e:
            print(f"TTS error: {e}")

    def alert(self, alert_type: str, message: str, severity: str = 'warning'):
        """
        Trigger an alert.

        Args:
            alert_type: Type of alert ('hr_high', 'hr_low', 'cardiac_drift', 'decoupling')
            message: Message to speak/display
            severity: 'warning' or 'critical'
        """
        # Queue audio alert
        if self.config.audio_enabled:
            # Simplify message for speech
            speech_message = self._simplify_for_speech(alert_type, severity)
            self._speech_queue.put(speech_message)

    def _simplify_for_speech(self, alert_type: str, severity: str) -> str:
        """Create a short, clear spoken message."""
        prefix = "Warning: " if severity == 'warning' else "Alert: "

        messages = {
            'hr_high': "Heart rate too high. Ease up.",
            'hr_low': "Heart rate too low. Push harder.",
            'cardiac_drift': "Cardiac drift detected. You may be fatiguing.",
            'decoupling': "Power and heart rate decoupling. Consider wrapping up."
        }

        return prefix + messages.get(alert_type, "Check your metrics.")

    def announce(self, message: str):
        """Announce a general message."""
        if self.config.audio_enabled:
            self._speech_queue.put(message)

    def play_sound(self, sound_type: str = 'alert'):
        """Play a notification sound."""
        if not self.config.audio_enabled:
            return

        try:
            if self._use_system_say:
                # Use macOS system sounds
                if sound_type == 'alert':
                    subprocess.run(
                        ['afplay', '/System/Library/Sounds/Ping.aiff'],
                        capture_output=True,
                        timeout=5
                    )
                elif sound_type == 'start':
                    subprocess.run(
                        ['afplay', '/System/Library/Sounds/Glass.aiff'],
                        capture_output=True,
                        timeout=5
                    )
                elif sound_type == 'stop':
                    subprocess.run(
                        ['afplay', '/System/Library/Sounds/Hero.aiff'],
                        capture_output=True,
                        timeout=5
                    )
        except Exception as e:
            print(f"Sound error: {e}")
