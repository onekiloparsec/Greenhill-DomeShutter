 # -*- coding: utf-8 -*-
"""
Created on Mon May 18 09:39:38 2026

@author: bemptage
"""
import atexit
import math
import time
import signal
import ctypes
import threading
import sys

class _Velleman:
    def __init__(self):
        #Load 64 or 32 bit velleman library: 
        if ctypes.sizeof(ctypes.c_voidp)*8 == 64 :  #64bit
            self.lib = ctypes.WinDLL("./K8055fpc64.dll")
        else:  #32bit
            self.lib = ctypes.WinDLL("./K8055D.dll")
        vStatus = self.lib.OpenDevice(0)
        if vStatus <0 :
            raise RuntimeError("Failed to connect to Velleman interface.")
    
    def set_output(self, channel):
        self.lib.SetDigitalChannel(channel)
    
    def clear_output(self, channel):
        self.lib.ClearDigitalChannel(channel)
    
    def read_inputs(self):
        return self.lib.ReadAllDigital()
    
    def read_analogue(self, channel):
        return self.lib.ReadAnalogChannel(channel)

    def close(self):
        # Release the board. Callers must de-energise the outputs FIRST: closing
        # the device does not clear the K8055 output latches.
        self.lib.CloseDevice()

class Dome_Control:
    # The DLL is cached per process and every instance addresses board 0, so all
    # Dome_Control objects share ONE device handle. Only the last one out may
    # close it, or an early CloseDevice silently swallows another instance's
    # de-energise calls.
    _live_instances = 0
    _instances_lock = threading.Lock()

    # Physical facts about this dome, previously buried in the RDP watchdog:
    # the telescope parks on the WEST side, so west closes first; and the two
    # motors are staggered to keep them off the same switch inrush.
    MOTOR_STAGGER = 1.0  # s between starting the two shells

    def __init__(self, calibration=None):
        """
        :param calibration: optional dict overriding the analogue travel limits,
            e.g. {'east_closed': 4, 'east_open': 231, 'west_closed': 2,
            'west_open': 228}. The defaults below are the original author's
            guesses, and a bench logic board will not read the same as the dome,
            so real deployments must supply measured values.
        """
        self.velleman = _Velleman()
        self.e_state = 'stopped'
        self.w_state = 'stopped'
        # Latched faults. A shell that failed to open (the shared reed switch
        # read closed while it was moving) must not be silently reported as
        # merely 'stopped': Alpaca has to surface it as shutterError.
        self.e_fault = None
        self.w_fault = None
        self._lock = threading.RLock()
        # output ports for opening/closing
        self.eosw = 6  # east open channel
        self.ecsw = 7  # east close channel
        self.wosw = 4  # west open channel
        self.wcsw = 5  # west close channel
        # input ports for limit switches
        self.wlim = 1  # west shutter limit switch: 0 on, 1 off
        self.elim = 2  # east shutter limit switch: 0 on, 1 off
        self.reed = 3  # dome close reed switch: 1 on, 0 off
        # analog channels for dome position
        self.wpos = 1
        self.epos = 2
        # timers for various timeout functions
        self.e_timer = 0
        self.w_timer = 0
        # direct position values and tolerance for positioning
        self.east_target = None
        self.west_target = None
        self.tolerance = 2
        self.east_position, self.west_position = self._read_shutter_positions()
        self.last_east, self.last_west = self.east_position, self.west_position
        # UNCALIBRATED DEFAULTS -- the original author's guesses. A closed
        # reading above the real value stalls the motor into the hard stop on
        # every close; one below it stops the shell while still ajar, with the
        # reed unmade and no fault reported. Override via `calibration`.
        self.east_closed_position, self.west_closed_position = 0, 0
        self.east_open_position, self.west_open_position = 235, 235
        if calibration:
            self.east_closed_position = calibration.get('east_closed', self.east_closed_position)
            self.east_open_position = calibration.get('east_open', self.east_open_position)
            self.west_closed_position = calibration.get('west_closed', self.west_closed_position)
            self.west_open_position = calibration.get('west_open', self.west_open_position)
            self.tolerance = calibration.get('tolerance', self.tolerance)
        for name, closed, opened in (('east', self.east_closed_position, self.east_open_position),
                                     ('west', self.west_closed_position, self.west_open_position)):
            if opened <= closed:
                raise ValueError(f"{name} calibration invalid: open ({opened}) must exceed closed ({closed})")
        
        self.dir_delay = 0.750  # s delay between switching directions
        # shutdown plumbing: the monitor thread must be stoppable, and the
        # relays must be de-energised on ANY exit path
        self._stop_event = threading.Event()
        self._monitor_thread = None
        self._shutdown_done = False
        self._shutdown_owner = None
        self._closed = False
        # deliberately NOT self._lock: shutdown must not queue behind a motion
        # section that may be sleeping for up to ~2.75 s
        self._shutdown_lock = threading.Lock()
        # Bumped by every stop. A motion that released the lock to sleep must
        # re-check it before energising, otherwise a stop issued during that
        # sleep is silently overridden by the motion that was already in flight.
        self._cmd_gen = 0
        with Dome_Control._instances_lock:
            Dome_Control._live_instances += 1
        self.stop_e()  # ensure dome stopped on initialisation
        self.stop_w()
        self._monitor(0.1)  # 100ms polling
        self._install_exit_hooks()

    def _may_energise(self, gen):
        """
        Final barrier before any relay is energised. Must be called with the
        lock held. Refuses if the controller is shutting down/closed, or if a
        stop was issued after this command captured its generation.
        """
        if self._closed or self._stop_event.is_set():
            return False
        return gen is None or gen == self._cmd_gen

    def _set_state(self, shutter, state):
        """
        :param shutter: which shutter to set state for
        :param state: state of shutter
        :return: nothing

        Should be called on any motor event, set the various states and timers appropriately.
        """
        now = time.time()
        if shutter.lower() == 'e':
            self.e_state = state
            self.e_timer = now
            # When moving, update position again
            if state.lower() != 'stopped':
                self.last_east = self.east_position
        elif shutter.lower() == 'w':
            self.w_state = state
            self.w_timer = now
            if state.lower() != 'stopped':
                self.last_west = self.west_position

    def open_e(self, target=None, gen=None):
        """
        :param target: raw analogue setpoint to stop at, or None for a manual
            open that runs until a limit switch or the stall timeout stops it.
        :param gen: command generation captured by the caller before it released
            the lock. If a stop was issued since, this motion is abandoned.

        The target is armed in the same locked section that energises the relay,
        so a setpoint can never outlive the motion it belongs to.
        """
        # stop the motor if the shutter is in the opposite state
        with self._lock:
            if self.e_fault:
                print(f"East shutter has a latched fault, refusing to open: {self.e_fault}")
                return
            rev = self.e_state == 'closing'
            if gen is None:
                gen = self._cmd_gen
        # Don't sleep during lock, so sleep outside lock
        if rev:
            self.stop_e()
            with self._lock:
                gen = self._cmd_gen  # our own stop bumped it; re-baseline
            time.sleep(self.dir_delay)

        with self._lock:
            if not self._may_energise(gen):
                return
            self.east_target = target
            self._set_state('e', 'opening')
            # redundant clear for safety
            self.velleman.clear_output(self.ecsw)
            self.velleman.set_output(self.eosw)

    def close_e(self, target=None, gen=None):
        with self._lock:
            rev = self.e_state == 'opening'
            if gen is None:
                gen = self._cmd_gen

        if rev:
            self.stop_e()
            with self._lock:
                gen = self._cmd_gen
            time.sleep(self.dir_delay)  # pause before swapping directions

        with self._lock:
            if not self._may_energise(gen):
                return
            self.east_target = target
            self._set_state('e', 'closing')
            self.velleman.clear_output(self.eosw)
            self.velleman.set_output(self.ecsw)

    def _drive_close_e(self):
        """
        Serves as a failsafe way to have the dome drive closed in the case of the closed switch failing on
        """
        if self.e_state == 'opening':
            self.stop_e()
            time.sleep(self.dir_delay)
        with self._lock:
            if not self._may_energise(None):
                return  # shutting down: do not energise anything
            #start driving the dome closed
            self.velleman.clear_output(self.eosw)
            self.velleman.set_output(self.ecsw)
        try:
            # TODO: replace with logic using dome position
            # wait() not sleep(): a shutdown must not have to wait this out
            self._stop_event.wait(2)  # let the dome close fully
        finally:
            # an exception here must never leave the close relay latched
            self.stop_e()

    def stop_e(self):
        with self._lock:
            self.velleman.clear_output(self.eosw)
            self.velleman.clear_output(self.ecsw)
            # a stopped shutter has no pending setpoint: leaving one armed would
            # abort the next manual Open/Close on its very first poll
            self.east_target = None
            # invalidate any motion that is mid-flight in its direction delay
            self._cmd_gen += 1
            self._set_state('e', 'stopped')

    def goto_e(self, setpoint):
        """
        Moves the shutter to the setpoint position, given as a percentage open.
        """
        target = self._convert_east_set(setpoint)
        with self._lock:
            gen = self._cmd_gen
            if self.east_position < target - self.tolerance:
                # open the dome to increase position. Do nothing if close to target (within tolerance value)
                direction = 'open'
            elif self.east_position > target + self.tolerance:
                direction = 'close'
            else:
                # Already within tolerance of the setpoint. If the shell is
                # moving, "go to where you already are" means STOP: leaving it
                # running would drive it on to the limit switch.
                direction = 'hold'
        # started outside the lock: open_e/close_e may sleep dir_delay to reverse,
        # and holding the lock across that sleep would suspend the other shell's
        # supervision and block the Stop button for the duration
        if direction == 'open':
            self.open_e(target=target, gen=gen)
        elif direction == 'close':
            self.close_e(target=target, gen=gen)
        else:
            self.stop_e()
    
    def open_w(self, target=None, gen=None):
        """
        :param target: raw analogue setpoint to stop at, or None for a manual open.
        :param gen: command generation captured before the caller released the lock.
        """
        # stop the motor if the shutter is in the opposite state
        with self._lock:
            if self.w_fault:
                print(f"West shutter has a latched fault, refusing to open: {self.w_fault}")
                return
            rev = self.w_state == 'closing'
            if gen is None:
                gen = self._cmd_gen

        if rev:
            self.stop_w()
            with self._lock:
                gen = self._cmd_gen  # our own stop bumped it; re-baseline
            time.sleep(self.dir_delay)

        with self._lock:
            if not self._may_energise(gen):
                return
            self.west_target = target
            self._set_state('w', 'opening')
            self.velleman.clear_output(self.wcsw)
            self.velleman.set_output(self.wosw)

    def close_w(self, target=None, gen=None):
        with self._lock:
            rev = self.w_state == 'opening'
            if gen is None:
                gen = self._cmd_gen

        if rev:
            self.stop_w()
            with self._lock:
                gen = self._cmd_gen
            time.sleep(self.dir_delay)

        with self._lock:
            if not self._may_energise(gen):
                return
            self.west_target = target
            self._set_state('w', 'closing')
            self.velleman.clear_output(self.wosw)
            self.velleman.set_output(self.wcsw)

    def _drive_close_w(self):
        """
        Serves as a failsafe way to have the dome drive closed in the case of the closed switch failing on
        """
        if self.w_state == 'opening':
            self.stop_w()
            time.sleep(self.dir_delay)
        with self._lock:
            if not self._may_energise(None):
                return  # shutting down: do not energise anything
            #start driving the dome closed
            self.velleman.clear_output(self.wosw)
            self.velleman.set_output(self.wcsw)
        try:
            # TODO: replace with logic using dome position
            self._stop_event.wait(2)  # let the dome close fully
        finally:
            self.stop_w()

    def stop_w(self):
        with self._lock:
            self.velleman.clear_output(self.wosw)
            self.velleman.clear_output(self.wcsw)
            self.west_target = None
            self._cmd_gen += 1
            self._set_state('w', 'stopped')

    def goto_w(self, setpoint):
        """
            Moves the west shutter to the setpoint position, as a percentage open.
        """
        target = self._convert_west_set(setpoint)
        with self._lock:
            gen = self._cmd_gen
            if self.west_position < target - self.tolerance:
                direction = 'open'
            elif self.west_position > target + self.tolerance:
                direction = 'close'
            else:
                # already there: if it is moving, arrest it (see goto_e)
                direction = 'hold'
        if direction == 'open':
            self.open_w(target=target, gen=gen)
        elif direction == 'close':
            self.close_w(target=target, gen=gen)
        else:
            self.stop_w()

    def _to_raw(self, percentage, closed_position, open_position):
        """
        Converts a 0-100 percentage-open value into a raw analogue setpoint.

        The percentage is a PERCENT, not a 0-1 fraction: 0 is fully closed and
        100 is fully open. The result is clamped to the calibrated travel so a
        bad input can never ask for a position the shutter cannot reach (an
        unreachable target never satisfies the stop test, which turns every
        goto into a drive-to-the-limit-switch).

        Rejects non-finite input rather than clamping it: min(1.0, nan) is 1.0
        in Python, so a NaN setpoint would silently clamp to FULLY OPEN.
        """
        fraction = float(percentage) / 100.0
        if not math.isfinite(fraction):
            raise ValueError(f"Shutter setpoint must be a finite percentage, got {percentage!r}")
        return int(round(max(0.0, min(1.0, fraction)) * (open_position - closed_position) + closed_position))

    # ------------------------------------------------------------------ #
    # Whole-dome operations                                              #
    # ------------------------------------------------------------------ #

    def is_moving(self):
        """True if either shell is under power."""
        with self._lock:
            return self.e_state != 'stopped' or self.w_state != 'stopped'

    def switches(self):
        """Current limit/reed switch states."""
        with self._lock:
            return self._read_shutter_switches()

    def target_counts(self, shell, percentage):
        """Raw analogue setpoint a given percentage open would resolve to."""
        if shell == 'east':
            return self._convert_east_set(percentage)
        if shell == 'west':
            return self._convert_west_set(percentage)
        raise ValueError(f"unknown shell {shell!r}")

    def snapshot(self):
        """
        A single consistent view of both shells, taken under one lock.

        Callers that need several related values (a UI frame, an Alpaca status
        response) must use this rather than reading the attributes one at a
        time, or they can observe a half-updated state where, say, the state
        says 'stopped' but the position is from before the stop.
        """
        with self._lock:
            span = self.east_open_position - self.east_closed_position
            return {
                'east': {
                    'position': self.east_position,
                    'percent': self._to_percent(self.east_position,
                                                self.east_closed_position,
                                                self.east_open_position),
                    'state': self.e_state,
                    'target': self.east_target,
                    'fault': self.e_fault,
                },
                'west': {
                    'position': self.west_position,
                    'percent': self._to_percent(self.west_position,
                                                self.west_closed_position,
                                                self.west_open_position),
                    'state': self.w_state,
                    'target': self.west_target,
                    'fault': self.w_fault,
                },
                'switches': self._read_shutter_switches(),
                'moving': self.e_state != 'stopped' or self.w_state != 'stopped',
                'tolerance_percent': (self.tolerance / float(span) * 100.0) if span > 0 else 0.0,
            }

    def faults(self):
        """Latched faults as {'east': str|None, 'west': str|None}."""
        with self._lock:
            return {'east': self.e_fault, 'west': self.w_fault}

    def clear_faults(self, shell='both'):
        """Clear latched faults so motion is permitted again."""
        with self._lock:
            if shell in ('east', 'both'):
                self.e_fault = None
            if shell in ('west', 'both'):
                self.w_fault = None

    def _stagger(self, first, second):
        """
        Run two motor commands one after the other on a background thread, so
        the caller returns immediately (Alpaca methods must not block) and the
        two motors never start together.

        The second command inherits the command generation captured before the
        first, so any stop issued during the stagger gap cancels it instead of
        starting a second motor after the operator asked everything to halt.
        """
        with self._lock:
            if self._closed or self._stop_event.is_set():
                return
            gen = self._cmd_gen

        def run():
            try:
                first()
                if self._stop_event.wait(self.MOTOR_STAGGER):
                    return  # shutting down
                with self._lock:
                    if self._cmd_gen != gen or self._closed:
                        return  # a stop landed in the stagger gap
                second()
            except Exception as e:
                print(f"Error during staggered dome operation: {e}")
                self.stop_e()
                self.stop_w()

        threading.Thread(target=run, daemon=True).start()

    def open_both(self):
        """Open both shells. Returns immediately; motion continues in background."""
        self._stagger(self.open_w, self.open_e)

    def close_both(self):
        """
        Close both shells. West goes first because the telescope parks on the
        west side, so that shell must clear the OTA before the east one moves.
        """
        self._stagger(self.close_w, self.close_e)

    def goto_both(self, percentage):
        """Drive both shells to the same aperture, staggered."""
        self._stagger(lambda: self.goto_w(percentage), lambda: self.goto_e(percentage))

    def stop_both(self):
        """Halt both shells immediately. This is the abort path: no threads."""
        self.stop_e()
        self.stop_w()

    def _to_percent(self, raw, closed_position, open_position):
        """
        Inverse of _to_raw: a raw analogue reading as a percentage open.
        Clamped, because the reading can sit slightly outside the calibrated
        travel through sensor noise or an imperfect calibration.
        """
        span = open_position - closed_position
        if span == 0:
            return 0.0
        return max(0.0, min(100.0, (raw - closed_position) / float(span) * 100.0))

    def east_percent_open(self):
        """East shutter aperture, 0 = closed, 100 = fully open."""
        with self._lock:
            return self._to_percent(self.east_position, self.east_closed_position,
                                    self.east_open_position)

    def west_percent_open(self):
        """West shutter aperture, 0 = closed, 100 = fully open."""
        with self._lock:
            return self._to_percent(self.west_position, self.west_closed_position,
                                    self.west_open_position)

    def _convert_west_set(self, percentage):
        """
        Converts a percentage value to a position value for the west shutter
        """
        return self._to_raw(percentage, self.west_closed_position, self.west_open_position)

    def _convert_east_set(self, percentage):
        """
        Converts a percentage value to a position value for the east shutter
        """
        return self._to_raw(percentage, self.east_closed_position, self.east_open_position)
    
    def _read_shutter_switches(self):
        """
        Checks the status of the dome input switches:
            1: West shutter: 0 on fully open
            2: East shutter: 0 on fully open
            3: Both segments fully closed
        output is sum of the bits
        State values:
            0: Both east and west fully open
            1: East fully open, but west not fully open
            2: West fully open, but east not fully open
            3: East and west not fully open
            7: East and west fully closed
        """
        
        def bit_set(v, chan):
            '''
            Check if a particular bit is set
            '''
            return (v & (1 << (chan - 1))) != 0
            
        val = self.velleman.read_inputs()
        
        return {'west_limit': not bit_set(val, self.wlim),
                'east_limit': not bit_set(val, self.elim),
                'all_closed': bit_set(val, self.reed),
                'raw': val
            }
    
    def _read_shutter_positions(self):
        """
        Returns the current values of the analog inputs on the Velleman
        """
        west = self.velleman.read_analogue(self.wpos)
        east = self.velleman.read_analogue(self.epos)
        return [east,west]
    
    def _update_status(self):
        """
        Reads out the analogue channels of the Velleman board, and checks their
        status. Includes logic to time out and close the digital channels while
        opening or closing
        """
        with self._lock:
            switches = self._read_shutter_switches()
            self.east_position, self.west_position = self._read_shutter_positions()
            now = time.time()

            def _east_set_reached():
                # logic for reaching set position, if one is active on motor activation
                if self.east_target is None:
                    # don't want to do anything if no target set
                    return False
                if self.e_state == 'opening':
                    # When position exceeds set target, we want this to evaluate as true (open past point)
                    return self.east_position >= self.east_target - self.tolerance
                elif self.e_state == 'closing':
                    # When position smaller than set target, we want to be able to stop motors
                    return self.east_position <= self.east_target + self.tolerance
                return False

            def _west_set_reached():
                if self.west_target is None:
                    return False
                if self.w_state == 'opening':
                    return self.west_position >= self.west_target - self.tolerance
                elif self.w_state == 'closing':
                    return self.west_position <= self.west_target + self.tolerance
                return False

            # logic for stops on east shutter opening
            if self.e_state == 'opening':
                # update the timer on positive position change
                if self.east_position > self.last_east:
                    self.e_timer = now
                # stop shutter if the position hasn't updated for more than 1 sec
                if (now - self.e_timer) > 1:
                    print("East shutter opening timeout, stopping")
                    self.stop_e()
                    self.east_target = None
                # stop if the dome is on the soft limit switch
                elif switches['east_limit']:
                    print("East shutter fully open, stopping")
                    self.stop_e()
                    self.east_target = None
                elif switches['all_closed'] and self.east_position > self.east_closed_position + 15 :
                    print("Dome opening failed, stopping")
                    # Stop command issued as part of _drive_close_e below
                    self.east_target = None
                    # latch it: the shell is left partially open with the reed
                    # claiming closed, which Alpaca must report as shutterError
                    self.e_fault = 'opening failed: all-closed switch made while opening'
                    # Dome is going to be in a partially open state now, and closing will not work as the switch says closed
                    self._drive_close_e()
                # stop the motors if a drive to position has been issued and we are close to that position
                elif _east_set_reached():
                    self.stop_e()
                    self.east_target = None
                # when opening, we only want the analogue channel to increase
                self.last_east = max(self.last_east, self.east_position)
            # logic for stops on east shutter closing
            elif self.e_state == 'closing':
                # update timer on negative position change
                if self.east_position < self.last_east:
                    self.e_timer = now
                if (now - self.e_timer) > 1:
                    print("East shutter closing timeout, stopping")
                    self.stop_e()
                    self.east_target = None
                # this is only true when west is also closed
                elif switches['all_closed']:
                    print("East shutter fully closed, stopping")
                    self.stop_e()
                    self.east_target = None
                # stop when we reach the shut position
                elif self.east_position <= self.east_closed_position:
                    print("East shutter fully closed, stopping")
                    self.stop_e()
                    self.east_target = None
                elif _east_set_reached():
                    self.stop_e()
                    self.east_target = None
                # when closing we only want the analog channel to decrease
                self.last_east = min(self.last_east, self.east_position)

            # logic for west shutters, as above
            if self.w_state == 'opening':
                if self.west_position > self.last_west:
                    self.w_timer = now
                if (now - self.w_timer) > 1:
                    print("West shutter opening timeout, stopping")
                    self.stop_w()
                    self.west_target = None
                elif switches['west_limit']:
                    print("West shutter fully open, stopping")
                    self.stop_w()
                    self.west_target = None
                elif switches['all_closed'] and self.west_position > self.west_closed_position + 15 :
                    print("Dome opening failed, stopping")
                    # Stop command issued as part of _drive_close_w()
                    self.west_target = None
                    self.w_fault = 'opening failed: all-closed switch made while opening'
                    self._drive_close_w()
                elif _west_set_reached():
                    self.stop_w()
                    self.west_target = None
                self.last_west = max(self.last_west, self.west_position)
            elif self.w_state == 'closing':
                if self.west_position < self.last_west:
                    self.w_timer = now
                if (now - self.w_timer) > 1:
                    print("West shutter closing timeout, stopping")
                    self.stop_w()
                    self.west_target = None
                elif switches['all_closed']:
                    print("West shutter fully closed, stopping")
                    self.stop_w()
                    self.west_target = None
                elif self.west_position <= self.west_closed_position:
                    print("West shutter fully closed, stopping")
                    self.stop_w()
                    self.west_target = None
                elif _west_set_reached():
                    self.stop_w()
                    self.west_target = None
                self.last_west = min(self.last_west, self.west_position)

    
    def _monitor(self, pollrate):
        # polling loop to return current dome status
        def loop():
            while not self._stop_event.is_set():
                try:
                    self._update_status()
                except Exception:
                    print("Error on updater")
                # wait() rather than sleep() so shutdown is not delayed by a
                # full poll interval
                self._stop_event.wait(pollrate)
        self._monitor_thread = threading.Thread(target=loop, daemon=True)
        self._monitor_thread.start()

    def _install_exit_hooks(self):
        """
        The K8055 holds its output latches in hardware: if this process dies
        while a motor is running, the relay stays energised and the shutter
        keeps driving with nothing supervising it. Cover every exit path we can.
        """
        atexit.register(self.shutdown)
        # signal handlers can only be installed from the main thread
        if threading.current_thread() is not threading.main_thread():
            return
        for signame in ('SIGINT', 'SIGTERM'):
            sig = getattr(signal, signame, None)
            if sig is None:
                continue
            try:
                previous = signal.getsignal(sig)
                if previous == signal.SIG_IGN:
                    continue  # the app deliberately ignores this signal; respect it

                def handler(signum, frame, _previous=previous):
                    self.shutdown()
                    if callable(_previous):
                        _previous(signum, frame)
                    # SIG_DFL, None (handler installed from C) or anything else:
                    # the board is closed and the monitor is gone, so carrying on
                    # would leave a live-looking UI driving a dead controller
                    sys.exit(128 + signum)

                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass  # not supported on this platform; atexit still applies

    def shutdown(self):
        """
        Stop the monitor thread, de-energise every motor channel and release the
        board. Idempotent, and safe to call from atexit, a signal handler, or the
        UI's close event.
        """
        me = threading.current_thread()
        with self._shutdown_lock:
            if self._shutdown_done or self._shutdown_owner is me:
                return  # already done, or re-entered by our own signal handler
            self._shutdown_owner = me
        try:
            # Refuse all further motion FIRST: a motion sleeping through its
            # direction delay must not wake up and energise a relay behind us.
            self._closed = True
            self._stop_event.set()

            thread = self._monitor_thread
            if thread is not None and thread is not me:
                # bounded: the monitor can be mid-_drive_close_* holding the lock
                thread.join(timeout=5.0)
                if thread.is_alive():
                    print("Monitor thread did not stop; clearing outputs anyway")

            self._force_de_energise()

            # only the last controller standing may release the shared board
            with Dome_Control._instances_lock:
                Dome_Control._live_instances -= 1
                last = Dome_Control._live_instances <= 0
            if last:
                try:
                    self.velleman.close()
                except Exception as e:
                    print(f"Error closing Velleman device: {e}")
        finally:
            with self._shutdown_lock:
                self._shutdown_owner = None
        # Marked complete only now: if the sequence above was interrupted (a
        # second Ctrl-C landing in the join), a later atexit call must retry
        # rather than return to an exit with the relays still hot.
        with self._shutdown_lock:
            self._shutdown_done = True

    def _force_de_energise(self):
        """
        Clear all four motor channels. De-energising is the one operation that
        must never be gated on a lock: if the monitor thread is wedged inside a
        hung DLL call while holding self._lock, waiting for it would leave the
        relays latched -- exactly the failure this whole path exists to prevent.
        """
        acquired = self._lock.acquire(timeout=1.0)
        if not acquired:
            print("Could not take the dome lock; clearing outputs unsynchronised")
        try:
            for channel in (self.eosw, self.ecsw, self.wosw, self.wcsw):
                try:
                    self.velleman.clear_output(channel)
                except Exception as e:
                    print(f"Error clearing channel {channel}: {e}")
            self.e_state = 'stopped'
            self.w_state = 'stopped'
            self.east_target = None
            self.west_target = None
        finally:
            if acquired:
                self._lock.release()
