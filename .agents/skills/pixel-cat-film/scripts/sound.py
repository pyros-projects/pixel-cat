"""Synthesize a quiet stereo ambience and place short Foley cues on the cut."""

import math
import random
import struct
import wave
from array import array

RATE = 24000
TAU = math.tau


def cue_samples(cue, rng):
    frequency = cue["hz"]
    end = cue.get("to_hz", frequency)
    length = cue["seconds"]
    kind = cue["voice"]
    low = 0
    for i in range(round(length * RATE)):
        t = i / RATE
        progress = t / length
        phase = TAU * (frequency * t + 0.5 * (end - frequency) * t * progress)
        white = rng.uniform(-1, 1)
        low += 0.08 * (white - low)
        if kind == "bell":
            tone = sum(
                v * math.sin(phase * k) * math.exp(-t * d / length)
                for k, v, d in (
                    (1, 0.55, 3),
                    (2.01, 0.23, 5),
                    (2.72, 0.15, 8),
                    (4.16, 0.08, 14),
                )
            )
            envelope = min(1, t / 0.006) * min(1, (length - t) / 0.06)
        elif kind == "knock":
            tone = (
                0.70 * math.sin(phase + 3 * (1 - math.exp(-38 * t)))
                + 0.9 * low
                + 0.15 * white
            )
            envelope = min(1, t / 0.003) * math.exp(-7 * progress)
        elif kind == "hiss":
            tone = 1.7 * low + 0.15 * white + 0.08 * math.sin(phase)
            envelope = math.sin(math.pi * progress) ** 1.2
        elif kind == "wheel":
            # Two joints per carriage, with a low rumble beneath the impacts.
            beat = (4 * t + 1.3 * t * t) % 1
            impact = math.exp(-18 * beat) + (
                0.65 * math.exp(-22 * (beat - 0.26)) if beat > 0.26 else 0
            )
            tone = (0.65 * math.sin(phase) + 0.9 * low) * impact + 0.10 * math.sin(
                0.5 * phase
            )
            envelope = math.sin(math.pi * progress) ** 0.7
        elif kind == "tone":
            tone = (
                0.72 * math.sin(phase)
                + 0.2 * math.sin(phase * 1.006)
                + 0.08 * math.sin(2 * phase)
            )
            envelope = math.sin(math.pi * progress) ** 0.8
        else:
            raise ValueError("Unknown Foley voice: " + kind)
        yield tone * envelope * cue["gain"]


def soundtrack(path, seconds, config, seed):
    count = round(seconds * RATE)
    rng = random.Random(seed)
    left, right = array("f", [0]) * count, array("f", [0]) * count
    lo_l = lo_r = 0
    fundamental = config["root"]
    for i in range(count):
        t = i / RATE
        lo_l += 0.018 * (rng.uniform(-1, 1) - lo_l)
        lo_r += 0.018 * (rng.uniform(-1, 1) - lo_r)
        swell = 0.78 + 0.22 * math.cos(t * 0.86)
        drone = config["drone"] * (
            math.sin(TAU * fundamental * t)
            + 0.23 * math.sin(TAU * fundamental * 1.501 * t)
        )
        left[i] = swell * (drone + config["air"] * lo_l)
        right[i] = swell * (0.94 * drone + config["air"] * lo_r)
    for cue in config["cues"]:
        offset = round(cue["at"] * RATE)
        pan = cue.get("pan", 0)
        lg, rg = math.sqrt((1 - pan) / 2), math.sqrt((1 + pan) / 2)
        delay = round(cue.get("echo", 0.16) * RATE)
        for n, value in enumerate(cue_samples(cue, rng)):
            i = offset + n
            if i >= count:
                break
            left[i] += lg * value
            right[i] += rg * value
            if delay and i + delay < count:
                left[i + delay] += rg * value * 0.19
                right[i + delay] += lg * value * 0.23
    samples = bytearray(count * 4)
    for i, (a, b) in enumerate(zip(left, right)):
        fade = min(1, i / (0.12 * RATE), (count - 1 - i) / (0.32 * RATE))
        struct.pack_into(
            "<hh",
            samples,
            4 * i,
            round(31000 * math.tanh(a) * fade),
            round(31000 * math.tanh(b) * fade),
        )
    with wave.open(str(path), "wb") as wav:
        wav.setparams((2, 2, RATE, 0, "NONE", ""))
        wav.writeframes(samples)
