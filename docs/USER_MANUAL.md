# BirriaSweep user manual

## Starting and stopping

1. Plug the stick into a USB 3 port.
2. Start `START_WITH_PAGE` or `START_BRIDGE_ONLY` (see [INSTALL.md](INSTALL.md)).
3. On the page, click **Add SDR**, choose **Wi-Fi stick (BirriaSDR bridge)**, then press the
   play button at the top left.

To stop, press the stop button, or simply close the page. The stick keeps running until you
close the bridge window (or press Ctrl+C in it). Only one page can use the bridge at a time: a
newly opened or reloaded page takes over from the old one.

## Live view

The live view shows 40 MHz around the centre frequency as a spectrum and a waterfall. Type the
centre frequency in **Radio > Centre**. The driver accepts 2156–2610 MHz and 4–8 GHz. Reception
has been verified from about 4.9 to 6.1 GHz. The fast sweep works above 4 GHz; below that, it
falls back to the slower retune.

The live view shows snapshots, not a gap-free stream. A snapshot is 0.4 ms of signal, taken
dozens of times a second. That is good for seeing signals but not enough to demodulate audio or
video.

The stick has a 0.5 MHz hole (the "DC notch") at the frequency it is tuned to. The bridge hides
it: it tunes 2 MHz to either side on alternate snapshots and merges the two, so you see a clean
40 MHz.

## Sweep

Tick **Sweep mode** in the left panel. You then choose the range:

- **Presets** cover the common FPV and control-link bands (for example the 5.8 GHz raceband).
- **Start / Stop** set a custom range in MHz. A new value applies when you press Enter or
  leave the field.
- **Resolution** sets the frequency resolution. Narrower bins show more detail but cost speed.
- The **band** selector draws the channel centres of an FPV video band on the spectrum.
- **Peak hold** keeps the strongest level seen, and **Clear** resets it.
- Drag across the spectrum to zoom into a part of the sweep. **Previous zoom** goes back.

How fast it sweeps: the stick covers 35 MHz per step, so 5.6–6.0 GHz takes 12 steps and is
redrawn about **30 times a second**. Twice the range takes twice as long.

The bridge also learns and removes the stick's filter shape, which would otherwise show as a
hump in every 35 MHz step. Each time you start a sweep, it needs about a second for the floor to
become flat.

## Gains

| Control | Range | What it does |
|---|---|---|
| LNA | 0–3 | Front-end amplifier. Start at 1. Raise it for weak signals; lower it if strong signals look smeared or produce copies. |
| Filter | 0–8 | Gain after the mixer. 4 is a good default. |
| Digital | 0–12 | Digital scaling only. Mainly useful for the live view of very weak signals. |

Gains apply immediately in both the live view and the sweep. The stick's own noise floor is
about 15 dB higher than a dedicated SDR's, so very weak signals can be hidden.

## Command-line options

Add options after the start file's name in a terminal (for example
`START_WITH_PAGE.bat --rate 80`), or edit the `.bat` file.

| Option | Default | Meaning |
|---|---|---|
| `--port N` | 8075 | Port for the page and the WebSocket. The hosted page then needs `?bridge=ws://127.0.0.1:N` at the end of its address. |
| `--no-browser` | off | Don't open the browser automatically. |
| `--allow-origin URL` | — | Also let a page hosted at this address connect. The hosted BrowSDR page and pages on this computer are always allowed. |
| `--settle-us N` | 100 | Microseconds discarded after each retune. Lower is faster, but the start of each step may then be smeared. |
| `--capture N` | 16384 | Samples per sweep step (multiple of 2048). Fewer give a faster but noisier sweep. |
| `--pipeline` | off | FFT on a second thread. About a third faster (≈40 sweeps/s), but the stick then overruns about once a minute; the bridge restarts it with a ~1 s gap. |
| `--rate 80` | 40 | Experimental 80 MS/s (70 MHz per step, ≈55 sweeps/s). It can overrun, especially on slower PCs. |
| `--dc-offset-mhz N` | 2 | Offset used to hide the DC notch. 0 shows the raw notch. |
| `--flat-steps N` | 24 | Sweep steps per update of the filter-shape correction. 0 turns it off. |
| `--fake` | off | No stick: synthetic tones. Use it to try the page. |

## Troubleshooting

| Symptom | What to do |
|---|---|
| `Could not open the stick` | Check that the stick is in a USB 3 port, the libusbK driver is installed ([INSTALL.md](INSTALL.md)) and no other program uses it. Then unplug and replug the stick. |
| `Port 8075 is in use` | The bridge is already running in another window. Use that one, or close it first. |
| Page says *bridge not running* | Start the bridge first. If you use the hosted page, allow it to access your local network when the browser asks (Chrome: the lock icon > Site settings > Local network access). |
| `refused a connection from …` | A page from another website tried to connect. If it's yours, add `--allow-origin` with its address. |
| `stream stopped` / `stream restarted after` in the window | The PC didn't collect samples fast enough (an "overrun"). The bridge restarts the stick automatically, at most three times a minute. If it happens often, close other heavy programs, use a different USB 3 port, and don't use `--pipeline` or `--rate 80`. |
| Nothing works even after a restart | Unplug the stick for a few seconds and plug it back in. The stick must start "cold". |
| Mirrored or doubled signals in the live view | Reload the page so that it uses the current version. |

## Good to know

- The stick only receives. BirriaSweep never transmits and never writes to the stick: every
  setting is lost when you unplug it.
- The levels are in dB relative to the stick's full scale, not calibrated dBm. Use them to
  compare signals.
