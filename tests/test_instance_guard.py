"""Single-instance guard tests.

The field incident these exist for: Ctrl-C the server, restart it straight
away, and be refused with "another instance already owns the K8055" -- because
the restart raced the old process, which was still releasing the board. The
guard now waits a bounded grace for the port instead of failing on the first
bind, and the shutdown path restores default signal handlers so a wedged
release can always be killed (freeing the port) with a second Ctrl-C.
"""
import signal
import socket
import threading

import pytest

import app


@pytest.fixture
def guard_state(monkeypatch):
    """Fresh guard global, quick polling, and no leaked port after the test.

    Leaking the bound guard socket would make every later test that starts a
    real server fail with the very refusal this file is about.
    """
    monkeypatch.setattr(app, '_instance_guard', None)
    monkeypatch.setattr(app, '_GUARD_POLL_SECONDS', 0.05)
    yield
    if app._instance_guard is not None:
        app._instance_guard.close()
        app._instance_guard = None


def _hold_guard_port():
    incumbent = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    incumbent.bind(('127.0.0.1', app._SINGLE_INSTANCE_PORT))
    incumbent.listen(1)
    return incumbent


def test_acquires_a_free_port_without_waiting(guard_state, capsys):
    assert app.acquire_single_instance_lock() is True
    assert app._instance_guard is not None
    assert capsys.readouterr().err == ''


def test_refused_immediately_without_grace(guard_state, capsys):
    incumbent = _hold_guard_port()
    try:
        assert app.acquire_single_instance_lock(grace_seconds=0.0) is False
    finally:
        incumbent.close()
    # A single failed attempt announces nothing: there was no wait to explain.
    assert 'Waiting' not in capsys.readouterr().err


def test_waits_out_a_dying_incumbent(guard_state, capsys):
    """The incident, in miniature: the port is released a moment after the new
    server asks for it, exactly as when a restart races the old shutdown."""
    incumbent = _hold_guard_port()
    releaser = threading.Timer(0.3, incumbent.close)
    releaser.start()
    try:
        assert app.acquire_single_instance_lock(grace_seconds=5.0) is True
    finally:
        releaser.join()

    err = capsys.readouterr().err
    assert 'previous dome server may still be shutting down' in err


def test_gives_up_when_the_incumbent_stays(guard_state, capsys):
    incumbent = _hold_guard_port()
    try:
        assert app.acquire_single_instance_lock(grace_seconds=0.4) is False
    finally:
        incumbent.close()

    err = capsys.readouterr().err
    assert 'previous dome server may still be shutting down' in err


def test_restore_default_signal_handlers():
    """On the way into the shutdown path, every further Ctrl-C or kill must act
    at the C level (SIG_DFL): a Python-level handler cannot run while the main
    thread is wedged in a K8055 DLL call, and a wedged process squats the guard
    port, refusing every restart."""
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        signal.signal(signal.SIGINT, lambda *a: None)
        signal.signal(signal.SIGTERM, app._signal_shutdown)

        app._restore_default_signal_handlers()

        assert signal.getsignal(signal.SIGINT) is signal.SIG_DFL
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)
