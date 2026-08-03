# -*- coding: utf-8 -*-
"""
A simulated Velleman K8055, so the dome server can be run and tested with no
hardware and on any OS.

It models the one property that makes the real board dangerous: the digital
outputs LATCH. Setting a channel energises a relay that stays energised until
something clears it, including after the controlling process dies. Every
shutdown test depends on that behaviour being reproduced faithfully.

Travel is modelled too, so a full open/close actually progresses and the
supervision logic (limit switches, stall timeout, setpoints) can be exercised.

Install it before importing dome_shutter:

    from board_sim import install
    install()
    import dome_shutter
"""
import threading
import time

# Channel numbers, mirroring Dome_Control
EAST_OPEN, EAST_CLOSE = 6, 7
WEST_OPEN, WEST_CLOSE = 4, 5
WEST_POS, EAST_POS = 1, 2


class SimulatedBoard:
    """Stands in for the K8055 DLL surface used by _Velleman."""

    def __init__(self, travel_seconds=9.0, open_counts=235, animate=False):
        self.travel_seconds = travel_seconds
        self.open_counts = open_counts
        self._animate = animate
        self._lock = threading.RLock()
        self._thread = None
        self._stop = threading.Event()
        self.reset()

    def reset(self):
        with self._lock:
            self.outputs = set()
            self.closed = False
            self.analog = {WEST_POS: 0.0, EAST_POS: 0.0}
            # Limit switches are active-low ("0 on fully open"); the reed is
            # active-high. 0b011 == mid-travel, nothing tripped.
            self.digital = 0b011
            self.hang = threading.Event()
            self._last_tick = time.monotonic()

    # ---- the DLL surface ----
    def OpenDevice(self, n):
        # Reopening after a close must work: a client setting Connected false
        # then true again is normal (ASCOM Conform does exactly that), and the
        # real board reopens happily. An earlier version left the travel thread
        # dead after a close, so the shells stopped moving for the rest of the
        # process and every subsequent open silently timed out on stall
        # detection -- the device looked broken when only the simulator was.
        with self._lock:
            self.closed = False
            self._last_tick = time.monotonic()
        self._stop.clear()
        if self._animate and (self._thread is None or not self._thread.is_alive()):
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return 0

    def CloseDevice(self):
        with self._lock:
            self.closed = True
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def SetDigitalChannel(self, ch):
        with self._lock:
            self.outputs.add(ch)

    def ClearDigitalChannel(self, ch):
        with self._lock:
            self.outputs.discard(ch)

    def ReadAllDigital(self):
        while self.hang.is_set():           # simulate a wedged USB call
            time.sleep(0.01)
        with self._lock:
            return self.digital

    def ReadAnalogChannel(self, ch):
        with self._lock:
            return int(round(self.analog[ch]))

    # ---- simulation ----
    def _run(self):
        rate = self.open_counts / self.travel_seconds   # counts per second
        while not self._stop.wait(0.05):
            now = time.monotonic()
            with self._lock:
                dt = now - self._last_tick
                self._last_tick = now
                for opench, closech, poschan in ((EAST_OPEN, EAST_CLOSE, EAST_POS),
                                                 (WEST_OPEN, WEST_CLOSE, WEST_POS)):
                    delta = 0.0
                    if opench in self.outputs:
                        delta = rate * dt
                    elif closech in self.outputs:
                        delta = -rate * dt
                    if delta:
                        self.analog[poschan] = min(
                            float(self.open_counts),
                            max(0.0, self.analog[poschan] + delta))
                self._update_switches()

    def _update_switches(self):
        """Derive the switch states from the simulated positions."""
        digital = 0b011                                  # nothing tripped
        if self.analog[WEST_POS] >= self.open_counts - 1:
            digital &= ~0b001                            # west at open limit
        if self.analog[EAST_POS] >= self.open_counts - 1:
            digital &= ~0b010                            # east at open limit
        if self.analog[WEST_POS] <= 1 and self.analog[EAST_POS] <= 1:
            digital |= 0b100                             # shared reed: both shut
        self.digital = digital

    # ---- helpers ----
    def motor_channels(self):
        with self._lock:
            return self.outputs & {EAST_OPEN, EAST_CLOSE, WEST_OPEN, WEST_CLOSE}

    def place(self, east=None, west=None):
        with self._lock:
            if east is not None:
                self.analog[EAST_POS] = float(east)
            if west is not None:
                self.analog[WEST_POS] = float(west)
            self._update_switches()


def install(board=None, animate=False):
    """
    Redirect ctypes.WinDLL to a SimulatedBoard, so dome_shutter drives the
    simulation instead of a real K8055. Returns the board.
    """
    import ctypes
    sim = board or SimulatedBoard(animate=animate)
    ctypes.WinDLL = lambda path: sim
    return sim
