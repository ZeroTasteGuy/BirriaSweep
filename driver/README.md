# The BirriaSDR fast-sweep patch

`birriasdr-fast-sweep.patch` applies to [BirriaSDR](https://github.com/ot0tot/BirriaSDR) commit
`35439a93fd845c1105559382b17f35c1a0abb5b9`:

```sh
git clone https://github.com/ot0tot/BirriaSDR && cd BirriaSDR
git checkout 35439a93fd845c1105559382b17f35c1a0abb5b9
git apply ../driver/birriasdr-fast-sweep.patch
```

The build scripts in `scripts/` do this for you.

## What it changes

| File | Change |
|---|---|
| `firmware/iq_stream.S` | Non-blocking RF script engine (`script_step`), called between sample packets. |
| `src/chips/mt7921_image.h` | The rebuilt firmware image (1952 bytes). `tools/rebuild_firmware.py --check` verifies it against the source. |
| `src/chips/mt7921_boot.c` | Larger ROM command frame (1200 → 4200 bytes) for the bigger image. |
| `src/chips/mt7921_tune.c` | Uploads per-frequency scripts and rings the firmware doorbell. Caches the band setup. Optional readback skip. |
| `src/chips/mt7921_rf.c` | RF register shadow, burst writes, polling without sleeps. |
| `src/chips/mt7921_capture.c` | Clears the script cache when a stream starts. |
| `src/io.c`, `src/internal.h`, `src/chips/mt7921.h` | Status polls spin for the first 2 ms instead of sleeping, a USB transfer counter for the trace, and the new state fields. |
| `tests/test_core.c` | Expects the new image size. All 12 tests pass. |

## Switches

The patch changes nothing unless these environment variables are set. The bridge sets all of
them to `1` unless you set one yourself; an empty value switches it off, for example
`set BIRRIASDR_FW_TUNE=` before starting.

| Variable | Effect |
|---|---|
| `BIRRIASDR_FW_TUNE` | Retune inside the stick's firmware (≥ 4 GHz, needs a running stream). This is the big speed-up. |
| `BIRRIASDR_RF_SHADOW` | Don't rewrite RF registers that already hold the value. |
| `BIRRIASDR_RF_BURST` | Write consecutive RF registers in one USB transfer. |
| `BIRRIASDR_RF_NOPOLL` | Busy-poll the RF bridge without 1 ms sleeps. |
| `BIRRIASDR_FAST_TUNE` | Skip reading back the PLL lock after a retune. Calibration still runs. |
| `BIRRIASDR_TRACE_TUNE` | Print the time of every retune step to stderr (diagnostics). |

## Rebuilding the firmware

Editing `firmware/iq_stream.S` requires an `nds32le-elf` binutils 2.42 toolchain. Once you have
it, run `python tools/rebuild_firmware.py` to regenerate `mt7921_image.h`. The shipped image is
byte-identical to what this toolchain produces from the patched source.
