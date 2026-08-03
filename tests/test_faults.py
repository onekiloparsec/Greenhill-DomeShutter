"""Fault-model tests, each mapped to a failure mode named by the maintainer.

The scenarios (icing, impossible switch states, close stalls, early limits)
come from the operators of the real dome; see the fault-code table in
Dome_Control.__init__ and docs/ALPACA.md.

Switch bit map (raw value, bits written msb..lsb as reed/east/west):
    bit 1 (0b001)  west limit, ACTIVE-LOW: 0 = west fully open
    bit 2 (0b010)  east limit, ACTIVE-LOW: 0 = east fully open
    bit 3 (0b100)  shared reed, ACTIVE-HIGH: 1 = upper segments shut
so raw 0b011 = mid-travel, 0b111 = all closed, and 4/5/6 are the "impossible"
states where the reed says shut while a shell sits at its open limit.
"""
import threading
import time

import pytest

import dome_shutter
from conftest import BOARD


@pytest.fixture
def dome(board):
    d = dome_shutter.Dome_Control()
    # keep the blind closes short so tests run in milliseconds
    d.drive_close_seconds = 0.05
    d.emergency_close_seconds = 0.05
    d.dir_delay = 0.01
    yield d
    if not d._shutdown_done:
        d.shutdown()


@pytest.fixture
def quiet_dome(dome):
    """Monitor parked: _update_status is driven by hand, deterministically."""
    dome._stop_event.set()
    if dome._monitor_thread:
        dome._monitor_thread.join(timeout=2)
    dome._stop_event.clear()
    return dome


def place_east(dome, counts):
    BOARD.analog[2] = counts
    dome.east_position = counts
    dome.last_east = counts


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# --- icing: reed stays made while opening ---------------------------------

def test_iced_opening_latches_stops_and_reseats(quiet_dome):
    d = quiet_dome
    place_east(d, 0)
    d.open_e()
    place_east(d, d.ice_detect_counts + 5)     # lower shell moved...
    BOARD.digital = 0b111                       # ...but the reed is still made
    d._update_status()
    assert d.e_fault_code == 'ICED'
    assert d.e_state == 'stopped'               # opening was arrested at once
    # the reseat is a short blind close on its own thread
    assert wait_for(lambda: d.ecsw in BOARD.outputs), 'blind reseat never energised'
    assert wait_for(lambda: not BOARD.motor_channels()), 'blind reseat never ended'


def test_iced_fault_blocks_open_but_never_close(quiet_dome):
    d = quiet_dome
    d.e_fault, d.e_fault_code = 'iced', 'ICED'
    d.open_e()
    assert not BOARD.motor_channels(), 'opened despite an ICED fault'
    d.close_e()
    assert d.ecsw in BOARD.outputs, 'a fault must never block closing'
    d.stop_e()


def test_iced_fault_autoclears_when_the_reed_releases(quiet_dome):
    # Maintainer-specified clear condition: the segments have separated, the
    # hazard is over, operation is re-allowed with no operator round-trip.
    d = quiet_dome
    d.e_fault, d.e_fault_code = 'iced', 'ICED'
    d.w_fault, d.w_fault_code = 'iced', 'ICED'
    BOARD.digital = 0b111                       # reed still made: no clear
    d._update_status()
    assert d.e_fault_code == 'ICED' and d.w_fault_code == 'ICED'
    BOARD.digital = 0b011                       # ice broke, reed released
    d._update_status()
    assert d.e_fault_code is None and d.w_fault_code is None
    d.open_e()                                  # and opening works again
    assert d.eosw in BOARD.outputs
    d.stop_e()


def test_clearfault_then_open_repeats_the_ice_breaking_cycle(quiet_dome):
    # The manual procedure: open a little, auto-reverse, try again. Via Alpaca
    # that is ClearFault + OpenShutter, repeated until the ice gives.
    d = quiet_dome
    place_east(d, 0)
    d.open_e()
    place_east(d, d.ice_detect_counts + 5)
    BOARD.digital = 0b111
    d._update_status()
    assert d.e_fault_code == 'ICED'
    wait_for(lambda: not BOARD.motor_channels())
    d.clear_faults('east')                      # operator retries
    place_east(d, 0)
    d.open_e()
    assert d.eosw in BOARD.outputs, 'retry after ClearFault must be possible'
    d.stop_e()


# --- impossible switch states (raw 4, 5, 6) --------------------------------

@pytest.mark.parametrize("raw,east_bad,west_bad", [
    (0b100, True, True),    # both lower shells at open limit, reed shut
    (0b101, True, False),   # east lower open, reed shut
    (0b110, False, True),   # west lower open, reed shut
])
def test_impossible_switch_state_latches_and_drives_closed(quiet_dome, raw, east_bad, west_bad):
    d = quiet_dome
    BOARD.digital = raw
    d._update_status()
    assert (d.e_fault_code == 'SWITCH_STATE') == east_bad
    assert (d.w_fault_code == 'SWITCH_STATE') == west_bad
    # the affected shell(s) must be driven closed, blind
    if west_bad:
        assert wait_for(lambda: d.wcsw in BOARD.outputs), 'west emergency close missing'
    if east_bad:
        assert wait_for(lambda: d.ecsw in BOARD.outputs), 'east emergency close missing'
    assert wait_for(lambda: not BOARD.motor_channels())
    # manual clear only: the reed releasing must NOT clear it (in this state
    # that could mean the upper segments just fell)
    BOARD.digital = 0b011
    d._update_status()
    if east_bad:
        assert d.e_fault_code == 'SWITCH_STATE'
    if west_bad:
        assert d.w_fault_code == 'SWITCH_STATE'


def test_impossible_state_acts_once_not_every_poll(quiet_dome):
    d = quiet_dome
    BOARD.digital = 0b101
    d._update_status()
    wait_for(lambda: d.ecsw in BOARD.outputs)
    wait_for(lambda: not BOARD.motor_channels())
    threads_before = threading.active_count()
    for _ in range(5):
        d._update_status()                      # condition persists
    assert threading.active_count() <= threads_before + 1, \
        'emergency close respawns on every poll'


# --- close stall without confirmation --------------------------------------

def test_close_stall_short_of_closed_latches_a_fault(quiet_dome):
    d = quiet_dome
    place_east(d, 100)
    d.close_e()
    d.e_timer = time.time() - 2                 # position frozen for >1 s
    d._update_status()
    assert d.e_state == 'stopped'
    assert d.e_fault_code == 'CLOSE_STALL', 'an unconfirmed close must latch'


def test_stall_at_the_closed_stop_is_not_a_fault(quiet_dome):
    # Stalling at/below closed+tolerance is the hard stop seating, not a fault.
    d = quiet_dome
    place_east(d, d.east_closed_position + d.tolerance)
    d.close_e()
    d.e_timer = time.time() - 2
    d._update_status()
    assert d.e_state == 'stopped'
    assert d.e_fault_code is None


def test_close_stall_autoclears_when_the_reed_confirms(quiet_dome):
    d = quiet_dome
    d.e_fault, d.e_fault_code = 'stalled', 'CLOSE_STALL'
    BOARD.digital = 0b111                       # reed made: genuinely closed
    d._update_status()
    assert d.e_fault_code is None


# --- early open limit -------------------------------------------------------

def test_open_limit_engaging_early_latches_a_fault(quiet_dome):
    d = quiet_dome
    place_east(d, 100)                          # far below open_position 235
    d.open_e()
    BOARD.digital = 0b001                       # east limit engaged (bit2 low)
    d._update_status()
    assert d.e_state == 'stopped'
    assert d.e_fault_code == 'EARLY_LIMIT'


def test_open_limit_near_the_calibrated_position_is_normal(quiet_dome):
    d = quiet_dome
    place_east(d, 230)                          # within early_limit_margin of 235
    d.open_e()
    BOARD.digital = 0b001
    d._update_status()
    assert d.e_state == 'stopped'
    assert d.e_fault_code is None


# --- stuck reed must not fake a goto-close complete -------------------------

def test_target_armed_close_ignores_a_stuck_reed(quiet_dome):
    # A goto-close is position-governed: with the reed stuck made (ice), the
    # old "all_closed -> stop" would have ended the close instantly, leaving
    # the shell exactly where it was.
    d = quiet_dome
    place_east(d, 200)
    d.goto_e(10)                                # target 23.5 -> closing
    assert d.e_state == 'closing'
    BOARD.digital = 0b111                       # reed stuck made
    d.e_timer = time.time()                     # not a stall
    d._update_status()
    assert d.e_state == 'closing', 'a stuck reed faked a goto-close complete'
    place_east(d, d._convert_east_set(10))      # now genuinely at target
    d._update_status()
    assert d.e_state == 'stopped'


def test_manual_close_still_stops_on_the_reed(quiet_dome):
    # No target armed: the reed is the legitimate "both shells closed" stop.
    d = quiet_dome
    place_east(d, 50)
    d.close_e()
    assert d.east_target is None
    BOARD.digital = 0b111
    d.e_timer = time.time()
    d._update_status()
    assert d.e_state == 'stopped'


# --- the maintainer's partial-open operability check ------------------------

def test_partially_open_dome_can_always_be_closed(quiet_dome):
    # "need to be able to ensure that we are able to operate the dome closed
    # when the shutters are partially open, and that nothing blocks us"
    d = quiet_dome
    place_east(d, 117)                          # ~50%, stationary, no fault
    BOARD.analog[1] = 60
    d.west_position = d.last_west = 60
    d.close_e()
    assert d.ecsw in BOARD.outputs
    d.stop_e()
    # and with every fault code latched, closing still works
    for code in ('ICED', 'SWITCH_STATE', 'CLOSE_STALL', 'EARLY_LIMIT'):
        d.e_fault, d.e_fault_code = 'x', code
        d.close_e()
        assert d.ecsw in BOARD.outputs, f'close blocked by {code}'
        d.stop_e()
        d.clear_faults()
