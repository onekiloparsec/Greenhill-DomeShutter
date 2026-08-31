"""Shutdown-path tests.

The K8055 latches its outputs in hardware, so a process that exits without
clearing them leaves a motor energised and the shutter driving unsupervised.
Ctrl-C always unwound through main()'s finally; SIGTERM did not, because its
default action terminates the process outright -- no finally, no atexit.

The end-to-end test at the bottom is the one that matters: a real server, a real
client connection, a real SIGTERM, and proof that the board was released before
the process died.
"""
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

import app
from config import Config

DEVICE_DIR = os.path.dirname(os.path.abspath(app.__file__))


@pytest.fixture
def restore_signals():
    """Put the interpreter's handlers back; these tests install real ones."""
    saved = {}
    for signame in ('SIGTERM', 'SIGBREAK'):
        sig = getattr(signal, signame, None)
        if sig is not None:
            saved[sig] = signal.getsignal(sig)
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)
    app._shutdown_signal = None


def test_handler_unwinds_the_main_thread(restore_signals):
    """SystemExit, not a direct shutdown call: main()'s finally is the only
    place that knows how to take the dome down, and it runs on this thread."""
    with pytest.raises(SystemExit) as exit_info:
        app._signal_shutdown(signal.SIGTERM, None)

    assert exit_info.value.code == 128 + signal.SIGTERM
    assert app._shutdown_signal == signal.SIGTERM


def test_a_second_signal_is_left_to_kill_us(restore_signals):
    """Releasing the board can block in the K8055 DLL. An operator who sends a
    second SIGTERM has decided this process must die regardless."""
    signal.signal(signal.SIGTERM, app._signal_shutdown)

    with pytest.raises(SystemExit):
        app._signal_shutdown(signal.SIGTERM, None)

    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL


@pytest.mark.skipif(sys.platform == 'win32',
                    reason='Windows has no deliverable SIGTERM; see docs/ALPACA.md')
def test_sigterm_is_installed(restore_signals):
    assert 'SIGTERM' in app.install_signal_handlers()
    assert signal.getsignal(signal.SIGTERM) is app._signal_shutdown


# --------------------------------------------------------------------------- #
# End to end                                                                   #
# --------------------------------------------------------------------------- #

def _put(port, member, body):
    request = urllib.request.Request(
        f'http://127.0.0.1:{port}/api/v1/dome/0/{member}',
        data=body.encode(), method='PUT',
        headers={'Content-Type': 'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status


def _wait_for_startup(server, deadline):
    """Block until the server says it is listening, or the wait runs out."""
    while time.monotonic() < deadline:
        if server.poll() is not None:
            pytest.fail(f'server exited during startup: {server.stdout.read()}')
        try:
            with urllib.request.urlopen(
                    'http://127.0.0.1:11111/management/apiversions', timeout=1):
                return
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    pytest.fail('server never came up')


@pytest.mark.skipif(sys.platform == 'win32',
                    reason='Windows has no deliverable SIGTERM; see docs/ALPACA.md')
@pytest.mark.skipif(not Config.log_to_stdout,
                    reason='this test reads the log off the child stdout')
def test_sigterm_releases_the_board_before_the_process_dies(tmp_path):
    """The regression this whole change exists for.

    Connect a client so the board is genuinely open -- before the fix the
    process died here with the outputs still latched -- then SIGTERM it and
    require the release to appear in the log.
    """
    environment = dict(os.environ, ALPYCA_LOG=str(tmp_path / 'alpyca.log'),
                       PYTHONUNBUFFERED='1')
    server = subprocess.Popen([sys.executable, 'simulate.py'], cwd=DEVICE_DIR,
                              env=environment, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True)
    try:
        _wait_for_startup(server, deadline=time.monotonic() + 30)
        assert _put(11111, 'connected', 'Connected=True') == 200
        assert _put(11111, 'openshutter', '') == 200
        time.sleep(0.5)         # let a motor actually be energised

        server.send_signal(signal.SIGTERM)
        output = server.communicate(timeout=30)[0]
    finally:
        if server.poll() is None:
            server.kill()
            server.communicate()

    assert server.returncode == 128 + signal.SIGTERM, output
    # Ordering matters as much as presence: the board must be released on the
    # way out, not merely mentioned somewhere in the log.
    positions = [output.find(marker) for marker in
                 ('==SIGNAL== SIGTERM', 'Disconnected from dome hardware',
                  '==SHUTDOWN==')]
    assert all(p != -1 for p in positions), output
    assert positions == sorted(positions), output


@pytest.mark.skipif(sys.platform == 'win32',
                    reason='no deliverable SIGINT to a child process on Windows')
def test_ctrl_c_then_immediate_restart_succeeds(tmp_path):
    """The field incident: Ctrl-C the server, restart straight away, and be
    told another instance owns the K8055. The restart must come up instead."""
    environment = dict(os.environ, ALPYCA_LOG=str(tmp_path / 'alpyca.log'),
                       PYTHONUNBUFFERED='1')
    server = subprocess.Popen([sys.executable, 'simulate.py'], cwd=DEVICE_DIR,
                              env=environment, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True)
    try:
        _wait_for_startup(server, deadline=time.monotonic() + 30)
        assert _put(11111, 'connected', 'Connected=True') == 200

        server.send_signal(signal.SIGINT)             # Ctrl-C
        output = server.communicate(timeout=30)[0]
    finally:
        if server.poll() is None:
            server.kill()
            server.communicate()

    # Died BY SIGINT (the interpreter re-raises it), with the board released.
    assert server.returncode == -signal.SIGINT, output
    assert 'Disconnected from dome hardware' in output
    assert '==SHUTDOWN==' in output

    restarted = subprocess.Popen([sys.executable, 'simulate.py'],
                                 cwd=DEVICE_DIR, env=environment,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
    try:
        # _wait_for_startup fails loudly, printing ==STARTUP FAILED== if the
        # guard refused -- which is exactly the regression under test.
        _wait_for_startup(restarted, deadline=time.monotonic() + 30)
    finally:
        if restarted.poll() is None:
            restarted.send_signal(signal.SIGINT)
            try:
                restarted.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                restarted.kill()
                restarted.communicate()


def test_startup_waits_for_a_dying_incumbent(tmp_path):
    """A restart that lands while the old process still holds the guard port
    must wait it out, not refuse. Modelled by holding the port ourselves and
    releasing it a moment after the new server starts asking."""
    incumbent = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    incumbent.bind(('127.0.0.1', app._SINGLE_INSTANCE_PORT))
    incumbent.listen(1)

    environment = dict(os.environ, ALPYCA_LOG=str(tmp_path / 'alpyca.log'),
                       PYTHONUNBUFFERED='1')
    server = subprocess.Popen([sys.executable, 'simulate.py'], cwd=DEVICE_DIR,
                              env=environment, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True)
    try:
        # Held port: the server must be waiting, neither serving nor dead.
        time.sleep(1.5)
        assert server.poll() is None, server.communicate()[0]

        incumbent.close()                             # the old instance exits
        _wait_for_startup(server, deadline=time.monotonic() + 30)
    finally:
        incumbent.close()
        if server.poll() is None:
            server.terminate()
        try:
            output = server.communicate(timeout=15)[0]
        except subprocess.TimeoutExpired:
            server.kill()
            output = server.communicate()[0]

    assert 'previous dome server may still be shutting down' in output
    assert '==STARTUP FAILED==' not in output
