"""Tests for the Alpaca device layer.

Two levels:
  * the ShutterStatus collapse, exercised exhaustively as a pure function over
    every combination of the two shells' states -- this is where a clamshell
    stops fitting ASCOM's single-shutter model, so it deserves full coverage;
  * the real HTTP surface, driven through falcon's test client, so the response
    envelope and error numbers are checked rather than assumed.
"""
import itertools
import json

import pytest

from conftest import BOARD

falcon = pytest.importorskip('falcon')
pytest.importorskip('toml')
from falcon import testing  # noqa: E402

import dome_shutter  # noqa: E402
from domedevice import (ACTION_CLEAR_FAULT, ACTION_GET_CAPABILITIES,
                        ACTION_GET_STATUS, ACTION_SET_APERTURE, ActionError,
                        GreenhillDome, ShutterState)  # noqa: E402

# Alpaca error numbers (see device/exceptions.py)
ERR_NOT_IMPLEMENTED = 0x400
ERR_INVALID_VALUE = 0x401
ERR_NOT_CONNECTED = 0x407
ERR_INVALID_OPERATION = 0x40B
ERR_ACTION_NOT_IMPLEMENTED = 0x40C

STATES = ('stopped', 'opening', 'closing')


def make_snapshot(e_state='stopped', w_state='stopped', e_pct=0.0, w_pct=0.0,
                  reed=False, e_fault=None, w_fault=None, dead_band=0.85):
    return {
        'east': {'position': 0, 'percent': e_pct, 'state': e_state,
                 'target': None, 'fault': e_fault},
        'west': {'position': 0, 'percent': w_pct, 'state': w_state,
                 'target': None, 'fault': w_fault},
        'switches': {'west_limit': False, 'east_limit': False,
                     'all_closed': reed, 'raw': 0},
        'moving': e_state != 'stopped' or w_state != 'stopped',
        'tolerance_percent': dead_band,
    }


collapse = GreenhillDome.collapse_shutter_status


# --- the two-shells-into-one-state collapse -------------------------------

@pytest.mark.parametrize("e_state,w_state", list(itertools.product(STATES, STATES)))
def test_a_fault_on_either_shell_always_wins(e_state, w_state):
    # a fault must never be masked by the other shell looking healthy, nor by
    # motion in progress
    for faults in (('stuck', None), (None, 'stuck'), ('stuck', 'stuck')):
        snap = make_snapshot(e_state=e_state, w_state=w_state,
                             e_fault=faults[0], w_fault=faults[1])
        assert collapse(snap) == ShutterState.shutterError


@pytest.mark.parametrize("w_state", STATES)
def test_closing_is_reported_ahead_of_opening(w_state):
    # a close-to-safety in progress must stay visible even if the other shell
    # is opening
    snap = make_snapshot(e_state='closing', w_state=w_state)
    assert collapse(snap) == ShutterState.shutterClosing


def test_opening_reported_when_nothing_is_closing():
    assert collapse(make_snapshot(e_state='opening')) == ShutterState.shutterOpening
    assert collapse(make_snapshot(w_state='opening')) == ShutterState.shutterOpening


def test_closed_requires_the_reed_and_both_shells_shut():
    assert collapse(make_snapshot(reed=True, e_pct=0.0, w_pct=0.0)) \
        == ShutterState.shutterClosed
    # the shared reed alone is not enough: it cannot speak for one shell
    assert collapse(make_snapshot(reed=True, e_pct=40.0, w_pct=0.0)) \
        == ShutterState.shutterOpen
    # nor are the positions alone
    assert collapse(make_snapshot(reed=False, e_pct=0.0, w_pct=0.0)) \
        == ShutterState.shutterOpen


@pytest.mark.parametrize("e_pct,w_pct", [(50.0, 0.0), (0.0, 50.0), (50.0, 50.0),
                                        (100.0, 0.0), (2.0, 0.0)])
def test_a_partly_open_stationary_dome_is_never_reported_closed(e_pct, w_pct):
    # ASCOM has no partial state. Reporting shutterClosed for a dome that is not
    # verifiably shut is the one error that could leave it open in rain, so any
    # non-confirmed state must collapse to shutterOpen.
    snap = make_snapshot(e_pct=e_pct, w_pct=w_pct, reed=False)
    assert collapse(snap) == ShutterState.shutterOpen


def test_every_state_combination_yields_a_valid_shutter_state():
    for e_state, w_state, reed in itertools.product(STATES, STATES, (True, False)):
        for e_pct, w_pct in itertools.product((0.0, 50.0, 100.0), repeat=2):
            snap = make_snapshot(e_state=e_state, w_state=w_state, reed=reed,
                                 e_pct=e_pct, w_pct=w_pct)
            assert collapse(snap) in set(ShutterState)


# --- the HTTP surface ------------------------------------------------------

@pytest.fixture
def client(board):
    """A falcon test client wired to the real routing from device/app.py."""
    import logging

    import app as device_app
    import dome as dome_module
    import exceptions as device_exceptions
    import shr

    # the responders log through these module globals, which main() normally sets
    quiet = logging.getLogger('greenhill-dome-tests')
    quiet.addHandler(logging.NullHandler())
    shr.set_shr_logger(quiet)
    device_exceptions.logger = quiet
    dome_module.logger = quiet

    dome_module.dome_dev = GreenhillDome(calibration=None, logger=None)
    falc_app = falcon.App()
    device_app.init_routes(falc_app, 'dome', dome_module)
    yield testing.TestClient(falc_app)
    if dome_module.dome_dev.connected:
        dome_module.dome_dev.disconnect()


def get(client, attr, **params):
    return client.simulate_get(f'/api/v1/dome/0/{attr}',
                               params={'ClientID': '1', 'ClientTransactionID': '1',
                                       **params}).json


def put(client, attr, **body):
    return client.simulate_put(f'/api/v1/dome/0/{attr}',
                               params={'ClientID': '1', 'ClientTransactionID': '1'},
                               body='&'.join(f'{k}={v}' for k, v in body.items()),
                               headers={'Content-Type':
                                        'application/x-www-form-urlencoded'}).json


def test_properties_report_not_connected_before_connecting(client):
    for attr in ('shutterstatus', 'slewing', 'athome', 'cansetshutter'):
        assert get(client, attr)['ErrorNumber'] == ERR_NOT_CONNECTED, attr


def test_metadata_is_readable_without_connecting(client):
    assert get(client, 'name')['Value'] == 'Greenhill Clamshell Dome'
    assert get(client, 'interfaceversion')['Value'] == 3
    assert get(client, 'connected')['Value'] is False


def test_connect_opens_the_board_and_disconnect_de_energises(client):
    assert put(client, 'connected', Connected='true')['ErrorNumber'] == 0
    assert get(client, 'connected')['Value'] is True
    put(client, 'openshutter')
    assert put(client, 'connected', Connected='false')['ErrorNumber'] == 0
    assert not BOARD.motor_channels(), 'disconnect left a motor energised'


def test_capability_flags_match_a_clamshell(client):
    put(client, 'connected', Connected='true')
    assert get(client, 'cansetshutter')['Value'] is True
    for attr in ('cansetazimuth', 'canslave', 'canpark', 'canfindhome',
                 'cansetpark', 'cansyncazimuth', 'cansetaltitude'):
        assert get(client, attr)['Value'] is False, attr


def test_azimuth_shaped_members_are_not_implemented(client):
    put(client, 'connected', Connected='true')
    for attr in ('azimuth', 'altitude', 'atpark'):
        assert get(client, attr)['ErrorNumber'] == ERR_NOT_IMPLEMENTED, attr
    for attr in ('slewtoazimuth', 'synctoazimuth', 'findhome', 'park',
                 'setpark', 'slewtoaltitude'):
        assert put(client, attr)['ErrorNumber'] == ERR_NOT_IMPLEMENTED, attr


def test_athome_returns_false_rather_than_raising(client):
    # deliberately unlike AtPark: the spec ties AtHome to a sensor, not to
    # CanFindHome, and Conform checks the asymmetry
    put(client, 'connected', Connected='true')
    result = get(client, 'athome')
    assert result['ErrorNumber'] == 0
    assert result['Value'] is False


def test_openshutter_starts_motion_and_slewing_becomes_true(client):
    put(client, 'connected', Connected='true')
    assert put(client, 'openshutter')['ErrorNumber'] == 0
    # open_both stages the two motors on a background thread; the first starts
    # immediately, so Slewing must already be true when the call returns
    deadline = __import__('time').time() + 2
    while __import__('time').time() < deadline:
        if get(client, 'slewing')['Value']:
            break
        __import__('time').sleep(0.02)
    assert get(client, 'slewing')['Value'] is True
    assert put(client, 'abortslew')['ErrorNumber'] == 0
    assert not BOARD.motor_channels()


def test_supportedactions_advertises_the_per_shell_aperture(client):
    actions = get(client, 'supportedactions')['Value']
    assert ACTION_SET_APERTURE in actions
    assert ACTION_GET_STATUS in actions
    assert ACTION_GET_CAPABILITIES in actions


def test_set_shell_aperture_drives_one_shell(client):
    put(client, 'connected', Connected='true')
    body = json.dumps({'shell': 'east', 'percent': 60})
    result = put(client, 'action', Action=ACTION_SET_APERTURE,
                 Parameters=__import__('urllib.parse', fromlist=['quote']).quote(body))
    assert result['ErrorNumber'] == 0
    payload = json.loads(result['Value'])
    assert payload['accepted'] is True
    assert payload['targets'] == {'east': 141}
    assert BOARD.EAST_OPEN in BOARD.outputs
    put(client, 'abortslew')


def test_unknown_action_returns_action_not_implemented(client):
    put(client, 'connected', Connected='true')
    result = put(client, 'action', Action='Greenhill:Nope', Parameters='')
    assert result['ErrorNumber'] == ERR_ACTION_NOT_IMPLEMENTED


@pytest.mark.parametrize("payload", [
    '{"shell":"middle","percent":50}',
    '{"shell":"east","percent":120}',
    '{"shell":"east","percent":-1}',
    '{"shell":"east","percent":"half"}',
    '{"shell":"east"}',
    'not json at all',
])
def test_bad_aperture_arguments_are_rejected_not_guessed(client, payload):
    put(client, 'connected', Connected='true')
    quote = __import__('urllib.parse', fromlist=['quote']).quote
    result = put(client, 'action', Action=ACTION_SET_APERTURE,
                 Parameters=quote(payload))
    assert result['ErrorNumber'] == ERR_INVALID_VALUE, payload
    assert not BOARD.motor_channels(), f'a bad request moved a motor: {payload}'


def test_capabilities_manifest_describes_the_aperture_arguments(client):
    quote = __import__('urllib.parse', fromlist=['quote']).quote
    put(client, 'connected', Connected='true')
    result = put(client, 'action', Action=ACTION_GET_CAPABILITIES, Parameters=quote(''))
    manifest = json.loads(result['Value'])
    assert manifest['schemaVersion'] == 1
    cmd = next(c for c in manifest['commands'] if c['id'] == ACTION_SET_APERTURE)
    names = {a['name']: a for a in cmd['args']}
    assert names['shell']['choices'] == ['east', 'west', 'both']
    assert (names['percent']['minimum'], names['percent']['maximum']) == (0, 100)
    # every advertised action must actually be dispatchable
    for command in manifest['commands']:
        assert command['id'] in get(client, 'supportedactions')['Value']


def test_a_latched_fault_blocks_open_but_never_close(client):
    put(client, 'connected', Connected='true')
    import dome as dome_module
    dome_module.dome_dev._dome.e_fault = 'test fault'

    assert get(client, 'shutterstatus')['Value'] == int(ShutterState.shutterError)
    assert put(client, 'openshutter')['ErrorNumber'] == ERR_INVALID_OPERATION
    assert not BOARD.motor_channels(), 'opened despite a latched fault'

    # closing must always be permitted
    assert put(client, 'closeshutter')['ErrorNumber'] == 0
    put(client, 'abortslew')

    quote = __import__('urllib.parse', fromlist=['quote']).quote
    cleared = put(client, 'action', Action=ACTION_CLEAR_FAULT,
                  Parameters=quote(json.dumps({'shell': 'both'})))
    assert cleared['ErrorNumber'] == 0
    assert put(client, 'openshutter')['ErrorNumber'] == 0
    put(client, 'abortslew')


def test_devicestate_returns_the_platform7_bulk_read(client):
    put(client, 'connected', Connected='true')
    values = {sv['Name']: sv['Value'] for sv in get(client, 'devicestate')['Value']}
    assert 'ShutterStatus' in values
    assert 'Slewing' in values
    assert 'TimeStamp' in values
