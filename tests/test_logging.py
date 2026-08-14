"""Startup logging tests.

All of these exist because of one field symptom: after the server was
interrupted or crashed, the next start died with an error about the log file
instead of starting. The dome was then unreachable over Alpaca until someone
went and deleted a file, which is the worst possible thing to need at the point
where you most want to close the shutters.

Two causes, both covered here: the log rollover renames a file the previous
process still holds open (fatal on Windows), and the single-instance guard was
checked only *after* that rollover, so the one message that explains the
situation never got printed.
"""
import logging
import os
import socket
import sys

import pytest

import app
import log as log_module


@pytest.fixture
def isolated_root():
    """Hand back the root logger untouched; init_logging() reconfigures it."""
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    yield root
    root.handlers[:] = saved[0]
    root.setLevel(saved[1])


@pytest.fixture
def log_path(tmp_path, monkeypatch):
    path = tmp_path / 'alpyca.log'
    monkeypatch.setattr(log_module, 'LOG_PATH', str(path))
    return path


def test_rollover_keeps_the_previous_log(log_path):
    """The normal path: last run's log is preserved as .1, this run starts clean."""
    log_path.write_text('PREVIOUS RUN\n')

    handler = log_module._make_file_handler(logging.Formatter('%(message)s'))
    handler.emit(logging.LogRecord('r', logging.INFO, __file__, 1,
                                   'THIS RUN', None, None))
    handler.close()

    assert log_path.read_text().strip() == 'THIS RUN'
    rotated = log_path.parent / (log_path.name + '.1')
    assert rotated.read_text().strip() == 'PREVIOUS RUN'


def test_locked_log_file_does_not_stop_the_server(log_path, monkeypatch, capsys):
    """A rollover that cannot rename must degrade, not raise.

    Models Windows' [WinError 32]: the file is openable by a second process but
    not renameable while the first still holds it. POSIX renames it happily, so
    the refusal has to be injected.
    """
    log_path.write_text('INCUMBENT\n')

    def refuse(self):
        raise PermissionError(32, 'The process cannot access the file because '
                                  'it is being used by another process')

    monkeypatch.setattr(logging.handlers.RotatingFileHandler, 'doRollover', refuse)

    handler = log_module._make_file_handler(logging.Formatter('%(message)s'))
    assert handler is not None
    handler.emit(logging.LogRecord('r', logging.INFO, __file__, 1,
                                   'NEW RUN', None, None))
    handler.close()

    # Appended, not truncated: the incumbent may still be writing this file.
    assert log_path.read_text().splitlines() == ['INCUMBENT', 'NEW RUN']
    assert 'Could not rotate' in capsys.readouterr().err


def test_unwritable_log_falls_back_to_console(tmp_path, monkeypatch, capsys):
    """No usable log file is a warning, never a reason to refuse to serve."""
    monkeypatch.setattr(log_module, 'LOG_PATH',
                        str(tmp_path / 'no-such-dir' / 'alpyca.log'))

    assert log_module._make_file_handler(logging.Formatter('%(message)s')) is None
    assert 'console logging only' in capsys.readouterr().err


def test_stdout_handler_survives_when_there_is_no_logfile(isolated_root, tmp_path,
                                                          monkeypatch):
    """log_to_stdout = false drops the console handler -- but only if there is a
    logfile to drop it in favour of. Otherwise the server has nowhere at all to
    report a stuck shutter."""
    monkeypatch.setattr(log_module, 'LOG_PATH',
                        str(tmp_path / 'no-such-dir' / 'alpyca.log'))
    monkeypatch.setattr(log_module.Config, 'log_to_stdout', False)

    logger = log_module.init_logging()

    assert logger.handlers, 'root logger left with no handlers at all'


def test_second_instance_is_refused_before_it_touches_the_log(monkeypatch, capsys):
    """The whole point of the ordering: an operator restarting while the old
    process is still alive must be told *that*, not handed a log-file error."""
    monkeypatch.setattr(app, '_instance_guard', None)
    incumbent = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    incumbent.bind(('127.0.0.1', app._SINGLE_INSTANCE_PORT))
    incumbent.listen(1)

    def fail(*args, **kwargs):
        pytest.fail('init_logging() ran before the single-instance guard')

    monkeypatch.setattr(app.log, 'init_logging', fail)
    try:
        with pytest.raises(SystemExit) as exit_info:
            app.main()
    finally:
        incumbent.close()

    assert exit_info.value.code == 1
    assert 'already owns the K8055' in capsys.readouterr().err


@pytest.mark.skipif('ALPYCA_LOG' in os.environ,
                    reason='the default path is what is under test')
def test_log_path_is_anchored_to_the_device_directory():
    """Not the working directory: two runs launched from different places must
    not write two different logs."""
    assert os.path.isabs(log_module.LOG_PATH)
    assert (os.path.dirname(log_module.LOG_PATH) ==
            os.path.dirname(os.path.abspath(log_module.__file__)))
