# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# dome.py - Alpaca API responders for the Greenhill clamshell dome
#
# Built from the AlpycaDevice template (templates/dome.py) --
# https://github.com/ASCOMInitiative/AlpycaDevice -- MIT, (c) 2022-2024 Bob Denny.
# See LICENSE-AlpycaDevice.txt.
#
# All hardware behaviour lives in domedevice.GreenhillDome; this module is only
# the HTTP surface. Keep it that way: it makes the interesting logic testable
# without a web server.
# -----------------------------------------------------------------------------
from datetime import datetime, timezone
from logging import Logger

from falcon import Request, Response, before

from config import Config
from domedevice import (ACTION_CLEAR_FAULT, ACTION_GET_CAPABILITIES,
                        ACTION_GET_STATUS, ACTION_SET_APERTURE, ActionError,
                        GreenhillDome, ShutterState)
from exceptions import *        # Nothing but exception classes
from shr import (MethodResponse, PreProcessRequest, PropertyResponse,
                 StateValue, get_request_field, to_bool)

logger: Logger = None

# ----------------------
# MULTI-INSTANCE SUPPORT
# ----------------------
maxdev = 0                      # Single instance

# -----------
# DEVICE INFO
# -----------
class DomeMetadata:
    """ Metadata describing the Dome Device """
    Name = 'Greenhill Clamshell Dome'
    Version = '0.1.0'
    Description = 'Greenhill Observatory 50cm clamshell dome (east/west shells)'
    DeviceType = 'Dome'
    # Stable across restarts: the Arcsecond relay identifies devices by UniqueID
    # and silently drops any device that does not present one.
    DeviceID = 'b8f2c1e4-6a3d-4e77-9c15-2d8a7f0b3e91'
    Info = ('Greenhill clamshell dome\n'
            'Two independently driven shells on a Velleman K8055\n'
            'Implements IDomeV3 with vendor Actions for per-shell aperture')
    MaxDeviceNumber = maxdev
    InterfaceVersion = 3        # IDomeV3 (ASCOM Platform 7)


# ----------------
# DEVICE INSTANCE
# ----------------
dome_dev: GreenhillDome = None


def start_dome_device(logger: Logger):
    """Create the device object. Does NOT touch the hardware: the board is only
    opened when a client sets Connected = True."""
    global dome_dev
    calibration = None
    if Config.calibrated:
        calibration = {
            'east_closed': Config.east_closed,
            'east_open': Config.east_open,
            'west_closed': Config.west_closed,
            'west_open': Config.west_open,
            'tolerance': Config.tolerance,
        }
    else:
        logger.warning('==CALIBRATION== config.toml has calibrated = false. The '
                       'built-in analogue limits are GUESSES: apertures will be '
                       'wrong and a close may stall into the hard stop. Measure '
                       'the real values before driving the dome unattended.')
    dome_dev = GreenhillDome(calibration=calibration, logger=logger)


# --------------------
# SHARED RESPONDER BITS
# --------------------
def _not_connected(req: Request, resp: Response) -> bool:
    """
    Emit the standard NotConnected response and report whether it was emitted.
    Uses the response type matching the HTTP method: a PUT must come back as a
    MethodResponse, not a PropertyResponse.
    """
    if dome_dev is None or not dome_dev.connected:
        err = NotConnectedException()
        if req.method == 'PUT':
            resp.text = MethodResponse(req, err).json
        else:
            resp.text = PropertyResponse(None, req, err).json
        return True
    return False


def _capability(value: bool):
    """Responder body for a constant capability flag."""
    def on_get(self, req: Request, resp: Response, devnum: int):
        if _not_connected(req, resp):
            return
        resp.text = PropertyResponse(value, req).json
    return on_get


# --------------------
# RESOURCE CONTROLLERS
# --------------------

@before(PreProcessRequest(maxdev))
class action:
    def on_put(self, req: Request, resp: Response, devnum: int):
        if _not_connected(req, resp):
            return
        name = get_request_field('Action', req)          # 400 if missing
        parameters = get_request_field('Parameters', req, False, '')
        try:
            value = dome_dev.action(name, parameters)
            resp.text = MethodResponse(req, value=value).json
        except NotImplementedError:
            resp.text = MethodResponse(
                req, ActionNotImplementedException(f'Action "{name}" is not supported')).json
        except ActionError as ex:
            resp.text = MethodResponse(req, InvalidValueException(str(ex))).json
        except Exception as ex:
            resp.text = MethodResponse(
                req, DriverException(0x500, 'Dome.Action failed', ex)).json


@before(PreProcessRequest(maxdev))
class commandblind:
    def on_put(self, req: Request, resp: Response, devnum: int):
        resp.text = MethodResponse(req, NotImplementedException()).json


@before(PreProcessRequest(maxdev))
class commandbool:
    def on_put(self, req: Request, resp: Response, devnum: int):
        resp.text = MethodResponse(req, NotImplementedException()).json


@before(PreProcessRequest(maxdev))
class commandstring:
    def on_put(self, req: Request, resp: Response, devnum: int):
        resp.text = MethodResponse(req, NotImplementedException()).json


@before(PreProcessRequest(maxdev))
class connect:
    """Platform 7 asynchronous connect. This driver connects fast enough to do
    it synchronously, so Connecting is never True."""
    def on_put(self, req: Request, resp: Response, devnum: int):
        try:
            dome_dev.connect()
            resp.text = MethodResponse(req).json
        except Exception as ex:
            resp.text = MethodResponse(
                req, DriverException(0x500, 'Dome.Connect failed', ex)).json


@before(PreProcessRequest(maxdev))
class disconnect:
    def on_put(self, req: Request, resp: Response, devnum: int):
        try:
            dome_dev.disconnect()
            resp.text = MethodResponse(req).json
        except Exception as ex:
            resp.text = MethodResponse(
                req, DriverException(0x500, 'Dome.Disconnect failed', ex)).json


@before(PreProcessRequest(maxdev))
class connecting:
    def on_get(self, req: Request, resp: Response, devnum: int):
        resp.text = PropertyResponse(False, req).json


@before(PreProcessRequest(maxdev))
class connected:
    def on_get(self, req: Request, resp: Response, devnum: int):
        resp.text = PropertyResponse(dome_dev.connected, req).json

    def on_put(self, req: Request, resp: Response, devnum: int):
        conn_str = get_request_field('Connected', req)
        conn = to_bool(conn_str)                        # 400 if not a bool
        try:
            if conn:
                dome_dev.connect()
            else:
                # Disconnecting de-energises the motors. That is deliberate: a
                # client dropping the connection must not leave a shell running
                # with nothing watching the limit switches.
                dome_dev.disconnect()
            resp.text = MethodResponse(req).json
        except Exception as ex:
            resp.text = MethodResponse(
                req, DriverException(0x500, f'Dome.Connected={conn} failed', ex)).json


@before(PreProcessRequest(maxdev))
class description:
    def on_get(self, req: Request, resp: Response, devnum: int):
        resp.text = PropertyResponse(DomeMetadata.Description, req).json


@before(PreProcessRequest(maxdev))
class driverinfo:
    def on_get(self, req: Request, resp: Response, devnum: int):
        resp.text = PropertyResponse(DomeMetadata.Info, req).json


@before(PreProcessRequest(maxdev))
class driverversion:
    def on_get(self, req: Request, resp: Response, devnum: int):
        resp.text = PropertyResponse(DomeMetadata.Version, req).json


@before(PreProcessRequest(maxdev))
class interfaceversion:
    def on_get(self, req: Request, resp: Response, devnum: int):
        resp.text = PropertyResponse(DomeMetadata.InterfaceVersion, req).json


@before(PreProcessRequest(maxdev))
class name:
    def on_get(self, req: Request, resp: Response, devnum: int):
        resp.text = PropertyResponse(DomeMetadata.Name, req).json


@before(PreProcessRequest(maxdev))
class supportedactions:
    def on_get(self, req: Request, resp: Response, devnum: int):
        # Must return a list, never PropertyNotImplemented. This list is how a
        # generic client discovers the per-shell aperture capability.
        resp.text = PropertyResponse(
            dome_dev.supported_actions() if dome_dev else [], req).json


@before(PreProcessRequest(maxdev))
class devicestate:
    """Platform 7 bulk state read: everything a client polls, in one round trip."""
    def on_get(self, req: Request, resp: Response, devnum: int):
        if _not_connected(req, resp):
            return
        try:
            status = dome_dev.shell_status()
            state = [
                StateValue('ShutterStatus', status['shutterStatus']),
                StateValue('Slewing', status['slewing']),
                StateValue('AtHome', False),
                StateValue('TimeStamp', datetime.now(timezone.utc)
                           .isoformat(timespec='milliseconds')
                           .replace('+00:00', 'Z')),
            ]
            resp.text = PropertyResponse(state, req).json
        except Exception as ex:
            resp.text = PropertyResponse(
                None, req, DriverException(0x500, 'Dome.Devicestate failed', ex)).json


# ---------------------------------------------------------------------- #
# Capability flags.                                                       #
#                                                                         #
# A clamshell has no azimuth, no home and no park position, so everything  #
# azimuth-shaped is False and the matching members raise                  #
# NotImplementedException. CanSetAltitude is False until the calibration   #
# survey provides a real degree mapping -- publishing a fabricated angle   #
# to a client that slaves a telescope to it would be worse than an honest  #
# refusal.                                                                #
# ---------------------------------------------------------------------- #

@before(PreProcessRequest(maxdev))
class canfindhome:
    on_get = _capability(False)


@before(PreProcessRequest(maxdev))
class canpark:
    on_get = _capability(False)


@before(PreProcessRequest(maxdev))
class cansetaltitude:
    on_get = _capability(False)


@before(PreProcessRequest(maxdev))
class cansetazimuth:
    on_get = _capability(False)


@before(PreProcessRequest(maxdev))
class cansetpark:
    on_get = _capability(False)


@before(PreProcessRequest(maxdev))
class cansetshutter:
    on_get = _capability(True)


@before(PreProcessRequest(maxdev))
class canslave:
    on_get = _capability(False)


@before(PreProcessRequest(maxdev))
class cansyncazimuth:
    on_get = _capability(False)


# ---------------------------------------------------------------------- #
# State                                                                   #
# ---------------------------------------------------------------------- #

@before(PreProcessRequest(maxdev))
class altitude:
    def on_get(self, req: Request, resp: Response, devnum: int):
        if _not_connected(req, resp):
            return
        # No calibrated mapping from potentiometer counts to degrees exists.
        # Per-shell aperture is available as a percentage via the vendor Action.
        resp.text = PropertyResponse(None, req, NotImplementedException()).json


@before(PreProcessRequest(maxdev))
class azimuth:
    def on_get(self, req: Request, resp: Response, devnum: int):
        if _not_connected(req, resp):
            return
        resp.text = PropertyResponse(None, req, NotImplementedException()).json


@before(PreProcessRequest(maxdev))
class athome:
    def on_get(self, req: Request, resp: Response, devnum: int):
        if _not_connected(req, resp):
            return
        # False rather than an exception: the spec ties AtHome to a home sensor
        # rather than to CanFindHome, and "not at home" is truthful here.
        resp.text = PropertyResponse(False, req).json


@before(PreProcessRequest(maxdev))
class atpark:
    def on_get(self, req: Request, resp: Response, devnum: int):
        if _not_connected(req, resp):
            return
        # The spec is explicit: raise NotImplementedException when CanPark is
        # False. Note this differs from AtHome above, and Conform checks it.
        resp.text = PropertyResponse(None, req, NotImplementedException()).json


@before(PreProcessRequest(maxdev))
class shutterstatus:
    def on_get(self, req: Request, resp: Response, devnum: int):
        if _not_connected(req, resp):
            return
        try:
            resp.text = PropertyResponse(int(dome_dev.shutter_status()), req).json
        except Exception as ex:
            resp.text = PropertyResponse(
                None, req, DriverException(0x500, 'Dome.Shutterstatus failed', ex)).json


@before(PreProcessRequest(maxdev))
class slaved:
    def on_get(self, req: Request, resp: Response, devnum: int):
        if _not_connected(req, resp):
            return
        # CanSlave is False, so the getter must still work and return False.
        resp.text = PropertyResponse(False, req).json

    def on_put(self, req: Request, resp: Response, devnum: int):
        if _not_connected(req, resp):
            return
        resp.text = MethodResponse(req, NotImplementedException()).json


@before(PreProcessRequest(maxdev))
class slewing:
    def on_get(self, req: Request, resp: Response, devnum: int):
        if _not_connected(req, resp):
            return
        try:
            # The spec counts clamshell leaf movement as slewing, so this is
            # True whenever either shell is under power.
            resp.text = PropertyResponse(dome_dev.slewing, req).json
        except Exception as ex:
            resp.text = PropertyResponse(
                None, req, DriverException(0x500, 'Dome.Slewing failed', ex)).json


# ---------------------------------------------------------------------- #
# Methods                                                                 #
# ---------------------------------------------------------------------- #

@before(PreProcessRequest(maxdev))
class abortslew:
    def on_put(self, req: Request, resp: Response, devnum: int):
        if _not_connected(req, resp):
            return
        try:
            dome_dev.abort_slew()
            resp.text = MethodResponse(req).json
        except Exception as ex:
            resp.text = MethodResponse(
                req, DriverException(0x500, 'Dome.Abortslew failed', ex)).json


@before(PreProcessRequest(maxdev))
class openshutter:
    def on_put(self, req: Request, resp: Response, devnum: int):
        if _not_connected(req, resp):
            return
        try:
            dome_dev.open_shutter()
            resp.text = MethodResponse(req).json
        except ActionError as ex:
            resp.text = MethodResponse(req, InvalidOperationException(str(ex))).json
        except Exception as ex:
            resp.text = MethodResponse(
                req, DriverException(0x500, 'Dome.Openshutter failed', ex)).json


@before(PreProcessRequest(maxdev))
class closeshutter:
    def on_put(self, req: Request, resp: Response, devnum: int):
        if _not_connected(req, resp):
            return
        try:
            dome_dev.close_shutter()
            resp.text = MethodResponse(req).json
        except Exception as ex:
            resp.text = MethodResponse(
                req, DriverException(0x500, 'Dome.Closeshutter failed', ex)).json


@before(PreProcessRequest(maxdev))
class findhome:
    def on_put(self, req: Request, resp: Response, devnum: int):
        if _not_connected(req, resp):
            return
        resp.text = MethodResponse(req, NotImplementedException()).json


@before(PreProcessRequest(maxdev))
class park:
    def on_put(self, req: Request, resp: Response, devnum: int):
        if _not_connected(req, resp):
            return
        resp.text = MethodResponse(req, NotImplementedException()).json


@before(PreProcessRequest(maxdev))
class setpark:
    def on_put(self, req: Request, resp: Response, devnum: int):
        if _not_connected(req, resp):
            return
        resp.text = MethodResponse(req, NotImplementedException()).json


@before(PreProcessRequest(maxdev))
class slewtoaltitude:
    def on_put(self, req: Request, resp: Response, devnum: int):
        if _not_connected(req, resp):
            return
        # Deliberately unimplemented for now. This IS the spec-blessed home for
        # a clamshell aperture ("including positioning clamshell leaves"), and
        # it would give clients polled progress for free -- but it carries a
        # single number, so it cannot express "east 60%, west closed", and
        # honouring it requires a calibrated counts-to-degrees mapping that does
        # not exist yet. Revisit after the calibration survey; until then the
        # per-shell Action is the truthful interface.
        resp.text = MethodResponse(req, NotImplementedException()).json


@before(PreProcessRequest(maxdev))
class slewtoazimuth:
    def on_put(self, req: Request, resp: Response, devnum: int):
        if _not_connected(req, resp):
            return
        resp.text = MethodResponse(req, NotImplementedException()).json


@before(PreProcessRequest(maxdev))
class synctoazimuth:
    def on_put(self, req: Request, resp: Response, devnum: int):
        if _not_connected(req, resp):
            return
        resp.text = MethodResponse(req, NotImplementedException()).json
