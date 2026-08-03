"""Shared test rig: dome code drivable with no hardware.

ctypes.WinDLL is redirected to a fake K8055 that models the one behaviour that
matters: digital outputs latch until cleared. Nothing else needs stubbing --
dome_shutter.py has no Windows-only imports since the Telescope/TCP/RDP/speaker
subsystems were removed.
"""
import os
import sys
import threading
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEVICE_DIR = os.path.join(REPO_ROOT, 'device')
for path in (REPO_ROOT, DEVICE_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

import ctypes  # noqa: E402


class FakeBoard:
    """Models a Velleman K8055: outputs latch until cleared."""

    # channel numbers, matching Dome_Control
    EAST_OPEN, EAST_CLOSE, WEST_OPEN, WEST_CLOSE = 6, 7, 4, 5
    WEST_POS, EAST_POS = 1, 2

    def __init__(self):
        self.reset()

    def reset(self):
        self.outputs = set()
        self.closed = False
        self.analog = {self.WEST_POS: 0, self.EAST_POS: 0}
        # limit switches active-low ("0 on fully open"), reed active-high.
        # 0b011 == mid-travel: neither limit reached, reed unmade.
        self.digital = 0b011
        self.hang = threading.Event()

    # --- the DLL surface Dome_Control uses ---
    def OpenDevice(self, n):
        return 0

    def CloseDevice(self):
        self.closed = True

    def SetDigitalChannel(self, ch):
        self.outputs.add(ch)

    def ClearDigitalChannel(self, ch):
        self.outputs.discard(ch)

    def ReadAllDigital(self):
        while self.hang.is_set():
            time.sleep(0.01)
        return self.digital

    def ReadAnalogChannel(self, ch):
        return self.analog[ch]

    # --- helpers for tests ---
    def set_reed(self, made):
        self.digital = (self.digital | 0b100) if made else (self.digital & ~0b100)

    def motor_channels(self):
        return self.outputs & {self.EAST_OPEN, self.EAST_CLOSE,
                               self.WEST_OPEN, self.WEST_CLOSE}


BOARD = FakeBoard()
ctypes.WinDLL = lambda path: BOARD


@pytest.fixture
def board():
    BOARD.reset()
    import dome_shutter
    dome_shutter.Dome_Control._live_instances = 0
    return BOARD
