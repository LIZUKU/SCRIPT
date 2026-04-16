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
SETTINGS_ORG = "ScriptTools"
SETTINGS_APP = "ProToolsCombined"

ACCENT_RED_BG = "#5a2a2a"
ACCENT_RED_BORDER = "#e84d4d"
ACCENT_RED_TEXT = "#e84d4d"
AXIS_X_COLOR = "#8f4a4a"
AXIS_Y_COLOR = "#4a8f4a"
AXIS_Z_COLOR = "#4a6f8f"

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
    "curves": [],
    "preview_objects": [],
    "script_jobs": [],
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

        reset_btn = QtWidgets.QPushButton("R")
        reset_btn.setToolTip("Reset to default")
        reset_btn.setFixedSize(18, 18)
        reset_btn.setStyleSheet("font-size: 9px; padding: 0px;")
        row.addWidget(reset_btn)

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
        reset_btn.clicked.connect(lambda: spinbox.setValue(default if decimals > 0 else int(default)))

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


def proarray_get_combined_bbox_center():
    objects = [o for o in _PROARRAY_STATE.get("original_objects", []) if _safe_exists(o)]
    if not objects:
        return [0.0, 0.0, 0.0]

    mins = [float("inf"), float("inf"), float("inf")]
    maxs = [float("-inf"), float("-inf"), float("-inf")]

    for obj in objects:
        try:
            bbox = cmds.exactWorldBoundingBox(obj)
        except:
            continue

        mins[0] = min(mins[0], bbox[0])
        mins[1] = min(mins[1], bbox[1])
        mins[2] = min(mins[2], bbox[2])
        maxs[0] = max(maxs[0], bbox[3])
        maxs[1] = max(maxs[1], bbox[4])
        maxs[2] = max(maxs[2], bbox[5])

    if mins[0] == float("inf"):
        return [0.0, 0.0, 0.0]

    return [
        (mins[0] + maxs[0]) * 0.5,
        (mins[1] + maxs[1]) * 0.5,
        (mins[2] + maxs[2]) * 0.5,
    ]


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

        seed_pos = _PROARRAY_STATE["original_bbox_centers"].get(obj) or get_bbox_center(obj)
        if not seed_pos:
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

                # Important: force a stable pivot on mesh bbox center to avoid large offsets
                # when source transforms have pivots far from the geometry.
                try:
                    bbox_center = _PROARRAY_STATE["original_bbox_centers"].get(obj) or get_bbox_center(obj)
                    cmds.xform(new_obj, ws=True, pivots=bbox_center)
                except:
                    pass

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
    for job_id in _CURVE_STATE.get("script_jobs", []):
        try:
            if cmds.scriptJob(exists=job_id):
                cmds.scriptJob(kill=job_id, force=True)
        except:
            pass
    _CURVE_STATE["script_jobs"] = []
    _CURVE_STATE["mesh"] = None
    _CURVE_STATE["curves"] = []
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
                           trim_ends=True,
                           safety_multiplier=1.0,
                           max_count=None):
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
    ) * max(0.01, float(safety_multiplier))

    if step <= 0.0001:
        return 1

    if trim_ends:
        usable_for_centers = max(0.0, available_length - step)
        if usable_for_centers <= 0.0:
            return 1
        count = int(math.floor(usable_for_centers / step)) + 1
    else:
        count = int(math.floor(available_length / step)) + 1

    count = max(1, count)
    if max_count is not None:
        try:
            count = min(count, max(1, int(max_count)))
        except:
            pass
    return count


def build_orientation_matrix(tangent, up_vector=(0.0, 1.0, 0.0)):
    x_axis = vec_norm(list(tangent))
    up = vec_norm(list(up_vector))

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


def get_curve_tangent_and_normal(curve, u_ratio):
    tangent = None
    normal = None

    try:
        tangent = cmds.pointOnCurve(curve, pr=u_ratio, nt=True, top=True)
    except:
        tangent = None

    try:
        sel = om.MSelectionList()
        sel.add(curve)
        dag_path = sel.getDagPath(0)

        if dag_path.apiType() == om.MFn.kTransform:
            dag_path.extendToShape()

        fn_curve = om.MFnNurbsCurve(dag_path)
        min_u, max_u = fn_curve.knotDomain
        param_u = min_u + ((max_u - min_u) * clamp(u_ratio, 0.0, 1.0))

        tangent_vec = fn_curve.tangent(param_u, om.MSpace.kWorld)
        normal_vec = fn_curve.normal(param_u, om.MSpace.kWorld)

        tangent = [tangent_vec.x, tangent_vec.y, tangent_vec.z]
        normal = [normal_vec.x, normal_vec.y, normal_vec.z]
    except:
        pass

    if not tangent:
        tangent = [1.0, 0.0, 0.0]

    return tangent, normal


def get_random_for_index(seed, index, axis_index):
    rnd = random.Random((seed * 1000003) + (index * 9176) + (axis_index * 131))
    return rnd.uniform(-1.0, 1.0)


def get_scale_random(seed, index):
    rnd = random.Random((seed * 2000003) + (index * 7919))
    return rnd.uniform(-1.0, 1.0)


def get_mesh_center_offset_local(mesh):
    if not _safe_exists(mesh):
        return (0.0, 0.0, 0.0)

    try:
        bbox = cmds.xform(mesh, q=True, bb=True, os=True)
        pivot = cmds.xform(mesh, q=True, rp=True, os=True)
        if not bbox or not pivot or len(bbox) < 6 or len(pivot) < 3:
            return (0.0, 0.0, 0.0)

        center = (
            (bbox[0] + bbox[3]) * 0.5,
            (bbox[1] + bbox[4]) * 0.5,
            (bbox[2] + bbox[5]) * 0.5
        )
        return (
            center[0] - pivot[0],
            center[1] - pivot[1],
            center[2] - pivot[2]
        )
    except:
        return (0.0, 0.0, 0.0)


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
    safety_multiplier=1.0,
    max_count=None,
    clear_existing=True,
    name_prefix="",
    orient_mode="world_up",
    center_on_bbox=False,
):
    if not _safe_exists(mesh) or not _safe_exists(curve):
        return []

    if clear_existing:
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
            trim_ends=trim_ends,
            safety_multiplier=safety_multiplier,
            max_count=max_count
        )

    count = max(1, int(count))
    results = []
    local_center_offset = get_mesh_center_offset_local(mesh)

    proxy_mesh = None
    source_mesh = mesh
    use_proxy = False

    try:
        bbox = cmds.exactWorldBoundingBox(mesh)
        bbox_center = [
            (bbox[0] + bbox[3]) * 0.5,
            (bbox[1] + bbox[4]) * 0.5,
            (bbox[2] + bbox[5]) * 0.5,
        ]
        offset = [-bbox_center[0], -bbox_center[1], -bbox_center[2]]

        if vec_len(offset) > 0.001:
            proxy_mesh = cmds.duplicate(mesh, rr=True, name="__curveScatter_proxy__")[0]
            cmds.move(offset[0], offset[1], offset[2], proxy_mesh + ".vtx[*]", r=True, ws=True)
            cmds.delete(proxy_mesh, constructionHistory=True)
            cmds.makeIdentity(proxy_mesh, apply=True, t=True, r=True, s=True, n=False, pn=True)
            cmds.xform(proxy_mesh, ws=True, pivots=[0, 0, 0])
            source_mesh = proxy_mesh
            use_proxy = True
    except Exception as e:
        cmds.warning("Proxy mesh creation failed: {}".format(str(e)))
        source_mesh = mesh
        use_proxy = False

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

    try:
        for i in range(count):
            if count == 1:
                u = (trimmed_start_u + trimmed_end_u) * 0.5 if trim_ends else start_u
            else:
                t = float(i) / float(count - 1)
                u = trimmed_start_u + ((trimmed_end_u - trimmed_start_u) * t)

            try:
                pos = cmds.pointOnCurve(curve, pr=u, p=True, top=True)
                tangent, curve_normal = get_curve_tangent_and_normal(curve, u)
            except:
                continue

            if use_instance:
                new_obj = cmds.instance(source_mesh, name="{}{}{:03d}".format(CURVE_PREVIEW_PREFIX, name_prefix, i + 1))[0]
            else:
                new_obj = cmds.duplicate(source_mesh, rr=True, name="{}{}{:03d}".format(CURVE_PREVIEW_PREFIX, name_prefix, i + 1))[0]

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
                up_vec = (0.0, 1.0, 0.0)
                if orient_mode == "curve_normal" and curve_normal and vec_len(curve_normal) > 1e-6:
                    up_vec = curve_normal

                matrix = build_orientation_matrix(tangent, up_vec)
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
                if center_on_bbox and not use_proxy:
                    cmds.move(
                        -local_center_offset[0],
                        -local_center_offset[1],
                        -local_center_offset[2],
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
                if center_on_bbox and not use_proxy:
                    cmds.move(
                        -local_center_offset[0],
                        -local_center_offset[1],
                        -local_center_offset[2],
                        new_obj,
                        r=True,
                        os=True
                    )

            cmds.scale(
                final_uniform_scale,
                final_uniform_scale,
                final_uniform_scale,
                new_obj,
                r=False,
                os=True
            )

            results.append(new_obj)
    finally:
        if use_proxy and proxy_mesh and _safe_exists(proxy_mesh):
            _safe_delete(proxy_mesh)

    if clear_existing:
        _CURVE_STATE["preview_objects"] = results
    else:
        _CURVE_STATE["preview_objects"].extend(results)
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
        self._settings_defaults = []

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

    def _register_default(self, widget, value):
        self._settings_defaults.append((widget, value))

    def load_settings(self, settings):
        settings.beginGroup("proarray")
        for widget, default_value in self._settings_defaults:
            key = widget.property("settings_key")
            if not key:
                continue
            raw = settings.value(key, default_value)
            if isinstance(widget, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
                widget.setValue(float(raw) if isinstance(widget, QtWidgets.QDoubleSpinBox) else int(float(raw)))
            elif isinstance(widget, (QtWidgets.QCheckBox, QtWidgets.QRadioButton)):
                if isinstance(raw, bool):
                    widget.setChecked(raw)
                else:
                    widget.setChecked(str(raw).lower() in ("1", "true", "yes"))
        settings.endGroup()

    def save_settings(self, settings):
        settings.beginGroup("proarray")
        for widget, _ in self._settings_defaults:
            key = widget.property("settings_key")
            if not key:
                continue
            if isinstance(widget, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
                settings.setValue(key, widget.value())
            elif isinstance(widget, (QtWidgets.QCheckBox, QtWidgets.QRadioButton)):
                settings.setValue(key, widget.isChecked())
        settings.endGroup()

    def reset_settings(self):
        for widget, default_value in self._settings_defaults:
            if isinstance(widget, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
                widget.setValue(default_value)
            elif isinstance(widget, (QtWidgets.QCheckBox, QtWidgets.QRadioButton)):
                widget.setChecked(default_value)

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

        self.btn_reset_center = QtWidgets.QPushButton()
        self.btn_reset_center.setFixedSize(28, 28)
        self.btn_reset_center.setToolTip("Reset locator to combined bounding-box center")
        self.btn_reset_center.setObjectName("refreshBtn")
        self.btn_reset_center.clicked.connect(self._on_reset_center)
        select_layout.addWidget(self.btn_reset_center)

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
        self.radio_radial.setProperty("settings_key", "mode_radial")
        self._register_default(self.radio_radial, True)
        self.radio_rect = QtWidgets.QRadioButton("Rectangular")
        self.radio_rect.toggled.connect(self._on_mode_changed)
        self.radio_rect.setProperty("settings_key", "mode_rect")
        self._register_default(self.radio_rect, False)
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
        self.angle_spin.setProperty("settings_key", "angle")
        self._register_default(self.angle_spin, 360.0)
        self.number_slider, self.number_spin = self._add_slider(radial_layout, "Number", 1, 50, 8, 0)
        self.number_spin.setProperty("settings_key", "number")
        self._register_default(self.number_spin, 8)
        self.repeat_slider, self.repeat_spin = self._add_slider(radial_layout, "Repeat", 1, 10, 1, 0)
        self.repeat_spin.setProperty("settings_key", "repeat")
        self._register_default(self.repeat_spin, 1)
        self.repeat_dist_slider, self.repeat_dist_spin = self._add_slider(radial_layout, "Repeat Distance", 0.0, 100.0, 1.0, 3)
        self.repeat_dist_spin.setProperty("settings_key", "repeat_distance")
        self._register_default(self.repeat_dist_spin, 1.0)

        axis_layout = QtWidgets.QHBoxLayout()
        axis_layout.setSpacing(8)
        axis_lbl = QtWidgets.QLabel("Axis")
        axis_lbl.setFixedWidth(105)
        axis_layout.addWidget(axis_lbl)

        self.axis_x = QtWidgets.QRadioButton("X")
        self.axis_y = QtWidgets.QRadioButton("Y")
        self.axis_y.setChecked(True)
        self.axis_z = QtWidgets.QRadioButton("Z")
        self.axis_x.setProperty("settings_key", "axis_x")
        self.axis_y.setProperty("settings_key", "axis_y")
        self.axis_z.setProperty("settings_key", "axis_z")
        self._register_default(self.axis_x, False)
        self._register_default(self.axis_y, True)
        self._register_default(self.axis_z, False)

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
        self.rad_dir_radial.setProperty("settings_key", "radial_dir_radial")
        self.rad_dir_axis.setProperty("settings_key", "radial_dir_axis")
        self._register_default(self.rad_dir_radial, True)
        self._register_default(self.rad_dir_axis, False)
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
        self.copies_x_spin.setProperty("settings_key", "copies_x")
        self._register_default(self.copies_x_spin, 3)
        self.copies_y_slider, self.copies_y_spin = self._add_slider(rect_layout, "Copies Y", 1, 30, 1, 0)
        self.copies_y_spin.setProperty("settings_key", "copies_y")
        self._register_default(self.copies_y_spin, 1)
        self.copies_z_slider, self.copies_z_spin = self._add_slider(rect_layout, "Copies Z", 1, 30, 1, 0)
        self.copies_z_spin.setProperty("settings_key", "copies_z")
        self._register_default(self.copies_z_spin, 1)
        self.rect_repeat_slider, self.rect_repeat_spin = self._add_slider(rect_layout, "Repeat", 1, 10, 1, 0)
        self.rect_repeat_spin.setProperty("settings_key", "rect_repeat")
        self._register_default(self.rect_repeat_spin, 1)
        self.rect_repeat_dist_slider, self.rect_repeat_dist_spin = self._add_slider(rect_layout, "Repeat Distance", 0.0, 100.0, 1.0, 3)
        self.rect_repeat_dist_spin.setProperty("settings_key", "rect_repeat_distance")
        self._register_default(self.rect_repeat_dist_spin, 1.0)

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
        self.rect_dir_end.setProperty("settings_key", "rect_dir_end")
        self.rect_dir_x.setProperty("settings_key", "rect_dir_x")
        self.rect_dir_y.setProperty("settings_key", "rect_dir_y")
        self.rect_dir_z.setProperty("settings_key", "rect_dir_z")
        self._register_default(self.rect_dir_end, False)
        self._register_default(self.rect_dir_x, True)
        self._register_default(self.rect_dir_y, False)
        self._register_default(self.rect_dir_z, False)

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
        self.chk_instance.setProperty("settings_key", "use_instance")
        self._register_default(self.chk_instance, True)
        layout.addWidget(self.chk_instance)

        self.chk_group = QtWidgets.QCheckBox("Group Result")
        self.chk_group.setChecked(True)
        self.chk_group.setProperty("settings_key", "group_result")
        self._register_default(self.chk_group, True)
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
        self.btn_reset_center.setText("\u2295")

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
            # Use bbox center as default locator pivot for radial mode.
            # This avoids the common "giga offset" when mesh pivots are far away.
            pivot = _PROARRAY_STATE["original_bbox_centers"][valid_objects[0]]
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
        if self._started and not _PROARRAY_STATE.get("is_processing"):
            self._rebuild_timer.start()

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

    def _on_reset_center(self):
        if not self._started:
            return
        loc = _PROARRAY_STATE.get("locator")
        if not _safe_exists(loc):
            return

        center = proarray_get_combined_bbox_center()
        cmds.xform(loc, ws=True, t=center)
        self._last_locator_pos = center[:]
        self._do_rebuild()
        self.status_label.setText("Locator centered on objects bbox")

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
        self._settings_defaults = []

        self._rebuild_timer = QtCore.QTimer()
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(20)
        self._rebuild_timer.timeout.connect(self._do_rebuild)

        _CURVE_STATE["ui_instance"] = self

        self._build_ui()
        self._setup_icons()

    def _register_default(self, widget, value):
        self._settings_defaults.append((widget, value))

    def load_settings(self, settings):
        settings.beginGroup("curve")
        count_max = int(float(settings.value("count_max", 30)))
        self._set_count_max(count_max)
        for widget, default_value in self._settings_defaults:
            key = widget.property("settings_key")
            if not key:
                continue
            raw = settings.value(key, default_value)
            if isinstance(widget, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
                widget.setValue(float(raw) if isinstance(widget, QtWidgets.QDoubleSpinBox) else int(float(raw)))
            elif isinstance(widget, (QtWidgets.QCheckBox, QtWidgets.QRadioButton)):
                if isinstance(raw, bool):
                    widget.setChecked(raw)
                else:
                    widget.setChecked(str(raw).lower() in ("1", "true", "yes"))
        settings.endGroup()
        self._sync_count_preset_from_range()

    def save_settings(self, settings):
        settings.beginGroup("curve")
        settings.setValue("count_max", int(self.count_spin.maximum()))
        for widget, _ in self._settings_defaults:
            key = widget.property("settings_key")
            if not key:
                continue
            if isinstance(widget, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
                settings.setValue(key, widget.value())
            elif isinstance(widget, (QtWidgets.QCheckBox, QtWidgets.QRadioButton)):
                settings.setValue(key, widget.isChecked())
        settings.endGroup()

    def reset_settings(self):
        for widget, default_value in self._settings_defaults:
            if isinstance(widget, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
                widget.setValue(default_value)
            elif isinstance(widget, (QtWidgets.QCheckBox, QtWidgets.QRadioButton)):
                widget.setChecked(default_value)
        self._set_count_max(30)

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

        self.curve_length_label = QtWidgets.QLabel("Length Total : -")
        layout.addWidget(self.curve_length_label)

        layout.addSpacing(2)

        dist_label = QtWidgets.QLabel("DISTRIBUTION")
        dist_label.setObjectName("sectionLabel")
        layout.addWidget(dist_label)

        self.count_slider, self.count_spin = self._add_slider(layout, "Count", 1, 1000, 10, 0, label_width=110)
        self.count_spin.setProperty("settings_key", "count")
        self._register_default(self.count_spin, 10)
        self._add_count_range_controls(layout)
        self.start_slider, self.start_spin = self._add_slider(layout, "Start", 0.0, 1.0, 0.0, 3, label_width=110)
        self.start_spin.setProperty("settings_key", "start")
        self._register_default(self.start_spin, 0.0)
        self.end_slider, self.end_spin = self._add_slider(layout, "End", 0.0, 1.0, 1.0, 3, label_width=110)
        self.end_spin.setProperty("settings_key", "end")
        self._register_default(self.end_spin, 1.0)

        self.chk_auto_fit = QtWidgets.QCheckBox("Auto Fit Count")
        self.chk_auto_fit.setChecked(False)
        self.chk_auto_fit.toggled.connect(self._on_auto_fit_toggled)
        self.chk_auto_fit.setProperty("settings_key", "auto_fit")
        self._register_default(self.chk_auto_fit, False)
        layout.addWidget(self.chk_auto_fit)

        self.chk_lock_no_overlap = QtWidgets.QCheckBox("Lock to Non-Overlap Count")
        self.chk_lock_no_overlap.setChecked(True)
        self.chk_lock_no_overlap.toggled.connect(self._on_rebuild)
        self.chk_lock_no_overlap.setProperty("settings_key", "lock_no_overlap")
        self._register_default(self.chk_lock_no_overlap, True)
        layout.addWidget(self.chk_lock_no_overlap)

        self.chk_trim_ends = QtWidgets.QCheckBox("Trim Ends")
        self.chk_trim_ends.setChecked(True)
        self.chk_trim_ends.toggled.connect(self._on_rebuild)
        self.chk_trim_ends.setProperty("settings_key", "trim_ends")
        self._register_default(self.chk_trim_ends, True)
        layout.addWidget(self.chk_trim_ends)

        self.padding_slider, self.padding_spin = self._add_slider(layout, "Padding", -10.0, 50.0, 0.0, 3, label_width=110)
        self.safety_slider, self.safety_spin = self._add_slider(layout, "Fit Safety", 0.5, 3.0, 1.05, 3, label_width=110)
        self.auto_max_slider, self.auto_max_spin = self._add_slider(layout, "Max Count", 1, 5000, 1000, 0, label_width=110)

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
        self._add_quick_rotation_buttons(layout)

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

        orient_mode_row = QtWidgets.QHBoxLayout()
        orient_mode_row.setSpacing(8)

        orient_mode_lbl = QtWidgets.QLabel("Orient Mode")
        orient_mode_lbl.setFixedWidth(110)
        orient_mode_row.addWidget(orient_mode_lbl)

        self.orient_mode_world_up = QtWidgets.QRadioButton("Tangent + WorldUp")
        self.orient_mode_world_up.setChecked(True)
        self.orient_mode_curve_normal = QtWidgets.QRadioButton("Tangent + Curve Normal")

        self.orient_mode_world_up.toggled.connect(self._on_rebuild)
        self.orient_mode_curve_normal.toggled.connect(self._on_rebuild)

        orient_mode_row.addWidget(self.orient_mode_world_up)
        orient_mode_row.addWidget(self.orient_mode_curve_normal)
        orient_mode_row.addStretch()
        layout.addLayout(orient_mode_row)

        self.chk_center_bbox = QtWidgets.QCheckBox("Center on Mesh Bounding Box (can offset if pivot is far)")
        self.chk_center_bbox.setChecked(False)
        self.chk_center_bbox.toggled.connect(self._on_rebuild)
        layout.addWidget(self.chk_center_bbox)

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
        self._register_persisted_controls()
        self._on_auto_fit_toggled()

    def _register_persisted_controls(self):
        controls = [
            (self.padding_spin, "padding", 0.0),
            (self.safety_spin, "fit_safety", 1.05),
            (self.auto_max_spin, "auto_max", 1000),
            (self.offx_spin, "off_x", 0.0),
            (self.offy_spin, "off_y", 0.0),
            (self.offz_spin, "off_z", 0.0),
            (self.rotx_spin, "rot_x", 0.0),
            (self.roty_spin, "rot_y", 0.0),
            (self.rotz_spin, "rot_z", 0.0),
            (self.rand_rotx_spin, "rand_rot_x", 0.0),
            (self.rand_roty_spin, "rand_rot_y", 0.0),
            (self.rand_rotz_spin, "rand_rot_z", 0.0),
            (self.scale_spin, "scale", 1.0),
            (self.rand_scale_spin, "rand_scale", 0.0),
            (self.seed_spin, "seed", 1),
            (self.chk_orient, "orient", True),
            (self.orient_mode_world_up, "orient_world_up", True),
            (self.orient_mode_curve_normal, "orient_curve_normal", False),
            (self.chk_center_bbox, "center_bbox", False),
            (self.chk_instance, "use_instance", True),
            (self.chk_group, "group_result", True),
            (self.fit_axis_x, "fit_axis_x", False),
            (self.fit_axis_y, "fit_axis_y", False),
            (self.fit_axis_z, "fit_axis_z", False),
            (self.fit_axis_longest, "fit_axis_longest", True),
        ]
        for widget, key, default_value in controls:
            widget.setProperty("settings_key", key)
            self._register_default(widget, default_value)

    def _set_count_max(self, max_value):
        max_value = int(clamp(max_value, 10, 5000))
        self.count_spin.setMaximum(max_value)
        current = int(self.count_spin.value())
        if current > max_value:
            self.count_spin.setValue(max_value)

    def _sync_count_preset_from_range(self):
        max_v = int(self.count_spin.maximum())
        if max_v <= 30:
            self.count_preset_combo.setCurrentText("0-30")
        elif max_v <= 100:
            self.count_preset_combo.setCurrentText("0-100")
        elif max_v <= 500:
            self.count_preset_combo.setCurrentText("0-500")
        else:
            self.count_preset_combo.setCurrentText("0-1000+")

    def _change_count_range(self, delta):
        step = 10 if self.count_spin.maximum() < 100 else 50
        self._set_count_max(self.count_spin.maximum() + (delta * step))
        self._sync_count_preset_from_range()
        self._on_rebuild()

    def _on_count_preset_changed(self, text):
        mapping = {"0-30": 30, "0-100": 100, "0-500": 500, "0-1000+": 1000}
        self._set_count_max(mapping.get(text, 1000))
        self._on_rebuild()

    def _add_count_range_controls(self, parent_layout):
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        row.setContentsMargins(0, 0, 0, 0)

        lbl = QtWidgets.QLabel("Count Range")
        lbl.setFixedWidth(110)
        row.addWidget(lbl)

        self.count_preset_combo = QtWidgets.QComboBox()
        self.count_preset_combo.addItems(["0-30", "0-100", "0-500", "0-1000+"])
        self.count_preset_combo.setCurrentIndex(0)
        self.count_preset_combo.currentTextChanged.connect(self._on_count_preset_changed)
        row.addWidget(self.count_preset_combo)

        btn_minus = QtWidgets.QPushButton("-")
        btn_minus.setToolTip("Reduce count range")
        btn_minus.setFixedSize(22, 20)
        btn_minus.clicked.connect(lambda: self._change_count_range(-1))
        row.addWidget(btn_minus)

        btn_plus = QtWidgets.QPushButton("+")
        btn_plus.setToolTip("Increase count range")
        btn_plus.setFixedSize(22, 20)
        btn_plus.clicked.connect(lambda: self._change_count_range(1))
        row.addWidget(btn_plus)

        row.addStretch()
        parent_layout.addLayout(row)

    def _add_quick_rotation_buttons(self, parent_layout):
        container = QtWidgets.QHBoxLayout()
        container.setSpacing(4)

        label = QtWidgets.QLabel("Quick Rot")
        label.setFixedWidth(110)
        container.addWidget(label)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)

        buttons = [
            ("X+45", self.rotx_spin, 45.0, 0, 0),
            ("X+90", self.rotx_spin, 90.0, 0, 1),
            ("Y+45", self.roty_spin, 45.0, 0, 2),
            ("Y+90", self.roty_spin, 90.0, 0, 3),
            ("Z+45", self.rotz_spin, 45.0, 1, 0),
            ("Z+90", self.rotz_spin, 90.0, 1, 1),
            ("Reset", None, 0.0, 1, 2),
        ]

        for text, spin, delta, r, c in buttons:
            btn = QtWidgets.QPushButton(text)
            btn.setFixedHeight(20)
            btn.setMinimumWidth(44)
            if text.startswith("X"):
                btn.setStyleSheet("background-color: %s;" % AXIS_X_COLOR)
            elif text.startswith("Y"):
                btn.setStyleSheet("background-color: %s;" % AXIS_Y_COLOR)
            elif text.startswith("Z"):
                btn.setStyleSheet("background-color: %s;" % AXIS_Z_COLOR)
            if spin is None:
                btn.clicked.connect(self._reset_rotation_offsets)
            else:
                btn.clicked.connect(lambda _, s=spin, d=delta: self._increment_rotation(s, d))
            grid.addWidget(btn, r, c)

        container.addLayout(grid)
        container.addStretch()
        parent_layout.addLayout(container)

    def _increment_rotation(self, spinbox, delta):
        new_value = clamp(spinbox.value() + delta, spinbox.minimum(), spinbox.maximum())
        spinbox.setValue(new_value)

    def _reset_rotation_offsets(self):
        self.rotx_spin.setValue(0.0)
        self.roty_spin.setValue(0.0)
        self.rotz_spin.setValue(0.0)

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
        curves = _CURVE_STATE.get("curves", [])

        self.mesh_label.setText("Mesh : {}".format(mesh.split("|")[-1] if mesh else "-"))
        if curves:
            if len(curves) == 1:
                self.curve_label.setText("Curve : {}".format(curves[0].split("|")[-1]))
            else:
                self.curve_label.setText("Curves : {} selected".format(len(curves)))
        else:
            self.curve_label.setText("Curve : -")

        if curves:
            total_length = sum(get_curve_length(c) for c in curves if _safe_exists(c))
            self.curve_length_label.setText("Length Total : {:.3f}".format(total_length))
        else:
            self.curve_length_label.setText("Length Total : -")

    def _setup_live_callbacks(self):
        for job_id in _CURVE_STATE.get("script_jobs", []):
            try:
                if cmds.scriptJob(exists=job_id):
                    cmds.scriptJob(kill=job_id, force=True)
            except:
                pass
        _CURVE_STATE["script_jobs"] = []

        mesh = _CURVE_STATE.get("mesh")
        curves = _CURVE_STATE.get("curves", [])
        ui = _CURVE_STATE.get("ui_instance")
        if not ui:
            return

        if mesh and _safe_exists(mesh):
            for shape in cmds.listRelatives(mesh, shapes=True, type="mesh") or []:
                for attr in (".outMesh", ".worldMesh"):
                    try:
                        job_id = cmds.scriptJob(
                            attributeChange=[shape + attr, ui._on_rebuild],
                            killWithScene=True
                        )
                        _CURVE_STATE["script_jobs"].append(job_id)
                    except:
                        pass

        for curve in curves:
            if not _safe_exists(curve):
                continue
            for shape in cmds.listRelatives(curve, shapes=True, type="nurbsCurve") or []:
                try:
                    job_id = cmds.scriptJob(
                        attributeChange=[shape + ".worldSpace", ui._on_rebuild],
                        killWithScene=True
                    )
                    _CURVE_STATE["script_jobs"].append(job_id)
                except:
                    pass

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
        self.auto_max_spin.setEnabled(is_auto)
        self.auto_max_slider.setEnabled(is_auto)
        self._on_rebuild()
        self._request_parent_resize()

    def _on_start(self):
        selection = get_transform_from_selection()

        if len(selection) < 2:
            cmds.warning("Sélectionne d'abord ton mesh puis une ou plusieurs curves.")
            self.status_label.setText("Select mesh then curve(s)")
            return

        mesh = selection[0]
        curves = [obj for obj in selection[1:] if is_curve_transform(obj)]

        if not is_mesh_transform(mesh):
            cmds.warning("Le premier objet sélectionné doit être un mesh transform.")
            self.status_label.setText("First selection must be a mesh")
            return

        if not curves:
            cmds.warning("Après le mesh, sélectionne au moins une NURBS curve transform.")
            self.status_label.setText("Need at least one curve")
            return

        _CURVE_STATE["mesh"] = mesh
        _CURVE_STATE["curves"] = curves
        _CURVE_STATE["started"] = True
        _CURVE_STATE["baked"] = False
        self._setup_live_callbacks()

        self._update_start_button()
        self._update_info_labels()
        self.select_frame.setVisible(True)
        self.status_label.setText("Started - {} curve(s)".format(len(curves)))

        self._do_rebuild()
        self._request_parent_resize()

    def _do_rebuild(self):
        if not _CURVE_STATE.get("started"):
            return

        mesh = _CURVE_STATE.get("mesh")
        curves = _CURVE_STATE.get("curves", [])

        valid_curves = [c for c in curves if _safe_exists(c)]
        if not _safe_exists(mesh) or not valid_curves:
            self.status_label.setText("Mesh or curve(s) missing")
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
            fit_safety = float(self.safety_spin.value())
            max_count = int(self.auto_max_spin.value())
            trim_ends = self.chk_trim_ends.isChecked()
            auto_fit = self.chk_auto_fit.isChecked()
            lock_no_overlap = self.chk_lock_no_overlap.isChecked()

            safe_count_limit = compute_auto_fit_count(
                mesh=mesh,
                curve=valid_curves[0],
                start_u=start_u,
                end_u=end_u,
                axis_mode=fit_axis,
                padding=padding,
                base_scale=base_scale,
                random_scale=rand_scale,
                trim_ends=trim_ends,
                safety_multiplier=fit_safety
            )

            if auto_fit:
                count = compute_auto_fit_count(
                    mesh=mesh,
                    curve=valid_curves[0],
                    start_u=start_u,
                    end_u=end_u,
                    axis_mode=fit_axis,
                    padding=padding,
                    base_scale=base_scale,
                    random_scale=rand_scale,
                    trim_ends=trim_ends,
                    safety_multiplier=fit_safety,
                    max_count=max_count
                )
                self.count_spin.blockSignals(True)
                self.count_spin.setValue(count)
                self.count_spin.blockSignals(False)
            else:
                count = int(self.count_spin.value())
                if lock_no_overlap:
                    count = min(count, safe_count_limit)
                    self.count_spin.blockSignals(True)
                    self.count_spin.setValue(count)
                    self.count_spin.blockSignals(False)

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
            orient_mode = "curve_normal" if self.orient_mode_curve_normal.isChecked() else "world_up"
            center_on_bbox = self.chk_center_bbox.isChecked()
            use_instance = self.chk_instance.isChecked()
            random_seed = int(self.seed_spin.value())

            all_results = []
            usable_len = 0.0
            for idx, curve in enumerate(valid_curves):
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
                    random_seed=random_seed + (idx * 97),
                    trim_ends=trim_ends,
                    fit_axis_mode=fit_axis,
                    padding=padding,
                    auto_fit=auto_fit,
                    safety_multiplier=fit_safety,
                    max_count=max_count if auto_fit else None,
                    clear_existing=(idx == 0),
                    name_prefix="c{}__".format(idx + 1),
                    orient_mode=orient_mode,
                    center_on_bbox=center_on_bbox
                )
                all_results.extend(result)
                usable_len += get_curve_length(curve) * max(0.0, (end_u - start_u))

            step_est = estimate_mesh_spacing(
                mesh,
                axis_mode=fit_axis,
                extra_padding=padding,
                base_scale=base_scale,
                random_scale=rand_scale
            ) * max(0.01, fit_safety)

            if auto_fit:
                self.status_label.setText(
                        "{} preview | usable {:.3f} | step {:.3f} | max {}".format(
                        len(all_results), usable_len, step_est, max_count
                    )
                )
            elif lock_no_overlap:
                self.status_label.setText(
                        "{} preview | usable {:.3f} | safe max {}".format(
                        len(all_results), usable_len, safe_count_limit
                    )
                )
            else:
                self.status_label.setText(
                    "{} preview | usable {:.3f}".format(len(all_results), usable_len)
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
        curves = [c for c in _CURVE_STATE.get("curves", []) if _safe_exists(c)]
        if curves:
            cmds.select(curves, r=True)

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
        self.curve_length_label.setText("Length Total : -")
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
        self._settings = QtCore.QSettings(SETTINGS_ORG, SETTINGS_APP)

        self._build_ui()
        apply_shared_style(self)
        self._load_settings()

        self.tabs.setCurrentIndex(0)

        QtCore.QTimer.singleShot(0, lambda: self._apply_tab_size(0, force=True))

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        top_row = QtWidgets.QHBoxLayout()
        top_row.addStretch()
        self.btn_reset_settings = QtWidgets.QPushButton("Reset All Settings")
        self.btn_reset_settings.setToolTip("Reset ProArray + Curve settings and clear saved preferences")
        self.btn_reset_settings.setFixedHeight(22)
        self.btn_reset_settings.clicked.connect(self._on_reset_all_settings)
        top_row.addWidget(self.btn_reset_settings)
        layout.addLayout(top_row)

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

    def _load_settings(self):
        self.tab_proarray.load_settings(self._settings)
        self.tab_curve.load_settings(self._settings)
        tab_index = int(float(self._settings.value("ui/current_tab", 0)))
        self.tabs.setCurrentIndex(clamp(tab_index, 0, self.tabs.count() - 1))

    def _save_settings(self):
        self._settings.setValue("ui/current_tab", self.tabs.currentIndex())
        self.tab_proarray.save_settings(self._settings)
        self.tab_curve.save_settings(self._settings)
        self._settings.sync()

    def _on_reset_all_settings(self):
        self._settings.clear()
        self.tab_proarray.reset_settings()
        self.tab_curve.reset_settings()
        self.request_resize()

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
        self._save_settings()
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
