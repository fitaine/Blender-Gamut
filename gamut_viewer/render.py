# SPDX-License-Identifier: GPL-3.0-or-later
"""Read a render slot as it would be saved (view transform applied) and sample it into Lab."""

import os
import tempfile

import bpy
import numpy as np

from . import color

class RenderReadError(Exception):
    pass


def render_result():
    for img in bpy.data.images:
        if img.type == 'RENDER_RESULT':
            return img
    return None


def display_device(scene):
    """The display the render would be saved for, honouring an output colour override."""
    settings = scene.render.image_settings
    if getattr(settings, "color_management", 'FOLLOW_SCENE') == 'OVERRIDE':
        return settings.display_settings.display_device
    return scene.display_settings.display_device


def _save_slot(img, slot_index, source_scene, path):
    """Save one render slot to a 16-bit PNG, as File > Save would, through the scene that rendered it.

    Blender ties a render to its scene, so the scene's own output settings are
    switched to 16-bit PNG for the save and put back straight after.
    """
    settings = source_scene.render.image_settings
    saved = (settings.file_format, settings.color_mode, settings.color_depth)
    slots = img.render_slots
    previous = slots.active_index
    try:
        settings.file_format = 'PNG'
        settings.color_mode = 'RGBA'
        settings.color_depth = '16'
        slots.active_index = slot_index
        img.save_render(path, scene=source_scene)
    except RuntimeError as ex:
        raise RenderReadError(f"No render in slot {slot_index + 1} for scene '{source_scene.name}'") from ex
    finally:
        slots.active_index = previous
        settings.file_format = saved[0]
        settings.color_mode = saved[1]
        settings.color_depth = saved[2]


PREVIEW_NAME = "Gamut Viewer · Render"


def preview_image():
    return bpy.data.images.get(PREVIEW_NAME)


def _preview_path():
    return os.path.join(bpy.utils.extension_path_user(__package__, create=True), "last_render.png")


def _read_png(path):
    """Read the saved render's raw encoded values, then keep it as the preview image."""
    img = bpy.data.images.load(path, check_existing=False)
    img.colorspace_settings.is_data = True     # raw encoded values, no conversion
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)

    old = preview_image()
    if old is not None:
        bpy.data.images.remove(old)
    img.name = PREVIEW_NAME
    img.colorspace_settings.is_data = False
    img.colorspace_settings.name = 'sRGB'
    return px.reshape(-1, 4)


def sample(slot_index, source_scene, count, seed=0):
    """Return (lab, display_rgb) for up to `count` opaque pixels of the render.

    display_rgb is linear sRGB, for colouring the points in the viewers.
    """
    img = render_result()
    if img is None:
        raise RenderReadError("No render yet. Press F12 in your scene first")

    device = display_device(source_scene)
    encoding = color.DISPLAY_ENCODINGS.get(device)
    if encoding is None:
        raise RenderReadError(f"Display device '{device}' is not supported (use sRGB, Display P3, Rec.1886 or Rec.2020)")

    fd, tmp = tempfile.mkstemp(suffix=".png", prefix="gamut_viewer_")
    os.close(fd)
    try:
        _save_slot(img, slot_index, source_scene, tmp)
        path = _preview_path()
        old = preview_image()
        if old is not None:                    # release the file before overwriting it
            bpy.data.images.remove(old)
        os.replace(tmp, path)
        px = _read_png(path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    px = px[px[:, 3] > 0.5, :3]
    if len(px) == 0:
        raise RenderReadError("The render is fully transparent")
    if len(px) > count:
        rng = np.random.default_rng(seed)
        px = px[rng.choice(len(px), count, replace=False)]

    linear = encoding.decode(px)
    lab = encoding.linear_to_lab(linear)
    display = color.lab_to_display(lab)
    return lab, display
