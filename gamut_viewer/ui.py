# SPDX-License-Identifier: GPL-3.0-or-later
import os

import blf
import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       IntProperty, PointerProperty, StringProperty)
from bpy_extras.io_utils import ImportHelper

from bpy.app.handlers import persistent

from . import gamuts, render, scene as gscene, sync, workspace

MODES = [
    ('ALL', "True colour", "Every point in its own colour"),
    ('HIGHLIGHT', "Outside in red", "Points the gamut cannot hold turn red"),
    ('OUTSIDE', "Only outside", "Hide every point the gamut can hold"),
]

_enum_cache = []   # Blender needs enum strings kept alive


def _gamut_items(self, context):
    _enum_cache.clear()
    for n, g in enumerate(gamuts.all_gamuts()):
        label = g.label + ("  (unreadable)" if g.error else "")
        icon = 'COLOR' if g.id.startswith("std:") else 'FILE'
        _enum_cache.append((g.id, label, g.error or g.label, icon, n))
    return _enum_cache


def _settings(context):
    sc = gscene.get_scene()
    return sc.gamut_viewer if sc else None


def evaluate_viewer(i, settings=None):
    """Rebuild viewer i's shell and re-test its points against it."""
    settings = settings or _settings(bpy.context)
    v = settings.viewers[i]
    g = gamuts.get(v.gamut)
    if g is None or g.error:
        gscene.set_shell(i, None)
        v.outside_pct, v.label = -1.0, (g.label if g else "?")
        return
    v.label = g.label
    gscene.set_shell(i, g)
    lab = gscene.get_lab(i)
    if lab is None:
        v.outside_pct = -1.0
        return
    mask = g.outside(lab, settings.threshold)
    gscene.set_outside(i, mask)
    v.outside_pct = float(mask.mean() * 100)


def _on_gamut(self, context):
    s = _settings(context)
    if s:
        evaluate_viewer(list(s.viewers).index(self), s)


def _on_mode(self, context):
    s = _settings(context)
    if s:
        gscene.set_display_mode(list(s.viewers).index(self), self.mode)


def _on_threshold(self, context):
    for i in range(len(self.viewers)):
        evaluate_viewer(i, self)


class GamutViewerItem(bpy.types.PropertyGroup):
    gamut: EnumProperty(name="Gamut", items=_gamut_items, update=_on_gamut)
    mode: EnumProperty(name="Show", items=MODES, default='HIGHLIGHT', update=_on_mode)
    outside_pct: FloatProperty(default=-1.0)
    label: StringProperty()


def _slot_items(self, context):
    img = render.render_result()
    n = len(img.render_slots) if img else 8
    items = []
    for i in range(n):
        name = img.render_slots[i].name if img else ""
        items.append((str(i), name or f"Slot {i + 1}", "", i))
    return items


def _source_poll(self, sc):
    return sc.name != gscene.SCENE_NAME


class GamutViewerSettings(bpy.types.PropertyGroup):
    source_scene: PointerProperty(
        name="Scene", type=bpy.types.Scene, poll=_source_poll,
        description="Scene whose colour management (view transform, look, exposure) the render is read with")
    slot: EnumProperty(name="Render Slot", items=_slot_items,
                       description="Which F12 render slot to analyse")
    sample_count: IntProperty(name="Points", default=50000, min=1000, max=1000000, soft_max=300000,
                              description="How many pixels to sample from the render")
    point_size: FloatProperty(name="Point Size", default=0.004, min=0.0005, max=0.05, precision=4,
                              update=lambda s, c: gscene.set_point_radius(s.point_size))
    shell_opacity: FloatProperty(name="Shell Opacity", default=0.12, min=0.0, max=1.0, subtype='FACTOR',
                                 update=lambda s, c: gscene.set_shell_opacity(s.shell_opacity))
    sync_views: BoolProperty(name="Sync Views", default=False,
                             description="Orbit, pan and zoom all four viewers together",
                             update=lambda s, c: sync.start() if s.sync_views else None)
    shell_coloured: BoolProperty(name="Coloured Shell", default=True,
                                 description="Paint each part of the gamut shell in its own colour, to see which hues clip. "
                                             "Colours your screen cannot show are desaturated along their hue",
                                 update=lambda s, c: gscene.set_shell_coloured(s.shell_coloured))
    threshold: FloatProperty(name="Tolerance (ΔE)", default=3.0, min=0.5, max=20.0,
                             description="For ICC profiles: how far (ΔE76) a colour may drift through the "
                                         "profile before it counts as out of gamut",
                             update=_on_threshold)
    viewers: CollectionProperty(type=GamutViewerItem)


# ---------------------------------------------------------------- operators

DEFAULT_GAMUTS = ["std:srgb", "std:adobe_rgb", "std:display_p3", "std:rec2020"]


def ensure_settings(context, source=None):
    sc = gscene.ensure_scene()
    s = sc.gamut_viewer
    while len(s.viewers) < gscene.VIEWER_COUNT:
        v = s.viewers.add()
        v.gamut = DEFAULT_GAMUTS[len(s.viewers) - 1]
    if source is not None and source != sc:
        s.source_scene = source
    if s.source_scene is None:
        s.source_scene = next((x for x in bpy.data.scenes if x != sc), None)
    for i, v in enumerate(s.viewers):
        gscene.set_display_mode(i, v.mode)
    gscene.set_point_radius(s.point_size)
    gscene.set_shell_opacity(s.shell_opacity)
    gscene.set_shell_coloured(s.shell_coloured)
    return s


class GAMUT_OT_setup(bpy.types.Operator):
    bl_idname = "gamut_viewer.setup"
    bl_label = "Open Gamut Workspace"
    bl_description = "Create the Gamut workspace with four viewers, or switch to it"

    def execute(self, context):
        source = context.scene if context.scene.name != gscene.SCENE_NAME else None
        s = ensure_settings(context, source)
        for i in range(gscene.VIEWER_COUNT):
            evaluate_viewer(i, s)
        workspace.open_workspace(context, source)
        return {'FINISHED'}


class GAMUT_OT_analyze(bpy.types.Operator):
    bl_idname = "gamut_viewer.analyze"
    bl_label = "Analyse Render"
    bl_description = "Read the chosen render slot and place its colours in the four viewers"

    def execute(self, context):
        s = ensure_settings(context)
        if s.source_scene is None:
            self.report({'ERROR'}, "Pick the scene the render comes from")
            return {'CANCELLED'}
        try:
            lab, rgb = render.sample(int(s.slot), s.source_scene, s.sample_count)
        except render.RenderReadError as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        for i in range(gscene.VIEWER_COUNT):
            gscene.set_points(i, lab, rgb)
            gscene.set_display_mode(i, s.viewers[i].mode)
            evaluate_viewer(i, s)
        gscene.set_point_radius(s.point_size)
        workspace.show_preview()
        self.report({'INFO'}, f"{len(lab):,} colours sampled")
        return {'FINISHED'}


class GAMUT_OT_add_icc(bpy.types.Operator, ImportHelper):
    bl_idname = "gamut_viewer.add_icc"
    bl_label = "Add ICC Profiles"
    bl_description = "Copy printer, paper or screen ICC profiles into the add-on so every viewer can use them"

    filter_glob: StringProperty(default="*.icc;*.icm", options={'HIDDEN'})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'})
    directory: StringProperty(subtype='DIR_PATH', options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        system = {'WINDOWS': r"C:\Windows\System32\spool\drivers\color",
                  'DARWIN': "/Library/ColorSync/Profiles"}.get(bpy.app.build_platform.decode().upper())
        if system and os.path.isdir(system):
            self.filepath = system + os.sep
        return super().invoke(context, event)

    def execute(self, context):
        names = [f.name for f in self.files if f.name] or [os.path.basename(self.filepath)]
        added, failed = 0, []
        for name in names:
            try:
                gamuts.add_profile(os.path.join(self.directory or os.path.dirname(self.filepath), name))
                added += 1
            except Exception as ex:
                failed.append(f"{name}: {ex}")
        if failed:
            self.report({'WARNING'}, "Skipped " + "; ".join(failed))
        if added:
            self.report({'INFO'}, f"Added {added} profile(s)")
        return {'FINISHED'}


class GAMUT_OT_remove_icc(bpy.types.Operator):
    bl_idname = "gamut_viewer.remove_icc"
    bl_label = "Remove Profile"
    bl_description = "Remove this ICC profile from the add-on (the original file is not touched)"
    gamut: StringProperty()

    def execute(self, context):
        s = _settings(context)
        gscene.drop_shell_cache(self.gamut)
        gamuts.remove_profile(self.gamut)
        if s:
            for i, v in enumerate(s.viewers):
                if v.gamut not in {g.id for g in gamuts.all_gamuts()}:
                    v.gamut = "std:srgb"
        return {'FINISHED'}


class GAMUT_OT_open_profiles(bpy.types.Operator):
    bl_idname = "gamut_viewer.open_profiles"
    bl_label = "Open Profiles Folder"
    bl_description = "Show the folder the add-on reads ICC profiles from. Files dropped there appear in the lists"

    def execute(self, context):
        bpy.ops.wm.path_open(filepath=gamuts.profiles_dir())
        return {'FINISHED'}


# ---------------------------------------------------------------- panels

def _draw(layout, context):
    s = _settings(context)
    if s is None or len(s.viewers) < gscene.VIEWER_COUNT:
        layout.operator(GAMUT_OT_setup.bl_idname, icon='WORKSPACE')
        return

    col = layout.column(align=True)
    col.prop(s, "source_scene")
    col.prop(s, "slot", text="Slot")
    row = layout.row()
    row.scale_y = 1.5
    row.operator(GAMUT_OT_analyze.bl_idname, icon='PLAY')
    layout.prop(s, "sync_views", icon='LINKED' if s.sync_views else 'UNLINKED')

    for i, v in enumerate(s.viewers):
        box = layout.box()
        head = box.row()
        head.label(text=f"Viewer {i + 1}")
        if v.outside_pct >= 0:
            head.label(text=f"{v.outside_pct:.1f}% outside")
        box.prop(v, "gamut", text="")
        box.prop(v, "mode", text="")

    col = layout.column(align=True)
    col.prop(s, "sample_count")
    col.prop(s, "point_size")
    col.prop(s, "shell_opacity")
    col.prop(s, "shell_coloured")
    col.prop(s, "threshold")

    box = layout.box()
    box.label(text="ICC profiles", icon='FILE')
    for g in gamuts.all_gamuts():
        if g.id.startswith("icc:"):
            r = box.row()
            r.label(text=g.label, icon='ERROR' if g.error else 'NONE')
            r.operator(GAMUT_OT_remove_icc.bl_idname, text="", icon='X', emboss=False).gamut = g.id
    r = box.row(align=True)
    r.operator(GAMUT_OT_add_icc.bl_idname, text="Add ICC…", icon='ADD')
    r.operator(GAMUT_OT_open_profiles.bl_idname, text="", icon='FILE_FOLDER')


class GAMUT_PT_image(bpy.types.Panel):
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Gamut"
    bl_label = "Gamut Viewer"

    def draw(self, context):
        _draw(self.layout, context)


class GAMUT_PT_view3d(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Gamut"
    bl_label = "Gamut Viewer"

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.scene.name == gscene.SCENE_NAME

    def draw(self, context):
        _draw(self.layout, context)


# ---------------------------------------------------------------- viewer labels

_handle = None


def _viewer_index(context):
    sc = context.scene
    if sc is None or sc.name != gscene.SCENE_NAME:
        return None
    for i in range(gscene.VIEWER_COUNT):
        lc = context.view_layer.layer_collection.children.get(gscene.collection_name(i))
        if lc and lc.visible_get():
            return i
    return None


def _draw_label():
    context = bpy.context
    i = _viewer_index(context)
    if i is None:
        return
    s = context.scene.gamut_viewer
    if i >= len(s.viewers):
        return
    v = s.viewers[i]
    text = f"{i + 1} · {v.label}"
    if v.outside_pct >= 0:
        text += f" · {v.outside_pct:.1f}% outside"
    font = 0
    blf.size(font, 15 * context.preferences.system.ui_scale)
    blf.color(font, 0.92, 0.92, 0.92, 1.0)
    blf.position(font, 16 * context.preferences.system.ui_scale, 16 * context.preferences.system.ui_scale, 0)
    blf.draw(font, text)


classes = (
    GamutViewerItem, GamutViewerSettings,
    GAMUT_OT_setup, GAMUT_OT_analyze, GAMUT_OT_add_icc, GAMUT_OT_remove_icc, GAMUT_OT_open_profiles,
    GAMUT_PT_image, GAMUT_PT_view3d,
)


def menu_entry(self, context):
    self.layout.operator(GAMUT_OT_setup.bl_idname, icon='WORKSPACE')


@persistent
def _on_load(_):
    sc = gscene.get_scene()
    if sc is not None and sc.gamut_viewer.sync_views:
        sync.start()


def register():
    global _handle
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.gamut_viewer = PointerProperty(type=GamutViewerSettings)
    bpy.types.IMAGE_MT_view.append(menu_entry)
    _handle = bpy.types.SpaceView3D.draw_handler_add(_draw_label, (), 'WINDOW', 'POST_PIXEL')
    bpy.app.handlers.load_post.append(_on_load)


def unregister():
    global _handle
    sync.stop()
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        _handle = None
    bpy.types.IMAGE_MT_view.remove(menu_entry)
    del bpy.types.Scene.gamut_viewer
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
