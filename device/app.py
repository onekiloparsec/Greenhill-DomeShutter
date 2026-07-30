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
import socket
import sys
import traceback
from enum import IntEnum
from wsgiref.simple_server import WSGIRequestHandler, make_server

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


class LoggingWSGIRequestHandler(WSGIRequestHandler):
    """Subclass of WSGIRequestHandler allowing us to control WSGI server's logging"""

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
    logger = log.init_logging()
    log.logger = logger
    exceptions.logger = logger
    discovery.logger = logger
    dome.logger = logger
    set_shr_logger(logger)

    if not acquire_single_instance_lock():
        logger.error('==STARTUP FAILED== Another instance of the dome server (or '
                     'the legacy PySide6 app) already owns the K8055. Two '
                     'processes writing the same relay outputs is unsafe; '
                     'refusing to start.')
        sys.exit(1)

    dome.start_dome_device(logger)

    sys.excepthook = custom_excepthook

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
                         handler_class=LoggingWSGIRequestHandler) as httpd:
            logger.info(f'==STARTUP== Greenhill dome server on '
                        f'{Config.ip_address}:{Config.port}. Time stamps are UTC.')
            httpd.serve_forever()
    finally:
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
