# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# logging.py - Shared global logging object
# Part of the AlpycaDevice Alpaca skeleton/template device driver
#
# Author:   Robert B. Denny <rdenny@dc3.com> (rbd)
#
# Python Compatibility: Requires Python 3.7 or later
# GitHub: https://github.com/ASCOMInitiative/AlpycaDevice
#
# -----------------------------------------------------------------------------
# MIT License
#
# Copyright (c) 2022-2024 Bob Denny
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
# -----------------------------------------------------------------------------
# Edit History:
# 01-Jan-2023   rbd 0.1 Initial edit, moved from config.py
# 15-Jan-2023   rbd 0.1 Documentation. No logic changes.
# 08-Nov-2023   rbd 0.4 Log name is now 'alpyca'
# 17-Feb-2024   rbd 0.6 Additional documentation.

import logging
import logging.handlers
import os
import sys
import time
from config import Config

global logger
#logger: logging.Logger = None  # Master copy (root) of the logger
logger = None                   # Safe on Python 3.7 but no intellisense in VSCode etc.

# LOCAL CHANGE (not upstream AlpycaDevice): resolved against THIS FILE rather
# than the working directory, for the same reason config.py resolves config.toml
# that way. Upstream's bare 'alpyca.log' puts the log wherever the server
# happened to be launched from, so two runs started from different directories
# write two different logs and neither is where the operator looks.
#
# ALPYCA_LOG overrides it, for a deployment whose install directory is not
# writable and for tests, which must not scribble over the observatory's log.
LOG_PATH = (os.environ.get('ALPYCA_LOG')
            or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'alpyca.log'))


def _make_file_handler(formatter):
    """Build the rotating file handler, or return None if the log file is
    unusable.

    LOCAL CHANGE (not upstream AlpycaDevice). A dome server that will not start
    is a dome that cannot be closed, and nothing about a log file is worth that.
    Every failure here degrades to console-only logging rather than killing
    startup.
    """
    # Probe the file up front. delay=True means the constructor never touches
    # the disk, so without this an unwritable log directory surfaces as logging's
    # own "--- Logging error ---" printed alongside every record the server ever
    # emits, burying the dome's messages instead of standing aside.
    existed = os.path.exists(LOG_PATH)
    try:
        with open(LOG_PATH, 'a'):
            pass
    except OSError as ex:
        print(f'==LOGGING== Cannot write {LOG_PATH}: {ex}. Continuing with '
              f'console logging only.', file=sys.stderr)
        return None
    if not existed:
        # Undo the probe, or the rollover below would push an empty file into
        # the backups and cost a generation of real logs.
        try:
            os.remove(LOG_PATH)
        except OSError:
            pass

    handler = logging.handlers.RotatingFileHandler(LOG_PATH,
                                                    mode='w',
                                                    delay=True,     # Prevent creation of empty logs
                                                    maxBytes=Config.max_size_mb * 1000000,
                                                    backupCount=Config.num_keep_logs)
    handler.setLevel(Config.log_level)
    handler.setFormatter(formatter)

    # Start each run with a fresh log, keeping the previous one as .1. The
    # rollover *renames* the existing file, and on Windows a rename fails while
    # any process still holds it open -- a predecessor wedged in the K8055 DLL,
    # an antivirus scan, an editor. Refusing to serve the dome over that is the
    # wrong trade: keep the old file and append to it instead.
    try:
        handler.doRollover()
    except OSError as ex:
        # Append rather than truncate, so a file the incumbent may still be
        # writing survives. RotatingFileHandler already forces mode 'a' whenever
        # maxBytes > 0 -- the mode='w' above has never had any effect, the fresh
        # log comes from the rollover -- so this only bites if max_size_mb is
        # set to 0. delay=True means the stream is not open yet and
        # FileHandler._open() reads self.mode when it finally opens it.
        handler.mode = 'a'
        print(f'==LOGGING== Could not rotate {LOG_PATH}: {ex}. Appending to the '
              f'existing log instead.', file=sys.stderr)
    return handler


def init_logging():
    """ Create the logger - called at app startup

        **MASTER LOGGER**

        This single logger is used throughout. The module name (the param for
        get_logger()) isn't needed and would be 'root' anyway, sort of useless.
        Logs time stamps in UTC/ISO format, and with fractional seconds. Since
        our config options allow for suppression of logging to stdout, we remove
        the default stdout handler. Thank heaven that Python logging is
        thread-safe!

        This logger is passed around throughout the app and may be used
        throughout, even the device control. The :py:class:`config.Config` class
        has options to control the number of back generations of logs to keep,
        as well as the max size (at which point the log will be rotated). Also
        there is an option to cause logged messages to go to the console for
        debugging purposes. A new log is started each time the app is started.

    Returns:
        Customized Python logger.

    """

    logging.basicConfig(level=Config.log_level)
    logger = logging.getLogger()                # Root logger, see above
    formatter = logging.Formatter('%(asctime)s.%(msecs)03d %(levelname)s %(message)s', '%Y-%m-%dT%H:%M:%S')
    formatter.converter = time.gmtime           # UTC time
    stdout_handler = logger.handlers[0]         # This is the stdout handler, level set above
    stdout_handler.setFormatter(formatter)
    # Add a logfile handler, same formatter and level
    handler = _make_file_handler(formatter)
    if handler is not None:
        logger.addHandler(handler)
    if not Config.log_to_stdout and handler is not None:
        """
            This allows control of logging to stdout by simply
            removing the stdout handler from the logger's
            handler list. It's always handler[0] as created
            by logging.basicConfig()

            LOCAL CHANGE (not upstream AlpycaDevice): only when there is a
            logfile to drop it in favour of. With no file handler this would
            leave the server with nowhere at all to report a stuck shutter.
        """
        logger.debug('Logging to stdout disabled in settings')
        logger.removeHandler(stdout_handler)
    return logger