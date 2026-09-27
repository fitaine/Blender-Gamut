# SPDX-License-Identifier: GPL-3.0-or-later
"""Every gamut the viewers can show: four built-in RGB spaces plus user ICC profiles."""

import os
import shutil

import bpy

from . import color

ICC_EXTENSIONS = (".icc", ".icm")

_icc_cache = {}          # path -> (mtime, ICCProfile or error string)


def profiles_dir():
    return bpy.utils.extension_path_user(__package__, path="profiles", create=True)


class StandardGamut:
    def __init__(self, space):
        self.id = "std:" + space.id
        self.label = space.label
        self.space = space
        self.error = None

    def hull(self):
        return color.standard_hull(self.space)

    def outside(self, lab, threshold):
        return color.standard_outside(self.space, lab)


class ICCGamut:
    def __init__(self, path):
        self.path = path
        self.id = "icc:" + os.path.basename(path)
        self.profile = None
        self.error = None
        mtime = os.path.getmtime(path)
        cached = _icc_cache.get(path)
        if cached and cached[0] == mtime:
            loaded = cached[1]
        else:
            try:
                from . import icc
                loaded = icc.ICCProfile(path)
            except Exception as ex:  # a broken or unsupported file must not break the list
                loaded = str(ex)
            _icc_cache[path] = (mtime, loaded)
        if isinstance(loaded, str):
            self.error = loaded
            self.label = os.path.basename(path)
        else:
            self.profile = loaded
            self.label = loaded.label

    def hull(self):
        return self.profile.hull()

    def outside(self, lab, threshold):
        return self.profile.outside(lab, threshold)


def icc_paths():
    folder = profiles_dir()
    return sorted(
        os.path.join(folder, f) for f in os.listdir(folder)
        if f.lower().endswith(ICC_EXTENSIONS)
    )


def all_gamuts():
    items = [StandardGamut(s) for s in color.STANDARD_SPACES]
    items += [ICCGamut(p) for p in icc_paths()]
    return items


def get(gamut_id):
    for g in all_gamuts():
        if g.id == gamut_id:
            return g
    return None


def add_profile(src):
    """Copy an ICC file into the add-on's profile folder after checking it loads."""
    from . import icc
    icc.ICCProfile(src)
    dst = os.path.join(profiles_dir(), os.path.basename(src))
    if os.path.abspath(src) != os.path.abspath(dst):
        shutil.copy2(src, dst)
    return "icc:" + os.path.basename(dst)


def remove_profile(gamut_id):
    if not gamut_id.startswith("icc:"):
        return
    path = os.path.join(profiles_dir(), gamut_id[4:])
    _icc_cache.pop(path, None)
    if os.path.isfile(path):
        os.remove(path)
