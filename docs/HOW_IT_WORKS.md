# How BirriaSweep works

```
 MT7921AU stick ──USB 3, libusbK──▶ BirriaSDR (patched) ──ctypes──▶ birria_bridge.py ──WebSocket──▶ BrowSDR page
   firmware: I/Q stream                C library                      FFT, DC fill,          spectrum, waterfall,
   + RF script engine                                                 flatness, server        sweep display
```

## The stick

The MT7921AU is a Wi-Fi 6E chip. [BirriaSDR](https://github.com/ot0tot/BirriaSDR) loads a small
replacement firmware into the chip's NDS32 microcontroller. That firmware streams raw 14-bit I/Q
samples at 40 MS/s (experimentally 80 MS/s) over USB, about 160 MB/s at 40 MS/s. Nothing is
written to flash, so unplugging the stick restores it.

## Fast retune: the RF script in the firmware

A sweep is a loop of "retune, wait, capture 0.4 ms". When the PC does the retune, it takes
dozens of USB control transfers, and each one costs about 100–200 µs on Windows. That limited
the sweep to about one band per second, and to about 18 per second even after the PC-side
optimisations below.

The patch (`driver/birriasdr-fast-sweep.patch`) moves the retune into the stick:

1. The PC works out once, for each frequency, which RF registers to write. This is a "script"
   of up to 42 operations: write, read-modify-write, poll a status bit, and copy a calibration
   field. The PC uploads the script into free microcontroller RAM (64 slots of 512 bytes from
   `0x02041200`) and caches it.
2. To retune, the PC writes the slot address and a sequence number into a control block at
   `0x02041000`. This "doorbell" is a single 8-byte write.
3. The firmware's `script_step` runs between sample packets. It does one SPI transaction per
   call and returns at once while the SPI bridge is busy, so the USB stream never stalls.
4. When the script ends, the firmware writes the sequence number back with a status and the time
   it took (about 150–750 µs). The PC polls for that, drops the first `--settle-us` of samples,
   and captures.

The control block layout:

| Offset | Written by | Meaning |
|---|---|---|
| +0 | firmware | Magic `FTR1`, so the PC knows the feature is there |
| +4 | PC | Script address |
| +8 | PC | Request sequence (written last) |
| +12 | firmware | Done sequence |
| +16 | firmware | Status: 0 ok, 1 poll timeout, 3 bad operation |
| +20 | firmware | Script time in µs |
| +24..+44 | firmware | Internal engine state |

The patch also speeds up the PC-side path, which remains the fallback below 4 GHz or without
`BIRRIASDR_FW_TUNE`:

- It keeps a shadow copy of RF registers, so unchanged values are not rewritten.
- It writes several registers in one USB transfer.
- It skips a 1 ms sleep while polling.
- It caches the band setup.

The combined effect is about 1 sweep per second before and about 31 now, over 5.6–6.0 GHz at
40 MS/s.

PLL calibration is **never** skipped. An earlier experiment that skipped it lost reception
entirely.

## The bridge

`bridge/birria_bridge.py` is one Python file. It uses numpy, and it reaches the driver through
ctypes. A single port (8075) serves both:

- **HTTP:** the bundled page from `ui/`, with the cross-origin isolation headers that BrowSDR
  wants.
- **WebSocket:** commands and data.

Only pages on this computer and the hosted BrowSDR page may open the WebSocket. Any other
website is refused, unless you allow it with `--allow-origin`.

### Protocol

The page sends JSON text messages:

| Command | Fields | Effect |
|---|---|---|
| `info` | — | Replies `{"type":"info","name":…,"rate":…}` |
| `gain` | `lna`, `lpf`, `digital` | Sets the gains |
| `freq` | `hz` | Sets the live-view centre |
| `rx` | — | Starts the live view |
| `sweep` | `startMHz`, `stopMHz`, `fftSize` | Starts sweeping |
| `stop` | — | Stops |

The bridge answers with binary messages:

- **Live view:** 16384 complex int16 samples, interleaved I/Q. The page scales them to int8
  with a slow automatic gain control.
- **Sweep:** one message per step. It holds an 8-byte little-endian frequency of the first bin
  in Hz, then float32 dB values, one per bin.

Errors arrive as `{"type":"error","message":…}`.

### Sweep processing

For each step, the bridge tunes to the step centre and captures 16384 samples (0.41 ms). It
then FFTs them in blocks of `fftSize`, using one float32 window with the level scaling folded
in, and averages the blocks. It keeps the middle 35 MHz of the 40 MHz, where the receive filter
is flat, and sends that as one row.

### Hiding the DC notch

The chip has a hardware notch about 0.5 MHz wide at the tuned frequency. The industry fix is
to tune off-centre so that the notch falls where you don't look. HackRF's `hackrf_sweep` does
this.

- **Live view:** alternate snapshots are tuned 2 MHz below and above the requested centre. The
  two spectra are aligned and merged bin by bin, using the snapshot without the notch in each
  place. The whole 40 MHz stays visible.
- **Sweep:** alternate passes shift every step by ±1 MHz. The bins inside the notch are filled
  from the previous pass, which had the notch somewhere else. This keeps the full sweep speed.

### Removing the filter shape

Every 35 MHz step has the same gentle hump from the stick's filters. Every `--flat-steps` steps
(24 by default), the bridge does the following:

1. It takes the per-bin median over those steps. Real signals occupy only a few steps, so the
   median is the filter shape.
2. It smooths the result.
3. It blends it into a running estimate (80 % old, 20 % new).
4. It subtracts that estimate from every row.

The bridge window shows how much was removed.

### Recovering from overruns

If the PC doesn't drain the USB stream in time, the firmware stops it (state 9). The bridge
then reopens the stick, at most three times a minute, and the sweep or live view continues
after a gap of about a second.

## Performance notes

| Setting | Sweeps per second (5.6–6.0 GHz) | Stability |
|---|---|---|
| Default (40 MS/s) | ≈31 | No overruns in long tests |
| `--pipeline` | ≈40 | About one overrun a minute |
| `--rate 80` | ≈55 | Marginal: overruns |

The time per step is roughly:

- about 0.3 ms for the retune doorbell and its completion,
- 0.1 ms of settling,
- 0.41 ms of capture,
- about 0.3 ms of FFT,
- plus the USB polling overhead.

Halving `--capture` makes the sweep faster but noisier.
