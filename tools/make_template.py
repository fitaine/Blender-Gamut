"""Rebuild gamut_viewer/app_template/startup.blend, the file behind + > Print > Gamut.

Run with Blender's interface (screen layouts cannot be built in background mode):

    blender --factory-startup --python tools/make_template.py

Blender opens, builds the layout step by step, saves the template and quits.
"""

import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from gamut_viewer import workspace  # noqa: E402  (plain import, the add-on is not registered)

OUT = os.path.join(ROOT, "gamut_viewer", "app_template", "startup.blend")
TICK = 0.1


def _override(window, area):
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return dict(window=window, screen=window.screen, area=area, region=region)


def _split(window, area, direction, factor):
    before = {a.as_pointer() for a in window.screen.areas}
    with bpy.context.temp_override(**_override(window, area)):
        bpy.ops.screen.area_split(direction=direction, factor=factor)
    return next(a for a in window.screen.areas if a.as_pointer() not in before).as_pointer()


def _area(window, pointer):
    return next(a for a in window.screen.areas if a.as_pointer() == pointer)


class Maker:
    def __init__(self):
        self.w = bpy.context.window_manager.windows[0]
        self.layout = self.w.workspace
        self.step = "duplicate"
        self.a = {}

    def __call__(self):
        try:
            return self.run()
        except Exception:
            import traceback
            traceback.print_exc()
            bpy.ops.wm.quit_blender()
            return None

    def run(self):
        w = self.w
        if self.step == "duplicate":
            before = {x.as_pointer() for x in bpy.data.workspaces}
            with bpy.context.temp_override(window=w):
                bpy.ops.workspace.duplicate()
            self.ws = next(x for x in bpy.data.workspaces if x.as_pointer() not in before)
            self.ws.name = workspace.WORKSPACE_NAME
            self.ws[workspace.TAG] = True
            w.workspace = self.ws
            self.step = "main"
            return TICK

        if self.step == "main":
            if w.workspace != self.ws:
                return TICK
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
            self.step = "right"
            return TICK

        if self.step == "right":
            self.a["right"] = _split(w, _area(w, self.a["main"]), 'VERTICAL', 0.78)
            self.step = "right_rows"
            return TICK

        if self.step == "right_rows":
            if _area(w, self.a["right"]).x < _area(w, self.a["main"]).x:
                self.a["main"], self.a["right"] = self.a["right"], self.a["main"]
            self.a["options"] = _split(w, _area(w, self.a["right"]), 'HORIZONTAL', 0.6)
            self.step = "rows"
            return TICK

        if self.step == "rows":
            if _area(w, self.a["options"]).y > _area(w, self.a["right"]).y:   # options go below
                self.a["options"], self.a["right"] = self.a["right"], self.a["options"]
            self.a["bottom"] = _split(w, _area(w, self.a["main"]), 'HORIZONTAL', 0.5)
            self.step = "cols"
            return TICK

        if self.step == "cols":
            if _area(w, self.a["bottom"]).y > _area(w, self.a["main"]).y:
                self.a["main"], self.a["bottom"] = self.a["bottom"], self.a["main"]
            _split(w, _area(w, self.a["main"]), 'VERTICAL', 0.5)
            self.step = "cols2"
            return TICK

        if self.step == "cols2":
            _split(w, _area(w, self.a["bottom"]), 'VERTICAL', 0.5)
            self.step = "types"
            return TICK

        if self.step == "types":
            _area(w, self.a["right"]).type = 'IMAGE_EDITOR'
            _area(w, self.a["options"]).type = 'PROPERTIES'
            self.step = "style"
            return TICK

        if self.step == "style":
            viewers, preview, options = workspace.areas(w)
            for area in viewers:
                workspace.style_viewer(area.spaces.active)
                workspace.style_dormant(area.spaces.active)
            workspace.style_preview(preview.spaces.active)
            workspace.style_options(options.spaces.active)
            self.step = "context"
            return TICK

        if self.step == "context":
            _, _, options = workspace.areas(w)
            options.spaces.active.context = workspace.OPTIONS_TAB
            print("areas:", [(a.type, a.x, a.y, a.width, a.height) for a in w.screen.areas])
            # Pin an empty Gamut scene to the workspace, so that adding it from the + menu
            # opens straight onto that scene and Blender itself remembers the user's scene.
            gamut = bpy.data.scenes.new(workspace.gscene.SCENE_NAME)
            gamut.world = None
            w.scene = gamut
            self.ws.use_pin_scene = True
            w.workspace = self.layout            # leaving a pinned workspace records its scene
            self.step = "back"
            return TICK

        if self.step == "back":
            if w.workspace != self.layout:
                return TICK
            w.workspace = self.ws
            self.step = "strip"
            return TICK

        if self.step == "strip":
            if w.workspace != self.ws:
                return TICK
            print("scene on the Gamut workspace:", w.scene.name)
            # the + menu lists every workspace in the template file, so keep only Gamut
            others = [x for x in bpy.data.workspaces if x != self.ws]
            bpy.data.batch_remove(ids=others)
            self.step = "save"
            return TICK

        if self.step == "save":
            print("workspaces kept:", [x.name for x in bpy.data.workspaces])
            os.makedirs(os.path.dirname(OUT), exist_ok=True)
            bpy.ops.wm.save_as_mainfile(filepath=OUT, compress=True, copy=True)
            print("saved", OUT, os.path.getsize(OUT))
            bpy.ops.wm.quit_blender()
            return None
        return None


bpy.app.timers.register(Maker(), first_interval=1.0)
