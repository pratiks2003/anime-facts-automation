"""Procedurally synthesised sound effects (no licensing concerns)."""

from __future__ import annotations

from typing import List

import numpy as np

SR = 48000


def _env(n: int, attack: float, release: float) -> np.ndarray:
    a = max(1, int(attack * SR))
    env = np.ones(n, dtype=np.float32)
    env[:a] = np.linspace(0, 1, a)
    env[a:] = np.exp(-np.linspace(0, release, n - a))
    return env


def impact(duration: float = 0.9, seed: int = 1) -> np.ndarray:
    """Deep cinematic boom for the hook."""
    n = int(duration * SR)
    t = np.arange(n) / SR
    freq = 55 + 70 * np.exp(-t * 18)  # pitch drop
    body = np.sin(2 * np.pi * np.cumsum(freq) / SR)
    noise = np.random.default_rng(seed).standard_normal(n).astype(np.float32)
    click = noise * np.exp(-t * 60) * 0.35
    return ((body * _env(n, 0.003, 6.0)) + click).astype(np.float32) * 0.9


def whoosh(duration: float = 0.45, seed: int = 2) -> np.ndarray:
    """Filtered-noise swoosh for scene transitions."""
    n = int(duration * SR)
    noise = np.random.default_rng(seed).standard_normal(n).astype(np.float32)
    # moving one-pole low-pass: cutoff sweeps up then down
    t = np.linspace(0, 1, n)
    alpha = 0.02 + 0.25 * np.sin(np.pi * t) ** 2
    out = np.zeros(n, dtype=np.float32)
    y = 0.0
    for i in range(n):
        y += alpha[i] * (noise[i] - y)
        out[i] = y
    env = np.sin(np.pi * t) ** 1.5
    out = out * env
    return (out / (np.abs(out).max() or 1.0) * 0.45).astype(np.float32)


def pop(duration: float = 0.25) -> np.ndarray:
    """Short bright 'ding' for the outro question."""
    n = int(duration * SR)
    t = np.arange(n) / SR
    tone = np.sin(2 * np.pi * 1320 * t) * 0.6 + np.sin(2 * np.pi * 1980 * t) * 0.3
    return (tone * _env(n, 0.002, 9.0) * 0.35).astype(np.float32)


def build_sfx_track(total: float, scene_starts: List[float], roles: List[str]) -> np.ndarray:
    track = np.zeros(int(total * SR) + SR, dtype=np.float32)

    def place(sound: np.ndarray, at: float) -> None:
        i = max(0, int(at * SR))
        j = min(len(track), i + len(sound))
        track[i:j] += sound[: j - i]

    swoosh = whoosh()
    for start, role in zip(scene_starts, roles):
        if role == "hook":
            place(impact(), max(0.0, start - 0.05))
        elif role == "outro":
            place(swoosh, start - 0.2)
            place(pop(), start + 0.05)
        else:
            place(swoosh, start - 0.2)
    return track[: int(total * SR)]
