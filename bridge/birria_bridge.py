"""BirriaSweep bridge: an MT7921AU/MT7961 Wi-Fi stick as a spectrum analyser in the browser.

Browsers can't open this stick over WebUSB on Windows (it needs libusbK), so this program
opens it with the BirriaSDR native library and serves it to the BrowSDR page: the page itself
over http://127.0.0.1:8075/ (when a built page is next to this file) and the samples over a
WebSocket on the same port.

Run:  python birria_bridge.py [--lib DIR] [--ui DIR] [--fake]
Needs numpy, and birriasdr.dll, birria_display.dll and libusb-1.0.dll from a BirriaSDR build
with driver/birriasdr-fast-sweep.patch (libbirriasdr.so / libbirria_display.so on Linux).
See docs/HOW_IT_WORKS.md for the protocol and the signal processing.
"""
import argparse, base64, ctypes as C, hashlib, json, mimetypes, os, queue, socket, struct, sys
import threading, time, webbrowser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import numpy as np

FULL_SCALE = 8192.0           # 14-bit samples
LIVE_RATE = 40_000_000        # the page's live view always gets this rate (BrowSDR device setting)
LEVEL_REFERENCE_FFT = 65536   # BrowSDR's level reference: dB values match its own FFT at this size
DC_GUARD_HZ = 400_000         # half-width of the stick's DC notch around its tuned frequency
HOSTED_PAGE = 'https://zerotasteguy.github.io/BrowSDR_Sweep/'  # always allowed to connect
OPEN_FLAGS = 1 | 2            # OPEN_EXPERIMENTAL_MT7961 | OPEN_EXPERIMENTAL_TUNING (| 4 for 80 MS/s)

# Fast-retune options of the patched driver (driver/README.md). Set before the library loads;
# a value already present in the environment wins, and an empty value switches an option off.
DRIVER_OPTIONS = ('BIRRIASDR_RF_SHADOW', 'BIRRIASDR_RF_BURST', 'BIRRIASDR_RF_NOPOLL',
                  'BIRRIASDR_FAST_TUNE', 'BIRRIASDR_FW_TUNE')


@dataclass
class Config:
    rate: int = 40_000_000        # samples per second
    span_hz: int = 35_000_000     # used per sweep step: the flat middle of the receive filter
    capture: int = 16384          # samples per sweep step and per live snapshot (0.41 ms)
    batch: int = 16384            # samples per native callback
    settle_us: int = 100          # discarded after each retune
    pipeline: bool = False        # FFT+send on a worker thread
    read_priority: int = 1        # Windows priority of the native read thread (1 = above normal)
    dc_offset_hz: int = 2_000_000 # tuning offset that moves the DC notch (0 = leave the notch)
    flat_steps: int = 24          # sweep steps per update of the filter-shape correction (0 = off)
    watch_samples: int = 65536    # samples per row when a sweep range fits in one tuning (1.6 ms)
    watch_detector: str = 'peak'  # per-bin 'peak' or 'mean' over those samples


cfg = Config()


class Gain(C.Structure):
    _fields_ = [('lna', C.c_uint8), ('lpf', C.c_uint8), ('digital', C.c_uint8)]


def find_lib_dir(start):
    """The folder holding the BirriaSDR libraries, searched below `start` and its parent."""
    names = ('birriasdr.dll', 'birria_display.dll') if os.name == 'nt' else \
            ('libbirriasdr.so', 'libbirria_display.so')
    for base in (start, start.parent):
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d != 'node_modules']
            if all(n in files for n in names):
                return Path(root)
    return None


class Stick:
    """The real receiver: one continuous native stream, retuned live.

    The stream is read by BirriaSDR's display mailbox (birria_display), entirely in native code.
    take() asks the mailbox for the next window of samples; nothing crosses into Python per USB
    transfer."""

    def __init__(self, directory):
        for name in DRIVER_OPTIONS:
            os.environ.setdefault(name, '1')
            if not os.environ[name]:
                del os.environ[name]
        if os.name == 'nt':
            self._dll_dir = os.add_dll_directory(str(directory.resolve()))
            C.CDLL(str(directory / 'libusb-1.0.dll'))
            self.lib = C.CDLL(str(directory / 'birriasdr.dll'))
            self.disp = C.CDLL(str(directory / 'birria_display.dll'))
        else:
            self.lib = C.CDLL(str(directory / 'libbirriasdr.so'))
            self.disp = C.CDLL(str(directory / 'libbirria_display.so'))
        P = C.c_void_p
        for name, args, res in [
                ('open_ex', [C.POINTER(P), C.c_uint32, C.c_uint32], C.c_int),
                ('close', [P], C.c_int),
                ('set_center_freq', [P, C.c_uint64], C.c_int),
                ('set_sample_rate', [P, C.c_uint32], C.c_int),
                ('set_tuner_bandwidth', [P, C.c_uint32], C.c_int),
                ('set_tuner_gain_mode', [P, C.c_int], C.c_int),
                ('set_gain_codes', [P, C.POINTER(Gain)], C.c_int),
                ('read_async', [P, P, P, C.c_uint32, C.c_uint32], C.c_int),
                ('cancel_async', [P], C.c_int),
                ('get_stream_stats', [P, P], C.c_int),
                ('strerror', [C.c_int], C.c_char_p)]:
            fn = getattr(self.lib, 'birriasdr_' + name)
            fn.argtypes, fn.restype = args, res
        for name, args, res in [
                ('create', [], P),
                ('configure', [P, C.c_uint32, C.c_uint64, C.c_int], C.c_int),
                ('take', [P, C.POINTER(C.c_int16), C.c_uint32, C.POINTER(C.c_uint32),
                          C.POINTER(C.c_uint64), C.POINTER(C.c_uint64)], C.c_int),
                ('counters', [P, C.POINTER(C.c_uint64)], None)]:
            fn = getattr(self.disp, 'bs_display_' + name)
            fn.argtypes, fn.restype = args, res
        self.box = self.disp.bs_display_create()
        self.scratch = np.empty((262144, 2), np.int16)
        self.generation, self.last_first = 0, 0
        self.center, self.gain = 5_695_000_000, (1, 4, 0)
        self.restarts = []                         # times of recent automatic restarts
        self.lock = threading.Lock()               # serialises tune, gain and reopen
        self._open()

    def _open(self):
        L = self.lib
        self.dev = C.c_void_p()
        self.error = None
        self.check(L.birriasdr_open_ex(C.byref(self.dev), 0, OPEN_FLAGS | (4 if cfg.rate == 80_000_000 else 0)))
        self.check(L.birriasdr_set_sample_rate(self.dev, cfg.rate))
        if cfg.rate == 80_000_000:
            self.check(L.birriasdr_set_tuner_bandwidth(self.dev, cfg.rate))
        self.check(L.birriasdr_set_tuner_gain_mode(self.dev, 1))
        self.check(L.birriasdr_set_gain_codes(self.dev, C.byref(Gain(*self.gain))))
        self.check(L.birriasdr_set_center_freq(self.dev, self.center))
        # A new stream numbers its samples from 0 again: restart the mailbox count to match.
        self.configure(cfg.capture, reset_counters=1)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def check(self, code):
        if code < 0:
            raise RuntimeError('BirriaSDR: ' + self.lib.birriasdr_strerror(code).decode())

    def _read(self):
        """Runs the native stream until it stops. The mailbox's own C function is the callback,
        with small batches: a fresh window every 0.41 ms instead of every 6.5 ms."""
        callback = C.cast(self.disp.bs_display_callback, C.c_void_p)
        if os.name == 'nt' and cfg.read_priority:
            k = C.windll.kernel32
            k.GetCurrentThread.restype = C.c_void_p
            k.SetThreadPriority.argtypes = [C.c_void_p, C.c_int]
            k.SetThreadPriority(k.GetCurrentThread(), cfg.read_priority)
        code = self.lib.birriasdr_read_async(self.dev, callback, self.box, 0, cfg.batch)
        if code < 0:
            self.error = self.lib.birriasdr_strerror(code).decode()
            print('stream stopped:', self.error, flush=True)
            stats = (C.c_uint8 * 48)()             # struct birriasdr_stream_stats
            if self.lib.birriasdr_get_stream_stats(self.dev, stats) == 0:
                samples, packets, usb = struct.unpack_from('<3Q', bytes(stats))
                state, dma, stage, _ = struct.unpack_from('<IIii', bytes(stats), 24)
                print(f'  stream stats: samples {samples}, packets {packets}, usb bytes {usb}, '
                      f'firmware state {state}, dma errors {dma}, stage {stage}', flush=True)

    def ensure_stream(self):
        """Reopen the device (same frequency and gain) if the native stream has stopped.
        The stick sometimes overruns (firmware state 9); a client command or take() then
        restarts it, at most 3 times a minute."""
        if not self.error:
            return
        now = time.monotonic()
        self.restarts = [t for t in self.restarts if now - t < 60]
        if len(self.restarts) >= 3:
            raise RuntimeError(f'stream stopped: {self.error} (already restarted 3 times this minute)')
        reason = self.error
        self.restarts.append(now)
        with self.lock:
            self.close()
            self._open()
        # A fresh stream needs a moment (firmware boot) before samples flow again.
        start, deadline = self.samples(), time.monotonic() + 5
        while self.samples() == start and not self.error and time.monotonic() < deadline:
            time.sleep(0.01)
        print('stream restarted after:', reason, flush=True)

    def configure(self, size, reset_counters=0):
        self.generation += 1
        self.disp.bs_display_configure(self.box, size, self.generation, reset_counters)

    def samples(self):
        """Samples received since the stream started."""
        out = (C.c_uint64 * 5)()
        self.disp.bs_display_counters(self.box, out)
        return out[0]

    def tune(self, hz):
        self.ensure_stream()                       # a stopped stream may refuse the retune
        with self.lock:
            self.check(self.lib.birriasdr_set_center_freq(self.dev, int(hz)))
            self.center = int(hz)

    def set_gain(self, lna, lpf, digital):
        with self.lock:
            self.check(self.lib.birriasdr_set_gain_codes(self.dev, C.byref(Gain(lna, lpf, digital))))
            self.gain = (lna, lpf, digital)

    def take(self, size, after=0, timeout=0.5):
        """Next published window of `size` samples starting at or after sample `after`."""
        self.configure(size)
        count, first, gen = C.c_uint32(), C.c_uint64(), C.c_uint64()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.error:
                self.ensure_stream()               # raises if it keeps failing
                self.configure(size)
                after, end = 0, time.monotonic() + timeout + 1   # the new stream counts from 0
                continue
            r = self.disp.bs_display_take(self.box, self.scratch.ctypes.data_as(C.POINTER(C.c_int16)),
                                          len(self.scratch), C.byref(count), C.byref(first), C.byref(gen))
            if r == 1 and gen.value == self.generation and first.value >= after:
                self.last_first = first.value
                return self.scratch[:count.value].copy()
            if r == 1:
                self.configure(size)               # too early: re-arm, or the mailbox waits 50 ms
            time.sleep(0.0002)
        raise RuntimeError('no samples from the stick')   # not OSError: that means the client left

    def close(self):
        self.lib.birriasdr_cancel_async(self.dev)
        self.reader.join(2)
        self.lib.birriasdr_close(self.dev)


class FakeStick:
    """Synthetic tones at 5695, 5806 and 5917 MHz plus noise, with the real stick's DC notch,
    for testing without hardware."""

    def __init__(self):
        self.center, self.n, self.rng = 5_695_000_000, 0, np.random.default_rng()
        self.last_first = 0

    def tune(self, hz): self.center = int(hz)
    def ensure_stream(self): pass
    def set_gain(self, *gain): pass
    def samples(self): return self.n
    def close(self): pass

    def take(self, size, after=0, timeout=0.5):
        t = np.arange(size) / cfg.rate
        x = 20 * (self.rng.standard_normal(size) + 1j * self.rng.standard_normal(size))
        for f in (5_695e6, 5_806e6, 5_917e6):
            if abs(f - self.center) < cfg.rate / 2:
                x += 3000 * np.exp(2j * np.pi * (f - self.center) * t)
        if abs(5_800e6 - self.center) < cfg.rate / 2:      # 50 us bursts every 5 ms
            on = ((self.n + np.arange(size)) % (cfg.rate // 200)) < cfg.rate // 20000
            x += on * 3000 * np.exp(2j * np.pi * (5_800e6 - self.center) * t)
        self.last_first = self.n
        self.n += size
        spectrum = np.fft.fft(x)
        notch = int(250_000 / cfg.rate * size)
        spectrum[:notch + 1] = spectrum[-notch:] = 0
        x = np.fft.ifft(spectrum)
        return np.stack([x.real, x.imag], 1).astype(np.int16)


def as_complex(iq):
    """int16 I/Q pairs -> complex64, without a temporary per component."""
    return iq.astype(np.float32).view(np.complex64).reshape(-1)


@dataclass
class SweepPlan:
    """Everything fixed for one sweep range and bin width."""
    fft_size: int          # FFT length at the stick's rate
    bin_hz: float
    keep: int              # bins sent per step
    shift: int             # tuning offset in bins that moves the DC notch between passes
    window: np.ndarray     # Hann window with the level scaling folded in
    capture: int           # samples taken per step

    @staticmethod
    def make(requested_fft_size):
        fft_size = requested_fft_size * cfg.rate // 20_000_000   # same bin width as BrowSDR at 20 MS/s
        bin_hz = cfg.rate / fft_size
        keep = int(cfg.span_hz / bin_hz) // 2 * 2
        # Half the live offset keeps step edges off the filter roll-off; never past the spare bins.
        spare = (fft_size - keep) // 2 - 1
        shift = min(spare, int(round(cfg.dc_offset_hz / 2 / bin_hz)))
        # Power is |FFT(x * hann / full scale)|^2 / n^2, plus BrowSDR's reference-size offset.
        level = 10 ** (10 * np.log10(requested_fft_size / LEVEL_REFERENCE_FFT) / 20)
        window = (np.hanning(fft_size) * level / FULL_SCALE / fft_size).astype(np.float32)
        return SweepPlan(fft_size, bin_hz, keep, shift, window, max(cfg.capture, fft_size))


class Session:
    """One connected page: its mode (idle, live 'rx' or 'sweep') and the loop that feeds it."""

    def __init__(self, stick, ws):
        self.stick, self.ws = stick, ws
        self.mode, self.lock = 'idle', threading.Lock()
        self.center = 5_695_000_000
        self.range = (5600, 6000)
        self.fft_size = 1024
        self.settle = cfg.settle_us * cfg.rate // 1_000_000   # samples discarded after a retune
        self.plan = None
        self.parity = 1                 # which side of each step the stick is tuned to this pass
        self.previous_rows = {}         # last row sent per step, to fill the DC notch
        self.flat_profile, self.flat_recent = None, []
        self.flat_profiles = {}         # learned filter shape per FFT size, kept for narrow ranges
        self.single = False             # the sweep range fits in one tuning: see watch()
        self.live_last = None           # (centre, side, spectrum) of the previous live capture
        self.stats, self.work, self.last_report = [], [], 0.0
        # With --pipeline, FFT+send run on their own thread, so the next retune starts as soon as
        # take() returns. One worker keeps the steps in order; at most two steps wait for it.
        self.jobs = queue.Queue(maxsize=2)
        threading.Thread(target=self._work, daemon=True).start()

    def command(self, msg):
        cmd = msg.get('cmd')
        with self.lock:
            if cmd == 'gain':
                self.stick.set_gain(int(msg['lna']), int(msg['lpf']), int(msg['digital']))
            elif cmd == 'freq':
                self.center = int(msg['hz'])
            elif cmd == 'rx':
                self.stick.ensure_stream()
                self.mode = 'rx'
            elif cmd == 'sweep':
                self.range = (int(msg['startMHz']), int(msg['stopMHz']))
                self.fft_size = min(4096, max(16, int(msg.get('fftSize', 1024))))
                self.plan = SweepPlan.make(self.fft_size)
                self.single = (self.range[1] - self.range[0]) * 1_000_000 <= cfg.span_hz
                self.previous_rows.clear()
                self.flat_profile, self.flat_recent = self.flat_profiles.get(self.plan.fft_size), []
                self.stick.ensure_stream()
                self.mode = 'sweep'
            elif cmd == 'stop':
                self.mode = 'idle'
        if cmd == 'info':
            self.ws.send_text(json.dumps({'type': 'info', 'name': 'BirriaSDR MT7921AU', 'rate': cfg.rate}))

    def run(self):
        while self.ws.open:
            try:
                if self.mode == 'rx':
                    self.ws.send_binary(self.live_snapshot().tobytes())
                    time.sleep(1 / 60)              # display snapshots, not a continuous stream
                elif self.mode == 'sweep':
                    self.watch() if self.single else self.sweep_once()
                else:
                    time.sleep(0.02)
            except (OSError, ConnectionError):
                if not self.ws.open:
                    break
                time.sleep(0.05)                    # a transient socket hiccup: keep the session
            except Exception as e:
                self.send_error(e)
                self.mode = 'idle'
        self.jobs.put(None)                         # ends the FFT worker

    def send_error(self, error):
        try:
            self.ws.send_text(json.dumps({'type': 'error', 'message': str(error)}))
        except (OSError, ConnectionError):
            pass

    # ---- live view -------------------------------------------------------------------------

    def live_snapshot(self):
        """One display snapshot centred on self.center without the stick's DC notch.

        Offset tuning: the stick is tuned dc_offset_hz below and above the centre on alternate
        snapshots. Each capture is moved to the centre in the frequency domain, and every bin is
        taken from the capture that has it farther from its own notch and band edge. The newest
        capture is merged with the previous one, so a snapshot costs one retune. The result is
        turned back into I/Q, so the page shows the full bandwidth with no gap."""
        n = cfg.capture
        if not cfg.dc_offset_hz:
            self.stick.tune(self.center)
            iq = self.stick.take(n)
            if cfg.rate == LIVE_RATE:
                return iq
            return self.to_page_rate(np.fft.fftshift(np.fft.fft(as_complex(iq))))
        bin_hz = cfg.rate / n
        k = int(round(cfg.dc_offset_hz / bin_hz))
        with self.lock:
            centre, last = self.center, self.live_last
            if last and last[0] != centre:
                last = None
            side = -last[1] if last else -1
            self.stick.tune(centre + side * k * bin_hz)
            mark = self.stick.samples() + self.settle
        iq = self.stick.take(n, after=mark)
        spectrum = np.roll(np.fft.fftshift(np.fft.fft(as_complex(iq))), side * k)   # bins relative to centre
        self.live_last = (centre, side, spectrum)
        offsets = (np.arange(n) - n // 2) * bin_hz
        edge = cfg.rate / 2 * 0.95

        def score(s):                               # distance from that capture's notch and edge
            d = np.abs(offsets - s * k * bin_hz)
            return np.minimum(d - DC_GUARD_HZ, edge - d)
        if last:
            merged = np.where(score(side) >= score(last[1]), spectrum, last[2])
        else:                                       # first capture at this centre: its notch shows once
            merged = np.where(score(side) > -edge, spectrum, 0)   # drop the bins that wrapped around
        return self.to_page_rate(merged)

    @staticmethod
    def to_page_rate(spectrum):
        """Centred spectrum -> int16 I/Q at the page's LIVE_RATE. At 80 MS/s this keeps the
        middle 40 MHz, where the wider receive filter is flat to the edges."""
        m = len(spectrum) * LIVE_RATE // cfg.rate
        lo = (len(spectrum) - m) // 2
        y = np.fft.ifft(np.fft.ifftshift(spectrum[lo:lo + m])) * (m / len(spectrum))
        return np.clip(np.round(np.stack([y.real, y.imag], 1)), -8192, 8191).astype(np.int16)

    # ---- sweep -----------------------------------------------------------------------------

    def sweep_once(self):
        """One pass over the range in span_hz steps. Alternate passes tune each step `shift` bins
        above/below its centre, so the DC notch never sits on the same frequency twice in a row
        and fft_send can fill it from the previous pass."""
        plan, (start, stop) = self.plan, self.range
        self.parity = -self.parity
        shift = plan.shift * self.parity
        f = start * 1_000_000
        while self.mode == 'sweep' and f < stop * 1_000_000 and self.ws.open:
            centre = f + plan.keep // 2 * plan.bin_hz
            t0 = time.perf_counter()
            with self.lock:
                self.stick.tune(centre + shift * plan.bin_hz)
                mark = self.stick.samples() + self.settle
            t1 = time.perf_counter()
            iq = self.stick.take(plan.capture, after=mark)
            t2 = time.perf_counter()
            if cfg.pipeline:
                self.jobs.put((self.fft_send, (centre, iq, plan, shift)))   # waits only if two steps behind
            else:
                self.fft_send(centre, iq, plan, shift)
            self.stats.append((t1 - t0, t2 - t1))
            f += plan.keep * plan.bin_hz
        self.report()

    def watch(self):
        """A sweep range that fits in one tuning: tune once and analyse the stream window after
        window, so short bursts (video, telemetry, Wi-Fi packets) show up. A wide sweep sees each
        frequency for 0.4 ms per pass; this sees most of the signal. Each row is the per-bin peak
        (or mean) over cfg.watch_samples samples; the FFTs run on the worker thread while the next
        window is read. The filter shape is not learned here (a steady signal would never move
        out of its bins), only the one learned by a wide sweep is applied."""
        plan, (start, stop) = self.plan, self.range
        lo, hi = start * 1_000_000, stop * 1_000_000
        margin = DC_GUARD_HZ + 2 * plan.bin_hz
        # Put the DC notch just above the range when the range is narrow enough, else mid-range.
        tuned = hi + margin if (hi + margin) - cfg.span_hz / 2 <= lo else (lo + hi) / 2
        tuned = round(tuned / plan.bin_hz) * plan.bin_hz
        with self.lock:
            self.stick.tune(tuned)
            after = self.stick.samples() + self.settle
        size = max(plan.fft_size, cfg.watch_samples // plan.fft_size * plan.fft_size)
        started, used, rows = time.monotonic(), 0, 0
        first = after
        while self.mode == 'sweep' and self.single and self.plan is plan and self.range == (start, stop) \
                and self.ws.open:
            iq = self.stick.take(size, after=after)
            first = self.stick.last_first
            after = first + len(iq)
            used += len(iq)
            rows += 1
            self.jobs.put((self.watch_send, (tuned, iq, plan)))
            if time.monotonic() - started >= 2:
                elapsed = time.monotonic() - started
                print(f'single tuning: {rows / elapsed:.0f} rows/s, {used / (elapsed * cfg.rate):.0%} of the '
                      f'signal analysed ({cfg.watch_detector} of {size / cfg.rate * 1e3:.1f} ms per row)', flush=True)
                started, used, rows = time.monotonic(), 0, 0

    def watch_send(self, tuned, iq, plan):
        n, keep = plan.fft_size, plan.keep
        frames = as_complex(iq)[: len(iq) // n * n].reshape(-1, n)
        spectrum = np.fft.fft(frames * plan.window, axis=1)
        power = spectrum.real ** 2 + spectrum.imag ** 2
        power = power.max(axis=0) if cfg.watch_detector == 'peak' else power.mean(axis=0)
        db = 10 * np.log10(np.fft.fftshift(power) + 1e-20)
        db -= self.flatness(db, learn=False)
        lo = n // 2 - keep // 2
        row = db[lo:lo + keep].astype(np.float32)
        guard = int(DC_GUARD_HZ / plan.bin_hz)
        a, b = keep // 2 - guard - 1, keep // 2 + guard + 1     # the notch at the tuned frequency
        row[a + 1:b] = np.interp(np.arange(a + 1, b), [a, b], [row[a], row[b]])
        try:
            self.ws.send_binary(struct.pack('<Q', int(round(tuned - keep // 2 * plan.bin_hz))) + row.tobytes())
        except (OSError, ConnectionError):
            pass

    def _work(self):
        while (job := self.jobs.get()) is not None:
            try:
                job[0](*job[1])
            except Exception as e:
                self.send_error(e)

    def fft_send(self, centre, iq, plan, shift):
        """Send one step: uint64 LE frequency (Hz) of the first bin, then float32 dB bins at the
        plan's bin width, levelled like BrowSDR's own FFT. The stick was tuned `shift` bins above
        `centre`; the same RF slice is sent whatever the shift."""
        t = time.perf_counter()
        n, keep = plan.fft_size, plan.keep
        frames = as_complex(iq)[: len(iq) // n * n].reshape(-1, n)
        spectrum = np.fft.fft(frames * plan.window, axis=1)
        power = np.fft.fftshift((spectrum.real ** 2 + spectrum.imag ** 2).mean(axis=0))
        db = 10 * np.log10(power + 1e-20)
        db -= self.flatness(db)
        lo = n // 2 - keep // 2 - shift
        first_hz = int(round(centre + (lo + shift - n // 2) * plan.bin_hz))
        row = db[lo:lo + keep].astype(np.float32)
        if cfg.dc_offset_hz:
            # Bins inside the notch (around the tuned frequency) come from the previous pass,
            # which was tuned to the other side; interpolate only until there is one.
            guard = int(DC_GUARD_HZ / plan.bin_hz)
            notch = np.arange(max(0, keep // 2 + shift - guard), min(keep, keep // 2 + shift + guard + 1))
            previous = self.previous_rows.get(first_hz)
            if previous is not None and len(previous) == keep:
                row[notch] = previous[notch]
            elif len(notch):
                a, b = max(notch[0] - 1, 0), min(notch[-1] + 1, keep - 1)
                row[notch] = np.interp(notch, [a, b], [row[a], row[b]])
            self.previous_rows[first_hz] = row
        try:
            self.ws.send_binary(struct.pack('<Q', first_hz) + row.tobytes())
        except (OSError, ConnectionError):
            pass
        self.work.append(time.perf_counter() - t)

    def flatness(self, db, learn=True):
        """The stick's receive filter makes the noise floor sag toward the edges of every capture,
        the same shape at every tuning. Learn it from the steps themselves: every flat_steps
        steps, take the per-bin median over them (signals sit at different bins in each step, so
        they drop out), smooth it across ~1% of the capture, and follow it slowly. Returns the
        correction in dB, zero at the capture's middle, so the overall level stays put."""
        if cfg.flat_steps <= 0:
            return 0.0
        if not learn:
            return 0.0 if self.flat_profile is None else self.flat_profile
        self.flat_recent.append(db.copy())
        if len(self.flat_recent) >= cfg.flat_steps:
            shape = np.median(np.array(self.flat_recent), axis=0)
            width = max(1, len(db) // 200)
            shape = np.convolve(np.pad(shape, width, mode='edge'), np.ones(2 * width + 1) / (2 * width + 1), 'valid')
            middle, eighth = len(db) // 2, len(db) // 8
            shape -= np.median(shape[middle - eighth: middle + eighth])
            self.flat_profile = shape if self.flat_profile is None else 0.8 * self.flat_profile + 0.2 * shape
            self.flat_profiles[len(db)] = self.flat_profile
            self.flat_recent.clear()
        return 0.0 if self.flat_profile is None else self.flat_profile

    def report(self):
        """Every 2 s: where a sweep step's time goes."""
        now = time.monotonic()
        if not self.stats or now - self.last_report < 2:
            return
        tune, wait = (1000 * sum(v) / len(self.stats) for v in zip(*self.stats))
        work = 1000 * sum(self.work) / max(len(self.work), 1)
        shape = '' if self.flat_profile is None else \
            f', filter shape removed: {self.flat_profile.min():.1f}..{self.flat_profile.max():.1f} dB'
        print(f'per step: retune {tune:.2f} ms, wait for samples {wait:.2f} ms, FFT+send {work:.2f} ms{shape}',
              flush=True)
        self.stats.clear()
        self.work.clear()
        self.last_report = now


class WebSocket:
    """Just enough RFC 6455 for one local client."""

    def __init__(self, conn):
        self.conn, self.open, self.wlock = conn, True, threading.Lock()

    @staticmethod
    def handshake(conn, key):
        accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
        conn.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                     b'Sec-WebSocket-Accept: ' + accept + b'\r\n\r\n')
        return WebSocket(conn)

    def _send(self, opcode, payload):
        n = len(payload)
        head = bytes([0x80 | opcode]) + (bytes([n]) if n < 126 else
                                         bytes([126]) + struct.pack('>H', n) if n < 65536 else
                                         bytes([127]) + struct.pack('>Q', n))
        with self.wlock:
            try:
                self.conn.sendall(head + payload)
            except OSError:
                self.open = False
                raise

    def send_text(self, s): self._send(1, s.encode())
    def send_binary(self, b): self._send(2, b)

    def _exact(self, n):
        buf = b''
        while len(buf) < n:
            chunk = self.conn.recv(n - len(buf))
            if not chunk:
                raise ConnectionError
            buf += chunk
        return buf

    def receive(self):
        """Next text message, or None when the client leaves."""
        try:
            while True:
                b0, b1 = self._exact(2)
                n = b1 & 0x7F
                if n == 126: n = struct.unpack('>H', self._exact(2))[0]
                elif n == 127: n = struct.unpack('>Q', self._exact(8))[0]
                mask = self._exact(4) if b1 & 0x80 else b'\0\0\0\0'
                data = bytes(c ^ mask[i % 4] for i, c in enumerate(self._exact(n)))
                op = b0 & 0x0F
                if op == 8:
                    break
                if op == 9:
                    self._send(10, data)
                elif op == 1:
                    return data.decode()
        except (OSError, ConnectionError):
            pass
        self.close()
        return None

    def close(self):
        self.open = False
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


class Server:
    """One port for both jobs: plain HTTP requests get the page's files, a WebSocket upgrade
    gets the stick. Only one page uses the stick at a time; a new one replaces the old."""

    def __init__(self, stick, port, ui, origins):
        self.stick, self.port, self.ui = stick, port, ui
        self.origins = {o.rstrip('/') for o in origins}
        self.current = None                        # (WebSocket, thread) of the page using the stick
        self.takeover = threading.Lock()

    def origin_allowed(self, origin):
        """Pages on this computer always; other sites only when listed with --allow-origin.
        Without this check any website open in the browser could drive the stick."""
        if not origin:
            return True                            # not a browser (scripts, tests)
        host = urlsplit(origin).hostname
        return host in ('127.0.0.1', 'localhost', '::1') or origin.rstrip('/') in self.origins

    def serve_forever(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Windows' SO_REUSEADDR would let a second bridge share the port; it has its own option.
        srv.setsockopt(socket.SOL_SOCKET, getattr(socket, 'SO_EXCLUSIVEADDRUSE', socket.SO_REUSEADDR), 1)
        try:
            srv.bind(('127.0.0.1', self.port))
        except OSError:
            sys.exit(f'Port {self.port} is in use: is the bridge already running in another window?')
        srv.listen(16)
        srv.settimeout(0.5)                        # lets Ctrl+C through on Windows
        while True:
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()

    def handle(self, conn):
        try:
            conn.settimeout(5)
            data = b''
            while b'\r\n\r\n' not in data and len(data) < 65536:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            lines = data.decode('latin1').split('\r\n')
            request = lines[0].split(' ')
            headers = {k.strip().lower(): v.strip() for k, _, v in (line.partition(':') for line in lines[1:])}
            key = headers.get('sec-websocket-key')
            if key:
                if not self.origin_allowed(headers.get('origin')):
                    print('refused a connection from', headers.get('origin'), '(see --allow-origin)', flush=True)
                    conn.sendall(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                    return
                conn.settimeout(None)
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.session(WebSocket.handshake(conn, key))
            elif len(request) >= 2 and request[0] in ('GET', 'HEAD'):
                self.send_file(conn, request[1], request[0] == 'HEAD')
        except (OSError, ConnectionError):
            pass
        finally:
            conn.close()

    def send_file(self, conn, target, head_only):
        # Cross-origin isolation, as BrowSDR's own server sends: it enables SharedArrayBuffer.
        def reply(status, body, content_type='text/plain; charset=utf-8'):
            conn.sendall(f'HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\nContent-Length: {len(body)}\r\n'
                         f'Cache-Control: no-cache\r\nCross-Origin-Opener-Policy: same-origin\r\n'
                         f'Cross-Origin-Embedder-Policy: require-corp\r\nConnection: close\r\n\r\n'.encode() + (b'' if head_only else body))
        if not self.ui:
            return reply('200 OK', b'BirriaSweep bridge is running. Open the BrowSDR page and choose '
                                   b'"Wi-Fi stick (BirriaSDR bridge)".')
        path = (self.ui / unquote(urlsplit(target).path).lstrip('/')).resolve()
        if path.is_dir():
            path = path / 'index.html'
        if self.ui not in path.parents or not path.is_file():
            return reply('404 Not Found', b'not found')
        kind = 'application/wasm' if path.suffix == '.wasm' else \
            mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        if path.suffix in ('.js', '.mjs'):
            kind = 'text/javascript'               # Windows' registry can map .js to text/plain
        reply('200 OK', path.read_bytes(), kind)

    def session(self, ws):
        with self.takeover:                        # a reloaded page arrives before the old one leaves
            if self.current:
                old_ws, old_thread = self.current
                old_ws.close()
                old_thread.join(3)
            self.current = (ws, threading.current_thread())
        print('page connected', flush=True)
        session = Session(self.stick, ws)
        worker = threading.Thread(target=session.run, daemon=True)
        worker.start()
        while (text := ws.receive()) is not None:
            try:
                session.command(json.loads(text))
            except Exception as e:
                session.send_error(e)
        session.mode = 'idle'
        worker.join(2)
        print('page disconnected', flush=True)


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--lib', type=Path, help='folder with birriasdr.dll, birria_display.dll, libusb-1.0.dll')
    ap.add_argument('--ui', type=Path, help='folder with the built BrowSDR page (default: ui/ next to this file)')
    ap.add_argument('--port', type=int, default=8075)
    ap.add_argument('--no-browser', action='store_true', help="don't open the page in the browser")
    ap.add_argument('--allow-origin', action='append', default=[], metavar='URL',
                    help='also accept a page hosted at this origin, e.g. https://example.github.io')
    ap.add_argument('--fake', action='store_true', help='synthetic tones, no stick needed')
    ap.add_argument('--rate', type=int, choices=(40, 80), default=40,
                    help='MS/s; 80 is experimental (70 MHz per sweep step) and can overrun')
    ap.add_argument('--capture', type=int, default=cfg.capture,
                    help='samples per sweep step, multiple of 2048 (fewer = less averaging)')
    ap.add_argument('--batch', type=int, default=0,
                    help='samples per native callback, multiple of 2048, at least --capture')
    ap.add_argument('--settle-us', type=int, default=cfg.settle_us, help='microseconds discarded after each retune')
    ap.add_argument('--pipeline', action='store_true',
                    help='FFT on a second thread: about a third faster, but the stick then overruns '
                         'about once a minute (the bridge restarts it, with a ~1 s gap)')
    ap.add_argument('--read-priority', type=int, default=cfg.read_priority,
                    help='Windows thread priority of the native reader (0 normal, 1 above normal)')
    ap.add_argument('--flat-steps', type=int, default=cfg.flat_steps,
                    help='sweep steps per update of the learned filter-shape correction (0 = off)')
    ap.add_argument('--watch-ms', type=float, default=cfg.watch_samples / cfg.rate * 1e3,
                    help='a sweep range that fits in one tuning gives one row per this many ms of signal')
    ap.add_argument('--watch-detector', choices=('peak', 'mean'), default=cfg.watch_detector,
                    help='how such a row combines its FFTs: peak shows short bursts, mean a steadier floor')
    ap.add_argument('--dc-offset-mhz', type=float, default=cfg.dc_offset_hz / 1e6,
                    help="tuning offset that hides the stick's DC notch (0 = leave the notch)")
    args = ap.parse_args()

    cfg.settle_us, cfg.pipeline, cfg.read_priority = args.settle_us, args.pipeline, args.read_priority
    cfg.flat_steps, cfg.dc_offset_hz = args.flat_steps, int(args.dc_offset_mhz * 1e6)
    cfg.watch_detector = args.watch_detector
    cfg.capture = max(2048, args.capture // 2048 * 2048)
    cfg.batch = max(cfg.capture, args.batch // 2048 * 2048)
    if args.rate == 80:
        cfg.rate, cfg.span_hz = 80_000_000, 70_000_000
    cfg.watch_samples = min(262144, max(2048, int(args.watch_ms * cfg.rate / 1000)))

    ui = (args.ui or here / 'ui').resolve()
    if not (ui / 'index.html').is_file():
        if args.ui:
            sys.exit(f'no index.html in {ui}')
        ui = None
    if args.fake:
        stick = FakeStick()
    else:
        lib = args.lib or find_lib_dir(here)
        if not lib:
            sys.exit('BirriaSDR libraries not found; pass --lib FOLDER (see docs/INSTALL.md)')
        print('library:', lib, flush=True)
        try:
            stick = Stick(lib)
        except (RuntimeError, OSError) as e:
            sys.exit(f'Could not open the stick: {e}\nCheck: stick in a USB 3 port, libusbK driver installed '
                     f'(docs/INSTALL.md), no other program using it. Replug the stick and try again.')
    url = f'http://127.0.0.1:{args.port}/'
    print(f'BirriaSweep bridge running. Keep this window open; close it with Ctrl+C.', flush=True)
    print(f'Page: {url}' if ui else f'Bridge only: open {HOSTED_PAGE} (or your own BrowSDR page) and choose '
          f'"Wi-Fi stick (BirriaSDR bridge)"' + ('' if args.port == 8075 else f'; add ?bridge=ws://127.0.0.1:{args.port}'),
          flush=True)
    if ui and not args.no_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        Server(stick, args.port, ui, [HOSTED_PAGE.split('/BrowSDR')[0]] + args.allow_origin).serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stick.close()


if __name__ == '__main__':
    main()
