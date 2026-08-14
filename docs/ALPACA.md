# Greenhill clamshell dome — Alpaca device server

An ASCOM Alpaca `Dome` device server for the Greenhill 50 cm clamshell, built on
the [AlpycaDevice](https://github.com/ASCOMInitiative/AlpycaDevice) template
(MIT, © Bob Denny — see `device/LICENSE-AlpycaDevice.txt`).

Once this is running, the dome is drivable by anything that speaks Alpaca: NINA,
Voyager, Arcsecond, `alpyca` scripts. Previously only the local PySide6 window
could move it.

## Layout

| File | Role |
|---|---|
| `dome_shutter.py` (repo root) | Hardware + supervision. Owns the K8055. Unchanged in purpose. |
| `device/domedevice.py` | Bridge: ASCOM semantics, the two-shells-to-one-status collapse, vendor Actions. No hardware access. |
| `device/dome.py` | Alpaca HTTP responders. Thin — all behaviour lives in the bridge. |
| `device/app.py` | Startup, routing, discovery, single-instance guard. |
| `device/config.toml` | Network, logging, **and the calibration constants**. |
| `device/board_sim.py` | Simulated K8055 for development and CI. |
| `device/simulate.py` | Runs the server against the simulator. |

Vendored unchanged from AlpycaDevice: `shr.py`, `exceptions.py`, `discovery.py`,
`setup.py`. Modified: `config.py` (calibration section; config path
resolved relative to the module so a Windows service can find it),
`log.py` (log path likewise; no log-file problem may prevent startup —
see below), `management.py` (points at `DomeMetadata`).

## Running

```bash
pip install falcon    # TOML is read with the stdlib tomllib (Python >= 3.11)
```

Against the real board, from the `device` directory:

```bash
python app.py
```

Against the simulator, on any OS, no hardware:

```bash
python simulate.py
```

Then `http://<host>:11111/api/v1/dome/0/...`, with `/management/v1/configureddevices`
for discovery by address and UDP broadcast on 32227 for automatic discovery.

**Only one process may own the K8055.** The server binds a loopback port as a
mutex and refuses to start if another instance holds it. It cannot detect the
legacy PySide6 app, which opens the board directly — do not run both.

### The log, and restarting

`device/alpyca.log`, always beside `log.py` and never in the launch directory.
Each run rotates the last one to `.1`, keeping ten generations. Set `ALPYCA_LOG`
to put it elsewhere.

Nothing about the log file can stop the dome from being served. A log directory
that cannot be written falls back to the console; a rotation that cannot rename
appends to the existing file instead. Both print a `==LOGGING==` line to stderr
and carry on.

This matters on restart. Rotating means renaming a file the previous process may
still have open, which Windows refuses outright — so a start that followed a
crash or an interrupt used to die with a log-file error, and the operator was
left with an Alpaca-unreachable dome and a file to go and delete. Note what that
error was really telling you: **a log file still held open means a process still
holding it, and that process still owns the board.** The single-instance guard is
therefore checked first, before the logger exists, and says so plainly on stderr:

```
==STARTUP FAILED== Another instance of the dome server ... already owns the K8055.
```

If you see that after what looked like a clean exit, the previous server is still
alive — most likely wedged in a `CloseDevice` call on the way out. Check for it
before starting a new one, because the relay latches are still its to clear.

### Stopping the server

**The K8055 latches its outputs in hardware.** Closing the device does not clear
them, and neither does exiting. A server that dies without running its shutdown
path leaves a motor energised and the shutter driving with nothing watching it.
That is the difference between the ways of stopping it:

| How you stop it | Caught? | What happens |
|---|---|---|
| Ctrl-C | yes | `KeyboardInterrupt` unwinds `main()`; board released. |
| `kill` / SIGTERM (POSIX) | yes | Handler raises `SystemExit`; same path. |
| Ctrl-Break (Windows) | yes | SIGBREAK; same path. |
| Closing the console window (Windows) | **no** | ~5 s, then the process dies with the relays hot. |
| `taskkill /F`, `kill -9`, power loss | **never** | Nothing runs. Relays stay as they were. |

A clean stop logs, in this order:

```
==SIGNAL== SIGTERM received; stopping the dome.
Disconnected from dome hardware
==SHUTDOWN== Dome server stopped.
```

If the middle line is missing, the board was never released — treat the dome as
possibly still driving and check it.

A **second** SIGTERM kills the process outright: the first one restores the
default handler on its way past. That is deliberate. Releasing the board can
block inside the K8055 DLL, and an operator who has decided this process must
die now needs a way to say so that does not depend on the DLL answering.

Two gaps remain, both Windows-only and neither fixable in `signal`:

* Windows delivers no SIGTERM. `os.kill(pid, SIGTERM)` and `taskkill /F` both
  become `TerminateProcess`, which no handler can intercept. Ctrl-C and
  Ctrl-Break are the covered ways to stop it there.
* The console window's close button raises `CTRL_CLOSE_EVENT`, which Python does
  not surface as a signal at all. Covering it needs `SetConsoleCtrlHandler`
  through `ctypes`. **Until that exists, do not stop the server by closing its
  window** — Ctrl-C in the window instead.

`Dome_Control` also installs handlers of its own, but only when constructed on
the main thread. Here the board is opened from a WSGI worker thread the first
time a client sets `Connected = true`, so that path always takes its early
return: `device/app.py` holds the only signal handlers this server has.

## ASCOM member mapping

A clamshell has no azimuth, no home position and no park position, so a large
part of `IDome` is honestly unsupported rather than faked.

| Member | Value | Why |
|---|---|---|
| `CanSetShutter` | `true` | Both shells drive open and closed. |
| `CanSetAltitude` | `false` | See *SlewToAltitude* below. |
| `CanSetAzimuth`, `CanSlave`, `CanSyncAzimuth` | `false` | No azimuth axis exists. |
| `CanPark`, `CanSetPark`, `CanFindHome` | `false` | No park or home position. |
| `Azimuth`, `Altitude` | `NotImplementedException` | No azimuth; no calibrated altitude. |
| `AtPark` | `NotImplementedException` | Required when `CanPark` is false. |
| `AtHome` | `false` | Deliberately *not* an exception — the spec ties `AtHome` to a home sensor rather than to `CanFindHome`. Conform checks this asymmetry against `AtPark`. |
| `Slewing` | `true` while either shell moves | The spec counts "clamshell leaves" as slewing, not just azimuth motion. |
| `ShutterStatus` | collapsed, see below | |
| `OpenShutter` | both shells, staggered | |
| `CloseShutter` | both shells, **west first** | The telescope parks west, so that shell must clear the OTA first. |
| `AbortSlew` | stops both immediately | |
| `Connected` / `Connect` / `Disconnect` | opens/releases the board | Disconnecting **de-energises the motors**: a client dropping its connection must not leave a shell running unsupervised. |
| `InterfaceVersion` | `3` | IDomeV3, ASCOM Platform 7. `DeviceState` implemented. |

### ShutterStatus: two shells, one state

ASCOM describes one shutter. This dome has two independently driven shells, so
the collapse loses information. It is ordered by decreasing safety significance:

1. any latched fault → `shutterError`
2. either shell closing → `shutterClosing` (before *opening*, so a close-to-safety stays visible)
3. either shell opening → `shutterOpening`
4. shared reed made **and** both shells at their closed position → `shutterClosed`
5. anything else → `shutterOpen`

Step 5 is the important one. **A shell stopped half way reports `shutterOpen`, not
`shutterClosed`.** ASCOM has no partial state, and reporting "shut" for a dome
that is not verifiably shut is the one error that could leave it open in rain.
Step 4 requires positive confirmation from both the reed *and* both positions,
because the reed is a single shared switch and cannot speak for one shell alone.

The information the collapse discards is available losslessly through
`Greenhill:GetShellStatus`.

## The per-shell aperture ("goto")

Carried on ASCOM's own extension mechanism: `SupportedActions` + `Action`.
`Parameters` is a single string per the Alpaca API, so arguments travel as JSON
inside it.

```
Greenhill:SetShellAperture   {"shell":"east"|"west"|"both","percent":0..100}
                             -> {"accepted":true,"targets":{"east":141}}
Greenhill:GetShellStatus     ""   -> per-shell percent, state, target, fault
Greenhill:GetCapabilities    ""   -> self-describing manifest (below)
Greenhill:ClearFault         {"shell":"east"|"west"|"both"}
```

`percent` is **0 = closed, 100 = fully open**, one convention everywhere.
Out-of-range or malformed arguments are **rejected** (`InvalidValueException`,
0x401) rather than clamped — a client asking for 120 % has a bug, and quietly
opening fully instead is not a guess worth making on behalf of a moving roof.

`Greenhill:GetCapabilities` returns an argument schema, so a client can build a
UI for this without any Greenhill-specific code compiled into it. That is the
whole point: the extension is *data published by the device*, not a plugin
installed into the client.

### Why not `SlewToAltitude`?

It is genuinely the spec-blessed home for this — the ASCOM docs for `Altitude`
say "including positioning clamshell leaves" — and it would give clients polled
progress for free via `Slewing`. It is deliberately deferred because:

1. It carries **one number**. Two independently commanded shells cannot express
   "east 60 %, west closed", which is the wind-shielding case that motivates the
   feature at all.
2. `CanSetAltitude = true` obliges a calibrated counts-to-degrees mapping. There
   isn't one (see below). Publishing a fabricated angle to a client that slaves a
   telescope to it is worse than an honest `NotImplementedException`.

Revisit after the calibration survey, as an *addition* for the symmetric case.

## Operator conventions — two scales, one boundary

The **local PySide6 UI shows percent CLOSED**: sliders and bars read 100 when
the dome is shut, matching the original controller the operators know. The
**controller and the Alpaca surface are percent OPEN**: 0 = closed, 100 = open.
The inversion lives in exactly one place, the `DomeWorker` slots in
`dome_UI.py`. Do not add a second one anywhere else.

Displayed positions (local UI and `Greenhill:GetShellStatus` alike) derive from
`last_east`/`last_west`, not the live ADC reading. The pot jitters ±1 count at
rest; the clamped-monotonic last-values are the maintainer's jitter-free
solution, at the cost of a ~1 % jump on direction reversal. Safety logic keeps
using the live reading.

## Calibration — what is known and what is still needed

Confirmed by the maintainer from testing on the real board:

* `235` is *"pretty close to correct"* for the open position on **both** shells
* the at-rest ADC jitter is **±1 count** (so `tolerance = 2` is sane)
* the relation between the analogue value and the *apparent* opening is
  probably a **power law**, not linear

Still to measure, per shell: the reading at the mechanical **closed** stop, and
the `aperture_exponent` for that power law (`config.toml`, default `1.0` =
linear; setpoint and readback share one pair of inverse transforms, so any
exponent round-trips). If a pure power law fits poorly, a manual transform
belongs in `Dome_Control._to_percent`/`_to_raw` — that is the seam.

Why the closed value matters most:

* a **closed** value above the truth → the position stop is never reached, so
  every close terminates by stalling into the hard stop
* a **closed** value below the truth → the shell stops while still ajar, reed
  unmade, no fault reported. Software says shut; the dome is open a crack

`config.toml` ships `calibrated = false` and warns at startup. A bench logic
board will not read the same as the dome: keep one config per installation.

## Switch states

The three digital inputs, as enumerated by the maintainer (raw value = sum of
bits; limits are active-low, the reed active-high):

| raw | reed | east limit | west limit | Meaning |
|----:|:---:|:---:|:---:|---|
| 0 | – | open | open | dome fully open |
| 1 | – | open | – | east fully open, west unknown |
| 2 | – | – | open | west fully open, east unknown |
| 3 | – | – | – | dome partially open (or closed but unconfirmed) |
| **4** | shut | open | open | **FAULT, potentially fatal** — uppers shut, both lowers open |
| **5** | shut | open | – | **FAULT** — uppers shut, east lower open |
| **6** | shut | – | open | **FAULT** — uppers shut, west lower open |
| 7 | shut | – | – | all closed |

States 4–6 should be unreachable if the supervision works; if they are ever
read, the affected shells are **driven closed immediately** (blind — neither
the reed nor necessarily the position can be trusted there), west first and
strictly sequential, because the IPS feeding the shutter motors also powers
the mount, cameras and PC.

## Faults

Latched per shell as a message plus a machine code (`Greenhill:GetShellStatus`
reports both). While any fault is latched: `ShutterStatus` = `shutterError`,
opening that shell is refused (`InvalidOperationException`, 0x40B), and
**closing is never blocked** — it must not be possible to lock the dome open.

| Code | Cause | Cleared by |
|---|---|---|
| `ICED` | Reed still made while opening past `ice_detect_counts`: the upper segments are stuck (icing). The shell stops at once and reseats with a short blind close. | **Auto**, when the reed releases (segments separated — maintainer-specified), or `ClearFault`. |
| `SWITCH_STATE` | Impossible raw states 4/5/6 (table above). Emergency blind close fires. | **Manual only** — in this state a releasing reed could mean the uppers just fell. |
| `CLOSE_STALL` | A close stalled short of closed without the reed confirming: shell may be ajar (water ingress risk in wind + rain). Stalling *at* the closed stop is the hard stop seating, not a fault. | **Auto**, when the reed confirms closed, or `ClearFault`. |
| `EARLY_LIMIT` | Open limit engaged more than `early_limit_margin` counts below the calibrated open: suspected switch fault. | **Manual only.** |

**The ice-breaking procedure via Alpaca** (mirrors the existing manual one:
open a little, reverse to the hard stop, repeat until the ice gives):
`ClearFault` + `OpenShutter` performs one controlled cycle — the shell opens to
`ice_detect_counts`, faults, and reseats itself. When the ice breaks the reed
releases and the fault clears itself; normal operation resumes with no further
operator action.

A goto (target-armed) close is position-governed: a stuck-made reed cannot fake
its completion. Manual full closes keep the reed as their legitimate
"both shells closed" stop.

**Open hardware question (maintainer):** the board *may* reset the analogue
position to 0 while the reed is engaged. If it does, the `ICED` trigger never
fires (position pinned at 0 while the lower shell moves) and the protection is
dead. This must be settled on hardware before the dome runs unattended.

## Testing

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

No hardware, any OS. `tests/test_alpaca_dome.py` drives the real Falcon routing
through a test client, and exercises the `ShutterStatus` collapse exhaustively
over every combination of shell states.

### ASCOM Conform Universal

Both Conform suites pass against `simulate.py`:

```
alpacaprotocol   no errors, issues or information alerts
conformance      no errors, warnings or issues; all members within target response times
```

`.github/workflows/conformance.yml` runs both on every push. It runs in CI rather
than locally because ConformU ships as a Linux x64 binary and the runner is Linux
x64. Two things worth knowing if you run it by hand:

* it needs `libicu` or it aborts at startup;
* `alpacaprotocol` writes only a log, no results file, even with `--resultsfile`.
  Its verdict is the exit code, which is the error count.

Conform found two real defects that our own tests could not, both in the HTTP
layer inherited from the AlpycaDevice sample:

1. **A duplicated `ClientID`/`ClientTransactionID` returned HTTP 500.** Falcon
   collapses repeated keys into a list, `int(['1', ''])` raises `TypeError`, and
   `shr.PreProcessRequest._pos_or_zero` only caught `ValueError`. Alpaca requires
   400. Fixed in `shr.py`.
2. **The server spoke HTTP/1.0 and closed every connection.** Every .NET ASCOM
   client pools connections through `HttpClient`, so the *next* request on a
   pooled socket was reset — appearing as "An error occurred while sending the
   request" on whatever member happened to be next, which is why a different one
   failed each run. `wsgiref`'s `WSGIRequestHandler` serves exactly one request
   and never loops, and `wsgiref.handlers.BaseHandler` hardcodes
   `http_version = '1.0'`, so `protocol_version` alone changes nothing. `app.py`
   now supplies an HTTP/1.1 handler plus the request loop, and serves each
   connection on its own thread. Confirmed by contrast with the reference ASCOM
   Alpaca Simulators, which keeps the connection open.

## Commissioning checklist (on the real dome, from the maintainer)

1. **Settle the reed/position question** above: does the board reset the
   analogue position while the reed is engaged? The `ICED` protection depends
   on the answer.
2. **Partial-open operation**: confirm the dome closes from every partially
   open state and that nothing (including latched faults) blocks a close.
3. **Simulate the frozen-shut fault**: clamp the upper segments (multiple
   clamps) and verify the `ICED` detect → stop → reseat cycle against real
   motors.
4. **Verify "closed" is truly seated**: if the segments rest slightly ajar,
   water can ingress in high wind + rain. If so, consider a hold-on delay
   before de-energising at the end of a close (the legacy controller kept the
   motor powered ~1.5 s after reaching closed) — deliberately **not**
   implemented yet; it needs the real dome to tune.
5. Re-run both ASCOM Conform suites against the real hardware.
6. The calibration survey: closed readings, the aperture exponent, and a check
   of the full-travel time against `emergency_close_seconds`.

## Not yet done

* Conform has only been run against the **simulator**; see the checklist.
* No Windows service packaging yet (NSSM or similar). Note that a service stop
  arrives as neither a signal nor a console event, so packaging one means
  wiring its stop request into the same shutdown path — see *Stopping the
  server*.
* `SetConsoleCtrlHandler`, so closing the console window on Windows releases the
  board instead of leaving the relays hot. The signal handlers cannot reach that
  event; see *Stopping the server*.
* `SlewToAltitude`, pending calibration.
* The close seat-delay (checklist item 4), if the real dome shows it is needed.
