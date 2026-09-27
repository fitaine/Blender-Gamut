# SPDX-License-Identifier: GPL-3.0-or-later
"""Gamut Viewer: see a render's colours as a 3D point cloud inside screen and print gamuts."""

from . import ui


def register():
    ui.register()


def unregister():
    ui.unregister()
