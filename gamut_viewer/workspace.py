# SPDX-License-Identifier: GPL-3.0-or-later
"""The Gamut workspace.

The layout ships as an app template (app_template/startup.blend), which puts
"Gamut Viewer > Gamut" in the + menu of the workspace tabs. Whenever a Gamut
workspace becomes active and is not wired up yet, a watcher finishes the job:
Gamut scene, one gamut per viewer, pinned scene.

Blender only switches workspace and recomputes areas when it redraws, so
everything here runs one step per timer tick.
"""

import math
import os
import shutil

import bpy
from mathutils import Euler

from . import scene as gscene

WORKSPACE_NAME = "Gamut"
TEMPLATE_NAME = "Gamut_Viewer"          # shown as "Gamut Viewer" in Blender's menus
TAG = "gamut_viewer_workspace"
TICK = 0.05
WATCH = 0.2

busy = False


def find_workspace():
    for ws in bpy.data.workspaces:
        if ws.get(TAG):
            return ws
    return None


# ---------------------------------------------------------------- app template

def template_source():
    return os.path.join(os.path.dirname(__file__), "app_template", "startup.blend")


def template_dir():
    return bpy.utils.user_resource('SCRIPTS', path=os.path.join("startup", "bl_app_templates_user", TEMPLATE_NAME))


def install_template():
    src = template_source()
    if not os.path.isfile(src):
        return
    dst = template_dir()
    os.makedirs(dst, exist_ok=True)
    shutil.copy2(src, os.path.join(dst, "startup.blend"))
    with open(os.path.join(dst, "gamut_viewer.txt"), "w", encoding="utf-8") as f:
        f.write("Installed by the Gamut Viewer add-on. Removed when the add-on is disabled.\n")


def uninstall_template():
    dst = template_dir()
    if os.path.isfile(os.path.join(dst, "gamut_viewer.txt")):
        shutil.rmtree(dst, ignore_errors=True)


# ---------------------------------------------------------------- area setup (shared by the template maker)

def _override(window, area):
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return dict(window=window, screen=window.screen, area=area, region=region)


def style_viewer(space):
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


def style_preview(space):
    space.show_region_ui = False
    space.show_region_toolbar = False
    space.show_region_tool_header = False


OPTIONS_TAB = 'WORLD'     # the Gamut scene has no world, so this tab only shows our panels


def style_options(space):
    for p in space.bl_rna.properties:
        if p.identifier.startswith("show_properties_"):
            setattr(space, p.identifier, p.identifier == "show_properties_" + OPTIONS_TAB.lower())


def areas(window):
    """(viewers top-left..bottom-right, preview area, options area) of the current screen."""
    screen = window.screen
    viewers = sorted((a for a in screen.areas if a.type == 'VIEW_3D'), key=lambda a: (-a.y, a.x))
    preview = next((a for a in screen.areas if a.type == 'IMAGE_EDITOR'), None)
    options = next((a for a in screen.areas if a.type == 'PROPERTIES'), None)
    return viewers, preview, options


# ---------------------------------------------------------------- wiring a Gamut workspace into this file

class _Configure:
    """Wire the active Gamut workspace to the Gamut scene, then pin it."""

    def __init__(self, window, ws):
        self.window, self.ws = window, ws
        self.source = window.scene if window.scene.name != gscene.SCENE_NAME else None
        self.step, self.wait = "wire", 0

    def __call__(self):
        global busy
        try:
            result = self.run()
        except Exception:
            import traceback
            traceback.print_exc()
            result = None
        if result is None:
            busy = False
        return result

    def run(self):
        w = self.window
        if self.step == "wire":
            if w.workspace != self.ws:
                return None
            from . import ui
            s = ui.ensure_settings(bpy.context, self.source)
            w.scene = gscene.get_scene()
            for i in range(gscene.VIEWER_COUNT):
                ui.evaluate_viewer(i, s)
            self.step = "viewers"
            return TICK

        if self.step == "viewers":
            viewers, preview, options = areas(w)
            for index, area in enumerate(viewers[:gscene.VIEWER_COUNT]):
                space = area.spaces.active
                space.use_local_collections = True
                with bpy.context.temp_override(**_override(w, area), space_data=space):
                    bpy.ops.object.hide_collection(collection_index=index + 1, extend=False)
            if preview is not None:
                from .render import preview_image
                preview.spaces.active.image = preview_image()
            for area in viewers:
                area.spaces.active.show_region_tool_header = False
            if preview is not None:
                preview.spaces.active.show_region_tool_header = False
            if options is not None:
                style_options(options.spaces.active)
                try:
                    options.spaces.active.context = OPTIONS_TAB
                except TypeError:
                    pass
            self.ws.use_pin_scene = True
            if self.source is None:
                return None
            other = bpy.data.workspaces.get(_came_from.get(w.as_pointer(), ""))
            if other is None or other == self.ws or other.use_pin_scene:
                other = next((x for x in bpy.data.workspaces if x != self.ws and not x.use_pin_scene), None)
            if other is None:
                return None
            w.workspace = other                  # hop out and back so Blender remembers the user's scene
            self.step = "hop"
            return TICK

        if self.step == "hop":
            self.wait += 1
            if w.workspace == self.ws and self.wait < 5:
                return TICK
            w.scene = self.source
            w.workspace = self.ws
            return None
        return None


def _needs_wiring(ws):
    return ws.get(TAG) and not ws.use_pin_scene


_came_from = {}          # window -> name of the last non-Gamut workspace, for the scene hop


def _watch():
    """Finish any Gamut workspace that was just added from the + menu."""
    global busy
    for window in bpy.context.window_manager.windows:
        if window.workspace is not None and not window.workspace.get(TAG):
            _came_from[window.as_pointer()] = window.workspace.name
    if not busy:
        for window in bpy.context.window_manager.windows:
            ws = window.workspace
            if ws is not None and _needs_wiring(ws):
                busy = True
                bpy.app.timers.register(_Configure(window, ws), first_interval=TICK)
                break
    return WATCH


def start_watching():
    if not bpy.app.timers.is_registered(_watch):
        bpy.app.timers.register(_watch, first_interval=WATCH, persistent=True)


def stop_watching():
    if bpy.app.timers.is_registered(_watch):
        bpy.app.timers.unregister(_watch)


def open_workspace(context):
    """Switch to the Gamut workspace, adding it from the template if this file has none."""
    ws = find_workspace()
    if ws is not None:
        context.window.workspace = ws
        return
    if os.path.isfile(template_source()):
        bpy.ops.workspace.append_activate(idname=WORKSPACE_NAME, filepath=template_source())


def show_preview():
    """Point the preview area of every Gamut workspace at the analysed render."""
    from .render import preview_image
    img = preview_image()
    if img is None:
        return
    for ws in bpy.data.workspaces:
        if not ws.get(TAG):
            continue
        for screen in ws.screens:
            for area in screen.areas:
                if area.type == 'IMAGE_EDITOR':
                    area.spaces.active.image = img
                    area.tag_redraw()
