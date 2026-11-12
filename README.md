# BirriaSweep

Turn a cheap MT7921AU Wi-Fi 6E USB stick (for example the BrosTrend AXE3000) into a
5–7 GHz spectrum analyser in your browser. It shows a live 40 MHz spectrum and waterfall, and it
sweeps a whole band, such as 5.6–6.0 GHz, about 30 times a second.

It builds on two projects:

- [BirriaSDR](https://github.com/ot0tot/BirriaSDR) streams raw I/Q samples from the stick.
  BirriaSweep adds a patch that retunes the stick inside its own firmware, which makes sweeping
  about 30 times faster.
- [BrowSDR](https://github.com/ZeroTasteGuy/BrowSDR_Sweep) is the web page that draws the
  spectrum, waterfall and sweep.

Between them sits a small Python **bridge**. Browsers can't reach this stick over WebUSB on
Windows, so the bridge opens it natively and streams to the page over a local WebSocket.

```
 USB stick ──libusb──▶ BirriaSDR driver ──▶ birria_bridge.py ──WebSocket 127.0.0.1:8075──▶ BrowSDR page
```

## Quick start (Windows)

1. Download `BirriaSweep-windows-x64.zip` from
   [Releases](../../releases) and unzip it anywhere.
2. Install the libusbK driver for the stick once, with Zadig. See
   [docs/INSTALL.md](docs/INSTALL.md).
3. Plug the stick into a **USB 3** port and double-click one of these:

   | File | What it does |
   |---|---|
   | `START_WITH_PAGE.bat` | Starts the bridge and opens the page bundled in the ZIP. Works offline. |
   | `START_BRIDGE_ONLY.bat` | Starts only the bridge. Then open the hosted page <https://zerotasteguy.github.io/BrowSDR_Sweep/>. Its other radios (HackRF, ESP32 and so on) are on the same page. |

4. On the page, click **Add SDR**, choose **Wi-Fi stick (BirriaSDR bridge)**, and press play.

Nothing else needs installing: the ZIP brings its own Python. The
[user manual](docs/USER_MANUAL.md) explains the sweep, the gains and what to do when something
goes wrong.

## Linux

```sh
sudo apt install git cmake build-essential libusb-1.0-0-dev python3-numpy nodejs npm
scripts/build_linux.sh          # or: scripts/build_linux.sh --no-page
build/BirriaSweep/start_with_page.sh
```

You need a udev rule so that you can open the stick without root. See [docs/INSTALL.md](docs/INSTALL.md).

## Documentation

- [Install guide](docs/INSTALL.md): driver, USB port, building from source.
- [User manual](docs/USER_MANUAL.md): live view, sweep, gains, command-line options, troubleshooting.
- [How it works](docs/HOW_IT_WORKS.md): the bridge protocol, the in-firmware retune, how the DC
  notch and the filter shape are hidden.
- [Driver patch](driver/README.md): what the BirriaSDR patch changes and its switches.

## Repository layout

| Path | Contents |
|---|---|
| `bridge/birria_bridge.py` | The bridge. It needs only Python 3.10+ and numpy. |
| `driver/birriasdr-fast-sweep.patch` | The patch for BirriaSDR commit `35439a9`, including the firmware. |
| `start/` | The start files that go into the package. |
| `scripts/build_windows.ps1`, `scripts/build_linux.sh` | Build the complete package from source. |
| `.github/workflows/release.yml` | Builds the Windows ZIP on GitHub. A `v*` tag publishes it as a release. |

## Licences

- BirriaSweep's own code (the bridge, the patch and the scripts) is MIT.
- BirriaSDR is MIT.
- The bundled BrowSDR page is AGPL-3.0. Its source is at
  <https://github.com/ZeroTasteGuy/BrowSDR_Sweep>, and its licence travels with the ZIP in
  `ui/BrowSDR-LICENSE.txt`.

This is receive-only software: it never transmits.
