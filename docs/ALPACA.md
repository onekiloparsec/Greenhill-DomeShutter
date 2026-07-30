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
`log.py`, `setup.py`. Modified: `config.py` (calibration section; config path
resolved relative to the module so a Windows service can find it),
`management.py` (points at `DomeMetadata`).

## Running

```bash
pip install falcon toml
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

## Calibration — read before driving the real dome

`config.toml` ships `calibrated = false` and the analogue limits inherited from
`dome_shutter.py` (`0` closed, `235` open). **Those are guesses, never measured.**
The server logs a warning at startup while they are in use.

Per shell, someone must record:

* the reading with the shell against its mechanical **closed** stop
* the reading at its **open** limit switch
* the at-rest jitter over ~60 s, which sets a sane `tolerance`

Why it matters:

* a **closed** value above the truth → the position stop is never reached, so
  every close terminates by stalling into the hard stop
* a **closed** value below the truth → the shell stops while still ajar, reed
  unmade, no fault reported. Software says shut; the dome is open a crack
* a wrong **open** value → every intermediate aperture is proportionally wrong

A bench logic board will not read the same as the dome. Keep one `config.toml`
per installation rather than editing one back and forth.

## Faults

A shell that fails to open (the shared reed reads closed while it is moving)
latches a fault. While latched:

* `ShutterStatus` reports `shutterError`
* opening is refused (`InvalidOperationException`, 0x40B)
* **closing is always permitted** — it must never be possible to lock the dome open

Clear with `Greenhill:ClearFault` once an operator has looked at the dome.

## Testing

```bash
pip install pytest falcon toml
python -m pytest tests/ -q
```

No hardware, any OS. `tests/test_alpaca_dome.py` drives the real Falcon routing
through a test client, and exercises the `ShutterStatus` collapse exhaustively
over every combination of shell states.

## Not yet done

* **ASCOM Conform Universal has not been run.** Everything here follows the spec
  as read, but Conform is the arbiter and it should be run against
  `simulate.py` before the server goes near the motors.
* No Windows service packaging yet (NSSM or similar).
* `SlewToAltitude`, pending calibration.
