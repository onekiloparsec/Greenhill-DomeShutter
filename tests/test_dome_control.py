"""Safety regression tests for Dome_Control.

Runs with NO Velleman board and on any OS: see tests/conftest.py for the stubs.

Every test here corresponds to a defect that was live in production. Do not
delete one without understanding which failure it is holding shut.
"""
import threading
import time

import pytest

import dome_shutter
from conftest import BOARD



@pytest.fixture
def board():
    BOARD.outputs.clear()
    BOARD.closed = False
    BOARD.analog = {1: 0, 2: 0}
    BOARD.digital = 0b011
    BOARD.hang.clear()
    dome_shutter.Dome_Control._live_instances = 0
    return BOARD


@pytest.fixture
def dome(board):
    d = dome_shutter.Dome_Control()
    yield d
    if not d._shutdown_done:
        d.shutdown()


@pytest.fixture
def quiet_dome(dome):
    """A dome whose monitor thread is parked, for deterministic state assertions."""
    dome._stop_event.set()
    if dome._monitor_thread:
        dome._monitor_thread.join(timeout=2)
    dome._stop_event.clear()          # _may_energise must not see a shutdown
    return dome


def place_east(dome, counts):
    BOARD.analog[2] = counts
    dome.east_position = counts
    dome.last_east = counts


def motor_channels(dome):
    return {dome.eosw, dome.ecsw, dome.wosw, dome.wcsw} & BOARD.outputs


# --- setpoint scaling: was multiplied by 235 with a 0-100 input ------------

@pytest.mark.parametrize("percent,expected", [(0, 0), (25, 59), (50, 118), (100, 235)])
def test_setpoint_percent_maps_to_counts(quiet_dome, percent, expected):
    assert quiet_dome._convert_east_set(percent) == expected


@pytest.mark.parametrize("percent", [-10, -0.001, 100.001, 250, 10_000])
def test_setpoint_is_clamped_to_travel(quiet_dome, percent):
    # an unreachable target never satisfies the stop test, which silently turns
    # every goto into a drive-to-the-limit-switch
    assert 0 <= quiet_dome._convert_east_set(percent) <= 235


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_setpoint_is_rejected(quiet_dome, bad):
    # min(1.0, nan) is 1.0 in Python, so clamping a NaN would open the dome fully
    with pytest.raises(ValueError):
        quiet_dome._convert_east_set(bad)


# --- one convention: 0 = closed, 100 = fully open, setpoint and readback ---

@pytest.mark.parametrize("percent", [0, 25, 50, 75, 100])
def test_percent_round_trips_through_raw_counts(quiet_dome, percent):
    raw = quiet_dome._convert_east_set(percent)
    place_east(quiet_dome, raw)
    assert round(quiet_dome.east_percent_open()) == percent


def test_percent_open_is_clamped_outside_calibrated_travel(quiet_dome):
    place_east(quiet_dome, -5)              # sensor noise below the closed stop
    assert quiet_dome.east_percent_open() == 0.0
    place_east(quiet_dome, 260)             # or above the open stop
    assert quiet_dome.east_percent_open() == 100.0


def test_display_percent_is_immune_to_adc_jitter(quiet_dome):
    # The pot jitters +/-1 count at rest. The display value derives from
    # last_east (clamped monotonic during motion, frozen when stopped), the
    # maintainer's jitter-free solution, so it must not follow the live wiggle.
    place_east(quiet_dome, 118)                      # 50% and stationary
    baseline = quiet_dome.east_display_percent()
    for wiggle in (117, 119, 118, 117, 119):
        BOARD.analog[2] = wiggle
        quiet_dome.east_position = wiggle            # live value follows the ADC
        assert quiet_dome.east_display_percent() == baseline
    # the live accessor, by contrast, is allowed to move
    assert quiet_dome.east_percent_open() != baseline


def test_a_higher_setpoint_opens_and_a_lower_one_closes(quiet_dome):
    # the setpoint is percent OPEN, so a larger number must drive towards open.
    # An inversion anywhere in the chain shows up here.
    place_east(quiet_dome, quiet_dome._convert_east_set(50))
    quiet_dome.goto_e(90)
    assert quiet_dome.e_state == 'opening'
    quiet_dome.stop_e()
    quiet_dome.goto_e(10)
    assert quiet_dome.e_state == 'closing'


# --- stale setpoints ------------------------------------------------------

def test_stop_clears_pending_setpoint(quiet_dome):
    place_east(quiet_dome, 100)
    quiet_dome.goto_e(80)
    assert quiet_dome.east_target == 188
    quiet_dome.stop_e()
    assert quiet_dome.east_target is None
    assert not motor_channels(quiet_dome)


def test_manual_close_after_goto_survives_first_poll(quiet_dome):
    # a stale target made _east_set_reached() true immediately, so the next
    # manual Close was aborted on its very first poll
    place_east(quiet_dome, 100)
    quiet_dome.goto_e(80)
    quiet_dome.stop_e()
    quiet_dome.close_e()
    quiet_dome._update_status()
    assert quiet_dome.e_state == 'closing'
    assert quiet_dome.ecsw in BOARD.outputs


def test_goto_to_current_position_arrests_a_moving_shutter(quiet_dome):
    # "go to where you already are" must STOP, not silently disarm and let the
    # shell run on to the limit switch
    place_east(quiet_dome, 105)
    quiet_dome.open_e()
    assert quiet_dome.eosw in BOARD.outputs
    quiet_dome.goto_e(45)                       # target 106, within tolerance
    assert quiet_dome.e_state == 'stopped'
    assert not motor_channels(quiet_dome)
    assert quiet_dome.east_target is None


# --- a stop must win against a motion sleeping in its direction delay ------

def test_stop_during_direction_delay_is_not_overridden(quiet_dome):
    place_east(quiet_dome, 100)
    quiet_dome.close_e()                        # now 'closing'
    started = threading.Event()

    def reverse():
        started.set()
        quiet_dome.open_e()                     # reverses: stops, sleeps dir_delay

    t = threading.Thread(target=reverse)
    t.start()
    started.wait(1)
    time.sleep(0.1)                             # land inside the dir_delay sleep
    quiet_dome.stop_e()                         # operator hits Stop
    t.join(timeout=3)
    assert not motor_channels(quiet_dome), "a stop was overridden by an in-flight motion"
    assert quiet_dome.e_state == 'stopped'


# --- shutdown -------------------------------------------------------------

def test_shutdown_de_energises_and_releases_the_board(dome):
    dome.open_e()
    assert dome.eosw in BOARD.outputs
    t0 = time.time()
    dome.shutdown()
    assert not motor_channels(dome)
    assert BOARD.closed
    assert not dome._monitor_thread.is_alive()
    assert time.time() - t0 < 3.0


def test_shutdown_is_idempotent(dome):
    dome.shutdown()
    dome.shutdown()
    assert not motor_channels(dome)


def test_motion_is_refused_after_shutdown(dome):
    dome.shutdown()
    dome.open_e()
    dome.close_w()
    assert not motor_channels(dome), "a post-shutdown command reached the relays"


def test_shutdown_still_de_energises_when_the_board_hangs(dome):
    # the monitor wedges inside a DLL call holding the lock; de-energising must
    # not be gated on that lock
    dome.open_e()
    BOARD.hang.set()
    time.sleep(0.2)
    stopper = threading.Timer(2.0, BOARD.hang.clear)
    stopper.start()
    t0 = time.time()
    dome.shutdown()
    stopper.cancel()
    BOARD.hang.clear()
    assert not motor_channels(dome)
    assert time.time() - t0 < 10.0


def test_interrupted_shutdown_can_be_retried(dome):
    # the completion flag must be set only after the relays are actually clear,
    # or a second Ctrl-C landing in the join permanently disables the de-energise
    dome.open_e()
    original = dome._force_de_energise
    calls = {"n": 0}

    def explode():
        calls["n"] += 1
        if calls["n"] == 1:
            raise KeyboardInterrupt("operator hit Ctrl-C again")
        return original()

    dome._force_de_energise = explode
    with pytest.raises(KeyboardInterrupt):
        dome.shutdown()
    assert not dome._shutdown_done, "an interrupted shutdown must not mark itself complete"
    dome.shutdown()                              # atexit retries
    assert not motor_channels(dome)


def test_second_controller_does_not_close_the_shared_board(board):
    # both instances share one device handle; an early CloseDevice would swallow
    # the other's de-energise calls
    a = dome_shutter.Dome_Control()
    b = dome_shutter.Dome_Control()
    b.shutdown()
    assert not BOARD.closed, "a secondary controller released the shared board"
    a.open_e()
    assert a.eosw in BOARD.outputs, "the board was still usable check failed"
    a.shutdown()
    assert BOARD.closed
    assert not motor_channels(a)
