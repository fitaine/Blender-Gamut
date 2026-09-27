# SPDX-License-Identifier: GPL-3.0-or-later
"""The Gamut workspace: a 2 x 2 grid of 3D viewers, and the render with the controls on the right.

Blender only switches workspace and recomputes area sizes when it redraws,
so the layout is built one step per timer tick, never in a single call.
The user's current workspace is duplicated and only the duplicate is edited.
"""

import math

import bpy
from mathutils import Euler

from . import scene as gscene

WORKSPACE_NAME = "Gamut"
TAG = "gamut_viewer_workspace"
TICK = 0.05

building = False       # True while a _Builder is running


def find_workspace():
    for ws in bpy.data.workspaces:
        if ws.get(TAG):
            return ws
    return None


def _area(window, pointer):
    return next((a for a in window.screen.areas if a.as_pointer() == pointer), None)


def _override(window, area):
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return dict(window=window, screen=window.screen, area=area, region=region)


def _split(window, area, direction, factor):
    before = {a.as_pointer() for a in window.screen.areas}
    with bpy.context.temp_override(**_override(window, area)):
        bpy.ops.screen.area_split(direction=direction, factor=factor)
    new = next(a for a in window.screen.areas if a.as_pointer() not in before)
    return new.as_pointer()


def _setup_viewer(window, area, index):
    space = area.spaces.active
    space.show_region_toolbar = False
    space.show_region_ui = False
    space.show_region_tool_header = False
    space.lens = 35
    space.clip_start = 0.001
    space.clip_end = 100.0

    sh = space.shading
    sh.type = 'MATERIAL'
    sh.use_scene_world = False
    sh.use_scene_lights = False
    sh.studiolight_background_alpha = 0.0

    ov = space.overlay
    for attr in ("show_floor", "show_axis_x", "show_axis_y", "show_axis_z", "show_cursor",
                 "show_object_origins", "show_extras", "show_outline_selected",
                 "show_relationship_lines", "show_text", "show_stats"):
        if hasattr(ov, attr):
            setattr(ov, attr, False)

    r3d = space.region_3d
    r3d.view_perspective = 'PERSP'
    r3d.view_location = (0.0, 0.0, 0.5)
    r3d.view_rotation = Euler((math.radians(62), 0.0, math.radians(35))).to_quaternion()
    r3d.view_distance = 2.8

    # show only this viewer's collection, in this viewport only
    space.use_local_collections = True
    with bpy.context.temp_override(**_override(window, area), space_data=space):
        bpy.ops.object.hide_collection(collection_index=index + 1, extend=False)


def _setup_panel_area(area):
    area.type = 'IMAGE_EDITOR'
    space = area.spaces.active
    from .render import preview_image
    space.image = preview_image()
    space.show_region_ui = True
    space.show_region_toolbar = False


def _select_gamut_tab(area):
    """Only works once the sidebar has drawn and knows its tabs."""
    ui = next((r for r in area.regions if r.type == 'UI'), None)
    try:
        ui.active_panel_category = "Gamut"
        return ui.active_panel_category == "Gamut"
    except (TypeError, ValueError, AttributeError):
        return False


def show_preview():
    """Point every Image Editor in the Gamut workspace at the analysed render."""
    ws = find_workspace()
    from .render import preview_image
    img = preview_image()
    if ws is None or img is None:
        return
    for screen in ws.screens:
        for area in screen.areas:
            if area.type == 'IMAGE_EDITOR':
                area.spaces.active.image = img
                area.tag_redraw()


class _Builder:
    """Timer-driven, one screen operation per redraw."""

    def __init__(self, window, ws, original_ws, source_scene, done=None):
        self.window, self.ws, self.original_ws = window, ws, original_ws
        self.source_scene, self.done = source_scene, done
        self.step, self.wait = "switch", 0
        self.a = {}

    def __call__(self):
        global building
        try:
            result = self.run()
        except Exception:
            import traceback
            traceback.print_exc()
            result = None
        if result is None:
            building = False
        return result

    def run(self):
        w = self.window
        if self.step == "switch":
            if w.workspace != self.ws:
                self.wait += 1
                return TICK if self.wait < 100 else None
            w.scene = gscene.ensure_scene()
            main = max(w.screen.areas, key=lambda a: a.width * a.height)
            main.type = 'VIEW_3D'
            self.a["main"] = main.as_pointer()
            self.step = "close"
            return TICK

        if self.step == "close":
            others = [a for a in w.screen.areas if a.as_pointer() != self.a["main"]]
            if others:
                with bpy.context.temp_override(**_override(w, others[0])):
                    bpy.ops.screen.area_close()
                return TICK
            self.step = "split_right"
            return TICK

        if self.step == "split_right":
            self.a["right"] = _split(w, _area(w, self.a["main"]), 'VERTICAL', 0.78)
            self.step = "split_rows"
            return TICK

        if self.step == "split_rows":
            main, right = _area(w, self.a["main"]), _area(w, self.a["right"])
            if right.x < main.x:                       # keep the controls on the right
                self.a["main"], self.a["right"] = self.a["right"], self.a["main"]
            self.a["bottom"] = _split(w, _area(w, self.a["main"]), 'HORIZONTAL', 0.5)
            self.step = "split_top"
            return TICK

        if self.step == "split_top":
            top, bottom = _area(w, self.a["main"]), _area(w, self.a["bottom"])
            if bottom.y > top.y:
                self.a["main"], self.a["bottom"] = self.a["bottom"], self.a["main"]
            self.a["top_right"] = _split(w, _area(w, self.a["main"]), 'VERTICAL', 0.5)
            self.step = "split_bottom"
            return TICK

        if self.step == "split_bottom":
            self.a["bottom_right"] = _split(w, _area(w, self.a["bottom"]), 'VERTICAL', 0.5)
            self.step = "configure"
            return TICK

        if self.step == "configure":
            viewers = sorted(
                (a for a in w.screen.areas if a.as_pointer() != self.a["right"]),
                key=lambda a: (-a.y, a.x))                # top-left, top-right, bottom-left, bottom-right
            for index, area in enumerate(viewers):
                _setup_viewer(w, area, index)
            _setup_panel_area(_area(w, self.a["right"]))
            self.ws.use_pin_scene = True
            self.step = "tab"
            return TICK

        if self.step == "tab":
            self.wait += 1
            if not _select_gamut_tab(_area(w, self.a["right"])) and self.wait < 140:
                return TICK
            if self.source_scene is None:
                self.step = "finish"
            else:
                self.step = "unpin_hop"
                w.workspace = self.original_ws          # remember the user's scene for the way back
            return TICK

        if self.step == "unpin_hop":
            if w.workspace != self.original_ws:
                return TICK
            w.scene = self.source_scene
            w.workspace = self.ws
            self.step = "finish"
            return TICK

        if self.step == "finish":
            if w.workspace != self.ws:
                return TICK
            if self.done:
                self.done()
            return None
        return None


def open_workspace(context, source_scene, done=None):
    global building
    window = context.window
    if building:
        return None
    gscene.ensure_scene()
    ws = find_workspace()
    if ws is not None:
        window.workspace = ws
        return ws

    original_ws = window.workspace
    before = {w.as_pointer() for w in bpy.data.workspaces}
    with context.temp_override(window=window):
        bpy.ops.workspace.duplicate()
    ws = next(w for w in bpy.data.workspaces if w.as_pointer() not in before)
    ws.name = WORKSPACE_NAME
    ws[TAG] = True
    window.workspace = ws
    building = True
    bpy.app.timers.register(_Builder(window, ws, original_ws, source_scene, done), first_interval=TICK)
    return ws
