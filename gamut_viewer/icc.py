# SPDX-License-Identifier: GPL-3.0-or-later
"""ICC profiles through LittleCMS (bundled with Pillow).

Pillow only transforms 8-bit Lab, and stores a* and b* as signed bytes.
The helpers below hide that; everything else in the add-on uses float Lab.
"""

import os

import numpy as np
from PIL import Image, ImageCms

from . import color

_LAB = ImageCms.createProfile("LAB")          # D50, the ICC connection space
_INTENT = ImageCms.Intent.RELATIVE_COLORIMETRIC

_MODES = {"RGB": "RGB", "CMYK": "CMYK", "GRAY": "L"}


def _encode_lab(lab):
    lab = np.asarray(lab, dtype=np.float64)
    out = np.empty(lab.shape, dtype=np.uint8)
    out[..., 0] = np.clip(np.round(lab[..., 0] * 255 / 100), 0, 255)
    out[..., 1] = np.clip(np.round(lab[..., 1]), -128, 127).astype(np.int8).view(np.uint8)
    out[..., 2] = np.clip(np.round(lab[..., 2]), -128, 127).astype(np.int8).view(np.uint8)
    return out


def _decode_lab(raw):
    raw = np.asarray(raw)
    return np.stack([
        raw[..., 0].astype(np.float64) * 100 / 255,
        raw[..., 1].astype(np.uint8).view(np.int8).astype(np.float64),
        raw[..., 2].astype(np.uint8).view(np.int8).astype(np.float64),
    ], axis=-1)


def _column(arr, mode):
    """(N, C) uint8 array -> an N x 1 Pillow image."""
    arr = np.ascontiguousarray(arr, dtype=np.uint8)
    if mode == "L":
        return Image.fromarray(arr.reshape(-1, 1), "L")
    return Image.frombytes(mode, (1, arr.shape[0]), arr.tobytes())


def _pixels(img, channels):
    return np.frombuffer(img.tobytes(), dtype=np.uint8).reshape(-1, channels)


class ICCProfile:
    """One printer, paper or screen profile, loaded once and cached."""

    def __init__(self, path):
        self.path = path
        self.profile = ImageCms.getOpenProfile(path)
        p = self.profile.profile
        space = (p.xcolor_space or "").strip().upper()
        if space not in _MODES:
            raise ValueError(f"unsupported colour space '{space}'")
        self.space = space
        self.mode = _MODES[space]
        self.channels = {"RGB": 3, "CMYK": 4, "GRAY": 1}[space]
        desc = (p.profile_description or "").strip()
        self.label = desc or os.path.splitext(os.path.basename(path))[0]
        self._to_lab = ImageCms.buildTransform(self.profile, _LAB, self.mode, "LAB", _INTENT)
        self._from_lab = ImageCms.buildTransform(_LAB, self.profile, "LAB", self.mode, _INTENT)

    def device_to_lab(self, device):
        """(N, channels) uint8 device values -> (N, 3) Lab."""
        img = ImageCms.applyTransform(_column(device, self.mode), self._to_lab)
        return _decode_lab(_pixels(img, 3))

    def hull(self, n=21):
        """Gamut boundary as (Lab vertices, faces)."""
        if self.space == "RGB":
            rgb, faces = color.cube_surface(n)
            device = np.round(rgb * 255).astype(np.uint8)
            return self.device_to_lab(device), faces
        if self.space == "CMYK":
            s = np.round(np.linspace(0, 255, 11)).astype(np.uint8)
            grid = np.stack(np.meshgrid(s, s, s, s, indexing="ij"), axis=-1).reshape(-1, 4)
        else:  # GRAY: a line on the neutral axis, drawn as a thin spindle
            grid = np.arange(256, dtype=np.uint8).reshape(-1, 1)
        return color.star_hull(self.device_to_lab(grid))

    def outside(self, lab, threshold=3.0):
        """True where a colour does not survive a round trip through the profile.

        The comparison is against the 8-bit-quantised input, so Lab rounding
        alone never counts as out of gamut.
        """
        raw = _encode_lab(lab)
        img = _column(raw, "LAB")
        dev = ImageCms.applyTransform(img, self._from_lab)
        back = ImageCms.applyTransform(dev, self._to_lab)
        d = _decode_lab(_pixels(back, 3)) - _decode_lab(raw)
        return np.sqrt((d * d).sum(axis=1)) > threshold


def self_test():
    """Pillow's Lab byte layout must match _encode/_decode_lab. Returns sRGB red in Lab."""
    srgb = ImageCms.createProfile("sRGB")
    t = ImageCms.buildTransform(srgb, _LAB, "RGB", "LAB", _INTENT)
    red = ImageCms.applyTransform(_column(np.array([[255, 0, 0]]), "RGB"), t)
    return _decode_lab(_pixels(red, 3))[0]
