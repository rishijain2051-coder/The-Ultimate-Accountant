"""
Generate the background bed for the tutorial.

Synthesised rather than sourced: an original piece has no licence to honour, no
attribution to get wrong, and nothing to download - which matters for a project
whose whole premise is that it works offline.

It is written to sit *under* text a viewer is reading, so it is deliberately
uneventful: a slow four-chord loop, sparse bell notes from one pentatonic
scale, no percussion, no transients to pull the eye off the subtitle, and a
ceiling around -20 dBFS.

    python media/music.py           # writes media/work/bed.wav and auditions it
"""

import math
import wave
from pathlib import Path

import numpy as np

RATE = 44100
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "media" / "work" / "bed.wav"

# A minor, with the sixth and ninth left in so it stays warm rather than sad.
# Each chord is held a long time; the point is that nothing demands attention.
CHORD_SECONDS = 15.5
PROGRESSION = [
    ("Am9",    [57, 64, 67, 71, 74]),      # A  E  G  B  D
    ("Fmaj9",  [53, 60, 65, 69, 72]),      # F  C  F  A  C
    ("Cmaj7",  [48, 55, 64, 67, 71]),      # C  G  E  G  B
    ("Gsus2",  [55, 62, 67, 69, 74]),      # G  D  G  A  D
]
# the bell voice picks from this, so it can never clash with the pad
PENTATONIC = [69, 72, 74, 76, 79, 81, 84]


def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def pad_note(midi, seconds, detune=0.15):
    """One sustained note: a few harmonics, slightly detuned, slow to arrive."""
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    f = hz(midi)
    wave_ = np.zeros(n)
    # partial weights chosen to stay soft - no strong odd harmonics
    for harmonic, weight in ((1, 1.0), (2, 0.34), (3, 0.14), (4, 0.07), (6, 0.03)):
        for cents in (-detune, 0.0, detune):
            wave_ += weight * np.sin(
                2 * np.pi * f * harmonic * (1 + cents / 100) * t
                + harmonic * midi)          # deterministic phase offset
    wave_ /= 4.6

    attack = int(3.4 * RATE)
    release = int(5.0 * RATE)
    env = np.ones(n)
    env[:attack] = np.linspace(0, 1, attack) ** 1.7
    if release < n:
        env[-release:] = np.linspace(1, 0, release) ** 1.5
    # a slow breath, so a fifteen-second chord is not literally static
    env *= 1 + 0.07 * np.sin(2 * np.pi * 0.055 * t + midi)
    return wave_ * env


def bell(midi, seconds=5.2):
    """A struck note with a long exponential tail. Sparse, never rhythmic."""
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    f = hz(midi)
    tone = (np.sin(2 * np.pi * f * t)
            + 0.5 * np.sin(2 * np.pi * f * 2 * t)
            + 0.12 * np.sin(2 * np.pi * f * 3.01 * t))
    env = np.exp(-t * 1.25)
    strike = int(0.012 * RATE)
    env[:strike] *= np.linspace(0, 1, strike)
    return tone * env * 0.34


def sub(midi, seconds):
    """The root, two octaves down, just enough to give the pad a floor."""
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    f = hz(midi - 24)
    env = np.ones(n)
    fade = int(2.6 * RATE)
    env[:fade] = np.linspace(0, 1, fade)
    env[-fade:] = np.linspace(1, 0, fade)
    return 0.16 * np.sin(2 * np.pi * f * t) * env


def lowpass(x, cutoff=4500.0):
    """One-pole, applied twice. Takes the glare off the harmonics."""
    a = math.exp(-2 * math.pi * cutoff / RATE)
    for _ in range(2):
        out = np.empty_like(x)
        acc = 0.0
        for i in range(0, len(x), 8192):          # chunked, to stay in cache
            block = x[i:i + 8192]
            for j, v in enumerate(block):
                acc = a * acc + (1 - a) * v
                out[i + j] = acc
        x = out
    return x


def _lowpass_fast(x, cutoff=4500.0):
    """The same filter as a single vectorised pass via lfilter when available."""
    try:
        from scipy.signal import lfilter
    except ImportError:
        return lowpass(x, cutoff)
    a = math.exp(-2 * math.pi * cutoff / RATE)
    for _ in range(2):
        x = lfilter([1 - a], [1, -a], x)
    return x


def stereo_delay(left, right, ms=118, mix=0.26):
    """A short offset between channels. Width without a reverb tail."""
    d = int(RATE * ms / 1000)
    l2 = np.concatenate([np.zeros(d), right[:-d]]) if d < len(right) else right
    r2 = np.concatenate([np.zeros(int(d * 1.6)),
                         left[:-int(d * 1.6)]]) if int(d * 1.6) < len(left) else left
    return left + mix * l2, right + mix * r2


def compose(seconds):
    """Loop the progression for as long as the video runs."""
    total = int(seconds * RATE) + RATE
    left = np.zeros(total)
    right = np.zeros(total)

    rng = np.random.default_rng(20260923)          # same bed every build
    chords = int(math.ceil(seconds / CHORD_SECONDS)) + 1

    for index in range(chords):
        name, notes = PROGRESSION[index % len(PROGRESSION)]
        start = int(index * CHORD_SECONDS * RATE)
        if start >= total:                         # the spare chord can start past the end
            break
        hold = CHORD_SECONDS + 5.0                 # overlap, so chords dissolve
        for position, midi in enumerate(notes):
            voice = pad_note(midi, hold)
            # spread the voices across the stereo field, low notes centred
            pan = 0.5 + (position - (len(notes) - 1) / 2) * 0.13
            pan = min(max(pan, 0.12), 0.88)
            end = min(start + len(voice), total)
            take = end - start
            left[start:end] += voice[:take] * (1 - pan)
            right[start:end] += voice[:take] * pan

        root = sub(notes[0], hold)
        end = min(start + len(root), total)
        left[start:end] += root[:end - start] * 0.5
        right[start:end] += root[:end - start] * 0.5

        # one or two bells per chord, never on the beat
        for _ in range(rng.integers(1, 3)):
            midi = int(rng.choice(PENTATONIC))
            offset = start + int(rng.uniform(1.5, CHORD_SECONDS - 1.0) * RATE)
            note = bell(midi)
            end = min(offset + len(note), total)
            if end <= offset:
                continue
            pan = float(rng.uniform(0.25, 0.75))
            left[offset:end] += note[:end - offset] * (1 - pan)
            right[offset:end] += note[:end - offset] * pan

    left = _lowpass_fast(left)
    right = _lowpass_fast(right)
    left, right = stereo_delay(left, right)

    n = int(seconds * RATE)
    left, right = left[:n], right[:n]

    # settle the level, then soft-clip rather than hard-limit
    peak = max(np.abs(left).max(), np.abs(right).max(), 1e-9)
    target = 10 ** (-20 / 20) * 3.2
    left, right = left / peak * target, right / peak * target
    left, right = np.tanh(left), np.tanh(right)

    fade_in, fade_out = int(5.0 * RATE), int(7.0 * RATE)
    ramp_in = np.linspace(0, 1, fade_in) ** 1.4
    ramp_out = np.linspace(1, 0, fade_out) ** 1.4
    for channel in (left, right):
        channel[:fade_in] *= ramp_in
        channel[-fade_out:] *= ramp_out

    return left, right


def write(seconds, path=OUT):
    path.parent.mkdir(parents=True, exist_ok=True)
    left, right = compose(seconds)
    frames = np.empty(len(left) * 2, dtype=np.int16)
    frames[0::2] = np.clip(left, -1, 1) * 32700
    frames[1::2] = np.clip(right, -1, 1) * 32700
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames.tobytes())
    return path


if __name__ == "__main__":
    import sys
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 45.0
    path = write(seconds)
    size = path.stat().st_size / 1048576
    print(f"  {path}  {seconds:.0f}s  {size:.1f} MB")
