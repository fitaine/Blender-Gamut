# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep the four viewers looking from the same angle while 'Sync Views' is on.

Polls the viewers; whichever one moved since the last tick leads, and the
others copy its rotation, target, distance and projection.
"""

import bpy

from . import scene as gscene
from . import workspace

INTERVAL = 0.02
_last = None


def _views():
    ws = workspace.find_workspace()
    if ws is None:
        return []
    out = []
    for window in bpy.context.window_manager.windows:
        if window.workspace != ws:
            continue
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                out.append((area, area.spaces.active.region_3d))
    return out


def _state(r3d):
    return (tuple(r3d.view_rotation), tuple(r3d.view_location), r3d.view_distance, r3d.view_perspective)


def _same(a, b, eps=1e-6):
    if a[3] != b[3]:
        return False
    flat_a = (*a[0], *a[1], a[2])
    flat_b = (*b[0], *b[1], b[2])
    return all(abs(x - y) <= eps for x, y in zip(flat_a, flat_b))


def _apply(r3d, st):
    r3d.view_rotation = st[0]
    r3d.view_location = st[1]
    r3d.view_distance = st[2]
    r3d.view_perspective = st[3]


def _enabled():
    sc = gscene.get_scene()
    return sc is not None and sc.gamut_viewer.sync_views


def _tick():
    global _last
    if not _enabled():
        _last = None
        return None
    views = _views()
    if len(views) < 2:
        return 0.1
    states = [_state(r) for _, r in views]
    if _last is None:
        leader = 0
    else:
        moved = [i for i, st in enumerate(states) if not _same(st, _last)]
        if not moved:
            return INTERVAL
        leader = moved[0]
    lead = states[leader]
    for i, (area, r3d) in enumerate(views):
        if i != leader and not _same(states[i], lead):
            _apply(r3d, lead)
            area.tag_redraw()
    _last = lead
    return INTERVAL


def start():
    global _last
    _last = None
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=INTERVAL, persistent=True)


def stop():
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
