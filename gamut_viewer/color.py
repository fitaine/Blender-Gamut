# SPDX-License-Identifier: GPL-3.0-or-later
"""Colour maths with no Blender or Pillow dependency: RGB spaces, XYZ and CIE Lab.

Lab is always relative to D50, the ICC profile connection space, so that
render colours, standard RGB gamuts and printer profiles share one space.
"""

import numpy as np

D50 = np.array([0.96422, 1.0, 0.82521])
D65 = np.array([0.95047, 1.0, 1.08883])

_BRADFORD = np.array([
    [0.8951, 0.2664, -0.1614],
    [-0.7502, 1.7135, 0.0367],
    [0.0389, -0.0685, 1.0296],
])


def _adapt(src_white, dst_white):
    s = _BRADFORD @ src_white
    d = _BRADFORD @ dst_white
    return np.linalg.inv(_BRADFORD) @ np.diag(d / s) @ _BRADFORD


D65_TO_D50 = _adapt(D65, D50)
D50_TO_D65 = _adapt(D50, D65)


def _rgb_to_xyz(primaries, white_xy=(0.3127, 0.3290)):
    """RGB -> XYZ matrix from xy chromaticities of R, G, B and the white point."""
    def xy_to_xyz(x, y):
        return np.array([x / y, 1.0, (1.0 - x - y) / y])

    cols = np.stack([xy_to_xyz(*p) for p in primaries], axis=1)
    white = xy_to_xyz(*white_xy)
    scale = np.linalg.solve(cols, white)
    return cols * scale


def srgb_decode(v):
    v = np.asarray(v, dtype=np.float64)
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def srgb_encode(v):
    v = np.clip(np.asarray(v, dtype=np.float64), 0.0, None)
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * v ** (1 / 2.4) - 0.055)


def gamma_decode(g):
    return lambda v: np.clip(np.asarray(v, dtype=np.float64), 0.0, None) ** g


class RGBSpace:
    def __init__(self, ident, label, primaries, decode):
        self.id = ident
        self.label = label
        self.to_xyz = _rgb_to_xyz(primaries)          # linear RGB -> XYZ (D65)
        self.from_xyz = np.linalg.inv(self.to_xyz)
        self.decode = decode                           # encoded -> linear

    def linear_to_lab(self, rgb):
        return xyz_to_lab((rgb @ self.to_xyz.T) @ D65_TO_D50.T)

    def lab_to_linear(self, lab):
        return (lab_to_xyz(lab) @ D50_TO_D65.T) @ self.from_xyz.T


_P709 = [(0.640, 0.330), (0.300, 0.600), (0.150, 0.060)]
_PADOBE = [(0.640, 0.330), (0.210, 0.710), (0.150, 0.060)]
_PP3 = [(0.680, 0.320), (0.265, 0.690), (0.150, 0.060)]
_P2020 = [(0.708, 0.292), (0.170, 0.797), (0.131, 0.046)]

SRGB = RGBSpace("srgb", "sRGB", _P709, srgb_decode)
ADOBE_RGB = RGBSpace("adobe_rgb", "Adobe RGB (1998)", _PADOBE, gamma_decode(563 / 256))
DISPLAY_P3 = RGBSpace("display_p3", "Display P3", _PP3, srgb_decode)
REC2020 = RGBSpace("rec2020", "Rec.2020", _P2020, gamma_decode(2.4))

STANDARD_SPACES = [SRGB, ADOBE_RGB, DISPLAY_P3, REC2020]

# Blender display devices -> how a render saved "as render" is encoded.
DISPLAY_ENCODINGS = {
    "sRGB": SRGB,
    "Display P3": DISPLAY_P3,
    "Rec.1886": RGBSpace("rec1886", "Rec.1886", _P709, gamma_decode(2.4)),
    "Rec.2020": REC2020,
}


def xyz_to_lab(xyz, white=D50):
    t = np.asarray(xyz, dtype=np.float64) / white
    f = np.where(t > 216 / 24389, np.cbrt(t), (24389 / 27 * t + 16) / 116)
    return np.stack([
        116 * f[..., 1] - 16,
        500 * (f[..., 0] - f[..., 1]),
        200 * (f[..., 1] - f[..., 2]),
    ], axis=-1)


def lab_to_xyz(lab, white=D50):
    lab = np.asarray(lab, dtype=np.float64)
    fy = (lab[..., 0] + 16) / 116
    fx = fy + lab[..., 1] / 500
    fz = fy - lab[..., 2] / 200
    f = np.stack([fx, fy, fz], axis=-1)
    t = np.where(f ** 3 > 216 / 24389, f ** 3, (116 * f - 16) / (24389 / 27))
    return t * white


def lab_to_display(lab, iterations=14):
    """Lab -> linear sRGB in [0, 1] for showing on screen.

    Colours the screen cannot show are pulled toward grey along their own hue
    (lightness and hue kept, chroma reduced) instead of clipped per channel,
    so a paper's cyan still reads as cyan.
    """
    lab = np.asarray(lab, dtype=np.float64)
    L = np.clip(lab[:, :1], 0.0, 100.0)
    ab = lab[:, 1:]

    def fits(scale):
        rgb = SRGB.lab_to_linear(np.hstack([L, ab * scale]))
        return (rgb >= -1e-4).all(axis=1) & (rgb <= 1 + 1e-4).all(axis=1)

    lo = np.zeros((len(lab), 1))
    hi = np.ones((len(lab), 1))
    ok = fits(hi)
    lo[ok] = 1.0
    for _ in range(iterations):
        mid = (lo + hi) / 2
        f = fits(mid)[:, None] & ~ok[:, None]
        lo = np.where(f, mid, lo)
        hi = np.where(f | ok[:, None], hi, mid)
    rgb = SRGB.lab_to_linear(np.hstack([L, ab * lo]))
    return np.clip(rgb, 0.0, 1.0)


def cube_surface(n):
    """Unique grid points on the surface of the unit RGB cube, plus quad faces.

    Mapping these through a colour space gives that space's gamut boundary,
    which keeps sharp edges and any concavities of the real shape.
    """
    idx = -np.ones((n, n, n), dtype=np.int64)
    coords = []
    for i in range(n):
        for j in range(n):
            for k in range(n):
                if 0 in (i, j, k) or n - 1 in (i, j, k):
                    idx[i, j, k] = len(coords)
                    coords.append((i, j, k))
    rgb = np.asarray(coords, dtype=np.float64) / (n - 1)

    faces = []
    last = n - 1
    for axis in range(3):
        for side in (0, last):
            for u in range(last):
                for v in range(last):
                    quad = []
                    for du, dv in ((0, 0), (1, 0), (1, 1), (0, 1)):
                        p = [0, 0, 0]
                        p[axis] = side
                        p[(axis + 1) % 3] = u + du
                        p[(axis + 2) % 3] = v + dv
                        quad.append(idx[tuple(p)])
                    if side == 0:
                        quad.reverse()
                    faces.append(quad)
    return rgb, np.asarray(faces, dtype=np.int64)


def star_hull(lab, n_lat=24, n_lon=48, center=(50.0, 0.0, 0.0)):
    """Gamut boundary from scattered Lab samples by segment maxima.

    Used for device spaces that are not three-channel (CMYK and others),
    where a cube surface does not exist. Keeps the farthest sample in each
    spherical sector around mid-grey and builds a closed sphere-like mesh.
    """
    d = np.asarray(lab, dtype=np.float64) - np.asarray(center)
    # Blender coords: x = a, y = b, z = L
    x, y, z = d[:, 1], d[:, 2], d[:, 0]
    r = np.sqrt(x * x + y * y + z * z)
    theta = np.arccos(np.clip(z / np.maximum(r, 1e-9), -1, 1))       # 0..pi from +L
    phi = np.mod(np.arctan2(y, x), 2 * np.pi)
    ti = np.clip((theta / np.pi * n_lat).astype(int), 0, n_lat - 1)
    pj = np.clip((phi / (2 * np.pi) * n_lon).astype(int), 0, n_lon - 1)

    radius = np.zeros((n_lat, n_lon))
    np.maximum.at(radius, (ti, pj), r)
    # fill empty sectors from their neighbours along the ring
    for i in range(n_lat):
        row = radius[i]
        if not row.any():
            row[:] = radius[max(i - 1, 0)]
            continue
        filled = row > 0
        idx = np.where(filled)[0]
        row[:] = np.interp(np.arange(n_lon), idx, row[idx], period=n_lon)

    verts, faces = [], []
    top = len(verts)
    verts.append((center[0] + radius[0].max(), center[1], center[2]))
    for i in range(n_lat):
        t = (i + 0.5) / n_lat * np.pi
        for j in range(n_lon):
            p = (j + 0.5) / n_lon * 2 * np.pi
            rr = radius[i, j]
            verts.append((center[0] + rr * np.cos(t),
                          center[1] + rr * np.sin(t) * np.cos(p),
                          center[2] + rr * np.sin(t) * np.sin(p)))
    bottom = len(verts)
    verts.append((center[0] - radius[-1].max(), center[1], center[2]))

    ring = lambda i, j: 1 + i * n_lon + (j % n_lon)
    for j in range(n_lon):
        faces.append([top, ring(0, j + 1), ring(0, j)])
        faces.append([bottom, ring(n_lat - 1, j), ring(n_lat - 1, j + 1)])
    for i in range(n_lat - 1):
        for j in range(n_lon):
            faces.append([ring(i, j), ring(i, j + 1), ring(i + 1, j + 1), ring(i + 1, j)])
    return np.asarray(verts), faces


def standard_hull(space, n=17):
    rgb, faces = cube_surface(n)
    # sample evenly in encoded values so dark regions get as many vertices as light ones
    lin = rgb ** 2.2
    return space.linear_to_lab(lin), faces


def standard_outside(space, lab, tolerance=1e-3):
    rgb = space.lab_to_linear(lab)
    return (rgb < -tolerance).any(axis=1) | (rgb > 1 + tolerance).any(axis=1)
