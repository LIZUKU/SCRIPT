# -*- coding: utf-8 -*-
"""
=============================================================================
PRO TOOLS COMBINED v1.1
-----------------------------------------------------------------------------
Includes:
- ProArray (Radial / Rectangular)
- Curve Distribute Pro

Compatible: Maya 2022 - 2025
=============================================================================
"""

import math
import random
import maya.cmds as cmds
import maya.api.OpenMaya as om

# Qt imports - Maya 2022-2024 use PySide2, Maya 2025+ uses PySide6
try:
    from PySide2 import QtWidgets, QtCore, QtGui
    PYSIDE_VERSION = 2
except ImportError:
    from PySide6 import QtWidgets, QtCore, QtGui
    PYSIDE_VERSION = 6

try:
    from shiboken2 import wrapInstance
except ImportError:
    from shiboken6 import wrapInstance

import maya.OpenMayaUI as omui


# ============================================================
# GLOBAL CONSTANTS
# ============================================================
WINDOW_TITLE = "Pro Tools Combined"

ACCENT_RED_BG = "#5a2a2a"
ACCENT_RED_BORDER = "#e84d4d"
ACCENT_RED_TEXT = "#e84d4d"

PROARRAY_LOCATOR_NAME = "ProArray_Pivot_LOC"
PROARRAY_PREVIEW_PREFIX = "ProArray_Preview_"

CURVE_PREVIEW_PREFIX = "CurveDistPreview_"
CURVE_RESULT_GROUP_NAME = "CurveDistribute_Result_GRP"


# ============================================================
# GLOBAL STATES
# ============================================================
_PROARRAY_STATE = {
    "original_objects": [],
    "original_positions": {},
    "original_bbox_centers": {},
    "preview_objects": [],
    "locator": None,
    "locator_script_jobs": [],
    "mesh_script_jobs": [],
    "selection_callback_id": None,
    "is_processing": False,
    "array_mode": "radial",
    "ui_instance": None,
}

_CURVE_STATE = {
    "mesh": None,
    "curve": None,
    "preview_objects": [],
    "started": False,
    "baked": False,
    "is_processing": False,
    "ui_instance": None,
}


# ============================================================
# MAYA MAIN WINDOW
# ============================================================
def get_maya_main_window():
    try:
        main_window_ptr = omui.MQtUtil.mainWindow()
        return wrapInstance(int(main_window_ptr), QtWidgets.QWidget)
    except:
        return None


# ============================================================
# GENERIC SAFE UTILS
# ============================================================
def _safe_exists(obj):
    if not obj:
        return False
    try:
        return cmds.objExists(obj)
    except:
        return False


def _safe_delete(obj):
    if not obj:
        return
    try:
        if cmds.objExists(obj):
            cmds.delete(obj)
    except:
        pass


def clamp(v, vmin, vmax):
    return max(vmin, min(vmax, v))


# ============================================================
# GENERIC VECTOR UTILS
# ============================================================
def vec_dot(a, b):
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]


def vec_len(v):
    return math.sqrt(v[0]*v[0] + v[1]*v[1] + v[2]*v[2])


def vec_norm(v):
    l = vec_len(v)
    if l < 1e-8:
        return [0.0, 0.0, 0.0]
    return [v[0]/l, v[1]/l, v[2]/l]


def vec_sub(a, b):
    return [a[0]-b[0], a[1]-b[1], a[2]-b[2]]


def vec_add(a, b):
    return [a[0]+b[0], a[1]+b[1], a[2]+b[2]]


def vec_mul(v, s):
    return [v[0]*s, v[1]*s, v[2]*s]


def vec_cross(a, b):
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0]
    ]


def project_on_plane(v, axis_unit):
    d = vec_dot(v, axis_unit)
    return vec_sub(v, vec_mul(axis_unit, d))


# ============================================================
# GENERIC UI HELPERS
# ============================================================
def apply_shared_style(widget):
    widget.setStyleSheet(f"""
        QWidget {{
            background-color: #2d2d2d;
            color: #b0b0b0;
            font-size: 11px;
        }}

        QDialog {{
            background-color: #2d2d2d;
            border: 1px solid #3d3d3d;
            border-radius: 4px;
        }}

        QTabWidget::pane {{
            border: 1px solid #3a3a3a;
            background: #2d2d2d;
            top: -1px;
        }}

        QTabBar::tab {{
            background: #3a3a3a;
            color: #b0b0b0;
            border: 1px solid #4a4a4a;
            padding: 6px 12px;
            min-width: 110px;
        }}

        QTabBar::tab:selected {{
            background: #454545;
            border-bottom: 1px solid #454545;
        }}

        QLabel {{
            color: #b0b0b0;
            font-size: 11px;
        }}

        QLabel#statusLabel {{
            color: {ACCENT_RED_TEXT};
            font-size: 10px;
            font-weight: bold;
            padding: 2px;
        }}

        QLabel#sectionLabel {{
            color: #707070;
            font-size: 9px;
            font-weight: bold;
            padding-top: 4px;
            border-top: 1px solid #3a3a3a;
            margin-top: 4px;
        }}

        QPushButton {{
            background-color: #3a3a3a;
            color: #b0b0b0;
            border: 1px solid #4a4a4a;
            border-radius: 3px;
            font-size: 11px;
            padding: 4px 8px;
        }}

        QPushButton:hover {{
            background-color: #454545;
        }}

        QPushButton:pressed {{
            background-color: #2a2a2a;
        }}

        QPushButton#bakeBtn {{
            background-color: #4a6a8a;
            color: #ffffff;
        }}

        QPushButton#bakeBtn:hover {{
            background-color: #5a7a9a;
        }}

        QPushButton#selectMeshBtn {{
            background-color: #3a4a5a;
            border: 1px solid #4a5a6a;
            border-radius: 4px;
            font-size: 16px;
            font-weight: bold;
        }}

        QPushButton#selectMeshBtn:hover {{
            background-color: #4a5a6a;
        }}

        QPushButton#selectLocatorBtn, QPushButton#selectCurveBtn {{
            background-color: #5a4a3a;
            border: 1px solid #6a5a4a;
            border-radius: 4px;
            font-size: 16px;
            font-weight: bold;
        }}

        QPushButton#selectLocatorBtn:hover, QPushButton#selectCurveBtn:hover {{
            background-color: #6a5a4a;
        }}

        QPushButton#refreshBtn {{
            background-color: #3a5a3a;
            border: 1px solid #4a6a4a;
            border-radius: 4px;
            font-size: 14px;
        }}

        QPushButton#refreshBtn:hover {{
            background-color: #4a6a4a;
        }}

        QSlider::groove:horizontal {{
            height: 3px;
            background: #1a1a1a;
            border-radius: 1px;
        }}

        QSlider::handle:horizontal {{
            background: #888888;
            width: 12px;
            margin: -5px 0;
            border-radius: 6px;
        }}

        QSlider::handle:horizontal:hover {{
            background: #aaaaaa;
        }}

        QSpinBox, QDoubleSpinBox {{
            background-color: #252525;
            color: #b0b0b0;
            border: 1px solid #3a3a3a;
            border-radius: 2px;
            padding: 2px;
        }}

        QCheckBox, QRadioButton {{
            color: #b0b0b0;
            font-size: 11px;
            spacing: 6px;
        }}

        QCheckBox::indicator, QRadioButton::indicator {{
            width: 14px;
            height: 14px;
            border: 1px solid #4a4a4a;
            background-color: #252525;
        }}

        QCheckBox::indicator {{
            border-radius: 3px;
        }}

        QRadioButton::indicator {{
            border-radius: 7px;
        }}

        QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
            background-color: {ACCENT_RED_BG};
            border-color: {ACCENT_RED_BORDER};
        }}

        QFrame {{
            border: none;
        }}
    """)


class SliderMixin(object):
    def _add_slider(self, parent_layout, label, min_val, max_val, default, decimals, label_width=105):
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        row.setContentsMargins(0, 0, 0, 0)

        lbl = QtWidgets.QLabel(label)
        lbl.setFixedWidth(label_width)
        row.addWidget(lbl)

        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setMinimum(0)
        slider.setMaximum(1000)
        row.addWidget(slider)

        if decimals > 0:
            spinbox = QtWidgets.QDoubleSpinBox()
            spinbox.setDecimals(decimals)
            spinbox.setMinimum(min_val)
            spinbox.setMaximum(max_val if max_val < 999999 else 999999)
            step = (max_val - min_val) / 1000.0 if max_val > min_val else 0.01
            spinbox.setSingleStep(step)
        else:
            spinbox = QtWidgets.QSpinBox()
            spinbox.setMinimum(int(min_val))
            spinbox.setMaximum(int(max_val if max_val < 999999 else 999999))

        spinbox.setFixedWidth(82 if label_width >= 110 else 80)
        spinbox.setFixedHeight(20)
        spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        spinbox.setValue(default if decimals > 0 else int(default))
        row.addWidget(spinbox)

        def update_spinbox(val):
            ratio = val / 1000.0
            real_val = min_val + ratio * (max_val - min_val)
            spinbox.blockSignals(True)
            if decimals > 0:
                spinbox.setValue(real_val)
            else:
                spinbox.setValue(int(round(real_val)))
            spinbox.blockSignals(False)
            self._on_rebuild()

        def update_slider(val):
            clamped = max(min_val, min(max_val, float(val)))
            ratio = (clamped - min_val) / (max_val - min_val) if (max_val - min_val) > 0 else 0
            slider.blockSignals(True)
            slider.setValue(int(ratio * 1000))
            slider.blockSignals(False)
            self._on_rebuild()

        slider.valueChanged.connect(update_spinbox)
        spinbox.valueChanged.connect(update_slider)

        ratio = (float(default) - min_val) / (max_val - min_val) if (max_val - min_val) > 0 else 0
        slider.setValue(int(ratio * 1000))

        parent_layout.addLayout(row)
        return slider, spinbox


# ============================================================
# PROARRAY CORE
# ============================================================
def get_bbox_center(obj):
    if not _safe_exists(obj):
        return [0, 0, 0]
    try:
        bbox = cmds.exactWorldBoundingBox(obj)
        return [
            (bbox[0] + bbox[3]) / 2.0,
            (bbox[1] + bbox[4]) / 2.0,
            (bbox[2] + bbox[5]) / 2.0
        ]
    except:
        return cmds.xform(obj, query=True, worldSpace=True, translation=True)


def proarray_cleanup_preview():
    for obj in _PROARRAY_STATE.get("preview_objects", []):
        _safe_delete(obj)
    _PROARRAY_STATE["preview_objects"] = []

    try:
        for obj in cmds.ls(type="transform") or []:
            if obj.startswith(PROARRAY_PREVIEW_PREFIX):
                _safe_delete(obj)
    except:
        pass


def proarray_cleanup_locator():
    for job_id in _PROARRAY_STATE.get("locator_script_jobs", []):
        try:
            if cmds.scriptJob(exists=job_id):
                cmds.scriptJob(kill=job_id, force=True)
        except:
            pass
    _PROARRAY_STATE["locator_script_jobs"] = []

    for job_id in _PROARRAY_STATE.get("mesh_script_jobs", []):
        try:
            if cmds.scriptJob(exists=job_id):
                cmds.scriptJob(kill=job_id, force=True)
        except:
            pass
    _PROARRAY_STATE["mesh_script_jobs"] = []

    cb_id = _PROARRAY_STATE.get("selection_callback_id")
    if cb_id is not None:
        try:
            om.MMessage.removeCallback(cb_id)
        except:
            pass
        _PROARRAY_STATE["selection_callback_id"] = None

    _safe_delete(_PROARRAY_STATE.get("locator"))
    _PROARRAY_STATE["locator"] = None
    _safe_delete(PROARRAY_LOCATOR_NAME)


def proarray_full_cleanup():
    proarray_cleanup_preview()
    proarray_cleanup_locator()

    originals = _PROARRAY_STATE.get("original_objects", [])
    valid_originals = [o for o in originals if _safe_exists(o)]

    if valid_originals:
        cmds.select(valid_originals, replace=True)
    else:
        cmds.select(clear=True)

    _PROARRAY_STATE["original_objects"] = []
    _PROARRAY_STATE["original_positions"] = {}
    _PROARRAY_STATE["original_bbox_centers"] = {}
    _PROARRAY_STATE["is_processing"] = False


def proarray_create_pivot_locator(position, mode="radial"):
    proarray_cleanup_locator()

    loc = cmds.spaceLocator(name=PROARRAY_LOCATOR_NAME)[0]
    cmds.xform(loc, worldSpace=True, translation=position)

    shape = cmds.listRelatives(loc, shapes=True)[0]
    cmds.setAttr(shape + ".localScaleX", 0.5)
    cmds.setAttr(shape + ".localScaleY", 0.5)
    cmds.setAttr(shape + ".localScaleZ", 0.5)
    cmds.setAttr(shape + ".overrideEnabled", 1)
    cmds.setAttr(shape + ".overrideColor", 17 if mode == "radial" else 13)

    _PROARRAY_STATE["locator"] = loc
    return loc


def proarray_get_locator_position():
    loc = _PROARRAY_STATE.get("locator")
    if _safe_exists(loc):
        return cmds.xform(loc, query=True, worldSpace=True, translation=True)
    return [0, 0, 0]


def proarray_setup_locator_callbacks(ui_instance):
    loc = _PROARRAY_STATE.get("locator")
    if not loc:
        return

    for job_id in _PROARRAY_STATE.get("locator_script_jobs", []):
        try:
            if cmds.scriptJob(exists=job_id):
                cmds.scriptJob(kill=job_id, force=True)
        except:
            pass
    _PROARRAY_STATE["locator_script_jobs"] = []

    for attr in [".translateX", ".translateY", ".translateZ"]:
        job_id = cmds.scriptJob(
            attributeChange=[loc + attr, ui_instance._on_locator_moved],
            killWithScene=True
        )
        _PROARRAY_STATE["locator_script_jobs"].append(job_id)


def proarray_setup_mesh_callbacks(ui_instance):
    for job_id in _PROARRAY_STATE.get("mesh_script_jobs", []):
        try:
            if cmds.scriptJob(exists=job_id):
                cmds.scriptJob(kill=job_id, force=True)
        except:
            pass
    _PROARRAY_STATE["mesh_script_jobs"] = []

    cb_id = _PROARRAY_STATE.get("selection_callback_id")
    if cb_id is not None:
        try:
            om.MMessage.removeCallback(cb_id)
        except:
            pass
        _PROARRAY_STATE["selection_callback_id"] = None

    for obj in _PROARRAY_STATE.get("original_objects", []):
        if not _safe_exists(obj):
            continue

        shapes = cmds.listRelatives(obj, shapes=True, type="mesh") or []
        for shape in shapes:
            if not _safe_exists(shape):
                continue

            try:
                job_id = cmds.scriptJob(
                    attributeChange=[shape + ".outMesh", ui_instance._on_mesh_modified],
                    killWithScene=True
                )
                _PROARRAY_STATE["mesh_script_jobs"].append(job_id)

                job_id2 = cmds.scriptJob(
                    attributeChange=[shape + ".worldMesh", ui_instance._on_mesh_modified],
                    killWithScene=True
                )
                _PROARRAY_STATE["mesh_script_jobs"].append(job_id2)
            except:
                pass

    try:
        job_id = cmds.scriptJob(
            event=["SelectionChanged", ui_instance._on_selection_changed],
            killWithScene=True
        )
        _PROARRAY_STATE["mesh_script_jobs"].append(job_id)
    except:
        pass

    try:
        job_id = cmds.scriptJob(
            event=["DagObjectCreated", ui_instance._on_mesh_modified],
            killWithScene=True
        )
        _PROARRAY_STATE["mesh_script_jobs"].append(job_id)
    except:
        pass


def proarray_select_locator():
    loc = _PROARRAY_STATE.get("locator")
    if _safe_exists(loc):
        cmds.select(loc, replace=True)


def proarray_select_original_mesh():
    originals = _PROARRAY_STATE.get("original_objects", [])
    valid = [o for o in originals if _safe_exists(o)]
    if valid:
        cmds.select(valid, replace=True)


def build_radial_array(objects, angle=360, number=3, repeat=1,
                       axis='y', repeat_distance=1.0, repeat_direction="radial",
                       use_instance=True):
    if not objects:
        return []

    proarray_cleanup_preview()
    center = proarray_get_locator_position()

    number = max(1, int(number))
    repeat = max(1, int(repeat))
    repeat_distance = float(repeat_distance)

    if angle >= 360:
        angle_step = float(angle) / float(number)
    else:
        angle_step = float(angle) / float(max(1, number - 1)) if number > 1 else float(angle)

    axis_vec = {'x': [1, 0, 0], 'y': [0, 1, 0], 'z': [0, 0, 1]}.get(axis, [0, 1, 0])
    axis_unit = vec_norm(axis_vec)

    previews = []

    for obj in objects:
        if not _safe_exists(obj):
            continue

        seed_pos = _PROARRAY_STATE["original_positions"].get(obj, {}).get("position")
        if not seed_pos:
            seed_pos = cmds.xform(obj, q=True, ws=True, t=True)

        seed_vec = vec_sub(seed_pos, center)
        seed_vec_plane = project_on_plane(seed_vec, axis_unit)
        seed_dir = vec_norm(seed_vec_plane)

        if vec_len(seed_dir) < 1e-8:
            seed_dir = vec_norm(seed_vec)
        if vec_len(seed_dir) < 1e-8:
            seed_dir = [1.0, 0.0, 0.0]

        for r in range(repeat):
            if r == 0 or repeat_distance == 0.0:
                ring_off = [0.0, 0.0, 0.0]
            else:
                if repeat_direction == "axis":
                    ring_off = vec_mul(axis_unit, repeat_distance * r)
                else:
                    ring_off = vec_mul(seed_dir, repeat_distance * r)

            start_i = 1 if r == 0 else 0

            for i in range(start_i, number):
                current_angle = angle_step * i

                if use_instance:
                    new_obj = cmds.instance(obj, name="%s%s_r%d_%d" % (PROARRAY_PREVIEW_PREFIX, obj.split("|")[-1], r, i))[0]
                else:
                    new_obj = cmds.duplicate(obj, name="%s%s_r%d_%d" % (PROARRAY_PREVIEW_PREFIX, obj.split("|")[-1], r, i))[0]

                if axis == 'x':
                    cmds.rotate(current_angle, 0, 0, new_obj, pivot=center, relative=True, worldSpace=True)
                elif axis == 'y':
                    cmds.rotate(0, current_angle, 0, new_obj, pivot=center, relative=True, worldSpace=True)
                else:
                    cmds.rotate(0, 0, current_angle, new_obj, pivot=center, relative=True, worldSpace=True)

                if ring_off != [0.0, 0.0, 0.0]:
                    cmds.move(ring_off[0], ring_off[1], ring_off[2], new_obj, relative=True, worldSpace=True)

                previews.append(new_obj)

    _PROARRAY_STATE["preview_objects"] = previews
    return previews


def build_rectangular_array(objects, copies_x=3, copies_y=1, copies_z=1,
                            repeat=1, repeat_distance=1.0, repeat_direction="x",
                            use_instance=True):
    if not objects:
        return []

    proarray_cleanup_preview()
    loc_pos = proarray_get_locator_position()
    previews = []

    total_x = max(1, int(copies_x))
    total_y = max(1, int(copies_y))
    total_z = max(1, int(copies_z))
    repeat = max(1, int(repeat))
    repeat_distance = float(repeat_distance)

    for obj in objects:
        if not _safe_exists(obj):
            continue

        start_center = _PROARRAY_STATE["original_bbox_centers"].get(obj) or get_bbox_center(obj)
        obj_pos = _PROARRAY_STATE["original_positions"].get(obj, {}).get("position")
        if not obj_pos:
            obj_pos = cmds.xform(obj, q=True, ws=True, t=True)

        total_vec = vec_sub(loc_pos, start_center)
        total_len = vec_len(total_vec)
        total_dir = vec_norm(total_vec) if total_len > 1e-8 else [1.0, 0.0, 0.0]

        if repeat_direction == "x":
            rep_dir = [1.0, 0.0, 0.0]
        elif repeat_direction == "y":
            rep_dir = [0.0, 1.0, 0.0]
        elif repeat_direction == "z":
            rep_dir = [0.0, 0.0, 1.0]
        else:
            rep_dir = total_dir

        rep_dir = vec_norm(rep_dir)
        if vec_len(rep_dir) < 1e-8:
            rep_dir = [1.0, 0.0, 0.0]

        for r in range(repeat):
            block_off = vec_mul(rep_dir, repeat_distance * r)

            for ix in range(total_x):
                for iy in range(total_y):
                    for iz in range(total_z):
                        if r == 0 and ix == 0 and iy == 0 and iz == 0:
                            continue

                        fx = ix / max(1, total_x - 1) if total_x > 1 else 0.0
                        fy = iy / max(1, total_y - 1) if total_y > 1 else 0.0
                        fz = iz / max(1, total_z - 1) if total_z > 1 else 0.0

                        if total_x > 1 and total_y == 1 and total_z == 1:
                            t = fx
                            base_pos = vec_add(obj_pos, vec_mul(total_vec, t))
                        elif total_y > 1 and total_x == 1 and total_z == 1:
                            t = fy
                            base_pos = vec_add(obj_pos, vec_mul(total_vec, t))
                        elif total_z > 1 and total_x == 1 and total_y == 1:
                            t = fz
                            base_pos = vec_add(obj_pos, vec_mul(total_vec, t))
                        else:
                            active_axes = sum([total_x > 1, total_y > 1, total_z > 1]) or 1
                            s = (fx + fy + fz)
                            base_pos = [
                                obj_pos[0] + (total_vec[0] / active_axes) * s,
                                obj_pos[1] + (total_vec[1] / active_axes) * s,
                                obj_pos[2] + (total_vec[2] / active_axes) * s
                            ]

                        new_pos = vec_add(base_pos, block_off)

                        if use_instance:
                            new_obj = cmds.instance(
                                obj, name="%s%s_r%d_%d_%d_%d" % (PROARRAY_PREVIEW_PREFIX, obj.split("|")[-1], r, ix, iy, iz)
                            )[0]
                        else:
                            new_obj = cmds.duplicate(
                                obj, name="%s%s_r%d_%d_%d_%d" % (PROARRAY_PREVIEW_PREFIX, obj.split("|")[-1], r, ix, iy, iz)
                            )[0]

                        cmds.xform(new_obj, ws=True, t=new_pos)
                        previews.append(new_obj)

    _PROARRAY_STATE["preview_objects"] = previews
    return previews


def proarray_bake_array(group_result=True):
    originals = _PROARRAY_STATE.get("original_objects", [])
    previews = _PROARRAY_STATE.get("preview_objects", [])

    if not previews:
        cmds.warning("Rien à bake. Cliquez d'abord sur Start.")
        return None

    final_objects = []

    for obj in originals:
        if _safe_exists(obj):
            final_objects.append(obj)

    for obj in previews:
        if _safe_exists(obj):
            base_name = obj.replace(PROARRAY_PREVIEW_PREFIX, "").split("|")[-1]
            try:
                new_name = cmds.rename(obj, "proarray_%s" % base_name)
                final_objects.append(new_name)
            except:
                final_objects.append(obj)

    _PROARRAY_STATE["preview_objects"] = []

    result = None
    if group_result and final_objects:
        mode = _PROARRAY_STATE.get("array_mode", "radial")
        group_name = "ProArray_Radial_grp" if mode == "radial" else "ProArray_Rect_grp"
        result = cmds.group(final_objects, name=group_name)
        cmds.select(result)
    else:
        cmds.select(final_objects)
        result = final_objects

    proarray_cleanup_locator()
    _PROARRAY_STATE["original_objects"] = []
    _PROARRAY_STATE["original_positions"] = {}
    _PROARRAY_STATE["original_bbox_centers"] = {}

    return result


# ============================================================
# CURVE DISTRIBUTE CORE
# ============================================================
def curve_cleanup_preview():
    for obj in _CURVE_STATE.get("preview_objects", []):
        _safe_delete(obj)
    _CURVE_STATE["preview_objects"] = []

    try:
        for obj in cmds.ls(type="transform") or []:
            if obj.startswith(CURVE_PREVIEW_PREFIX):
                _safe_delete(obj)
    except:
        pass


def curve_full_cleanup():
    curve_cleanup_preview()
    _CURVE_STATE["mesh"] = None
    _CURVE_STATE["curve"] = None
    _CURVE_STATE["started"] = False
    _CURVE_STATE["baked"] = False
    _CURVE_STATE["is_processing"] = False


def get_transform_from_selection():
    return cmds.ls(sl=True, long=True, transforms=True) or []


def is_mesh_transform(obj):
    if not _safe_exists(obj):
        return False
    shapes = cmds.listRelatives(obj, s=True, f=True) or []
    for s in shapes:
        if cmds.nodeType(s) == "mesh":
            return True
    return False


def is_curve_transform(obj):
    if not _safe_exists(obj):
        return False
    shapes = cmds.listRelatives(obj, s=True, f=True) or []
    for s in shapes:
        if cmds.nodeType(s) == "nurbsCurve":
            return True
    return False


def get_curve_length(curve):
    if not _safe_exists(curve):
        return 0.0
    try:
        return cmds.arclen(curve)
    except:
        return 0.0


def get_mesh_world_bbox_size(obj):
    if not _safe_exists(obj):
        return [0.0, 0.0, 0.0]

    try:
        bbox = cmds.exactWorldBoundingBox(obj)
        return [
            abs(bbox[3] - bbox[0]),
            abs(bbox[4] - bbox[1]),
            abs(bbox[5] - bbox[2])
        ]
    except:
        return [0.0, 0.0, 0.0]


def estimate_mesh_spacing(obj, axis_mode="longest", extra_padding=0.0, base_scale=1.0, random_scale=0.0):
    size = get_mesh_world_bbox_size(obj)
    sx, sy, sz = size

    if axis_mode == "x":
        base = sx
    elif axis_mode == "y":
        base = sy
    elif axis_mode == "z":
        base = sz
    else:
        base = max(size)

    worst_scale = max(0.0001, float(base_scale) + abs(float(random_scale)))
    return max(0.0001, (base * worst_scale) + float(extra_padding))


def compute_auto_fit_count(mesh, curve, start_u, end_u,
                           axis_mode="longest",
                           padding=0.0,
                           base_scale=1.0,
                           random_scale=0.0,
                           trim_ends=True):
    if not _safe_exists(mesh) or not _safe_exists(curve):
        return 1

    curve_length = get_curve_length(curve)
    usable_ratio = max(0.0, min(1.0, float(end_u)) - max(0.0, min(1.0, float(start_u))))
    available_length = curve_length * usable_ratio

    step = estimate_mesh_spacing(
        mesh,
        axis_mode=axis_mode,
        extra_padding=padding,
        base_scale=base_scale,
        random_scale=random_scale
    )

    if step <= 0.0001:
        return 1

    if trim_ends:
        usable_for_centers = max(0.0, available_length - step)
        if usable_for_centers <= 0.0:
            return 1
        count = int(math.floor(usable_for_centers / step)) + 1
    else:
        count = int(math.floor(available_length / step)) + 1

    return max(1, count)


def build_orientation_matrix_from_tangent(tangent, world_up=(0.0, 1.0, 0.0)):
    x_axis = vec_norm(list(tangent))
    up = vec_norm(list(world_up))

    if abs(vec_dot(x_axis, up)) > 0.999:
        up = [0.0, 0.0, 1.0]

    z_axis = vec_norm(vec_cross(x_axis, up))
    y_axis = vec_norm(vec_cross(z_axis, x_axis))

    return [
        x_axis[0], x_axis[1], x_axis[2], 0.0,
        y_axis[0], y_axis[1], y_axis[2], 0.0,
        z_axis[0], z_axis[1], z_axis[2], 0.0,
        0.0,       0.0,       0.0,       1.0
    ]


def get_random_for_index(seed, index, axis_index):
    rnd = random.Random((seed * 1000003) + (index * 9176) + (axis_index * 131))
    return rnd.uniform(-1.0, 1.0)


def get_scale_random(seed, index):
    rnd = random.Random((seed * 2000003) + (index * 7919))
    return rnd.uniform(-1.0, 1.0)


def build_curve_distribution(
    mesh,
    curve,
    count=10,
    start_u=0.0,
    end_u=1.0,
    orient=True,
    use_instance=True,
    offset_pos=(0.0, 0.0, 0.0),
    offset_rot=(0.0, 0.0, 0.0),
    rand_rot=(0.0, 0.0, 0.0),
    base_scale=1.0,
    rand_scale=0.0,
    random_seed=1,
    trim_ends=True,
    fit_axis_mode="longest",
    padding=0.0,
    auto_fit=False,
):
    if not _safe_exists(mesh) or not _safe_exists(curve):
        return []

    curve_cleanup_preview()

    start_u = clamp(float(start_u), 0.0, 1.0)
    end_u = clamp(float(end_u), 0.0, 1.0)
    if end_u < start_u:
        end_u = start_u

    if auto_fit:
        count = compute_auto_fit_count(
            mesh=mesh,
            curve=curve,
            start_u=start_u,
            end_u=end_u,
            axis_mode=fit_axis_mode,
            padding=padding,
            base_scale=base_scale,
            random_scale=rand_scale,
            trim_ends=trim_ends
        )

    count = max(1, int(count))
    results = []

    trimmed_start_u = start_u
    trimmed_end_u = end_u

    if trim_ends and count > 1:
        curve_len = max(0.0001, get_curve_length(curve))
        step_est = estimate_mesh_spacing(
            mesh,
            axis_mode=fit_axis_mode,
            extra_padding=padding,
            base_scale=base_scale,
            random_scale=rand_scale
        )
        half_ratio = (step_est * 0.5) / curve_len
        max_half_ratio = max(0.0, (end_u - start_u) * 0.49)
        half_ratio = min(half_ratio, max_half_ratio)

        trimmed_start_u = clamp(start_u + half_ratio, 0.0, 1.0)
        trimmed_end_u = clamp(end_u - half_ratio, 0.0, 1.0)

        if trimmed_end_u < trimmed_start_u:
            trimmed_start_u = start_u
            trimmed_end_u = end_u

    for i in range(count):
        if count == 1:
            u = (trimmed_start_u + trimmed_end_u) * 0.5 if trim_ends else start_u
        else:
            t = float(i) / float(count - 1)
            u = trimmed_start_u + ((trimmed_end_u - trimmed_start_u) * t)

        try:
            pos = cmds.pointOnCurve(curve, pr=u, p=True, top=True)
            tangent = cmds.pointOnCurve(curve, pr=u, nt=True, top=True)
        except:
            continue

        if use_instance:
            new_obj = cmds.instance(mesh, name="{}{:03d}".format(CURVE_PREVIEW_PREFIX, i + 1))[0]
        else:
            new_obj = cmds.duplicate(mesh, rr=True, name="{}{:03d}".format(CURVE_PREVIEW_PREFIX, i + 1))[0]

        rx_rand = rand_rot[0] * get_random_for_index(random_seed, i, 0)
        ry_rand = rand_rot[1] * get_random_for_index(random_seed, i, 1)
        rz_rand = rand_rot[2] * get_random_for_index(random_seed, i, 2)

        final_rot = (
            offset_rot[0] + rx_rand,
            offset_rot[1] + ry_rand,
            offset_rot[2] + rz_rand
        )

        scale_rand_val = get_scale_random(random_seed, i)
        final_uniform_scale = max(0.001, base_scale + (rand_scale * scale_rand_val))

        if orient:
            matrix = build_orientation_matrix_from_tangent(tangent, (0.0, 1.0, 0.0))
            cmds.xform(new_obj, ws=True, matrix=matrix)

            cmds.xform(
                new_obj,
                ws=True,
                t=(
                    pos[0] + offset_pos[0],
                    pos[1] + offset_pos[1],
                    pos[2] + offset_pos[2]
                )
            )

            cmds.rotate(
                final_rot[0],
                final_rot[1],
                final_rot[2],
                new_obj,
                r=True,
                os=True
            )
        else:
            cmds.xform(
                new_obj,
                ws=True,
                t=(
                    pos[0] + offset_pos[0],
                    pos[1] + offset_pos[1],
                    pos[2] + offset_pos[2]
                )
            )
            cmds.xform(new_obj, ws=True, ro=final_rot)

        cmds.scale(
            final_uniform_scale,
            final_uniform_scale,
            final_uniform_scale,
            new_obj,
            r=False,
            os=True
        )

        results.append(new_obj)

    _CURVE_STATE["preview_objects"] = results
    return results


def curve_bake_distribution(group_result=True):
    previews = [obj for obj in _CURVE_STATE.get("preview_objects", []) if _safe_exists(obj)]
    mesh = _CURVE_STATE.get("mesh")

    if not previews:
        cmds.warning("Rien à bake. Clique d'abord sur Start.")
        return None

    final_objects = []

    for obj in previews:
        if not _safe_exists(obj):
            continue
        try:
            clean_name = obj.replace(CURVE_PREVIEW_PREFIX, "curveDist_")
            clean_name = clean_name.replace("|", "_")
            renamed = cmds.rename(obj, clean_name)
            final_objects.append(renamed)
        except:
            final_objects.append(obj)

    _CURVE_STATE["preview_objects"] = []

    if mesh and _safe_exists(mesh):
        cmds.select(mesh, r=True)

    if group_result and final_objects:
        result = cmds.group(final_objects, name=CURVE_RESULT_GROUP_NAME)
        cmds.select(result, r=True)
        return result
    else:
        cmds.select(final_objects, r=True)
        return final_objects


# ============================================================
# TAB 1 - PROARRAY
# ============================================================
class ProArrayTab(QtWidgets.QWidget, SliderMixin):
    def __init__(self, parent=None):
        super(ProArrayTab, self).__init__(parent)

        self._started = False
        self._baked = False
        self._last_locator_pos = [0, 0, 0]

        self._rebuild_timer = QtCore.QTimer()
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(16)
        self._rebuild_timer.timeout.connect(self._do_rebuild)

        self._drag_timer = QtCore.QTimer()
        self._drag_timer.setInterval(33)
        self._drag_timer.timeout.connect(self._check_locator_drag)

        _PROARRAY_STATE["ui_instance"] = self

        self._build_ui()
        self._setup_icons()

    def _request_parent_resize(self):
        parent = self.window()
        if parent and hasattr(parent, "request_resize"):
            parent.request_resize()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        self.btn_start = QtWidgets.QPushButton("Start")
        self.btn_start.setFixedHeight(26)
        self.btn_start.setObjectName("startBtn")
        self.btn_start.clicked.connect(self._on_start)
        layout.addWidget(self.btn_start)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(QtCore.Qt.AlignCenter)
        self.status_label.setFixedHeight(16)
        layout.addWidget(self.status_label)

        self.select_frame = QtWidgets.QFrame()
        self.select_frame.setVisible(False)
        self.select_frame.setMinimumHeight(36)
        self.select_frame.setMaximumHeight(36)
        select_layout = QtWidgets.QHBoxLayout(self.select_frame)
        select_layout.setContentsMargins(0, 2, 0, 2)
        select_layout.setSpacing(6)

        select_layout.addStretch()

        self.btn_select_mesh = QtWidgets.QPushButton()
        self.btn_select_mesh.setFixedSize(28, 28)
        self.btn_select_mesh.setToolTip("Select Original Mesh")
        self.btn_select_mesh.setObjectName("selectMeshBtn")
        self.btn_select_mesh.clicked.connect(self._on_select_mesh)
        select_layout.addWidget(self.btn_select_mesh)

        self.btn_select_locator = QtWidgets.QPushButton()
        self.btn_select_locator.setFixedSize(28, 28)
        self.btn_select_locator.setToolTip("Select Locator")
        self.btn_select_locator.setObjectName("selectLocatorBtn")
        self.btn_select_locator.clicked.connect(self._on_select_locator)
        select_layout.addWidget(self.btn_select_locator)

        self.btn_refresh = QtWidgets.QPushButton()
        self.btn_refresh.setFixedSize(28, 28)
        self.btn_refresh.setToolTip("Refresh View")
        self.btn_refresh.setObjectName("refreshBtn")
        self.btn_refresh.clicked.connect(self._on_refresh)
        select_layout.addWidget(self.btn_refresh)

        select_layout.addStretch()
        layout.addWidget(self.select_frame)

        mode_label = QtWidgets.QLabel("MODE")
        mode_label.setObjectName("sectionLabel")
        layout.addWidget(mode_label)

        mode_layout = QtWidgets.QHBoxLayout()
        mode_layout.setSpacing(12)
        self.radio_radial = QtWidgets.QRadioButton("Radial")
        self.radio_radial.setChecked(True)
        self.radio_radial.toggled.connect(self._on_mode_changed)
        self.radio_rect = QtWidgets.QRadioButton("Rectangular")
        self.radio_rect.toggled.connect(self._on_mode_changed)
        mode_layout.addWidget(self.radio_radial)
        mode_layout.addWidget(self.radio_rect)
        mode_layout.addStretch()
        layout.addLayout(mode_layout)

        layout.addSpacing(2)

        self.radial_frame = QtWidgets.QFrame()
        radial_layout = QtWidgets.QVBoxLayout(self.radial_frame)
        radial_layout.setContentsMargins(0, 0, 0, 0)
        radial_layout.setSpacing(3)

        radial_label = QtWidgets.QLabel("RADIAL")
        radial_label.setObjectName("sectionLabel")
        radial_layout.addWidget(radial_label)

        self.angle_slider, self.angle_spin = self._add_slider(radial_layout, "Angle", 1, 360, 360, 1)
        self.number_slider, self.number_spin = self._add_slider(radial_layout, "Number", 1, 50, 8, 0)
        self.repeat_slider, self.repeat_spin = self._add_slider(radial_layout, "Repeat", 1, 10, 1, 0)
        self.repeat_dist_slider, self.repeat_dist_spin = self._add_slider(radial_layout, "Repeat Distance", 0.0, 100.0, 1.0, 3)

        axis_layout = QtWidgets.QHBoxLayout()
        axis_layout.setSpacing(8)
        axis_lbl = QtWidgets.QLabel("Axis")
        axis_lbl.setFixedWidth(105)
        axis_layout.addWidget(axis_lbl)

        self.axis_x = QtWidgets.QRadioButton("X")
        self.axis_y = QtWidgets.QRadioButton("Y")
        self.axis_y.setChecked(True)
        self.axis_z = QtWidgets.QRadioButton("Z")

        self.axis_x.toggled.connect(self._on_rebuild)
        self.axis_y.toggled.connect(self._on_rebuild)
        self.axis_z.toggled.connect(self._on_rebuild)

        axis_layout.addWidget(self.axis_x)
        axis_layout.addWidget(self.axis_y)
        axis_layout.addWidget(self.axis_z)
        axis_layout.addStretch()
        radial_layout.addLayout(axis_layout)

        dir_layout = QtWidgets.QHBoxLayout()
        dir_layout.setSpacing(8)
        dir_lbl = QtWidgets.QLabel("Repeat Dir")
        dir_lbl.setFixedWidth(105)
        dir_layout.addWidget(dir_lbl)

        self.rad_dir_radial = QtWidgets.QRadioButton("Radial")
        self.rad_dir_axis = QtWidgets.QRadioButton("Axis")
        self.rad_dir_radial.setChecked(True)
        self.rad_dir_radial.toggled.connect(self._on_rebuild)
        self.rad_dir_axis.toggled.connect(self._on_rebuild)

        dir_layout.addWidget(self.rad_dir_radial)
        dir_layout.addWidget(self.rad_dir_axis)
        dir_layout.addStretch()
        radial_layout.addLayout(dir_layout)

        layout.addWidget(self.radial_frame)

        self.rect_frame = QtWidgets.QFrame()
        self.rect_frame.setVisible(False)
        rect_layout = QtWidgets.QVBoxLayout(self.rect_frame)
        rect_layout.setContentsMargins(0, 0, 0, 0)
        rect_layout.setSpacing(3)

        rect_label = QtWidgets.QLabel("RECTANGULAR (Locator = End)")
        rect_label.setObjectName("sectionLabel")
        rect_layout.addWidget(rect_label)

        self.copies_x_slider, self.copies_x_spin = self._add_slider(rect_layout, "Copies X", 1, 30, 3, 0)
        self.copies_y_slider, self.copies_y_spin = self._add_slider(rect_layout, "Copies Y", 1, 30, 1, 0)
        self.copies_z_slider, self.copies_z_spin = self._add_slider(rect_layout, "Copies Z", 1, 30, 1, 0)
        self.rect_repeat_slider, self.rect_repeat_spin = self._add_slider(rect_layout, "Repeat", 1, 10, 1, 0)
        self.rect_repeat_dist_slider, self.rect_repeat_dist_spin = self._add_slider(rect_layout, "Repeat Distance", 0.0, 100.0, 1.0, 3)

        rdir_layout = QtWidgets.QHBoxLayout()
        rdir_layout.setSpacing(8)
        rdir_lbl = QtWidgets.QLabel("Repeat Dir")
        rdir_lbl.setFixedWidth(105)
        rdir_layout.addWidget(rdir_lbl)

        self.rect_dir_end = QtWidgets.QRadioButton("End")
        self.rect_dir_x = QtWidgets.QRadioButton("X")
        self.rect_dir_y = QtWidgets.QRadioButton("Y")
        self.rect_dir_z = QtWidgets.QRadioButton("Z")
        self.rect_dir_x.setChecked(True)

        for w in (self.rect_dir_end, self.rect_dir_x, self.rect_dir_y, self.rect_dir_z):
            w.toggled.connect(self._on_rebuild)

        rdir_layout.addWidget(self.rect_dir_end)
        rdir_layout.addWidget(self.rect_dir_x)
        rdir_layout.addWidget(self.rect_dir_y)
        rdir_layout.addWidget(self.rect_dir_z)
        rdir_layout.addStretch()
        rect_layout.addLayout(rdir_layout)

        layout.addWidget(self.rect_frame)

        layout.addSpacing(4)

        opt_label = QtWidgets.QLabel("OPTIONS")
        opt_label.setObjectName("sectionLabel")
        layout.addWidget(opt_label)

        self.chk_instance = QtWidgets.QCheckBox("Use Instances (linked)")
        self.chk_instance.setChecked(True)
        self.chk_instance.toggled.connect(self._on_rebuild)
        layout.addWidget(self.chk_instance)

        self.chk_group = QtWidgets.QCheckBox("Group Result")
        self.chk_group.setChecked(True)
        layout.addWidget(self.chk_group)

        layout.addSpacing(6)

        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(8)

        btn_cancel = QtWidgets.QPushButton("ESC")
        btn_cancel.setFixedHeight(24)
        btn_cancel.setFixedWidth(55)
        btn_cancel.clicked.connect(self._on_cancel)
        btn_layout.addWidget(btn_cancel)

        btn_layout.addStretch()

        btn_bake = QtWidgets.QPushButton("Bake")
        btn_bake.setFixedHeight(24)
        btn_bake.setFixedWidth(65)
        btn_bake.setObjectName("bakeBtn")
        btn_bake.clicked.connect(self._on_bake)
        btn_layout.addWidget(btn_bake)

        layout.addLayout(btn_layout)

    def _setup_icons(self):
        self.btn_select_mesh.setText("\u25a3")
        self.btn_select_locator.setText("\u271b")
        self.btn_refresh.setText("\u27f3")

    def _update_start_button(self):
        if self._started:
            self.btn_start.setStyleSheet(f"""
                QPushButton {{
                    background-color: {ACCENT_RED_BG};
                    color: #ffffff;
                    border: 1px solid {ACCENT_RED_BORDER};
                    border-radius: 3px;
                    font-size: 11px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: #6a2f2f;
                }}
            """)
        else:
            self.btn_start.setStyleSheet("")

    def _on_mode_changed(self):
        is_radial = self.radio_radial.isChecked()
        self.radial_frame.setVisible(is_radial)
        self.rect_frame.setVisible(not is_radial)
        _PROARRAY_STATE["array_mode"] = "radial" if is_radial else "rectangular"

        if self._started:
            loc = _PROARRAY_STATE.get("locator")
            if _safe_exists(loc):
                shape = cmds.listRelatives(loc, shapes=True)[0]
                cmds.setAttr(shape + ".overrideColor", 17 if is_radial else 13)
            self._do_rebuild()

        self._request_parent_resize()

    def _on_start(self):
        selection = cmds.ls(selection=True, transforms=True)
        if not selection:
            cmds.warning("Sélectionnez au moins un objet!")
            return

        valid_objects = []
        for obj in selection:
            shapes = cmds.listRelatives(obj, shapes=True) or []
            if shapes:
                valid_objects.append(obj)

        if not valid_objects:
            cmds.warning("Sélectionnez au moins un objet avec une forme!")
            return

        _PROARRAY_STATE["original_objects"] = valid_objects
        _PROARRAY_STATE["original_positions"] = {}
        _PROARRAY_STATE["original_bbox_centers"] = {}

        for obj in valid_objects:
            _PROARRAY_STATE["original_positions"][obj] = {
                "position": cmds.xform(obj, query=True, worldSpace=True, translation=True),
                "rotation": cmds.xform(obj, query=True, worldSpace=True, rotation=True),
            }
            _PROARRAY_STATE["original_bbox_centers"][obj] = get_bbox_center(obj)

        is_radial = self.radio_radial.isChecked()

        if is_radial:
            pivot = cmds.xform(valid_objects[0], query=True, worldSpace=True, rotatePivot=True)
        else:
            bbox_center = _PROARRAY_STATE["original_bbox_centers"][valid_objects[0]]
            pivot = [bbox_center[0] + 5, bbox_center[1], bbox_center[2]]

        mode = "radial" if is_radial else "rectangular"
        proarray_create_pivot_locator(pivot, mode)
        self._last_locator_pos = pivot[:]

        proarray_setup_locator_callbacks(self)
        proarray_setup_mesh_callbacks(self)
        self._drag_timer.start()

        self._started = True
        self._baked = False
        self._update_start_button()
        self.select_frame.setVisible(True)
        self.status_label.setText("%d objet(s) - Move locator!" % len(valid_objects))

        self._do_rebuild()
        self._request_parent_resize()

    def _on_locator_moved(self):
        if self._started and not _PROARRAY_STATE.get("is_processing"):
            self._rebuild_timer.start()

    def _on_mesh_modified(self):
        if self._started:
            cmds.refresh(force=True)

    def _on_selection_changed(self):
        if self._started:
            cmds.refresh(force=True)

    def _on_select_mesh(self):
        proarray_select_original_mesh()

    def _on_select_locator(self):
        proarray_select_locator()

    def _on_refresh(self):
        if self._started and _PROARRAY_STATE.get("original_objects"):
            self._do_rebuild()
            self.status_label.setText("Refreshed!")

    def _check_locator_drag(self):
        if not self._started:
            return

        current_pos = proarray_get_locator_position()
        moved = any(abs(current_pos[i] - self._last_locator_pos[i]) > 0.0001 for i in range(3))

        if moved:
            self._last_locator_pos = current_pos[:]
            if not _PROARRAY_STATE.get("is_processing"):
                self._rebuild_timer.start()

    def _on_rebuild(self):
        if not _PROARRAY_STATE.get("original_objects"):
            return
        if _PROARRAY_STATE.get("is_processing", False):
            return
        self._rebuild_timer.start()

    def _do_rebuild(self):
        if not _PROARRAY_STATE.get("original_objects"):
            return
        if _PROARRAY_STATE.get("is_processing", False):
            return

        _PROARRAY_STATE["is_processing"] = True

        try:
            objects = _PROARRAY_STATE["original_objects"]
            use_instance = self.chk_instance.isChecked()

            if self.radio_radial.isChecked():
                angle = self.angle_spin.value()
                number = int(self.number_spin.value())
                repeat = int(self.repeat_spin.value())

                axis = 'y'
                if self.axis_x.isChecked():
                    axis = 'x'
                elif self.axis_z.isChecked():
                    axis = 'z'

                rad_dir = "radial" if self.rad_dir_radial.isChecked() else "axis"

                build_radial_array(
                    objects,
                    angle=angle,
                    number=number,
                    repeat=repeat,
                    axis=axis,
                    repeat_distance=float(self.repeat_dist_spin.value()),
                    repeat_direction=rad_dir,
                    use_instance=use_instance
                )
            else:
                if self.rect_dir_y.isChecked():
                    rdir = "y"
                elif self.rect_dir_z.isChecked():
                    rdir = "z"
                elif self.rect_dir_end.isChecked():
                    rdir = "end"
                else:
                    rdir = "x"

                build_rectangular_array(
                    objects,
                    copies_x=int(self.copies_x_spin.value()),
                    copies_y=int(self.copies_y_spin.value()),
                    copies_z=int(self.copies_z_spin.value()),
                    repeat=int(self.rect_repeat_spin.value()),
                    repeat_distance=float(self.rect_repeat_dist_spin.value()),
                    repeat_direction=rdir,
                    use_instance=use_instance
                )

            preview_count = len(_PROARRAY_STATE.get("preview_objects", []))
            total = preview_count + len(objects)
            self.status_label.setText("%d copies (total: %d)" % (preview_count, total))

            cmds.refresh(force=False)

        except Exception as e:
            cmds.warning("Rebuild failed: %s" % str(e))
        finally:
            _PROARRAY_STATE["is_processing"] = False

    def _on_cancel(self):
        self._rebuild_timer.stop()
        self._drag_timer.stop()
        proarray_full_cleanup()
        self._started = False
        self._baked = False
        self._update_start_button()
        self.select_frame.setVisible(False)
        self.status_label.setText("")
        self._request_parent_resize()

    def _on_bake(self):
        self._rebuild_timer.stop()
        self._drag_timer.stop()

        if not _PROARRAY_STATE.get("preview_objects"):
            cmds.warning("Cliquez d'abord sur Start!")
            return

        result = proarray_bake_array(group_result=self.chk_group.isChecked())

        if result:
            self.status_label.setText("Array created!")
            self._baked = True

        self._started = False
        self._update_start_button()
        self.select_frame.setVisible(False)
        self._request_parent_resize()

    def shutdown(self):
        self._rebuild_timer.stop()
        self._drag_timer.stop()
        if self._started and not self._baked:
            proarray_full_cleanup()
        _PROARRAY_STATE["ui_instance"] = None


# ============================================================
# TAB 2 - CURVE DISTRIBUTE
# ============================================================
class CurveDistributeTab(QtWidgets.QWidget, SliderMixin):
    def __init__(self, parent=None):
        super(CurveDistributeTab, self).__init__(parent)

        self._rebuild_timer = QtCore.QTimer()
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(20)
        self._rebuild_timer.timeout.connect(self._do_rebuild)

        _CURVE_STATE["ui_instance"] = self

        self._build_ui()
        self._setup_icons()

    def _request_parent_resize(self):
        parent = self.window()
        if parent and hasattr(parent, "request_resize"):
            parent.request_resize()

    def _get_fit_axis_mode(self):
        if self.fit_axis_x.isChecked():
            return "x"
        elif self.fit_axis_y.isChecked():
            return "y"
        elif self.fit_axis_z.isChecked():
            return "z"
        return "longest"

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        self.btn_start = QtWidgets.QPushButton("Start")
        self.btn_start.setFixedHeight(26)
        self.btn_start.setObjectName("startBtn")
        self.btn_start.clicked.connect(self._on_start)
        layout.addWidget(self.btn_start)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(QtCore.Qt.AlignCenter)
        self.status_label.setFixedHeight(18)
        layout.addWidget(self.status_label)

        self.select_frame = QtWidgets.QFrame()
        self.select_frame.setVisible(False)
        self.select_frame.setMinimumHeight(36)
        self.select_frame.setMaximumHeight(36)

        select_layout = QtWidgets.QHBoxLayout(self.select_frame)
        select_layout.setContentsMargins(0, 2, 0, 2)
        select_layout.setSpacing(6)

        select_layout.addStretch()

        self.btn_select_mesh = QtWidgets.QPushButton()
        self.btn_select_mesh.setFixedSize(28, 28)
        self.btn_select_mesh.setObjectName("selectMeshBtn")
        self.btn_select_mesh.setToolTip("Select Mesh")
        self.btn_select_mesh.clicked.connect(self._on_select_mesh)
        select_layout.addWidget(self.btn_select_mesh)

        self.btn_select_curve = QtWidgets.QPushButton()
        self.btn_select_curve.setFixedSize(28, 28)
        self.btn_select_curve.setObjectName("selectCurveBtn")
        self.btn_select_curve.setToolTip("Select Curve")
        self.btn_select_curve.clicked.connect(self._on_select_curve)
        select_layout.addWidget(self.btn_select_curve)

        self.btn_refresh = QtWidgets.QPushButton()
        self.btn_refresh.setFixedSize(28, 28)
        self.btn_refresh.setObjectName("refreshBtn")
        self.btn_refresh.setToolTip("Refresh")
        self.btn_refresh.clicked.connect(self._on_refresh)
        select_layout.addWidget(self.btn_refresh)

        select_layout.addStretch()
        layout.addWidget(self.select_frame)

        input_label = QtWidgets.QLabel("INPUT")
        input_label.setObjectName("sectionLabel")
        layout.addWidget(input_label)

        self.mesh_label = QtWidgets.QLabel("Mesh : -")
        layout.addWidget(self.mesh_label)

        self.curve_label = QtWidgets.QLabel("Curve : -")
        layout.addWidget(self.curve_label)

        self.curve_length_label = QtWidgets.QLabel("Length : -")
        layout.addWidget(self.curve_length_label)

        layout.addSpacing(2)

        dist_label = QtWidgets.QLabel("DISTRIBUTION")
        dist_label.setObjectName("sectionLabel")
        layout.addWidget(dist_label)

        self.count_slider, self.count_spin = self._add_slider(layout, "Count", 1, 1000, 10, 0, label_width=110)
        self.start_slider, self.start_spin = self._add_slider(layout, "Start", 0.0, 1.0, 0.0, 3, label_width=110)
        self.end_slider, self.end_spin = self._add_slider(layout, "End", 0.0, 1.0, 1.0, 3, label_width=110)

        self.chk_auto_fit = QtWidgets.QCheckBox("Auto Fit Count")
        self.chk_auto_fit.setChecked(False)
        self.chk_auto_fit.toggled.connect(self._on_auto_fit_toggled)
        layout.addWidget(self.chk_auto_fit)

        self.chk_trim_ends = QtWidgets.QCheckBox("Trim Ends")
        self.chk_trim_ends.setChecked(True)
        self.chk_trim_ends.toggled.connect(self._on_rebuild)
        layout.addWidget(self.chk_trim_ends)

        self.padding_slider, self.padding_spin = self._add_slider(layout, "Padding", -10.0, 50.0, 0.0, 3, label_width=110)

        axis_row = QtWidgets.QHBoxLayout()
        axis_row.setSpacing(8)

        axis_lbl = QtWidgets.QLabel("Fit Axis")
        axis_lbl.setFixedWidth(110)
        axis_row.addWidget(axis_lbl)

        self.fit_axis_x = QtWidgets.QRadioButton("X")
        self.fit_axis_y = QtWidgets.QRadioButton("Y")
        self.fit_axis_z = QtWidgets.QRadioButton("Z")
        self.fit_axis_longest = QtWidgets.QRadioButton("Longest")
        self.fit_axis_longest.setChecked(True)

        for w in (self.fit_axis_x, self.fit_axis_y, self.fit_axis_z, self.fit_axis_longest):
            w.toggled.connect(self._on_rebuild)
            axis_row.addWidget(w)

        axis_row.addStretch()
        layout.addLayout(axis_row)

        layout.addSpacing(2)

        offset_pos_label = QtWidgets.QLabel("OFFSET POSITION")
        offset_pos_label.setObjectName("sectionLabel")
        layout.addWidget(offset_pos_label)

        self.offx_slider, self.offx_spin = self._add_slider(layout, "Offset X", -100.0, 100.0, 0.0, 3, label_width=110)
        self.offy_slider, self.offy_spin = self._add_slider(layout, "Offset Y", -100.0, 100.0, 0.0, 3, label_width=110)
        self.offz_slider, self.offz_spin = self._add_slider(layout, "Offset Z", -100.0, 100.0, 0.0, 3, label_width=110)

        layout.addSpacing(2)

        rot_label = QtWidgets.QLabel("BASE ROTATION")
        rot_label.setObjectName("sectionLabel")
        layout.addWidget(rot_label)

        self.rotx_slider, self.rotx_spin = self._add_slider(layout, "Rotate X", -360.0, 360.0, 0.0, 2, label_width=110)
        self.roty_slider, self.roty_spin = self._add_slider(layout, "Rotate Y", -360.0, 360.0, 0.0, 2, label_width=110)
        self.rotz_slider, self.rotz_spin = self._add_slider(layout, "Rotate Z", -360.0, 360.0, 0.0, 2, label_width=110)

        layout.addSpacing(2)

        rand_rot_label = QtWidgets.QLabel("RANDOM ROTATION")
        rand_rot_label.setObjectName("sectionLabel")
        layout.addWidget(rand_rot_label)

        self.rand_rotx_slider, self.rand_rotx_spin = self._add_slider(layout, "Random Rot X", 0.0, 360.0, 0.0, 2, label_width=110)
        self.rand_roty_slider, self.rand_roty_spin = self._add_slider(layout, "Random Rot Y", 0.0, 360.0, 0.0, 2, label_width=110)
        self.rand_rotz_slider, self.rand_rotz_spin = self._add_slider(layout, "Random Rot Z", 0.0, 360.0, 0.0, 2, label_width=110)

        layout.addSpacing(2)

        scale_label = QtWidgets.QLabel("SCALE")
        scale_label.setObjectName("sectionLabel")
        layout.addWidget(scale_label)

        self.scale_slider, self.scale_spin = self._add_slider(layout, "Scale", 0.001, 10.0, 1.0, 3, label_width=110)
        self.rand_scale_slider, self.rand_scale_spin = self._add_slider(layout, "Random Scale", 0.0, 5.0, 0.0, 3, label_width=110)

        layout.addSpacing(2)

        seed_label = QtWidgets.QLabel("RANDOM")
        seed_label.setObjectName("sectionLabel")
        layout.addWidget(seed_label)

        self.seed_slider, self.seed_spin = self._add_slider(layout, "Seed", 1, 9999, 1, 0, label_width=110)

        layout.addSpacing(4)

        opt_label = QtWidgets.QLabel("OPTIONS")
        opt_label.setObjectName("sectionLabel")
        layout.addWidget(opt_label)

        self.chk_orient = QtWidgets.QCheckBox("Orient to Curve")
        self.chk_orient.setChecked(True)
        self.chk_orient.toggled.connect(self._on_rebuild)
        layout.addWidget(self.chk_orient)

        self.chk_instance = QtWidgets.QCheckBox("Use Instances (linked)")
        self.chk_instance.setChecked(True)
        self.chk_instance.toggled.connect(self._on_rebuild)
        layout.addWidget(self.chk_instance)

        self.chk_group = QtWidgets.QCheckBox("Group Result")
        self.chk_group.setChecked(True)
        layout.addWidget(self.chk_group)

        layout.addSpacing(6)

        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(8)

        self.btn_cancel = QtWidgets.QPushButton("ESC")
        self.btn_cancel.setFixedHeight(24)
        self.btn_cancel.setFixedWidth(55)
        self.btn_cancel.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self.btn_cancel)

        btn_layout.addStretch()

        self.btn_bake = QtWidgets.QPushButton("Bake")
        self.btn_bake.setFixedHeight(24)
        self.btn_bake.setFixedWidth(65)
        self.btn_bake.setObjectName("bakeBtn")
        self.btn_bake.clicked.connect(self._on_bake)
        btn_layout.addWidget(self.btn_bake)

        layout.addLayout(btn_layout)

    def _setup_icons(self):
        self.btn_select_mesh.setText("\u25a3")
        self.btn_select_curve.setText("\u223f")
        self.btn_refresh.setText("\u27f3")

    def _update_start_button(self):
        if _CURVE_STATE["started"]:
            self.btn_start.setStyleSheet(f"""
                QPushButton {{
                    background-color: {ACCENT_RED_BG};
                    color: #ffffff;
                    border: 1px solid {ACCENT_RED_BORDER};
                    border-radius: 3px;
                    font-size: 11px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: #6a2f2f;
                }}
            """)
        else:
            self.btn_start.setStyleSheet("")

    def _update_info_labels(self):
        mesh = _CURVE_STATE.get("mesh")
        curve = _CURVE_STATE.get("curve")

        self.mesh_label.setText("Mesh : {}".format(mesh.split("|")[-1] if mesh else "-"))
        self.curve_label.setText("Curve : {}".format(curve.split("|")[-1] if curve else "-"))

        if curve and _safe_exists(curve):
            length = get_curve_length(curve)
            self.curve_length_label.setText("Length : {:.3f}".format(length))
        else:
            self.curve_length_label.setText("Length : -")

    def _on_rebuild(self):
        if not _CURVE_STATE.get("started"):
            return
        if _CURVE_STATE.get("is_processing"):
            return
        self._rebuild_timer.start()

    def _on_auto_fit_toggled(self):
        is_auto = self.chk_auto_fit.isChecked()
        self.count_spin.setEnabled(not is_auto)
        self.count_slider.setEnabled(not is_auto)
        self._on_rebuild()
        self._request_parent_resize()

    def _on_start(self):
        selection = get_transform_from_selection()

        if len(selection) < 2:
            cmds.warning("Sélectionne d'abord ton mesh puis ta curve.")
            self.status_label.setText("Select mesh then curve")
            return

        mesh = selection[0]
        curve = selection[1]

        if not is_mesh_transform(mesh):
            cmds.warning("Le premier objet sélectionné doit être un mesh transform.")
            self.status_label.setText("First selection must be a mesh")
            return

        if not is_curve_transform(curve):
            cmds.warning("Le deuxième objet sélectionné doit être une NURBS curve transform.")
            self.status_label.setText("Second selection must be a NURBS curve")
            return

        _CURVE_STATE["mesh"] = mesh
        _CURVE_STATE["curve"] = curve
        _CURVE_STATE["started"] = True
        _CURVE_STATE["baked"] = False

        self._update_start_button()
        self._update_info_labels()
        self.select_frame.setVisible(True)
        self.status_label.setText("Started - live preview active")

        self._do_rebuild()
        self._request_parent_resize()

    def _do_rebuild(self):
        if not _CURVE_STATE.get("started"):
            return

        mesh = _CURVE_STATE.get("mesh")
        curve = _CURVE_STATE.get("curve")

        if not _safe_exists(mesh) or not _safe_exists(curve):
            self.status_label.setText("Mesh or curve missing")
            return

        if _CURVE_STATE.get("is_processing"):
            return

        _CURVE_STATE["is_processing"] = True

        try:
            start_u = float(self.start_spin.value())
            end_u = float(self.end_spin.value())

            if end_u < start_u:
                end_u = start_u
                self.end_spin.blockSignals(True)
                self.end_spin.setValue(end_u)
                self.end_spin.blockSignals(False)

            base_scale = float(self.scale_spin.value())
            rand_scale = float(self.rand_scale_spin.value())
            fit_axis = self._get_fit_axis_mode()
            padding = float(self.padding_spin.value())
            trim_ends = self.chk_trim_ends.isChecked()
            auto_fit = self.chk_auto_fit.isChecked()

            if auto_fit:
                count = compute_auto_fit_count(
                    mesh=mesh,
                    curve=curve,
                    start_u=start_u,
                    end_u=end_u,
                    axis_mode=fit_axis,
                    padding=padding,
                    base_scale=base_scale,
                    random_scale=rand_scale,
                    trim_ends=trim_ends
                )
                self.count_spin.blockSignals(True)
                self.count_spin.setValue(count)
                self.count_spin.blockSignals(False)
            else:
                count = int(self.count_spin.value())

            offset_pos = (
                float(self.offx_spin.value()),
                float(self.offy_spin.value()),
                float(self.offz_spin.value()),
            )

            offset_rot = (
                float(self.rotx_spin.value()),
                float(self.roty_spin.value()),
                float(self.rotz_spin.value()),
            )

            rand_rot = (
                float(self.rand_rotx_spin.value()),
                float(self.rand_roty_spin.value()),
                float(self.rand_rotz_spin.value()),
            )

            orient = self.chk_orient.isChecked()
            use_instance = self.chk_instance.isChecked()
            random_seed = int(self.seed_spin.value())

            result = build_curve_distribution(
                mesh=mesh,
                curve=curve,
                count=count,
                start_u=start_u,
                end_u=end_u,
                orient=orient,
                use_instance=use_instance,
                offset_pos=offset_pos,
                offset_rot=offset_rot,
                rand_rot=rand_rot,
                base_scale=base_scale,
                rand_scale=rand_scale,
                random_seed=random_seed,
                trim_ends=trim_ends,
                fit_axis_mode=fit_axis,
                padding=padding,
                auto_fit=auto_fit
            )

            curve_len = get_curve_length(curve)
            usable_len = curve_len * max(0.0, (end_u - start_u))
            step_est = estimate_mesh_spacing(
                mesh,
                axis_mode=fit_axis,
                extra_padding=padding,
                base_scale=base_scale,
                random_scale=rand_scale
            )

            if auto_fit:
                self.status_label.setText(
                    "{} preview | usable {:.3f} | step {:.3f}".format(
                        len(result), usable_len, step_est
                    )
                )
            else:
                self.status_label.setText(
                    "{} preview | usable {:.3f}".format(len(result), usable_len)
                )

            cmds.refresh(force=False)

        except Exception as e:
            cmds.warning("Rebuild failed: {}".format(str(e)))
            self.status_label.setText("Rebuild failed")
        finally:
            _CURVE_STATE["is_processing"] = False

    def _on_select_mesh(self):
        mesh = _CURVE_STATE.get("mesh")
        if mesh and _safe_exists(mesh):
            cmds.select(mesh, r=True)

    def _on_select_curve(self):
        curve = _CURVE_STATE.get("curve")
        if curve and _safe_exists(curve):
            cmds.select(curve, r=True)

    def _on_refresh(self):
        if _CURVE_STATE.get("started"):
            self._do_rebuild()
            self.status_label.setText("Refreshed")

    def _on_cancel(self):
        self._rebuild_timer.stop()
        curve_full_cleanup()
        self._update_start_button()
        self.select_frame.setVisible(False)
        self.mesh_label.setText("Mesh : -")
        self.curve_label.setText("Curve : -")
        self.curve_length_label.setText("Length : -")
        self.status_label.setText("")
        self._request_parent_resize()

    def _on_bake(self):
        self._rebuild_timer.stop()

        if not _CURVE_STATE.get("started"):
            cmds.warning("Clique d'abord sur Start.")
            return

        if not _CURVE_STATE.get("preview_objects"):
            cmds.warning("Aucune preview à baker.")
            return

        result = curve_bake_distribution(group_result=self.chk_group.isChecked())

        if result:
            self.status_label.setText("Distribution created!")
            _CURVE_STATE["baked"] = True
            _CURVE_STATE["started"] = False
            self._update_start_button()
            self.select_frame.setVisible(False)
            self._request_parent_resize()

    def shutdown(self):
        self._rebuild_timer.stop()
        if _CURVE_STATE.get("started") and not _CURVE_STATE.get("baked"):
            curve_full_cleanup()
        _CURVE_STATE["ui_instance"] = None


# ============================================================
# MAIN COMBINED UI
# ============================================================
class ProToolsCombinedUI(QtWidgets.QDialog):
    _instance = None

    def __init__(self, parent=get_maya_main_window()):
        super(ProToolsCombinedUI, self).__init__(parent)

        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.WindowCloseButtonHint)

        self._min_width_proarray = 360
        self._min_width_curve = 390
        self._max_auto_height = 900

        self._build_ui()
        apply_shared_style(self)

        self.tabs.setCurrentIndex(0)

        QtCore.QTimer.singleShot(0, lambda: self._apply_tab_size(0, force=True))

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setDocumentMode(False)

        # TAB 1
        self.tab_proarray = ProArrayTab()
        self.tabs.addTab(self.tab_proarray, "ProArray")

        # TAB 2
        self.tab_curve = CurveDistributeTab()

        self.curve_scroll = QtWidgets.QScrollArea()
        self.curve_scroll.setWidgetResizable(True)
        self.curve_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.curve_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.curve_scroll.setWidget(self.tab_curve)

        self.tabs.addTab(self.curve_scroll, "Curve Distribute Pro")

        self.tabs.currentChanged.connect(self._on_tab_changed)

        layout.addWidget(self.tabs)

    def request_resize(self):
        QtCore.QTimer.singleShot(0, lambda: self._apply_tab_size(self.tabs.currentIndex(), force=True))

    def _get_tab_content_hint(self, index):
        if index == 0:
            self.tab_proarray.adjustSize()
            return self.tab_proarray.sizeHint()
        else:
            self.tab_curve.adjustSize()
            return self.tab_curve.sizeHint()

    def _apply_tab_size(self, index, force=False):
        self.layout().activate()
        self.tabs.updateGeometry()
        self.adjustSize()

        content_hint = self._get_tab_content_hint(index)

        if index == 0:
            target_w = max(self._min_width_proarray, content_hint.width() + 40)
            target_h = content_hint.height() + 90
            target_h = min(target_h, self._max_auto_height)

            self.setMinimumWidth(self._min_width_proarray)
            self.setMinimumHeight(250)
            self.setMaximumWidth(16777215)
            self.setMaximumHeight(16777215)

        else:
            target_w = max(self._min_width_curve, content_hint.width() + 40)
            target_h = content_hint.height() + 90
            target_h = max(620, min(target_h, self._max_auto_height))

            self.setMinimumWidth(self._min_width_curve)
            self.setMinimumHeight(620)
            self.setMaximumWidth(16777215)
            self.setMaximumHeight(16777215)

        self.resize(target_w, target_h)

    def _on_tab_changed(self, index):
        self._apply_tab_size(index, force=True)

    def closeEvent(self, event):
        try:
            self.tab_proarray.shutdown()
        except:
            pass

        try:
            self.tab_curve.shutdown()
        except:
            pass

        super(ProToolsCombinedUI, self).closeEvent(event)

    @classmethod
    def show_ui(cls):
        if cls._instance:
            try:
                cls._instance.close()
                cls._instance.deleteLater()
            except:
                pass

        cls._instance = cls()
        cls._instance.show()
        return cls._instance


def show_ui():
    return ProToolsCombinedUI.show_ui()


if __name__ == "__main__":
    show_ui()
