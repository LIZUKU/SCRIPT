# -*- coding: utf-8 -*-
"""
Sci-Fi Panel / Cut Generator for Maya 2025
Author: ChatGPT

Features:
- Select a mesh or selected faces
- Generate random cuts
- True Perpendicular 90 mode (grid-like cuts)
- Presets
- Sliders + numeric fields
- Panel pass (inset + depth)
- Surface variation / damage with visible ranges
- Resizable UI
- English UI

Tested logic for Maya cmds workflow.
Best used on planar surfaces / planes / wall pieces / floor panels.
"""

import maya.cmds as cmds
import random
import math

WINDOW_NAME = "scifiPanelGeneratorEnglishUI"

PRESETS = {
    "Clean Panels": {
        "cut_count": 8,
        "cut_mode": "Mixed 90",
        "angle_jitter": 3.0,
        "point_jitter": 0.08,
        "min_face_area": 0.0001,
        "one_cut_per_face": True,

        "panel_ratio": 0.30,
        "inset_min": 0.008,
        "inset_max": 0.025,
        "depth_min": -0.020,
        "depth_max": -0.006,
        "double_panel_chance": 0.20,

        "noise_ratio": 0.08,
        "noise_min": 0.05,
        "noise_max": 0.20,
    },

    "Dense Panels": {
        "cut_count": 20,
        "cut_mode": "Mixed 90",
        "angle_jitter": 5.0,
        "point_jitter": 0.12,
        "min_face_area": 0.0001,
        "one_cut_per_face": True,

        "panel_ratio": 0.45,
        "inset_min": 0.008,
        "inset_max": 0.030,
        "depth_min": -0.020,
        "depth_max": -0.006,
        "double_panel_chance": 0.35,

        "noise_ratio": 0.12,
        "noise_min": 0.08,
        "noise_max": 0.35,
    },

    "Perpendicular Grid": {
        "cut_count": 14,
        "cut_mode": "Perpendicular 90",
        "angle_jitter": 0.0,
        "point_jitter": 0.04,
        "min_face_area": 0.0001,
        "one_cut_per_face": False,

        "panel_ratio": 0.35,
        "inset_min": 0.008,
        "inset_max": 0.025,
        "depth_min": -0.018,
        "depth_max": -0.005,
        "double_panel_chance": 0.20,

        "noise_ratio": 0.10,
        "noise_min": 0.10,
        "noise_max": 0.50,
    },

    "Industrial Mess": {
        "cut_count": 24,
        "cut_mode": "Diagonal",
        "angle_jitter": 10.0,
        "point_jitter": 0.18,
        "min_face_area": 0.0001,
        "one_cut_per_face": True,

        "panel_ratio": 0.50,
        "inset_min": 0.006,
        "inset_max": 0.040,
        "depth_min": -0.030,
        "depth_max": -0.008,
        "double_panel_chance": 0.45,

        "noise_ratio": 0.20,
        "noise_min": 0.20,
        "noise_max": 1.20,
    },

    "Large Panels + Micro Detail": {
        "cut_count": 10,
        "cut_mode": "Perpendicular 90",
        "angle_jitter": 0.0,
        "point_jitter": 0.03,
        "min_face_area": 0.0001,
        "one_cut_per_face": False,

        "panel_ratio": 0.28,
        "inset_min": 0.015,
        "inset_max": 0.060,
        "depth_min": -0.025,
        "depth_max": -0.010,
        "double_panel_chance": 0.50,

        "noise_ratio": 0.10,
        "noise_min": 0.08,
        "noise_max": 0.40,
    },
}


# ---------------------------------------------------------
# UI helpers
# ---------------------------------------------------------

def set_slider_field_value(base_name, value):
    if cmds.floatSliderGrp(base_name, exists=True):
        cmds.floatSliderGrp(base_name, e=True, v=value)
    elif cmds.intSliderGrp(base_name, exists=True):
        cmds.intSliderGrp(base_name, e=True, v=value)


def get_float(name, default=0.0):
    try:
        return cmds.floatSliderGrp(name, q=True, v=True)
    except Exception:
        try:
            return cmds.floatField(name, q=True, v=True)
        except Exception:
            return default


def get_int(name, default=0):
    try:
        return cmds.intSliderGrp(name, q=True, v=True)
    except Exception:
        try:
            return cmds.intField(name, q=True, v=True)
        except Exception:
            return default


def get_checkbox(name, default=False):
    try:
        return cmds.checkBox(name, q=True, v=True)
    except Exception:
        return default


def get_option(name, default=""):
    try:
        return cmds.optionMenu(name, q=True, v=True)
    except Exception:
        return default


# ---------------------------------------------------------
# Selection helpers
# ---------------------------------------------------------

def get_selected_faces():
    sel = cmds.ls(sl=True, long=True) or []
    if not sel:
        cmds.warning("Please select a mesh or some faces.")
        return []

    result_faces = []

    for item in sel:
        if ".f[" in item:
            expanded = cmds.filterExpand(item, sm=34) or []
            result_faces.extend(expanded)
            continue

        node_type = cmds.nodeType(item)

        if node_type == "transform":
            shapes = cmds.listRelatives(item, s=True, ni=True, fullPath=True) or []
            for shape in shapes:
                if cmds.nodeType(shape) == "mesh":
                    faces = cmds.polyListComponentConversion(shape, toFace=True) or []
                    faces = cmds.filterExpand(faces, sm=34) or []
                    result_faces.extend(faces)

        elif node_type == "mesh":
            faces = cmds.polyListComponentConversion(item, toFace=True) or []
            faces = cmds.filterExpand(faces, sm=34) or []
            result_faces.extend(faces)

    result_faces = list(dict.fromkeys(result_faces))
    return result_faces


def get_selected_vertices_from_mesh_or_faces():
    sel = cmds.ls(sl=True, long=True) or []
    if not sel:
        cmds.warning("Please select a mesh, faces, or vertices.")
        return []

    verts = cmds.polyListComponentConversion(sel, toVertex=True) or []
    verts = cmds.filterExpand(verts, sm=31) or []
    return list(dict.fromkeys(verts))


def get_mesh_from_component(component):
    if "." in component:
        return component.split(".")[0]
    return component


# ---------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------

def object_exists(obj):
    return cmds.objExists(obj)


def get_face_area(face):
    try:
        area = cmds.polyEvaluate(face, faceArea=True)
        if isinstance(area, (tuple, list)):
            return area[0]
        return area
    except Exception:
        return 0.0


def get_bbox(component):
    try:
        return cmds.exactWorldBoundingBox(component)
    except Exception:
        return [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]


def get_face_center(face):
    try:
        bb = cmds.exactWorldBoundingBox(face)
        return [
            (bb[0] + bb[3]) * 0.5,
            (bb[1] + bb[4]) * 0.5,
            (bb[2] + bb[5]) * 0.5
        ]
    except Exception:
        return [0.0, 0.0, 0.0]


def get_bbox_size(bb):
    return (
        max(0.000001, bb[3] - bb[0]),
        max(0.000001, bb[4] - bb[1]),
        max(0.000001, bb[5] - bb[2])
    )


def get_face_normal(face):
    """
    Returns a face normal in world space if possible.
    Fallback = [0, 1, 0]
    """
    try:
        info = cmds.polyInfo(face, fn=True) or []
        if info:
            line = info[0]
            parts = line.split()
            vals = [float(x) for x in parts[-3:]]
            length = math.sqrt(vals[0] * vals[0] + vals[1] * vals[1] + vals[2] * vals[2])
            if length > 0.000001:
                return [vals[0] / length, vals[1] / length, vals[2] / length]
    except Exception:
        pass
    return [0.0, 1.0, 0.0]


def dominant_plane_from_bbox(face):
    """
    For planar meshes / planes, chooses the 2 biggest bbox axes.
    Returns something like ("x", "y"), ("x", "z"), ("y", "z")
    """
    bb = get_bbox(face)
    sx, sy, sz = get_bbox_size(bb)

    axes = [("x", sx), ("y", sy), ("z", sz)]
    axes = sorted(axes, key=lambda x: x[1], reverse=True)

    return axes[0][0], axes[1][0]


# ---------------------------------------------------------
# Random rotation helpers
# ---------------------------------------------------------

def random_cut_rotation(mode="Mixed 90", angle_jitter=5.0):
    if mode == "Mostly Vertical":
        base_x, base_y, base_z = 180.0, 0.0, -90.0

    elif mode == "Mostly Horizontal":
        base_x, base_y, base_z = 90.0, 0.0, 0.0

    elif mode == "Mixed 90":
        if random.random() < 0.5:
            base_x, base_y, base_z = 180.0, 0.0, -90.0
        else:
            base_x, base_y, base_z = 90.0, 0.0, 0.0

    elif mode == "Diagonal":
        base_x = 180.0
        base_y = 0.0
        base_z = random.choice([-45.0, 45.0, -135.0, 135.0])

    else:  # Fully Random
        base_x = random.uniform(0.0, 180.0)
        base_y = random.uniform(-30.0, 30.0)
        base_z = random.uniform(-180.0, 180.0)

    rx = base_x + random.uniform(-angle_jitter, angle_jitter)
    ry = base_y + random.uniform(-angle_jitter, angle_jitter)
    rz = base_z + random.uniform(-angle_jitter, angle_jitter)
    return rx, ry, rz


# ---------------------------------------------------------
# Cut logic
# ---------------------------------------------------------

def do_single_poly_cut(face, cut_mode, point_jitter=0.12, angle_jitter=5.0):
    if not object_exists(face):
        return False

    bb = get_bbox(face)
    cx, cy, cz = get_face_center(face)
    sx, sy, sz = get_bbox_size(bb)

    max_size = max(sx, sy, sz)
    jitter_amount = max_size * point_jitter

    px = cx + random.uniform(-jitter_amount, jitter_amount)
    py = cy + random.uniform(-jitter_amount, jitter_amount)
    pz = cz + random.uniform(-jitter_amount, jitter_amount)

    rx, ry, rz = random_cut_rotation(cut_mode, angle_jitter)

    try:
        cmds.polyCut(
            face,
            ch=1,
            pc=[px, py, pz],
            ro=[rx, ry, rz]
        )
        return True
    except Exception as e:
        print("polyCut failed on {} : {}".format(face, e))
        return False


def _polycut_with_axis(face, axis, position, center, plane_axes):
    """
    axis = which axis stays fixed for the cut point offset.
    plane_axes = tuple of dominant axes on the face, like ("x", "z")
    """
    px, py, pz = center[0], center[1], center[2]

    if axis == "x":
        px = position
    elif axis == "y":
        py = position
    elif axis == "z":
        pz = position

    # Rotation presets based on dominant plane
    # These are practical presets for planar hard-surface work.
    ax1, ax2 = plane_axes

    # If plane lives mostly in X/Y
    if set(plane_axes) == set(["x", "y"]):
        if axis == "x":
            rot = [180.0, 0.0, -90.0]
        else:
            rot = [90.0, 0.0, 0.0]

    # If plane lives mostly in X/Z
    elif set(plane_axes) == set(["x", "z"]):
        if axis == "x":
            rot = [180.0, 90.0, -90.0]
        else:
            rot = [0.0, 0.0, 0.0]

    # If plane lives mostly in Y/Z
    else:
        if axis == "y":
            rot = [90.0, 0.0, 90.0]
        else:
            rot = [0.0, 90.0, 0.0]

    try:
        cmds.polyCut(
            face,
            ch=1,
            pc=[px, py, pz],
            ro=rot
        )
        return True
    except Exception as e:
        print("Perpendicular polyCut failed on {} : {}".format(face, e))
        return False


def generate_true_perpendicular_grid(face, cut_count=12, point_jitter=0.04):
    """
    Real 90-degree crossing cuts based on the face bounding box.
    This is the mode matching your sketch:
    - one set of parallel cuts
    - another perpendicular set
    - actual 90-degree structure
    """
    if not object_exists(face):
        return 0

    bb = get_bbox(face)
    center = get_face_center(face)
    sx, sy, sz = get_bbox_size(bb)
    axis_a, axis_b = dominant_plane_from_bbox(face)

    axis_size = {
        "x": sx,
        "y": sy,
        "z": sz
    }

    axis_min = {
        "x": bb[0],
        "y": bb[1],
        "z": bb[2]
    }

    axis_max = {
        "x": bb[3],
        "y": bb[4],
        "z": bb[5]
    }

    size_a = axis_size[axis_a]
    size_b = axis_size[axis_b]

    # Split cuts between the 2 perpendicular directions
    cuts_a = max(1, int(round(cut_count * 0.5)))
    cuts_b = max(1, cut_count - cuts_a)

    created = 0

    # First direction
    for i in range(cuts_a):
        t = float(i + 1) / float(cuts_a + 1)
        pos = axis_min[axis_a] + size_a * t
        pos += random.uniform(-size_a * point_jitter, size_a * point_jitter)

        # clamp inside bbox
        pos = max(axis_min[axis_a] + size_a * 0.02, min(axis_max[axis_a] - size_a * 0.02, pos))

        if _polycut_with_axis(face, axis_a, pos, center, (axis_a, axis_b)):
            created += 1

    # Second direction
    for i in range(cuts_b):
        t = float(i + 1) / float(cuts_b + 1)
        pos = axis_min[axis_b] + size_b * t
        pos += random.uniform(-size_b * point_jitter, size_b * point_jitter)

        pos = max(axis_min[axis_b] + size_b * 0.02, min(axis_max[axis_b] - size_b * 0.02, pos))

        if _polycut_with_axis(face, axis_b, pos, center, (axis_a, axis_b)):
            created += 1

    return created


def generate_random_cuts(*args):
    faces = get_selected_faces()
    if not faces:
        cmds.warning("No valid faces found.")
        return

    cut_count = get_int("spg_cutCount", 12)
    cut_mode = get_option("spg_cutMode", "Mixed 90")
    angle_jitter = get_float("spg_angleJitter", 5.0)
    point_jitter = get_float("spg_pointJitter", 0.12)
    min_face_area = get_float("spg_minFaceArea", 0.0001)
    one_cut_per_face = get_checkbox("spg_oneCutPerFace", True)
    use_seed = get_checkbox("spg_useSeed", False)
    seed_value = get_int("spg_seedValue", 1234)

    if use_seed:
        random.seed(seed_value)

    valid_faces = [f for f in faces if get_face_area(f) >= min_face_area]
    if not valid_faces:
        cmds.warning("No faces large enough to cut.")
        return

    created = 0

    cmds.undoInfo(openChunk=True)
    try:
        if cut_mode == "Perpendicular 90":
            # In this mode we WANT multiple cuts on the same face,
            # because that is what creates the true grid layout.
            target_faces = valid_faces[:]

            # If many faces are selected, distribute cut count per face a bit
            per_face_cut_count = max(2, cut_count)

            for face in target_faces:
                created += generate_true_perpendicular_grid(
                    face=face,
                    cut_count=per_face_cut_count,
                    point_jitter=point_jitter
                )

        else:
            pool = valid_faces[:]

            for _ in range(cut_count):
                if not pool:
                    break

                face = random.choice(pool)
                ok = do_single_poly_cut(
                    face=face,
                    cut_mode=cut_mode,
                    point_jitter=point_jitter,
                    angle_jitter=angle_jitter
                )

                if ok:
                    created += 1

                if one_cut_per_face and face in pool:
                    pool.remove(face)

        print("Cuts created: {}".format(created))

    finally:
        cmds.undoInfo(closeChunk=True)


# ---------------------------------------------------------
# Panel pass
# ---------------------------------------------------------

def create_panels(*args):
    faces = get_selected_faces()
    if not faces:
        cmds.warning("No valid faces found.")
        return

    panel_ratio = get_float("spg_panelRatio", 0.35)
    inset_min = get_float("spg_insetMin", 0.008)
    inset_max = get_float("spg_insetMax", 0.030)
    depth_min = get_float("spg_depthMin", -0.020)
    depth_max = get_float("spg_depthMax", -0.006)
    double_panel_chance = get_float("spg_doublePanelChance", 0.35)
    min_face_area = get_float("spg_minFaceArea", 0.0001)
    use_seed = get_checkbox("spg_useSeed", False)
    seed_value = get_int("spg_seedValue", 1234)

    if use_seed:
        random.seed(seed_value)

    panel_ratio = max(0.0, min(1.0, panel_ratio))
    inset_min, inset_max = min(inset_min, inset_max), max(inset_min, inset_max)
    depth_min, depth_max = min(depth_min, depth_max), max(depth_min, depth_max)

    valid_faces = [f for f in faces if get_face_area(f) >= min_face_area]
    if not valid_faces:
        cmds.warning("No faces large enough for panel pass.")
        return

    count = 0

    cmds.undoInfo(openChunk=True)
    try:
        for f in valid_faces:
            if random.random() > panel_ratio:
                continue

            try:
                inset = random.uniform(inset_min, inset_max)
                depth = random.uniform(depth_min, depth_max)

                cmds.select(f, r=True)
                cmds.polyExtrudeFacet(
                    keepFacesTogether=False,
                    offset=inset,
                    localTranslateZ=depth
                )

                if random.random() < double_panel_chance:
                    second_inset = inset * random.uniform(0.35, 0.65)
                    second_depth = depth * random.uniform(0.25, 0.50)
                    cmds.polyExtrudeFacet(
                        keepFacesTogether=False,
                        offset=second_inset,
                        localTranslateZ=second_depth
                    )

                count += 1

            except Exception as e:
                print("Panel pass failed on {} : {}".format(f, e))

        cmds.select(clear=True)
        print("Panels created: {}".format(count))

    finally:
        cmds.undoInfo(closeChunk=True)


# ---------------------------------------------------------
# Surface noise / damage
# ---------------------------------------------------------

def add_surface_noise(*args):
    """
    Visible ranges:
    0.05 = very subtle
    0.20 = noticeable
    0.50 = medium
    1.00+ = strong
    """
    verts = get_selected_vertices_from_mesh_or_faces()
    if not verts:
        cmds.warning("Please select a mesh, faces, or vertices.")
        return

    noise_ratio = get_float("spg_noiseRatio", 0.12)
    noise_min = get_float("spg_noiseMin", 0.10)
    noise_max = get_float("spg_noiseMax", 0.50)
    use_seed = get_checkbox("spg_useSeed", False)
    seed_value = get_int("spg_seedValue", 1234)

    if use_seed:
        random.seed(seed_value)

    noise_ratio = max(0.0, min(1.0, noise_ratio))
    noise_min, noise_max = min(noise_min, noise_max), max(noise_min, noise_max)

    moved = 0

    cmds.undoInfo(openChunk=True)
    try:
        for v in verts:
            if random.random() > noise_ratio:
                continue

            try:
                # Move in world space.
                # Slight random 3D metal damage feel.
                pos = cmds.xform(v, q=True, ws=True, t=True)
                amount = random.uniform(noise_min, noise_max)

                offset = [
                    random.uniform(-amount, amount),
                    random.uniform(-amount, amount),
                    random.uniform(-amount, amount)
                ]

                cmds.xform(
                    v,
                    ws=True,
                    t=[pos[0] + offset[0], pos[1] + offset[1], pos[2] + offset[2]]
                )

                moved += 1

            except Exception as e:
                print("Noise failed on {} : {}".format(v, e))

        print("Surface noise moved {} vertices.".format(moved))

    finally:
        cmds.undoInfo(closeChunk=True)


# ---------------------------------------------------------
# Utility operations
# ---------------------------------------------------------

def subdivide_selection(*args):
    sel = cmds.ls(sl=True, long=True) or []
    if not sel:
        cmds.warning("Please select a mesh or faces.")
        return

    divisions = get_int("spg_subdivisions", 1)
    divisions = max(1, divisions)

    cmds.undoInfo(openChunk=True)
    try:
        cmds.polySubdivideFacet(sel, divisions=divisions, mode=0)
    except Exception as e:
        cmds.warning("Subdivision failed: {}".format(e))
    finally:
        cmds.undoInfo(closeChunk=True)


def triangulate_selection(*args):
    sel = cmds.ls(sl=True, long=True) or []
    if not sel:
        cmds.warning("Please select a mesh or faces.")
        return

    cmds.undoInfo(openChunk=True)
    try:
        cmds.polyTriangulate(sel)
    except Exception as e:
        cmds.warning("Triangulate failed: {}".format(e))
    finally:
        cmds.undoInfo(closeChunk=True)


def quadrangulate_selection(*args):
    sel = cmds.ls(sl=True, long=True) or []
    if not sel:
        cmds.warning("Please select a mesh or faces.")
        return

    cmds.undoInfo(openChunk=True)
    try:
        cmds.polyQuad(sel)
    except Exception as e:
        cmds.warning("Quadrangulate failed: {}".format(e))
    finally:
        cmds.undoInfo(closeChunk=True)


def delete_history_on_selection(*args):
    sel = cmds.ls(sl=True, long=True) or []
    if not sel:
        cmds.warning("Please select a mesh.")
        return

    meshes = set()
    for item in sel:
        meshes.add(get_mesh_from_component(item))

    cmds.undoInfo(openChunk=True)
    try:
        for mesh in meshes:
            if cmds.objExists(mesh):
                cmds.delete(mesh, ch=True)
        print("History deleted on selected mesh(es).")
    finally:
        cmds.undoInfo(closeChunk=True)


# ---------------------------------------------------------
# Presets
# ---------------------------------------------------------

def apply_preset(*args):
    preset_name = get_option("spg_presetMenu", "Dense Panels")
    data = PRESETS.get(preset_name)
    if not data:
        cmds.warning("Preset not found.")
        return

    set_slider_field_value("spg_cutCount", data["cut_count"])
    cmds.optionMenu("spg_cutMode", e=True, v=data["cut_mode"])
    set_slider_field_value("spg_angleJitter", data["angle_jitter"])
    set_slider_field_value("spg_pointJitter", data["point_jitter"])
    set_slider_field_value("spg_minFaceArea", data["min_face_area"])
    cmds.checkBox("spg_oneCutPerFace", e=True, v=data["one_cut_per_face"])

    set_slider_field_value("spg_panelRatio", data["panel_ratio"])
    set_slider_field_value("spg_insetMin", data["inset_min"])
    set_slider_field_value("spg_insetMax", data["inset_max"])
    set_slider_field_value("spg_depthMin", data["depth_min"])
    set_slider_field_value("spg_depthMax", data["depth_max"])
    set_slider_field_value("spg_doublePanelChance", data["double_panel_chance"])

    set_slider_field_value("spg_noiseRatio", data["noise_ratio"])
    set_slider_field_value("spg_noiseMin", data["noise_min"])
    set_slider_field_value("spg_noiseMax", data["noise_max"])

    print("Applied preset: {}".format(preset_name))


# ---------------------------------------------------------
# UI
# ---------------------------------------------------------

def build_ui():
    if cmds.window(WINDOW_NAME, exists=True):
        cmds.deleteUI(WINDOW_NAME)

    cmds.window(
        WINDOW_NAME,
        title="Sci-Fi Panel Generator",
        sizeable=True,
        widthHeight=(430, 760)
    )

    main_layout = cmds.scrollLayout(childResizable=True)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=8)

    cmds.text(
        label="Select a mesh or some faces, then generate cuts / panels / damage.",
        align="left"
    )
    cmds.separator(h=8, style="in")

    # Presets
    cmds.frameLayout(label="Presets", collapsable=True, collapse=False, marginWidth=10, marginHeight=10)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=6)

    cmds.optionMenu("spg_presetMenu", label="Preset")
    for preset_name in PRESETS.keys():
        cmds.menuItem(label=preset_name)

    cmds.button(label="Apply Preset", h=30, c=apply_preset)

    cmds.setParent("..")
    cmds.setParent("..")

    # Preparation
    cmds.frameLayout(label="Preparation", collapsable=True, collapse=False, marginWidth=10, marginHeight=10)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=6)

    cmds.intSliderGrp(
        "spg_subdivisions",
        label="Subdivisions",
        field=True,
        minValue=1,
        maxValue=10,
        fieldMinValue=1,
        fieldMaxValue=100,
        value=1
    )
    cmds.button(label="Subdivide Selection", h=30, c=subdivide_selection)
    cmds.button(label="Triangulate Selection", h=26, c=triangulate_selection)
    cmds.button(label="Quadrangulate Selection", h=26, c=quadrangulate_selection)
    cmds.button(label="Delete History On Selection", h=26, c=delete_history_on_selection)

    cmds.setParent("..")
    cmds.setParent("..")

    # Random cuts
    cmds.frameLayout(label="Random Cuts", collapsable=True, collapse=False, marginWidth=10, marginHeight=10)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=6)

    cmds.intSliderGrp(
        "spg_cutCount",
        label="Cut Count",
        field=True,
        minValue=1,
        maxValue=100,
        fieldMinValue=1,
        fieldMaxValue=1000,
        value=12
    )

    cmds.optionMenu("spg_cutMode", label="Cut Mode")
    for item in ["Mixed 90", "Mostly Vertical", "Mostly Horizontal", "Diagonal", "Fully Random", "Perpendicular 90"]:
        cmds.menuItem(label=item)

    cmds.floatSliderGrp(
        "spg_angleJitter",
        label="Angle Jitter",
        field=True,
        minValue=0.0,
        maxValue=45.0,
        fieldMinValue=0.0,
        fieldMaxValue=180.0,
        value=5.0,
        precision=3
    )

    cmds.floatSliderGrp(
        "spg_pointJitter",
        label="Point Offset",
        field=True,
        minValue=0.0,
        maxValue=1.0,
        fieldMinValue=0.0,
        fieldMaxValue=10.0,
        value=0.12,
        precision=3
    )

    cmds.floatSliderGrp(
        "spg_minFaceArea",
        label="Min Face Area",
        field=True,
        minValue=0.0,
        maxValue=1.0,
        fieldMinValue=0.0,
        fieldMaxValue=1000.0,
        value=0.0001,
        precision=5
    )

    cmds.checkBox("spg_oneCutPerFace", label="One Cut Per Face", v=True)

    cmds.button(label="Generate Random Cuts", h=34, c=generate_random_cuts)

    cmds.setParent("..")
    cmds.setParent("..")

    # Panel pass
    cmds.frameLayout(label="Panel Pass", collapsable=True, collapse=False, marginWidth=10, marginHeight=10)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=6)

    cmds.floatSliderGrp(
        "spg_panelRatio",
        label="Panel Ratio",
        field=True,
        minValue=0.0,
        maxValue=1.0,
        fieldMinValue=0.0,
        fieldMaxValue=1.0,
        value=0.35,
        precision=3
    )

    cmds.floatSliderGrp(
        "spg_insetMin",
        label="Inset Min",
        field=True,
        minValue=0.0,
        maxValue=0.2,
        fieldMinValue=0.0,
        fieldMaxValue=100.0,
        value=0.008,
        precision=4
    )

    cmds.floatSliderGrp(
        "spg_insetMax",
        label="Inset Max",
        field=True,
        minValue=0.0,
        maxValue=0.3,
        fieldMinValue=0.0,
        fieldMaxValue=100.0,
        value=0.030,
        precision=4
    )

    cmds.floatSliderGrp(
        "spg_depthMin",
        label="Depth Min",
        field=True,
        minValue=-2.0,
        maxValue=0.0,
        fieldMinValue=-100.0,
        fieldMaxValue=100.0,
        value=-0.020,
        precision=4
    )

    cmds.floatSliderGrp(
        "spg_depthMax",
        label="Depth Max",
        field=True,
        minValue=-2.0,
        maxValue=0.0,
        fieldMinValue=-100.0,
        fieldMaxValue=100.0,
        value=-0.006,
        precision=4
    )

    cmds.floatSliderGrp(
        "spg_doublePanelChance",
        label="Double Panel Chance",
        field=True,
        minValue=0.0,
        maxValue=1.0,
        fieldMinValue=0.0,
        fieldMaxValue=1.0,
        value=0.35,
        precision=3
    )

    cmds.button(label="Create Panels", h=34, c=create_panels)

    cmds.setParent("..")
    cmds.setParent("..")

    # Surface variation
    cmds.frameLayout(label="Surface Variation / Damage", collapsable=True, collapse=False, marginWidth=10, marginHeight=10)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=6)

    cmds.floatSliderGrp(
        "spg_noiseRatio",
        label="Noise Ratio",
        field=True,
        minValue=0.0,
        maxValue=1.0,
        fieldMinValue=0.0,
        fieldMaxValue=1.0,
        value=0.12,
        precision=3
    )

    cmds.floatSliderGrp(
        "spg_noiseMin",
        label="Noise Min",
        field=True,
        minValue=0.0,
        maxValue=2.0,
        fieldMinValue=0.0,
        fieldMaxValue=100.0,
        value=0.10,
        precision=4
    )

    cmds.floatSliderGrp(
        "spg_noiseMax",
        label="Noise Max",
        field=True,
        minValue=0.0,
        maxValue=5.0,
        fieldMinValue=0.0,
        fieldMaxValue=100.0,
        value=0.50,
        precision=4
    )

    cmds.button(label="Add Surface Noise", h=34, c=add_surface_noise)

    cmds.setParent("..")
    cmds.setParent("..")

    # Seed
    cmds.frameLayout(label="Random Seed", collapsable=True, collapse=False, marginWidth=10, marginHeight=10)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=6)

    cmds.checkBox("spg_useSeed", label="Use Fixed Seed", v=False)
    cmds.intSliderGrp(
        "spg_seedValue",
        label="Seed",
        field=True,
        minValue=0,
        maxValue=9999,
        fieldMinValue=0,
        fieldMaxValue=999999,
        value=1234
    )

    cmds.setParent("..")
    cmds.setParent("..")

    cmds.separator(h=10, style="in")
    cmds.text(
        label="Tip: 'Perpendicular 90' is the true grid-style mode.",
        align="left"
    )

    cmds.showWindow(WINDOW_NAME)


build_ui()