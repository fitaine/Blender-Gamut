# SPDX-License-Identifier: GPL-3.0-or-later
"""The Gamut scene: one collection per viewer, each with a point cloud and a gamut shell.

Blender units map Lab as x = a*/100, y = b*/100, z = L*/100.
"""

import bpy
import numpy as np

from . import color

SCENE_NAME = "Gamut Viewer"
VIEWER_COUNT = 4
SCALE = 100.0

POINTS_MAT = "Gamut Viewer · Points"
SHELL_MAT = "Gamut Viewer · Shell"
AXES_MAT = "Gamut Viewer · Axes"
POINTS_GN = "Gamut Viewer · Points"

PROP_OUTSIDE = "gamut_outside"
PROP_HIGHLIGHT = "gamut_highlight"


def lab_to_co(lab):
    lab = np.asarray(lab, dtype=np.float64)
    return np.stack([lab[:, 1], lab[:, 2], lab[:, 0]], axis=1) / SCALE


def co_to_lab(co):
    co = np.asarray(co, dtype=np.float64) * SCALE
    return np.stack([co[:, 2], co[:, 0], co[:, 1]], axis=1)


def collection_name(i):
    return f"Gamut Viewer {i + 1}"


def points_name(i):
    return f"Gamut Points {i + 1}"


def shell_name(i):
    return f"Gamut Shell {i + 1}"


def get_scene():
    return bpy.data.scenes.get(SCENE_NAME)


# ---------------------------------------------------------------- materials

def _new_material(name):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    return mat


def _points_material():
    mat = bpy.data.materials.get(POINTS_MAT)
    if mat:
        return mat
    mat = _new_material(POINTS_MAT)
    nt = mat.node_tree
    n, l = nt.nodes, nt.links
    col = n.new("ShaderNodeAttribute"); col.attribute_name = "Col"; col.location = (-600, 100)
    out_attr = n.new("ShaderNodeAttribute"); out_attr.attribute_name = PROP_OUTSIDE; out_attr.location = (-600, -100)
    hl = n.new("ShaderNodeAttribute"); hl.attribute_type = 'OBJECT'; hl.attribute_name = PROP_HIGHLIGHT; hl.location = (-600, -300)
    mul = n.new("ShaderNodeMath"); mul.operation = 'MULTIPLY'; mul.location = (-350, -200)
    mix = n.new("ShaderNodeMix"); mix.data_type = 'RGBA'; mix.location = (-150, 50)
    mix.inputs[7].default_value = (1.0, 0.02, 0.02, 1.0)          # B: highlight red
    emit = n.new("ShaderNodeEmission"); emit.location = (100, 50)
    out = n.new("ShaderNodeOutputMaterial"); out.location = (300, 50)
    l.new(out_attr.outputs["Fac"], mul.inputs[0])
    l.new(hl.outputs["Fac"], mul.inputs[1])
    l.new(mul.outputs[0], mix.inputs["Factor"])
    l.new(col.outputs["Color"], mix.inputs[6])                      # A
    l.new(mix.outputs[2], emit.inputs["Color"])
    l.new(emit.outputs[0], out.inputs["Surface"])
    return mat


def _shell_material():
    mat = bpy.data.materials.get(SHELL_MAT)
    if mat and "Coloured" in mat.node_tree.nodes:
        return mat
    mat = _new_material(SHELL_MAT)
    nt = mat.node_tree
    n, l = nt.nodes, nt.links
    lw = n.new("ShaderNodeLayerWeight"); lw.inputs["Blend"].default_value = 0.35; lw.location = (-700, 0)
    # alpha = opacity * (0.3 + 0.7 * facing): the silhouette reads, the middle stays clear
    ramp = n.new("ShaderNodeMapRange"); ramp.location = (-450, 0)
    ramp.inputs["To Min"].default_value = 0.3
    op = n.new("ShaderNodeValue"); op.name = op.label = "Opacity"; op.outputs[0].default_value = 0.12; op.location = (-450, -250)
    mul = n.new("ShaderNodeMath"); mul.operation = 'MULTIPLY'; mul.location = (-200, -50)
    # colour: each shell vertex in its own colour, or plain grey
    col = n.new("ShaderNodeAttribute"); col.attribute_name = "Col"; col.location = (-700, 350)
    tint = n.new("ShaderNodeValue"); tint.name = tint.label = "Coloured"; tint.outputs[0].default_value = 1.0; tint.location = (-700, 500)
    pick = n.new("ShaderNodeMix"); pick.data_type = 'RGBA'; pick.location = (-450, 350)
    pick.inputs[6].default_value = (0.7, 0.7, 0.7, 1.0)          # A: plain grey
    emit = n.new("ShaderNodeEmission"); emit.location = (-200, 200)
    l.new(tint.outputs[0], pick.inputs["Factor"])
    l.new(col.outputs["Color"], pick.inputs[7])                   # B: shell colour
    l.new(pick.outputs[2], emit.inputs["Color"])
    transp = n.new("ShaderNodeBsdfTransparent"); transp.location = (-200, 350)
    mix = n.new("ShaderNodeMixShader"); mix.location = (50, 150)
    out = n.new("ShaderNodeOutputMaterial"); out.location = (250, 150)
    l.new(lw.outputs["Facing"], ramp.inputs["Value"])
    l.new(ramp.outputs["Result"], mul.inputs[0])
    l.new(op.outputs[0], mul.inputs[1])
    l.new(mul.outputs[0], mix.inputs["Fac"])
    l.new(transp.outputs[0], mix.inputs[1])
    l.new(emit.outputs[0], mix.inputs[2])
    l.new(mix.outputs[0], out.inputs["Surface"])
    mat.surface_render_method = 'BLENDED'
    mat.use_backface_culling = False
    mat.use_transparency_overlap = True
    return mat


def _axes_material():
    mat = bpy.data.materials.get(AXES_MAT)
    if mat:
        return mat
    mat = _new_material(AXES_MAT)
    n, l = mat.node_tree.nodes, mat.node_tree.links
    emit = n.new("ShaderNodeEmission"); emit.inputs["Color"].default_value = (0.35, 0.35, 0.35, 1.0)
    out = n.new("ShaderNodeOutputMaterial"); out.location = (200, 0)
    l.new(emit.outputs[0], out.inputs["Surface"])
    return mat


def set_shell_opacity(value):
    mat = _shell_material()
    mat.node_tree.nodes["Opacity"].outputs[0].default_value = value


def set_shell_coloured(on):
    mat = _shell_material()
    mat.node_tree.nodes["Coloured"].outputs[0].default_value = 1.0 if on else 0.0


# ---------------------------------------------------------------- geometry nodes

def _points_node_group():
    ng = bpy.data.node_groups.get(POINTS_GN)
    if ng:
        return ng
    ng = bpy.data.node_groups.new(POINTS_GN, 'GeometryNodeTree')
    ng.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    r = ng.interface.new_socket("Radius", in_out='INPUT', socket_type='NodeSocketFloat')
    r.default_value, r.min_value = 0.004, 0.0
    ng.interface.new_socket("Only Outside", in_out='INPUT', socket_type='NodeSocketBool')
    ng.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    n, l = ng.nodes, ng.links
    gi = n.new("NodeGroupInput"); gi.location = (-700, 0)
    go = n.new("NodeGroupOutput"); go.location = (500, 0)
    m2p = n.new("GeometryNodeMeshToPoints"); m2p.location = (-450, 0)
    attr = n.new("GeometryNodeInputNamedAttribute"); attr.data_type = 'BOOLEAN'; attr.location = (-700, -250)
    attr.inputs["Name"].default_value = PROP_OUTSIDE
    inv = n.new("FunctionNodeBooleanMath"); inv.operation = 'NOT'; inv.location = (-450, -250)
    both = n.new("FunctionNodeBooleanMath"); both.operation = 'AND'; both.location = (-250, -200)
    delete = n.new("GeometryNodeDeleteGeometry"); delete.domain = 'POINT'; delete.location = (-50, 0)
    setmat = n.new("GeometryNodeSetMaterial"); setmat.location = (250, 0)
    setmat.inputs["Material"].default_value = _points_material()
    l.new(gi.outputs["Geometry"], m2p.inputs["Mesh"])
    l.new(gi.outputs["Radius"], m2p.inputs["Radius"])
    l.new(attr.outputs["Attribute"], inv.inputs[0])
    l.new(inv.outputs[0], both.inputs[0])
    l.new(gi.outputs["Only Outside"], both.inputs[1])
    l.new(m2p.outputs["Points"], delete.inputs["Geometry"])
    l.new(both.outputs[0], delete.inputs["Selection"])
    l.new(delete.outputs[0], setmat.inputs["Geometry"])
    l.new(setmat.outputs[0], go.inputs["Geometry"])
    return ng


def _points_modifier(obj):
    mod = obj.modifiers.get("Gamut Points")
    if mod is None:
        mod = obj.modifiers.new("Gamut Points", 'NODES')
        mod.node_group = _points_node_group()
    return mod


def _socket_id(ng, name):
    for item in ng.interface.items_tree:
        if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.name == name:
            return item.identifier
    raise KeyError(name)


def set_point_radius(radius):
    ng = bpy.data.node_groups.get(POINTS_GN)
    if not ng:
        return
    key = _socket_id(ng, "Radius")
    for i in range(VIEWER_COUNT):
        obj = bpy.data.objects.get(points_name(i))
        if obj and "Gamut Points" in obj.modifiers:
            obj.modifiers["Gamut Points"][key] = radius
            obj.update_tag()


def set_display_mode(i, mode):
    """mode: 'ALL', 'HIGHLIGHT' or 'OUTSIDE'."""
    obj = bpy.data.objects.get(points_name(i))
    if not obj:
        return
    obj[PROP_HIGHLIGHT] = 1.0 if mode in ('HIGHLIGHT', 'OUTSIDE') else 0.0
    mod = _points_modifier(obj)
    mod[_socket_id(mod.node_group, "Only Outside")] = mode == 'OUTSIDE'
    obj.update_tag()


# ---------------------------------------------------------------- scene

def _axes_object(scene):
    obj = bpy.data.objects.get("Gamut Axes")
    if obj:
        return obj
    cu = bpy.data.curves.new("Gamut Axes", 'CURVE')
    cu.dimensions = '3D'
    cu.bevel_depth = 0.002
    for a, b in (((0, 0, 0), (0, 0, 1)), ((-1.3, 0, 0.5), (1.3, 0, 0.5)), ((0, -1.3, 0.5), (0, 1.3, 0.5))):
        sp = cu.splines.new('POLY')
        sp.points.add(1)
        sp.points[0].co = (*a, 1)
        sp.points[1].co = (*b, 1)
    cu.materials.append(_axes_material())
    obj = bpy.data.objects.new("Gamut Axes", cu)
    obj.hide_select = True
    return obj


def ensure_scene():
    """Create (or complete) the Gamut scene and return it."""
    scene = get_scene() or bpy.data.scenes.new(SCENE_NAME)
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.look = 'None'
    scene.display_settings.display_device = 'sRGB'
    scene.render.engine = 'BLENDER_EEVEE_NEXT' if 'BLENDER_EEVEE_NEXT' in {
        e.identifier for e in scene.render.bl_rna.properties['engine'].enum_items} else 'BLENDER_EEVEE'

    axes = _axes_object(scene)
    for i in range(VIEWER_COUNT):
        coll = bpy.data.collections.get(collection_name(i))
        if coll is None:
            coll = bpy.data.collections.new(collection_name(i))
        if coll.name not in scene.collection.children:
            scene.collection.children.link(coll)
        if axes.name not in coll.objects:
            coll.objects.link(axes)

        pts = bpy.data.objects.get(points_name(i))
        if pts is None:
            pts = bpy.data.objects.new(points_name(i), bpy.data.meshes.new(points_name(i)))
            pts.hide_select = True
        if pts.name not in coll.objects:
            coll.objects.link(pts)
        _points_modifier(pts)
        if PROP_HIGHLIGHT not in pts:
            pts[PROP_HIGHLIGHT] = 0.0

        shell = bpy.data.objects.get(shell_name(i))
        if shell is None:
            shell = bpy.data.objects.new(shell_name(i), None)
        if shell.name not in coll.objects:
            coll.objects.link(shell)
    return scene


def set_points(i, lab, display_rgb):
    obj = bpy.data.objects[points_name(i)]
    old = obj.data
    me = bpy.data.meshes.new(points_name(i))
    co = lab_to_co(lab).astype(np.float32)
    me.vertices.add(len(co))
    me.vertices.foreach_set("co", co.ravel())
    col = me.color_attributes.new("Col", 'FLOAT_COLOR', 'POINT')
    rgba = np.ones((len(co), 4), dtype=np.float32)
    rgba[:, :3] = display_rgb
    col.data.foreach_set("color", rgba.ravel())
    me.attributes.new(PROP_OUTSIDE, 'BOOLEAN', 'POINT')
    me.update()
    obj.data = me
    if old is not None and old.users == 0:
        bpy.data.meshes.remove(old)


def get_lab(i):
    obj = bpy.data.objects.get(points_name(i))
    if obj is None or obj.type != 'MESH' or len(obj.data.vertices) == 0:
        return None
    co = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", co)
    return co_to_lab(co.reshape(-1, 3))


def set_outside(i, mask):
    obj = bpy.data.objects[points_name(i)]
    attr = obj.data.attributes.get(PROP_OUTSIDE)
    if attr is None:
        attr = obj.data.attributes.new(PROP_OUTSIDE, 'BOOLEAN', 'POINT')
    attr.data.foreach_set("value", np.asarray(mask, dtype=bool))
    obj.data.update()


def _shell_mesh(gamut):
    name = "Gamut Shell · " + gamut.id
    me = bpy.data.meshes.get(name)
    if me is not None and "Col" in me.color_attributes:
        return me
    if me is not None:                       # made by an older version, without colours
        me.name = name + " (old)"
    lab, faces = gamut.hull()
    me = bpy.data.meshes.new(name)
    me.from_pydata(lab_to_co(lab).tolist(), [], [list(map(int, f)) for f in faces])
    me.validate()
    rgba = np.ones((len(lab), 4), dtype=np.float32)
    rgba[:, :3] = color.lab_to_display(lab)
    me.color_attributes.new("Col", 'FLOAT_COLOR', 'POINT').data.foreach_set("color", rgba.ravel())
    me.materials.append(_shell_material())
    return me


def set_shell(i, gamut):
    """Swap viewer i's shell for `gamut`. Shell meshes are shared between viewers."""
    old = bpy.data.objects.get(shell_name(i))
    me = _shell_mesh(gamut) if gamut else None
    if old is not None and old.data is me:
        return
    colls = list(old.users_collection) if old else [bpy.data.collections[collection_name(i)]]
    if old is not None:
        bpy.data.objects.remove(old)
    obj = bpy.data.objects.new(shell_name(i), me)
    obj.hide_select = True
    for c in colls:
        c.objects.link(obj)


def drop_shell_cache(gamut_id):
    me = bpy.data.meshes.get("Gamut Shell · " + gamut_id)
    if me is not None:
        for i in range(VIEWER_COUNT):
            obj = bpy.data.objects.get(shell_name(i))
            if obj and obj.data is me:
                set_shell(i, None)
        bpy.data.meshes.remove(me)
