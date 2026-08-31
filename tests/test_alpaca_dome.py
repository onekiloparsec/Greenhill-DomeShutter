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
    # shutdown(), not disconnect(): the latter now waits for the LAST client to
    # let go, and the test may have connected under any ClientID.
    dome_module.dome_dev.shutdown()


def get(client, attr, **params):
    return client.simulate_get(f'/api/v1/dome/0/{attr}',
                               params={'ClientID': '1', 'ClientTransactionID': '1',
                                       **params}).json


def put(client, attr, **body):
    # ClientID goes in the BODY of a PUT, which is where Alpaca puts it and
    # where every real client puts it. It used to be passed as a query
    # parameter here, which the server does not read for a PUT -- harmless
    # while Connected was one global flag, and not harmless once connection
    # state became per client.
    fields = {'ClientID': '1', 'ClientTransactionID': '1'}
    fields.update(body)
    return client.simulate_put(f'/api/v1/dome/0/{attr}',
                               body='&'.join(f'{k}={v}' for k, v in fields.items()),
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



class TestUncaughtResponderException:
    """What a client sees when a responder raises something nobody caught.

    Most responders catch their own exceptions and answer with a
    DriverException inside the Alpaca envelope. This covers the ones that do
    not -- and, more to the point, the app-level handler that exists to catch
    whatever they miss.

    That handler used to raise a TypeError of its own: falcon made
    HTTPInternalServerError's arguments keyword-only in 3.0, and the
    AlpycaDevice sample it came from predates that. So the real fault was
    discarded and a TypeError escaped the WSGI app instead of becoming a 500 --
    the one thing a handler whose job is reporting errors must never do.
    Nothing caught it because no test had ever made a responder fail.
    """

    @pytest.fixture
    def broken_client(self, board):
        import logging

        import app as device_app
        import dome as dome_module
        import exceptions as device_exceptions
        import log as device_log
        import shr

        quiet = logging.getLogger('greenhill-dome-tests')
        quiet.addHandler(logging.NullHandler())
        shr.set_shr_logger(quiet)
        device_exceptions.logger = quiet
        dome_module.logger = quiet
        # custom_excepthook logs through this one, which main() normally sets.
        device_log.logger = quiet

        class BrokenDevice:
            def is_connected(self, client_id):
                raise RuntimeError('simulated driver fault')

        previous = dome_module.dome_dev
        dome_module.dome_dev = BrokenDevice()

        falc_app = falcon.App()
        device_app.init_routes(falc_app, 'dome', dome_module)
        falc_app.add_error_handler(
            Exception, device_app.falcon_uncaught_exception_handler)
        try:
            yield testing.TestClient(falc_app)
        finally:
            dome_module.dome_dev = previous

    def test_returns_500_rather_than_escaping_the_app(self, broken_client):
        response = broken_client.simulate_get(
            '/api/v1/dome/0/connected',
            params={'ClientID': '1', 'ClientTransactionID': '1'})
        assert response.status_code == 500

    def test_the_real_fault_reaches_the_log(self, broken_client, caplog):
        # The whole point of the handler. The 500 body deliberately says
        # nothing about the cause, so if it is not logged it is simply gone.
        with caplog.at_level('ERROR'):
            broken_client.simulate_get(
                '/api/v1/dome/0/connected',
                params={'ClientID': '1', 'ClientTransactionID': '1'})
        assert 'simulated driver fault' in caplog.text


# --- two clients, two connections ------------------------------------------

def put_as(client, attr, client_id, **body):
    fields = {'ClientID': client_id, 'ClientTransactionID': '1'}
    fields.update(body)
    return client.simulate_put(f'/api/v1/dome/0/{attr}',
                               body='&'.join(f'{k}={v}' for k, v in fields.items()),
                               headers={'Content-Type':
                                        'application/x-www-form-urlencoded'}).json


def get_as(client, attr, client_id, **params):
    return client.simulate_get(f'/api/v1/dome/0/{attr}',
                               params={'ClientID': client_id,
                                       'ClientTransactionID': '1', **params}).json


class TestPerClientConnection:
    """ASCOM has one `Connected` property; Alpaca serves many clients.

    This dome now has two: Arcsecond, and the weather service that closes it in
    bad weather. Sharing one flag between them meant either could de-energise
    the motors under the other -- possibly mid-close, which is the one moment
    it must not happen.
    """

    ARCSECOND = '4242'
    WEATHER = '1782'

    def test_connecting_one_client_does_not_connect_the_other(self, client):
        put_as(client, 'connected', self.ARCSECOND, Connected='true')
        assert get_as(client, 'connected', self.ARCSECOND)['Value'] is True
        assert get_as(client, 'connected', self.WEATHER)['Value'] is False

    def test_one_client_disconnecting_leaves_the_other_connected(self, client, board):
        put_as(client, 'connected', self.ARCSECOND, Connected='true')
        put_as(client, 'connected', self.WEATHER, Connected='true')

        put_as(client, 'connected', self.ARCSECOND, Connected='false')

        assert get_as(client, 'connected', self.ARCSECOND)['Value'] is False
        assert get_as(client, 'connected', self.WEATHER)['Value'] is True

    def test_the_board_stays_open_while_anyone_is_connected(self, client,
                                                            board):
        # The point of the whole exercise. Arcsecond going away must not
        # de-energise the motors while the weather service is mid-close.
        import dome as dome_module
        put_as(client, 'connected', self.ARCSECOND, Connected='true')
        put_as(client, 'connected', self.WEATHER, Connected='true')
        assert dome_module.dome_dev.hardware_open is True

        put_as(client, 'connected', self.ARCSECOND, Connected='false')
        assert dome_module.dome_dev.hardware_open is True

    def test_the_last_client_out_releases_the_board(self, client, board):
        # And the original rule still holds: a shell must never be left running
        # with nobody watching.
        import dome as dome_module
        put_as(client, 'connected', self.ARCSECOND, Connected='true')
        put_as(client, 'connected', self.WEATHER, Connected='true')
        put_as(client, 'connected', self.ARCSECOND, Connected='false')
        put_as(client, 'connected', self.WEATHER, Connected='false')
        assert dome_module.dome_dev.hardware_open is False

    def test_a_disconnected_client_cannot_command_the_dome(self, client, board):
        put_as(client, 'connected', self.ARCSECOND, Connected='true')
        result = put_as(client, 'closeshutter', self.WEATHER)
        assert result['ErrorNumber'] == ERR_NOT_CONNECTED

    def test_platform7_connect_and_disconnect_are_also_per_client(self, client,
                                                                  board):
        put_as(client, 'connect', self.ARCSECOND)
        put_as(client, 'connect', self.WEATHER)
        put_as(client, 'disconnect', self.ARCSECOND)
        assert get_as(client, 'connected', self.ARCSECOND)['Value'] is False
        assert get_as(client, 'connected', self.WEATHER)['Value'] is True

    def test_client_id_is_read_from_a_put_body(self, client, board):
        # Where Alpaca puts it, and where alpyca and our own client put it.
        import dome as dome_module
        put_as(client, 'connected', self.WEATHER, Connected='true')
        assert self.WEATHER in dome_module.dome_dev.clients.clients

    def test_client_id_in_a_put_query_string_is_still_honoured(self, client,
                                                              board):
        # Not where the spec puts it, but the failure would be silent: such a
        # client would connect as '0' and then never see itself connected.
        import dome as dome_module
        client.simulate_put(
            '/api/v1/dome/0/connected',
            params={'ClientID': '9911', 'ClientTransactionID': '1'},
            body='Connected=true',
            headers={'Content-Type': 'application/x-www-form-urlencoded'})
        assert '9911' in dome_module.dome_dev.clients.clients


class TestClientExpiry:
    """A client that crashes never says goodbye.

    The Python Alpaca library picks its ClientID with random.randint at import,
    so Arcsecond presents a fresh identity after every restart, and one per
    Celery worker. Without expiry those entries accumulate and "the last client
    disconnected" -- the condition that de-energises the motors -- never becomes
    true again.
    """

    def registry(self, timeout=300.0):
        from domedevice import ClientRegistry
        clock = {'now': 0.0}

        class Clock:
            def __call__(self): return clock['now']

        registry = ClientRegistry(timeout=timeout, clock=Clock())
        return registry, clock

    def test_a_silent_client_expires(self):
        registry, clock = self.registry(timeout=100.0)
        registry.connect('a')
        clock['now'] = 150.0
        assert registry.is_connected('a') is False

    def test_a_polling_client_does_not(self):
        registry, clock = self.registry(timeout=100.0)
        registry.connect('a')
        for step in range(1, 10):
            clock['now'] = step * 50.0
            assert registry.is_connected('a') is True

    def test_touch_does_not_enrol_a_stranger(self):
        # A discovery probe reading `name` must not become a connected client.
        registry, _ = self.registry()
        registry.touch('passer-by')
        assert registry.clients == []

    def test_sweep_reports_when_the_last_client_expires(self):
        registry, clock = self.registry(timeout=100.0)
        registry.connect('a')
        assert registry.sweep() is False
        clock['now'] = 150.0
        assert registry.sweep() is True
        assert registry.sweep() is False        # only once

    def test_expiry_releases_the_board(self, board):
        from domedevice import ClientRegistry, GreenhillDome
        clock = {'now': 0.0}

        class Clock:
            def __call__(self): return clock['now']

        dome = GreenhillDome(calibration=None, logger=None,
                             clients=ClientRegistry(timeout=100.0, clock=Clock()))
        dome.connect('a')
        assert dome.hardware_open is True
        clock['now'] = 150.0
        dome.sweep_clients()
        assert dome.hardware_open is False

    def test_a_restarted_client_under_a_new_id_does_not_pin_the_board(self, board):
        # Exactly what happens when a Celery worker restarts.
        from domedevice import ClientRegistry, GreenhillDome
        clock = {'now': 0.0}

        class Clock:
            def __call__(self): return clock['now']

        dome = GreenhillDome(calibration=None, logger=None,
                             clients=ClientRegistry(timeout=100.0, clock=Clock()))
        for generation, client_id in enumerate(('old', 'newer', 'newest')):
            clock['now'] = generation * 200.0
            dome.connect(client_id)
            dome.sweep_clients()
        assert dome.clients.clients == ['newest']
