# Installing BirriaSweep

## What you need

- An MT7921AU / MT7961 USB Wi-Fi stick that shows up as USB `0e8d:7961`, for example the
  BrosTrend AXE3000. Other boards with the same chip may work but are untested.
- A **USB 3** port. USB 2 can't carry the 40 MS/s stream. Plug the stick in directly, not
  through a busy hub.
- An antenna on the stick's main antenna connector.
- A Chromium-based browser (Chrome, Edge, Brave) or Firefox.

While BirriaSweep uses the stick, the stick is not a network adapter. You can switch it back
whenever you like (see the end of this page).

## Windows

### 1. Unzip

Download `BirriaSweep-windows-x64.zip` and unzip the **whole** archive to a folder, for example
`C:\BirriaSweep`. Don't run anything from inside the ZIP. The archive already contains Python,
numpy, the driver libraries and the page, so there is nothing else to install.

### 2. Give the stick the libusbK driver (once per stick)

1. Plug in the stick.
2. Download Zadig from <https://zadig.akeo.ie/> and run it.
3. Click **Options > List All Devices**.
4. In the list, choose **WiFi_If (Interface 3)**. The USB ID next to it must read `0E8D 7961`.
   Don't change the composite parent or the Bluetooth interfaces.
5. Set the target driver to **libusbK** (WinUSB does **not** work), then click
   **Replace Driver** (or **Install Driver**).
6. Unplug the stick and plug it back in.

### 3. Start

Double-click one of these:

- `START_WITH_PAGE.bat` starts the bridge and opens the bundled page at
  <http://127.0.0.1:8075/>. It needs no internet.
- `START_BRIDGE_ONLY.bat` starts only the bridge. Then open
  <https://zerotasteguy.github.io/BrowSDR_Sweep/>. The first time, Chrome may ask whether the
  site may access devices on your local network. Click **Allow**, because that is how the page
  reaches the bridge on your own computer.

A black window opens and says `BirriaSweep bridge running`. Keep it open while you use the page.
Close it, or press Ctrl+C in it, to stop.

You can also make a desktop shortcut: right-click the `.bat` file, then choose
**Send to > Desktop (create shortcut)**.

## Linux

### 1. Build

```sh
sudo apt install git cmake build-essential libusb-1.0-0-dev python3-numpy nodejs npm
git clone <this repository> BirriaSweep && cd BirriaSweep
scripts/build_linux.sh
```

The script downloads BirriaSDR at the tested commit and applies `driver/birriasdr-fast-sweep.patch`.
It builds and tests the driver, builds the page, and puts everything in `build/BirriaSweep`.
Use `--no-page` to skip the page, which also means you don't need Node.js. The page needs
Node.js 22.

### 2. USB access and the Wi-Fi driver

The kernel's `mt7921u` driver must not claim the stick, and your user needs access to it:

```sh
echo 'blacklist mt7921u' | sudo tee /etc/modprobe.d/birriasweep.conf
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="0e8d", ATTRS{idProduct}=="7961", MODE="0660", TAG+="uaccess"' \
  | sudo tee /etc/udev/rules.d/60-birriasweep.rules
sudo udevadm control --reload-rules
sudo modprobe -r mt7921u
```

Then replug the stick. The blacklist also stops other MT7921 Wi-Fi adapters from working. If
you need one, unbind just this stick instead.

### 3. Start

```sh
build/BirriaSweep/start_with_page.sh      # bridge + bundled page
build/BirriaSweep/start_bridge_only.sh    # bridge only, use the hosted page
```

## Building the Windows package yourself

The [release workflow](../.github/workflows/release.yml) builds the ZIP on GitHub. To build it
locally you need:

- git
- Visual Studio 2022 Build Tools with the C++ workload and CMake
- Python 3 with pip
- Node.js 22

Then run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

The result is `dist\BirriaSweep-windows-x64.zip`. Useful parameters:

- `-PageRef <branch>` builds the page from another branch.
- `-SkipTests` skips the driver's tests.

## Running from source without the package

```sh
pip install numpy
python bridge/birria_bridge.py --lib PATH/TO/BirriaSDR/build-windows/Release [--ui PATH/TO/BrowSDR/dist]
```

## Getting normal Wi-Fi back

- **Windows:** open Device Manager, find the stick under *libusbK USB Devices*, choose
  **Uninstall device**, tick **Delete the driver software** if it is offered, then replug.
- **Linux:** delete `/etc/modprobe.d/birriasweep.conf`, then run `sudo modprobe mt7921u`.

Nothing is ever written to the stick: all of BirriaSweep's settings are lost when you unplug it.
