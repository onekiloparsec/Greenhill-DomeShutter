# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# app.py - Alpaca device server for the Greenhill clamshell dome
#
# Adapted from the AlpycaDevice sample app.py --
# https://github.com/ASCOMInitiative/AlpycaDevice -- MIT, (c) 2022-2024 Bob Denny.
# See LICENSE-AlpycaDevice.txt. Changes: serves 'dome' instead of 'rotator',
# and refuses to start while another process holds the K8055.
# -----------------------------------------------------------------------------
import inspect
import signal
import socket
import sys
import traceback
from enum import IntEnum
from socketserver import ThreadingMixIn
from wsgiref.simple_server import (ServerHandler, WSGIRequestHandler,
                                   WSGIServer, make_server)

import discovery
import dome
import exceptions
import log
import management
import setup
from config import Config
from discovery import DiscoveryResponder
from falcon import App, HTTPInternalServerError, Request, Response
from shr import set_shr_logger

API_VERSION = 1

# Only ONE process may own the K8055 at a time. Two writers to the same output
# register, with no lock between them, is how you get a motor energised by one
# process and believed stopped by the other. The legacy PySide6 app opens the
# board directly, so this server must refuse to start alongside it.
_SINGLE_INSTANCE_PORT = 50815
_instance_guard = None


def acquire_single_instance_lock() -> bool:
    """
    Bind a loopback port as a process-wide mutex. Chosen over a lock file
    because the OS releases it even if we are killed with SIGKILL, where a stale
    lock file would block every subsequent start.
    """
    global _instance_guard
    guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        guard.bind(('127.0.0.1', _SINGLE_INSTANCE_PORT))
        guard.listen(1)
    except OSError:
        guard.close()
        return False
    _instance_guard = guard
    return True


# The K8055 holds its output latches in hardware. A process that dies without
# clearing them leaves a motor energised and the shutter driving with nothing
# supervising it, so every exit path that CAN be caught must be.
#
# Ctrl-C was always covered: Python's default SIGINT handler raises
# KeyboardInterrupt, which unwinds through main()'s finally. SIGTERM was not --
# its default action terminates the process outright, running neither that
# finally nor atexit.
#
# Dome_Control installs handlers of its own, but signal.signal() is legal only on
# the main thread and it says so (dome_shutter.py, _install_exit_hooks). On this
# server the board is opened from a WSGI worker thread, the first time a client
# sets Connected = true, so that path always takes the early return and leaves
# only its atexit hook -- which SIGTERM does not run either. These are therefore
# the only signal handlers the Alpaca server has.
_shutdown_signal = None


def _signal_shutdown(signum, frame):
    """Unwind main() so its finally block de-energises the dome."""
    global _shutdown_signal
    _shutdown_signal = signum
    # A second signal kills us outright. Releasing the board can block inside the
    # K8055 DLL, and an operator who has decided this process must die now needs
    # a way to say so that does not depend on the DLL answering.
    signal.signal(signum, signal.SIG_DFL)
    # Raised on the main thread at the point of interruption -- inside
    # serve_forever()'s select -- so it propagates exactly as KeyboardInterrupt
    # already does. Do NOT call httpd.shutdown() here: it waits on the
    # serve_forever loop that this very thread is running, and would deadlock.
    sys.exit(128 + signum)


def install_signal_handlers() -> list:
    """Install the shutdown handlers. Returns the signal names actually caught.

    Call from the main thread. SIGBREAK is Windows' Ctrl-Break and does not
    exist elsewhere; SIGTERM exists everywhere but is only really delivered on
    POSIX -- see the shutdown notes in docs/ALPACA.md for what remains
    uncatchable on Windows.
    """
    installed = []
    for signame in ('SIGTERM', 'SIGBREAK'):
        sig = getattr(signal, signame, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _signal_shutdown)
        except (ValueError, OSError):
            continue                # not supported here, or not the main thread
        installed.append(signame)
    return installed


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    """
    A WSGI server that handles each connection on its own thread.

    wsgiref's plain WSGIServer is single-threaded: one slow request blocks every
    other client. That matters here because ASCOM clients poll several
    properties at a time, and because a request arriving while the dome lock is
    held (a shell reversing direction holds it briefly) would stall the whole
    server rather than just that call.
    """
    daemon_threads = True   # never let a stuck request block process exit


class Http11ServerHandler(ServerHandler):
    """WSGI handler that writes an HTTP/1.1 status line.

    wsgiref.handlers.BaseHandler hardcodes http_version = '1.0', and it -- not
    WSGIRequestHandler.protocol_version -- is what writes the status line. So
    setting protocol_version alone produces a server that negotiates keep-alive
    at the socket layer while telling the client "HTTP/1.0", i.e. "I am closing
    this connection".
    """
    http_version = '1.1'


class LoggingWSGIRequestHandler(WSGIRequestHandler):
    """Subclass of WSGIRequestHandler allowing us to control WSGI server's logging"""

    # Every ASCOM .NET client (Conform Universal included) pools connections
    # through HttpClient. Served as HTTP/1.0 the connection closes after each
    # response, and the client's next request on that pooled socket is reset --
    # surfacing as "An error occurred while sending the request" on whichever
    # member happened to be next, which is why it moved around between runs.
    # Reproduced against the reference ASCOM simulator, which keeps the
    # connection open and passes cleanly.
    protocol_version = 'HTTP/1.1'

    # Idle keep-alive connections hold a thread each, so drop them.
    timeout = 60

    def handle(self):
        """Serve every request on this connection, rather than just the first.

        wsgiref's WSGIRequestHandler.handle() serves exactly one request and
        returns, so the connection always closes -- there is no keep-alive to be
        had from it no matter what protocol_version says. This restores the
        request loop that BaseHTTPRequestHandler.handle() normally provides.
        """
        self.close_connection = True
        self.handle_one_request()
        while not self.close_connection:
            self.handle_one_request()

    def handle_one_request(self):
        """One request, dispatched through WSGI. Mirrors wsgiref's handle()."""
        try:
            self.raw_requestline = self.rfile.readline(65537)
        except (TimeoutError, socket.timeout, ConnectionError, OSError):
            self.close_connection = True
            return

        if not self.raw_requestline:          # client hung up
            self.close_connection = True
            return
        if len(self.raw_requestline) > 65536:
            self.requestline = ''
            self.request_version = ''
            self.command = ''
            self.send_error(414)
            self.close_connection = True
            return
        if not self.parse_request():          # an error code has been sent already
            self.close_connection = True
            return

        handler = Http11ServerHandler(
            self.rfile, self.wfile, self.get_stderr(), self.get_environ(),
            multithread=True,
        )
        handler.request_handler = self        # backpointer for logging
        try:
            handler.run(self.server.get_app())
        except (ConnectionError, OSError):
            # client vanished mid-response; nothing to report, just stop
            self.close_connection = True
            return
        # parse_request() has already set close_connection from the request's
        # HTTP version and Connection header; honour it.

    def log_message(self, format: str, *args):
        # Requests are logged on the way in by shr.log_request, which keeps the
        # log in causal order. Suppress the wsgiref duplicate.
        pass


def init_routes(app: App, devname: str, module):
    """Route each responder class in `module` to its Alpaca URI by class name."""
    memlist = inspect.getmembers(module, inspect.isclass)
    for cname, ctype in memlist:
        # Only classes *defined* in the module and not the enum classes
        if ctype.__module__ == module.__name__ and not issubclass(ctype, IntEnum):
            app.add_route(
                f'/api/v{API_VERSION}/{devname}/{{devnum:int(min=0)}}/{cname.lower()}',
                ctype())


def custom_excepthook(exc_type, exc_value, exc_traceback):
    """Last-chance handler, so an unhandled exception reaches the logfile."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    log.logger.error(f'An uncaught {exc_type.__name__} exception occurred:')
    log.logger.error(exc_value)

    if Config.verbose_driver_exceptions and exc_traceback:
        for line in traceback.format_tb(exc_traceback):
            log.logger.error(repr(line))


def falcon_uncaught_exception_handler(req: Request, resp: Response,
                                     ex: BaseException, params):
    """Log exceptions escaping a responder instead of losing them to stdout."""
    exc = sys.exc_info()
    custom_excepthook(exc[0], exc[1], exc[2])
    raise HTTPInternalServerError(
        'Internal Server Error',
        'Alpaca endpoint responder failed. See logfile.')


def main():
    # The guard comes FIRST, before the logger, and reports to stderr rather
    # than to the log. init_logging() rotates alpyca.log, and rotating means
    # renaming a file the incumbent still has open: on Windows that rename
    # raises, so startup died with an unexplained log-file error instead of the
    # message below, and on Linux it silently succeeded and moved the running
    # server's log out from under it. Neither is a thing to do to a process that
    # is at that moment driving a shutter.
    if not acquire_single_instance_lock():
        print('==STARTUP FAILED== Another instance of the dome server (or '
              'the legacy PySide6 app) already owns the K8055. Two '
              'processes writing the same relay outputs is unsafe; '
              'refusing to start.', file=sys.stderr)
        sys.exit(1)

    logger = log.init_logging()
    log.logger = logger
    exceptions.logger = logger
    discovery.logger = logger
    dome.logger = logger
    set_shr_logger(logger)

    dome.start_dome_device(logger)

    sys.excepthook = custom_excepthook

    installed = install_signal_handlers()
    if 'SIGTERM' not in installed:
        logger.warning('==SIGNALS== No SIGTERM handler could be installed. A '
                       'kill will terminate this process without de-energising '
                       'the motors; stop the server with Ctrl-C instead.')
    elif sys.platform == 'win32':
        # Installing the handler succeeds here and buys nothing: Windows has no
        # SIGTERM to deliver. Say so, rather than let a silent startup imply a
        # cover that is not there.
        logger.warning('==SIGNALS== Windows delivers no SIGTERM, and closing '
                       'this console window cannot be caught at all -- either '
                       'leaves the K8055 relays energised. Stop the server with '
                       'Ctrl-C or Ctrl-Break.')

    # Discovery is a convenience: it lets clients find us without being told an
    # address. Losing it must NOT stop the dome from being controllable, so a
    # bind failure (another Alpaca driver, ASCOM Remote, or Docker already
    # holding UDP 32227) is a warning, not a fatal error.
    try:
        _DSC = DiscoveryResponder(Config.ip_address, Config.port)
    except Exception as ex:
        logger.warning(f'==DISCOVERY DISABLED== Could not bind the Alpaca discovery '
                       f'port: {ex}. The dome is still fully controllable at '
                       f'{Config.ip_address or "0.0.0.0"}:{Config.port}, but clients '
                       f'must be given that address explicitly.')

    falc_app = App()
    init_routes(falc_app, 'dome', dome)
    falc_app.add_route('/management/apiversions', management.apiversions())
    falc_app.add_route(f'/management/v{API_VERSION}/description',
                       management.description())
    falc_app.add_route(f'/management/v{API_VERSION}/configureddevices',
                       management.configureddevices())
    falc_app.add_route('/setup', setup.svrsetup())
    falc_app.add_route(f'/setup/v{API_VERSION}/dome/{{devnum}}/setup',
                       setup.devsetup())
    falc_app.add_error_handler(Exception, falcon_uncaught_exception_handler)

    try:
        with make_server(Config.ip_address, Config.port, falc_app,
                         server_class=ThreadingWSGIServer,
                         handler_class=LoggingWSGIRequestHandler) as httpd:
            logger.info(f'==STARTUP== Greenhill dome server on '
                        f'{Config.ip_address}:{Config.port}. Time stamps are UTC.')
            httpd.serve_forever()
    finally:
        # Logged before the de-energise, not after: if releasing the board wedges
        # in the DLL, the log still says what asked the server to stop.
        if _shutdown_signal is not None:
            logger.info(f'==SIGNAL== {signal.Signals(_shutdown_signal).name} '
                        f'received; stopping the dome.')
        # Whatever brings the server down, the motors must not be left running.
        # GreenhillDome.disconnect() is idempotent and safe when never connected.
        try:
            if dome.dome_dev is not None:
                dome.dome_dev.disconnect()
        except Exception as ex:
            logger.error(f'Error de-energising the dome during shutdown: {ex}')
        logger.info('==SHUTDOWN== Dome server stopped.')


if __name__ == '__main__':
    main()
