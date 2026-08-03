# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# domedevice.py - Bridges the Greenhill clamshell hardware to the ASCOM Dome
#                 interface. Nothing in here touches the K8055 directly; all
#                 hardware access goes through Dome_Control in dome_shutter.py.
# -----------------------------------------------------------------------------
"""
Mapping a two-shell clamshell onto ASCOM's single-shutter Dome model.

The hard part is not the HTTP: it is that ASCOM describes ONE shutter with one
state, while this dome has two independently driven shells with independent
positions. Collapsing two states into one necessarily loses information, so the
collapse is deliberately conservative -- see :meth:`shutter_status`. The detail
that is lost is still available losslessly through the vendor Actions, which is
what Arcsecond uses.

What this clamshell honestly cannot do: it has no azimuth, no home position and
no park position, so CanSetAzimuth / CanSlave / CanFindHome / CanPark /
CanSetPark / CanSyncAzimuth are all False and the corresponding members raise
NotImplementedException. CanSetAltitude is False *for now* because publishing a
degree mapping requires a calibration survey that has not happened; see
docs/ALPACA.md.
"""
import json
import os
import sys
import threading
from enum import IntEnum

# dome_shutter.py lives at the repository root, one level above this package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dome_shutter import Dome_Control  # noqa: E402


class ShutterState(IntEnum):
    shutterOpen = 0
    shutterClosed = 1
    shutterOpening = 2
    shutterClosing = 3
    shutterError = 4


VENDOR = 'Greenhill'

ACTION_SET_APERTURE = f'{VENDOR}:SetShellAperture'
ACTION_GET_STATUS = f'{VENDOR}:GetShellStatus'
ACTION_GET_CAPABILITIES = f'{VENDOR}:GetCapabilities'
ACTION_CLEAR_FAULT = f'{VENDOR}:ClearFault'

SUPPORTED_ACTIONS = [
    ACTION_SET_APERTURE,
    ACTION_GET_STATUS,
    ACTION_GET_CAPABILITIES,
    ACTION_CLEAR_FAULT,
]

SHELLS = ('east', 'west', 'both')


class ActionError(Exception):
    """A vendor Action was called with something the device cannot honour."""


class GreenhillDome:
    """
    Wraps Dome_Control with connect/disconnect semantics and the ASCOM state
    collapse. One instance per served device.
    """

    def __init__(self, calibration=None, logger=None):
        self._calibration = calibration or {}
        self._logger = logger
        self._dome = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Connection                                                         #
    # ------------------------------------------------------------------ #

    @property
    def connected(self):
        return self._dome is not None

    def connect(self):
        """
        Open the board. Dome_Control acquires the K8055 and starts its
        supervision thread in its constructor, so construction IS connection.
        """
        with self._lock:
            if self._dome is not None:
                return
            self._dome = Dome_Control(calibration=self._calibration)
            self._log(f'Connected to dome hardware; calibration={self._calibration}')

    def disconnect(self):
        """De-energise the motors, stop supervision and release the board."""
        with self._lock:
            if self._dome is None:
                return
            dome, self._dome = self._dome, None
        dome.shutdown()
        self._log('Disconnected from dome hardware')

    def _require(self):
        dome = self._dome
        if dome is None:
            raise ConnectionError('Dome is not connected')
        return dome

    def _log(self, message):
        if self._logger:
            self._logger.info(message)

    # ------------------------------------------------------------------ #
    # ASCOM Dome properties                                              #
    # ------------------------------------------------------------------ #

    @property
    def slewing(self):
        """
        True while ANY part of the dome is moving. The ASCOM spec is explicit
        that this covers clamshell leaves, not just azimuth motion, so a moving
        shell must report True even though this dome never slews in azimuth.
        """
        return self._require().is_moving()

    @property
    def at_home(self):
        """
        No home position exists. Returning False is honest and keeps clients
        that read this unconditionally working; CanFindHome is False so no
        client should be acting on it.
        """
        self._require()
        return False

    def shutter_status(self):
        """
        Collapse two independent shell states into one ShutterState.

        Ordered by decreasing safety significance, because whatever is reported
        is what an automated client will act on:

        1. Any latched fault             -> shutterError. A fault must never be
           masked by the other shell looking healthy.
        2. Either shell closing          -> shutterClosing. Reported before
           opening so a close-to-safety in progress is always visible.
        3. Either shell opening          -> shutterOpening.
        4. Reed made AND both shells at  -> shutterClosed. Requires positive
           their closed position            confirmation from BOTH the shared
                                            reed switch and both positions: the
                                            reed alone cannot tell us that a
                                            particular shell is shut.
        5. Anything else                 -> shutterOpen.

        Step 5 is the important one. A shell stopped half way is reported as
        shutterOpen, not shutterClosed, because ASCOM has no partial state and
        "not verifiably shut" must never be reported as shut -- that is the
        difference between a client leaving the dome alone and a client
        believing the dome is safe in rain. The true per-shell percentages are
        available through the vendor Actions.
        """
        return self.collapse_shutter_status(self._require().snapshot())

    @staticmethod
    def collapse_shutter_status(snap):
        """
        The collapse itself, as a pure function of a Dome_Control snapshot, so
        it can be tested exhaustively against every state combination without
        any hardware. See shutter_status() for the ordering rationale.
        """
        states = (snap['east']['state'], snap['west']['state'])
        if snap['east']['fault'] or snap['west']['fault']:
            return ShutterState.shutterError
        if 'closing' in states:
            return ShutterState.shutterClosing
        if 'opening' in states:
            return ShutterState.shutterOpening

        dead_band = snap['tolerance_percent']
        both_shut = (snap['east']['percent'] <= dead_band
                     and snap['west']['percent'] <= dead_band)
        if snap['switches']['all_closed'] and both_shut:
            return ShutterState.shutterClosed
        return ShutterState.shutterOpen

    # ------------------------------------------------------------------ #
    # ASCOM Dome methods                                                 #
    # ------------------------------------------------------------------ #

    def open_shutter(self):
        """Open both shells. Returns immediately; poll Slewing for completion."""
        dome = self._require()
        faults = dome.faults()
        if any(faults.values()):
            raise ActionError(f'Cannot open with a latched fault: {faults}. '
                              f'Clear it with {ACTION_CLEAR_FAULT}.')
        self._log('OpenShutter: opening both shells')
        dome.open_both()

    def close_shutter(self):
        """
        Close both shells, west first. Never blocked by a latched fault:
        closing must always be possible.
        """
        dome = self._require()
        self._log('CloseShutter: closing both shells, west first')
        dome.close_both()

    def abort_slew(self):
        """Halt both shells now."""
        dome = self._require()
        self._log('AbortSlew: stopping both shells')
        dome.stop_both()

    # ------------------------------------------------------------------ #
    # Vendor Actions -- the per-shell aperture this dome uniquely offers  #
    # ------------------------------------------------------------------ #

    def supported_actions(self):
        return list(SUPPORTED_ACTIONS)

    def action(self, name, parameters):
        """
        Dispatch an ASCOM Action. `parameters` is a single string per the Alpaca
        API, so structured arguments travel as JSON inside it.

        Raises ActionError for anything the device cannot honour; the responder
        maps that onto the right Alpaca exception.
        """
        if name == ACTION_GET_CAPABILITIES:
            return json.dumps(self.capabilities())

        if name == ACTION_GET_STATUS:
            return json.dumps(self.shell_status())

        if name == ACTION_SET_APERTURE:
            args = self._parse_json(parameters)
            shell = self._parse_shell(args.get('shell'))
            percent = self._parse_percent(args.get('percent'))
            return json.dumps(self.set_aperture(shell, percent))

        if name == ACTION_CLEAR_FAULT:
            args = self._parse_json(parameters) if parameters else {}
            shell = self._parse_shell(args.get('shell', 'both'))
            self._require().clear_faults(shell)
            self._log(f'Cleared latched fault(s) on {shell}')
            return json.dumps({'cleared': shell})

        raise NotImplementedError(name)

    def set_aperture(self, shell, percent):
        """Drive one or both shells to `percent` open (0 = closed, 100 = open)."""
        dome = self._require()
        if shell in ('east', 'both') and dome.e_fault:
            raise ActionError(f'East shell has a latched fault: {dome.e_fault}')
        if shell in ('west', 'both') and dome.w_fault:
            raise ActionError(f'West shell has a latched fault: {dome.w_fault}')

        self._log(f'SetShellAperture: {shell} -> {percent}% open')
        targets = {}
        for name in ('east', 'west'):
            if shell in (name, 'both'):
                targets[name] = dome.target_counts(name, percent)

        if shell == 'both':
            # stagger, for the same over-current reason as open_both/close_both
            dome.goto_both(percent)
        elif shell == 'east':
            dome.goto_e(percent)
        else:
            dome.goto_w(percent)

        return {'accepted': True, 'targets': targets}

    def shell_status(self):
        """Per-shell detail, which the collapsed ShutterStatus cannot carry.

        'percent' is the jitter-free display value (derived from the clamped
        monotonic last-position, not the live ADC reading), so a client
        rendering it does not flicker with the +/-1 count pot noise.
        """
        snap = self._require().snapshot()
        return {
            'east': {
                'percent': round(snap['east']['display_percent'], 1),
                'state': snap['east']['state'],
                'target': snap['east']['target'],
                'fault': snap['east']['fault'],
            },
            'west': {
                'percent': round(snap['west']['display_percent'], 1),
                'state': snap['west']['state'],
                'target': snap['west']['target'],
                'fault': snap['west']['fault'],
            },
            'slewing': snap['moving'],
            'shutterStatus': int(self.collapse_shutter_status(snap)),
            'calibrated': bool(self._calibration),
        }

    def capabilities(self):
        """
        Self-describing manifest. This is what lets a client build a UI for the
        per-shell aperture without any device-specific code compiled into it.
        """
        return {
            'schemaVersion': 1,
            'vendor': VENDOR,
            'deviceType': 'Dome',
            'calibrated': bool(self._calibration),
            'commands': [
                {
                    'id': ACTION_SET_APERTURE,
                    'label': 'Set shell aperture',
                    'danger': 'high',
                    'args': [
                        {'name': 'shell', 'type': 'enum', 'choices': list(SHELLS),
                         'required': True},
                        {'name': 'percent', 'type': 'number', 'minimum': 0,
                         'maximum': 100, 'unit': '%', 'required': True},
                    ],
                    'completion': {'kind': 'action_json', 'action': ACTION_GET_STATUS,
                                   'path': 'slewing', 'equals': False,
                                   'timeoutSeconds': 30},
                    'readback': ACTION_GET_STATUS,
                    'abort': 'AbortSlew',
                },
                {
                    'id': ACTION_GET_STATUS,
                    'label': 'Read per-shell status',
                    'danger': 'none',
                    'args': [],
                },
                {
                    'id': ACTION_CLEAR_FAULT,
                    'label': 'Clear latched fault',
                    'danger': 'medium',
                    'args': [
                        {'name': 'shell', 'type': 'enum', 'choices': list(SHELLS),
                         'required': False, 'default': 'both'},
                    ],
                },
            ],
        }

    # ------------------------------------------------------------------ #
    # Argument parsing                                                    #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_json(parameters):
        if not parameters:
            raise ActionError('Parameters must be a JSON object')
        try:
            args = json.loads(parameters)
        except (TypeError, ValueError) as exc:
            raise ActionError(f'Parameters is not valid JSON: {exc}') from exc
        if not isinstance(args, dict):
            raise ActionError('Parameters must be a JSON object')
        return args

    @staticmethod
    def _parse_shell(value):
        if not isinstance(value, str) or value.lower() not in SHELLS:
            raise ActionError(f'shell must be one of {list(SHELLS)}, got {value!r}')
        return value.lower()

    @staticmethod
    def _parse_percent(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ActionError(f'percent must be a number, got {value!r}')
        # Rejected rather than clamped: a client asking for 120% has a bug, and
        # silently opening fully instead is exactly the sort of guess that
        # should not be made on behalf of a moving roof.
        if not 0 <= value <= 100:
            raise ActionError(f'percent must be between 0 and 100, got {value!r}')
        return float(value)
