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
AXIS_TOGGLE_OFF_COLOR = "#4a4a4a"
AXIS_TOGGLE_ON_COLOR = "#d1a91f"

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
    "meshes": [],
    "temp_curves": [],
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


def get_dag_path(node):
    """
    Return an MDagPath from a Maya node name (transform or shape).
    Returns None when the node cannot be resolved.
    """
    if not _safe_exists(node):
        return None

    try:
        sel = om.MSelectionList()
        sel.add(node)
        dag_path = sel.getDagPath(0)
        return dag_path
    except:
        return None


def _get_isolated_model_panels():
    """
    Return modelPanel names where isolate select / viewSelected is enabled.
    """
    panels = cmds.getPanel(type="modelPanel") or []
    isolated_panels = []
    for panel in panels:
        try:
            if cmds.modelEditor(panel, exists=True) and cmds.modelEditor(panel, q=True, viewSelected=True):
                isolated_panels.append(panel)
        except:
            pass
    return isolated_panels


def add_nodes_to_active_isolate_sets(nodes):
    """
    Add nodes to isolate-select sets of active model panels.
    Safe no-op if no model panel is in isolate mode.
    """
    valid_nodes = [n for n in (nodes or []) if _safe_exists(n)]
    if not valid_nodes:
        return

    for panel in _get_isolated_model_panels():
        for node in valid_nodes:
            try:
                cmds.isolateSelect(panel, addDagObject=node)
            except:
                pass


def clamp(v, vmin, vmax):
    return max(vmin, min(vmax, v))


def parse_float_flexible(value, default=0.0):
    if value is None:
        return default
    try:
        return float(value)
    except:
        try:
            text = str(value).strip().replace(",", ".")
            return float(text)
        except:
            return default


def parse_int_flexible(value, default=0):
    return int(round(parse_float_flexible(value, default)))


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
            background-color: #4f5f4f;
            color: #ffffff;
            border: 1px solid #607260;
            font-weight: 600;
        }}

        QPushButton#bakeBtn:hover {{
            background-color: #5b705b;
        }}

        QPushButton#selectMeshBtn {{
            background-color: #3b3b3b;
            border: 1px solid #545454;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 0.2px;
        }}

        QPushButton#selectMeshBtn:hover {{
            background-color: #464646;
        }}

        QPushButton#selectLocatorBtn, QPushButton#selectCurveBtn {{
            background-color: #3b3b3b;
            border: 1px solid #545454;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 0.2px;
        }}

        QPushButton#selectLocatorBtn:hover, QPushButton#selectCurveBtn:hover {{
            background-color: #464646;
        }}

        QPushButton#refreshBtn {{
            background-color: #3b3b3b;
            border: 1px solid #545454;
            border-radius: 4px;
            font-size: 13px;
            font-weight: 600;
        }}

        QPushButton#refreshBtn:hover {{
            background-color: #464646;
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


class FlexibleDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    def __init__(self, parent=None):
        super(FlexibleDoubleSpinBox, self).__init__(parent)
        self.setLocale(QtCore.QLocale.c())

    def _normalize_text(self, text):
        return str(text).strip().replace(",", ".")

    def valueFromText(self, text):
        normalized = self._normalize_text(text)
        return parse_float_flexible(normalized, self.value())

    def validate(self, text, pos):
        normalized = self._normalize_text(text)
        if normalized in ("", "-", "+", ".", "-.", "+."):
            return (QtGui.QValidator.Intermediate, text, pos)
        try:
            float(normalized)
            return (QtGui.QValidator.Acceptable, text, pos)
        except:
            return (QtGui.QValidator.Invalid, text, pos)

    def fixup(self, text):
        return self._normalize_text(text)


class XYZSliderWidget(QtWidgets.QWidget):
    def __init__(self, parent, title, min_val, max_val, default, decimals=3, axis_colors=None):
        super(XYZSliderWidget, self).__init__(parent)
        self._title = title
        self._axis_order = ("x", "y", "z")
        self._active_axes = set()
        self._axis_buttons = {}
        self._axis_values = {"x": float(default), "y": float(default), "z": float(default)}
        self._on_change_callback = None

        if axis_colors is None:
            axis_colors = {"x": AXIS_TOGGLE_OFF_COLOR, "y": AXIS_TOGGLE_OFF_COLOR, "z": AXIS_TOGGLE_OFF_COLOR}

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)

        top_row = QtWidgets.QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(4)

        self.label = QtWidgets.QLabel(title)
        self.label.setFixedWidth(110)
        top_row.addWidget(self.label)

        for axis in self._axis_order:
            btn = QtWidgets.QPushButton(axis.upper())
            btn.setCheckable(True)
            btn.setFixedSize(22, 16)
            off_color = axis_colors.get(axis, AXIS_TOGGLE_OFF_COLOR)
            btn.setStyleSheet(
                "QPushButton { background-color: %s; font-size: 10px; font-weight: bold; } "
                "QPushButton:checked { background-color: %s; color: #101010; font-weight: bold; }"
                % (off_color, AXIS_TOGGLE_ON_COLOR)
            )
            btn.toggled.connect(lambda state, a=axis: self._on_axis_toggled(a, state))
            top_row.addWidget(btn)
            self._axis_buttons[axis] = btn

        self.reset_btn = QtWidgets.QPushButton("R")
        self.reset_btn.setToolTip("Reset XYZ values")
        self.reset_btn.setFixedSize(18, 16)
        self.reset_btn.setStyleSheet("font-size: 9px; padding: 0px;")
        self.reset_btn.clicked.connect(self.reset)
        top_row.addWidget(self.reset_btn)

        top_row.addStretch()
        root.addLayout(top_row)

        bottom_row = QtWidgets.QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(6)
        bottom_row.addSpacing(110)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(1000)
        bottom_row.addWidget(self.slider)

        if decimals > 0:
            spin = FlexibleDoubleSpinBox()
            spin.setDecimals(decimals)
            spin.setSingleStep((max_val - min_val) / 1000.0 if max_val > min_val else 0.01)
        else:
            spin = QtWidgets.QSpinBox()
        self.spinbox = spin
        self.spinbox.setMinimum(min_val if decimals > 0 else int(min_val))
        self.spinbox.setMaximum(max_val if decimals > 0 else int(max_val))
        self.spinbox.setValue(default if decimals > 0 else int(default))
        self.spinbox.setFixedWidth(82)
        self.spinbox.setFixedHeight(18)
        self.spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        bottom_row.addWidget(self.spinbox)
        root.addLayout(bottom_row)

        self._min_val = float(min_val)
        self._max_val = float(max_val)
        self._default = float(default)
        self._decimals = int(decimals)

        self.slider.valueChanged.connect(self._on_slider_changed)
        self.spinbox.valueChanged.connect(self._on_spinbox_changed)
        self._set_slider_from_value(default)

    def set_on_change_callback(self, fn):
        self._on_change_callback = fn

    def active_axes(self):
        return set(self._active_axes)

    def get_vector_values(self):
        return (
            float(self._axis_values["x"]),
            float(self._axis_values["y"]),
            float(self._axis_values["z"]),
        )

    def set_axis_value(self, axis, value):
        axis = str(axis).lower()
        if axis not in self._axis_values:
            return
        clamped = clamp(float(value), self._min_val, self._max_val)
        self._axis_values[axis] = clamped
        active = self._active_axes or set(self._axis_order)
        if axis in active:
            self.spinbox.blockSignals(True)
            self.spinbox.setValue(clamped if self._decimals > 0 else int(round(clamped)))
            self.spinbox.blockSignals(False)
            self._set_slider_from_value(clamped)
        self._emit_change()

    def reset(self):
        for axis in self._axis_order:
            self._axis_values[axis] = self._default
        self.spinbox.setValue(self._default if self._decimals > 0 else int(self._default))

    def _emit_change(self):
        if callable(self._on_change_callback):
            self._on_change_callback()

    def _set_slider_from_value(self, value):
        ratio = (float(value) - self._min_val) / (self._max_val - self._min_val) if (self._max_val - self._min_val) > 0 else 0.0
        self.slider.blockSignals(True)
        self.slider.setValue(int(clamp(ratio, 0.0, 1.0) * 1000.0))
        self.slider.blockSignals(False)

    def _on_axis_toggled(self, axis, checked):
        if checked:
            self._active_axes.add(axis)
            ref_value = self._axis_values.get(axis, self._default)
            self.spinbox.blockSignals(True)
            self.spinbox.setValue(ref_value if self._decimals > 0 else int(round(ref_value)))
            self.spinbox.blockSignals(False)
            self._set_slider_from_value(ref_value)
        else:
            self._active_axes.discard(axis)
        self._emit_change()

    def _on_slider_changed(self, slider_value):
        ratio = float(slider_value) / 1000.0
        real_val = self._min_val + ((self._max_val - self._min_val) * ratio)
        self.spinbox.blockSignals(True)
        self.spinbox.setValue(real_val if self._decimals > 0 else int(round(real_val)))
        self.spinbox.blockSignals(False)
        self._apply_value_to_axes(real_val)

    def _on_spinbox_changed(self, spin_val):
        real_val = clamp(float(spin_val), self._min_val, self._max_val)
        self._set_slider_from_value(real_val)
        self._apply_value_to_axes(real_val)

    def _apply_value_to_axes(self, value):
        targets = self._active_axes or set(self._axis_order)
        for axis in targets:
            self._axis_values[axis] = float(value)
        self._emit_change()


class SliderMixin(object):
    _CTRL_DRAG_PIXELS_PER_STEP = 8.0

    def _ensure_slider_mixin_state(self):
        if not hasattr(self, "_ctrl_drag_state"):
            self._ctrl_drag_state = {
                "active": False,
                "spinbox": None,
                "start_global_x": 0,
                "start_value": 0.0,
            }

    def _step_spinbox_value(self, spinbox, direction):
        step = spinbox.singleStep() if isinstance(spinbox, QtWidgets.QDoubleSpinBox) else 1
        new_value = spinbox.value() + (step * direction)
        spinbox.setValue(clamp(new_value, spinbox.minimum(), spinbox.maximum()))

    def _attach_spinbox_drag(self, spinbox):
        self._ensure_slider_mixin_state()
        editor = spinbox.lineEdit()
        if not editor:
            return
        editor.installEventFilter(self)
        editor.setProperty("ctrl_drag_spinbox", spinbox)

    def eventFilter(self, watched, event):
        self._ensure_slider_mixin_state()
        spinbox = watched.property("ctrl_drag_spinbox") if watched else None
        if spinbox is None:
            return super(SliderMixin, self).eventFilter(watched, event)

        event_type = event.type()
        if event_type == QtCore.QEvent.MouseButtonPress:
            if event.button() == QtCore.Qt.LeftButton and (event.modifiers() & QtCore.Qt.ControlModifier):
                self._ctrl_drag_state["active"] = True
                self._ctrl_drag_state["spinbox"] = spinbox
                self._ctrl_drag_state["start_global_x"] = event.globalPosition().x() if hasattr(event, "globalPosition") else event.globalX()
                self._ctrl_drag_state["start_value"] = float(spinbox.value())
                watched.setCursor(QtCore.Qt.SizeHorCursor)
                return True

        if event_type == QtCore.QEvent.MouseMove and self._ctrl_drag_state["active"]:
            active_spinbox = self._ctrl_drag_state.get("spinbox")
            if active_spinbox is not spinbox:
                return True
            current_x = event.globalPosition().x() if hasattr(event, "globalPosition") else event.globalX()
            delta_px = current_x - self._ctrl_drag_state["start_global_x"]
            drag_ratio = float(delta_px) / self._CTRL_DRAG_PIXELS_PER_STEP
            step = active_spinbox.singleStep() if isinstance(active_spinbox, QtWidgets.QDoubleSpinBox) else 1.0
            if event.modifiers() & QtCore.Qt.ShiftModifier:
                step *= 0.1
            new_value = self._ctrl_drag_state["start_value"] + (drag_ratio * step)
            if isinstance(active_spinbox, QtWidgets.QSpinBox):
                new_value = int(round(new_value))
            active_spinbox.setValue(clamp(new_value, active_spinbox.minimum(), active_spinbox.maximum()))
            return True

        if event_type == QtCore.QEvent.MouseButtonRelease and self._ctrl_drag_state["active"]:
            self._ctrl_drag_state["active"] = False
            self._ctrl_drag_state["spinbox"] = None
            watched.unsetCursor()
            return True

        return super(SliderMixin, self).eventFilter(watched, event)

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
            spinbox = FlexibleDoubleSpinBox()
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

        btn_minus = QtWidgets.QPushButton("-")
        btn_minus.setToolTip("Decrease value")
        btn_minus.setFixedSize(18, 18)
        btn_minus.setStyleSheet("font-size: 9px; padding: 0px;")
        btn_minus.clicked.connect(lambda: self._step_spinbox_value(spinbox, -1))
        row.addWidget(btn_minus)

        btn_plus = QtWidgets.QPushButton("+")
        btn_plus.setToolTip("Increase value")
        btn_plus.setFixedSize(18, 18)
        btn_plus.setStyleSheet("font-size: 9px; padding: 0px;")
        btn_plus.clicked.connect(lambda: self._step_spinbox_value(spinbox, 1))
        row.addWidget(btn_plus)

        reset_btn = QtWidgets.QPushButton("R")
        reset_btn.setToolTip("Reset to default")
        reset_btn.setFixedSize(18, 18)
        reset_btn.setStyleSheet("font-size: 9px; padding: 0px;")
        row.addWidget(reset_btn)

        def update_spinbox(val):
            ratio = val / 1000.0
            current_min = float(spinbox.minimum())
            current_max = float(spinbox.maximum())
            real_val = current_min + ratio * (current_max - current_min)
            spinbox.blockSignals(True)
            if decimals > 0:
                spinbox.setValue(real_val)
            else:
                spinbox.setValue(int(round(real_val)))
            spinbox.blockSignals(False)
            self._on_rebuild()

        def update_slider(val):
            current_min = float(spinbox.minimum())
            current_max = float(spinbox.maximum())
            clamped = max(current_min, min(current_max, float(val)))
            ratio = (clamped - current_min) / (current_max - current_min) if (current_max - current_min) > 0 else 0
            slider.blockSignals(True)
            slider.setValue(int(ratio * 1000))
            slider.blockSignals(False)
            self._on_rebuild()

        slider.valueChanged.connect(update_spinbox)
        spinbox.valueChanged.connect(update_slider)
        reset_btn.clicked.connect(lambda: spinbox.setValue(default if decimals > 0 else int(default)))

        ratio = (float(default) - min_val) / (max_val - min_val) if (max_val - min_val) > 0 else 0
        slider.setValue(int(ratio * 1000))
        self._attach_spinbox_drag(spinbox)

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
    add_nodes_to_active_isolate_sets(previews)
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
    add_nodes_to_active_isolate_sets(previews)
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
        add_nodes_to_active_isolate_sets([result])
        cmds.select(result)
    else:
        add_nodes_to_active_isolate_sets(final_objects)
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
    _CURVE_STATE["meshes"] = []
    _CURVE_STATE["curves"] = []
    for c in _CURVE_STATE.get("temp_curves", []):
        _safe_delete(c)
    _CURVE_STATE["temp_curves"] = []
    _CURVE_STATE["started"] = False
    _CURVE_STATE["baked"] = False
    _CURVE_STATE["is_processing"] = False


def get_transform_from_selection():
    return cmds.ls(sl=True, long=True, transforms=True) or []


def _unique_in_order(items):
    ordered = []
    seen = set()
    for item in items or []:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def extract_mesh_and_curve_transforms_from_selection(selection_items):
    """
    Resolve selected transforms, shapes, and components into unique mesh/curve transforms.
    Keeps user selection order when possible.
    """
    ordered_transforms = []

    for item in selection_items or []:
        root = item.split(".", 1)[0]
        if not _safe_exists(root):
            continue

        node_type = ""
        try:
            node_type = cmds.nodeType(root)
        except:
            pass

        transform = None
        if node_type == "transform":
            transform = root
        else:
            parents = cmds.listRelatives(root, parent=True, fullPath=True) or []
            if parents:
                transform = parents[0]

        if transform and _safe_exists(transform):
            ordered_transforms.append(transform)

    ordered_transforms = _unique_in_order(ordered_transforms)
    meshes = [obj for obj in ordered_transforms if is_mesh_transform(obj)]
    curves = [obj for obj in ordered_transforms if is_curve_transform(obj)]
    return meshes, curves


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


def set_nodes_hidden_in_outliner(nodes, hidden=True):
    """
    Toggle Maya's outliner-only visibility flag on transform nodes.
    Safe no-op when attribute or node is unavailable.
    """
    for node in (nodes or []):
        if not _safe_exists(node):
            continue
        try:
            if cmds.attributeQuery("hiddenInOutliner", node=node, exists=True):
                cmds.setAttr("{}.hiddenInOutliner".format(node), bool(hidden))
        except:
            pass


def set_nodes_viewport_visibility(nodes, visible=True):
    """
    Toggle transform visibility in viewport while keeping nodes in the outliner.
    """
    for node in (nodes or []):
        if not _safe_exists(node):
            continue
        try:
            if cmds.attributeQuery("visibility", node=node, exists=True):
                cmds.setAttr("{}.visibility".format(node), bool(visible))
        except:
            pass


def unlock_transform_channels(transform, attrs=("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz")):
    """
    Best-effort unlock of transform channels on preview objects.
    Returns attribute names that remain non-settable.
    """
    locked_or_blocked = []
    if not _safe_exists(transform):
        return locked_or_blocked

    for attr_name in attrs:
        plug = "{}.{}".format(transform, attr_name)
        try:
            cmds.setAttr(plug, lock=False, keyable=True, channelBox=True)
        except:
            pass
        try:
            if not cmds.getAttr(plug, settable=True):
                locked_or_blocked.append(attr_name)
        except:
            locked_or_blocked.append(attr_name)
    return locked_or_blocked


def apply_uniform_scale_safe(transform, value):
    """
    Best-effort absolute uniform scale set.
    """
    if not _safe_exists(transform):
        return False
    scale_val = max(0.001, float(value))
    for axis in ("sx", "sy", "sz"):
        try:
            cmds.setAttr("{}.{}".format(transform, axis), scale_val)
        except:
            pass
    try:
        current = cmds.getAttr("{}.scale".format(transform))[0]
        if all(abs(float(v) - scale_val) <= 1e-5 for v in current):
            return True
    except:
        pass

    try:
        cmds.xform(transform, objectSpace=True, scale=(scale_val, scale_val, scale_val))
        return True
    except:
        pass

    try:
        cmds.scale(scale_val, scale_val, scale_val, transform, r=False, os=True)
        return True
    except:
        return False


def _extract_edge_components(selection_items):
    edge_components = []
    for item in selection_items or []:
        if ".e[" in item and _safe_exists(item.split(".e[")[0]):
            edge_components.extend(cmds.ls(item, fl=True) or [])
    return edge_components


def _component_to_mesh_and_index(edge_component):
    try:
        mesh = edge_component.split(".e[", 1)[0]
        index = int(edge_component.split(".e[", 1)[1].rstrip("]"))
        return mesh, index
    except:
        return None, None


def _edge_loops_to_curves_from_selection():
    selection_items = cmds.ls(sl=True, long=True, fl=True) or []
    edge_components = _extract_edge_components(selection_items)
    if not edge_components:
        return [], []

    curves = []
    warnings = []
    processed = set()
    original_selection = cmds.ls(sl=True, long=True) or []

    try:
        for edge in edge_components:
            mesh, edge_idx = _component_to_mesh_and_index(edge)
            if not mesh or edge_idx is None:
                continue
            edge_key = "{}.e[{}]".format(mesh, edge_idx)
            if edge_key in processed:
                continue

            loop_indices = []
            try:
                loop_indices = cmds.polySelect(mesh, edgeLoop=edge_idx) or []
            except:
                loop_indices = []

            if isinstance(loop_indices, int):
                loop_indices = [loop_indices]
            if not loop_indices:
                loop_indices = [edge_idx]

            loop_edges = ["{}.e[{}]".format(mesh, i) for i in loop_indices]
            for le in loop_edges:
                processed.add(le)

            try:
                cmds.select(loop_edges, r=True)
                curve_res = cmds.polyToCurve(form=2, degree=1, conformToSmoothMeshPreview=1, ch=False)
                curve_transform = curve_res[0] if isinstance(curve_res, (list, tuple)) else curve_res
                if curve_transform and _safe_exists(curve_transform) and is_curve_transform(curve_transform):
                    curves.append(curve_transform)
                else:
                    warnings.append("Failed to create curve from edge loop on {}".format(mesh.split("|")[-1]))
            except Exception as e:
                warnings.append("polyToCurve failed on {}: {}".format(mesh.split("|")[-1], str(e)))
    finally:
        if original_selection:
            cmds.select(original_selection, r=True)
        else:
            cmds.select(clear=True)

    unique_curves = []
    seen = set()
    for c in curves:
        if c not in seen:
            seen.add(c)
            unique_curves.append(c)
    return unique_curves, warnings


def _pick_mesh_for_sample(meshes, mode, step, seed, sample_index):
    if not meshes:
        return None
    if len(meshes) == 1:
        return meshes[0]

    safe_step = max(1, int(step))
    if mode == "random":
        rnd = random.Random((int(seed) * 1000033) + (sample_index * 15731))
        return meshes[rnd.randrange(len(meshes))]
    if mode == "every_n":
        block_index = (sample_index // safe_step) % len(meshes)
        return meshes[block_index]
    return meshes[sample_index % len(meshes)]


def estimate_mesh_spacing_multi(meshes, axis_mode="longest", extra_padding=0.0, base_scale=1.0, random_scale=0.0):
    valid_meshes = [m for m in (meshes or []) if _safe_exists(m)]
    if not valid_meshes:
        return 0.0001
    return max(
        estimate_mesh_spacing(m, axis_mode=axis_mode, extra_padding=extra_padding, base_scale=base_scale, random_scale=random_scale)
        for m in valid_meshes
    )


def compute_auto_fit_count_multi(meshes, curve, start_u, end_u,
                                 axis_mode="longest",
                                 padding=0.0,
                                 base_scale=1.0,
                                 random_scale=0.0,
                                 trim_ends=True,
                                 safety_multiplier=1.0,
                                 max_count=None):
    valid_meshes = [m for m in (meshes or []) if _safe_exists(m)]
    if not valid_meshes or not _safe_exists(curve):
        return 1
    worst_mesh = max(
        valid_meshes,
        key=lambda m: estimate_mesh_spacing(
            m,
            axis_mode=axis_mode,
            extra_padding=padding,
            base_scale=base_scale,
            random_scale=random_scale
        )
    )
    return compute_auto_fit_count(
        mesh=worst_mesh,
        curve=curve,
        start_u=start_u,
        end_u=end_u,
        axis_mode=axis_mode,
        padding=padding,
        base_scale=base_scale,
        random_scale=random_scale,
        trim_ends=trim_ends,
        safety_multiplier=safety_multiplier,
        max_count=max_count
    )


def get_curve_length(curve):
    if not _safe_exists(curve):
        return 0.0
    try:
        return cmds.arclen(curve)
    except:
        return 0.0


def _get_curve_fn_data(curve):
    dag_path = get_dag_path(curve)
    if not dag_path:
        return None
    try:
        fn_curve = om.MFnNurbsCurve(dag_path)
        param_min, param_max = fn_curve.knotDomain
        return fn_curve, param_min, param_max
    except:
        return None


def _curve_percent_to_length(curve, percent):
    curve_data = _get_curve_fn_data(curve)
    if not curve_data:
        return None
    fn_curve, param_min, param_max = curve_data
    try:
        percent = clamp(float(percent), 0.0, 1.0)
        param = param_min + ((param_max - param_min) * percent)
        return fn_curve.findLengthFromParam(param)
    except:
        return None


def _curve_length_to_percent(curve, length_value):
    curve_data = _get_curve_fn_data(curve)
    if not curve_data:
        return None
    fn_curve, param_min, param_max = curve_data
    domain = max(1e-8, param_max - param_min)
    try:
        curve_len = max(0.0, fn_curve.length())
        length_value = clamp(float(length_value), 0.0, curve_len)
        param = fn_curve.findParamFromLength(length_value)
        return clamp((param - param_min) / domain, 0.0, 1.0)
    except:
        return None


def compute_curve_u_samples(curve, count, start_u, end_u, even_by_length=True):
    count = max(0, int(count))
    if count <= 0:
        return []
    closed_curve = _is_closed_curve(curve)
    if count == 1:
        return [start_u if closed_curve else (start_u + end_u) * 0.5]

    if even_by_length:
        start_len = _curve_percent_to_length(curve, start_u)
        end_len = _curve_percent_to_length(curve, end_u)
        if start_len is not None and end_len is not None:
            result = []
            if closed_curve:
                total_len = max(0.0, get_curve_length(curve))
                span_len = end_len - start_len
                if abs(span_len) < 1e-8:
                    span_len = total_len
                step_len = span_len / float(count)
                for i in range(count):
                    sample_len = start_len + (step_len * i)
                    if total_len > 1e-8:
                        sample_len = sample_len % total_len
                    u = _curve_length_to_percent(curve, sample_len)
                    if u is None:
                        break
                    result.append(u)
            else:
                for i in range(count):
                    t = float(i) / float(count - 1)
                    sample_len = start_len + ((end_len - start_len) * t)
                    u = _curve_length_to_percent(curve, sample_len)
                    if u is None:
                        break
                    result.append(u)
            if len(result) == count:
                return result
            cmds.warning(
                "Even-by-length sampling fallback: '{}' has degenerate segment, using parametric spacing.".format(curve)
            )
        else:
            cmds.warning(
                "Even-by-length sampling unavailable for '{}', using parametric spacing.".format(curve)
            )

    if closed_curve:
        span = end_u - start_u
        if abs(span) < 1e-8:
            span = 1.0
        return [start_u + (span * (float(i) / float(count))) for i in range(count)]

    return [start_u + ((end_u - start_u) * (float(i) / float(count - 1))) for i in range(count)]


def _is_closed_curve(curve):
    data = _get_curve_fn_data(curve)
    if not data:
        return False
    fn_curve, _, _ = data
    try:
        if fn_curve.form in (om.MFnNurbsCurve.kClosed, om.MFnNurbsCurve.kPeriodic):
            return True
    except:
        pass
    try:
        p0 = fn_curve.cvPosition(0, om.MSpace.kWorld)
        p1 = fn_curve.cvPosition(fn_curve.numCVs - 1, om.MSpace.kWorld)
        return (p0 - p1).length() <= 1e-5
    except:
        return False


def compute_curve_count_allocation(curves, total_count, start_u, end_u):
    """
    Split a total instance count across multiple curves proportionally to usable length.
    Always returns one integer per input curve, summing exactly to total_count.
    """
    valid_curves = [c for c in (curves or []) if _safe_exists(c)]
    curve_count = len(valid_curves)
    total_count = max(0, int(total_count))
    if curve_count == 0:
        return []
    if total_count <= 0:
        return [0 for _ in valid_curves]

    usable_ratio = max(0.0, min(1.0, float(end_u)) - max(0.0, min(1.0, float(start_u))))
    lengths = [max(0.0, get_curve_length(c) * usable_ratio) for c in valid_curves]
    total_len = sum(lengths)

    if total_len <= 1e-8:
        base = total_count // curve_count
        remainder = total_count - (base * curve_count)
        alloc = [base for _ in valid_curves]
        for i in range(remainder):
            alloc[i] += 1
        return alloc

    raw = [(l / total_len) * total_count for l in lengths]
    alloc = [int(math.floor(v)) for v in raw]
    remainder = total_count - sum(alloc)

    if remainder > 0:
        frac_rank = sorted(
            [(raw[i] - alloc[i], i) for i in range(curve_count)],
            key=lambda item: item[0],
            reverse=True
        )
        for _, idx in frac_rank[:remainder]:
            alloc[idx] += 1
    return alloc


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
    is_closed_curve = _is_closed_curve(curve)

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
    elif is_closed_curve:
        count = int(math.floor(available_length / step))
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


def _create_centered_proxy_mesh(mesh, proxy_name="__curveScatter_proxy__"):
    """
    Duplicate a mesh transform and recenter its geometry so local bbox center
    sits on the transform pivot. This makes curve placement consistent even
    when source meshes have very different pivot placements/world offsets.
    """
    if not _safe_exists(mesh):
        return None

    try:
        proxy = cmds.duplicate(mesh, rr=True, name=proxy_name)[0]
        bbox = cmds.xform(proxy, q=True, bb=True, os=True)
        if not bbox or len(bbox) < 6:
            return proxy

        center = (
            (bbox[0] + bbox[3]) * 0.5,
            (bbox[1] + bbox[4]) * 0.5,
            (bbox[2] + bbox[5]) * 0.5
        )

        if vec_len(center) > 0.0001:
            cmds.move(-center[0], -center[1], -center[2], proxy + ".vtx[*]", r=True, os=True)
            cmds.delete(proxy, constructionHistory=True)
        cmds.xform(proxy, ws=True, t=[0, 0, 0])
        cmds.xform(proxy, os=True, pivots=[0, 0, 0])
        return proxy
    except Exception:
        return None


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
    even_by_length=True,
    meshes=None,
    mesh_pick_mode="cycle",
    mesh_pick_step=2,
    sample_index_start=0,
):
    valid_meshes = _unique_in_order([m for m in (meshes or [mesh]) if _safe_exists(m)])
    if not valid_meshes or not _safe_exists(curve):
        return []
    primary_mesh = valid_meshes[0]

    if clear_existing:
        curve_cleanup_preview()

    start_u = clamp(float(start_u), 0.0, 1.0)
    end_u = clamp(float(end_u), 0.0, 1.0)
    if end_u < start_u:
        end_u = start_u

    if auto_fit:
        count = compute_auto_fit_count(
            mesh=primary_mesh,
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

    count = max(0, int(count))
    if count <= 0:
        return []
    results = []
    local_center_offsets = {m: get_mesh_center_offset_local(m) for m in valid_meshes}

    proxy_by_source = {}
    proxy_meshes_to_delete = []
    # Keep placement behavior consistent for single and multi mesh inputs.
    # Prevents large offsets when a source pivot is far from geometry center.
    use_proxy_mode = True

    try:
        # For multiple source meshes, build one centered proxy per input mesh.
        # This removes large placement offsets caused by heterogeneous pivot setups.
        if use_proxy_mode:
            for idx, src_mesh in enumerate(valid_meshes):
                proxy = _create_centered_proxy_mesh(src_mesh, proxy_name="__curveScatter_proxy_{}__".format(idx + 1))
                if proxy and _safe_exists(proxy):
                    proxy_meshes_to_delete.append(proxy)
                    proxy_by_source[src_mesh] = proxy
                else:
                    proxy_by_source[src_mesh] = src_mesh
        trimmed_start_u = start_u
        trimmed_end_u = end_u

        if trim_ends and count > 1:
            curve_len = max(0.0001, get_curve_length(curve))
            # Important for multi-mesh mode:
            # use the largest effective spacing across all mesh inputs so the
            # trimmed range keeps enough room for mesh 1/2/3... combinations.
            step_est = estimate_mesh_spacing_multi(
                valid_meshes,
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

        u_samples = compute_curve_u_samples(
            curve=curve,
            count=count,
            start_u=trimmed_start_u,
            end_u=trimmed_end_u,
            even_by_length=even_by_length
        )
        warned_locked_channels = False
        is_closed = _is_closed_curve(curve)
        first_u = u_samples[0] if u_samples else None

        for i, u in enumerate(u_samples):

            try:
                pos = cmds.pointOnCurve(curve, pr=u, p=True, top=True)
                tangent, curve_normal = get_curve_tangent_and_normal(curve, u)
            except:
                continue

            if is_closed and first_u is not None and i > 0:
                u_delta = abs(float(u) - float(first_u))
                if (u_delta <= 1e-8) or (abs(1.0 - u_delta) <= 1e-8):
                    continue

            if use_instance:
                sample_mesh = _pick_mesh_for_sample(
                    valid_meshes, mesh_pick_mode, mesh_pick_step, random_seed, int(sample_index_start) + i
                ) if len(valid_meshes) > 1 else primary_mesh
                if not _safe_exists(sample_mesh):
                    sample_mesh = primary_mesh
                source_for_spawn = proxy_by_source.get(sample_mesh, sample_mesh) if use_proxy_mode else sample_mesh
                if not _safe_exists(source_for_spawn):
                    source_for_spawn = sample_mesh
                new_obj = cmds.instance(source_for_spawn, name="{}{}{:03d}".format(CURVE_PREVIEW_PREFIX, name_prefix, i + 1))[0]
            else:
                sample_mesh = _pick_mesh_for_sample(
                    valid_meshes, mesh_pick_mode, mesh_pick_step, random_seed, int(sample_index_start) + i
                ) if len(valid_meshes) > 1 else primary_mesh
                if not _safe_exists(sample_mesh):
                    sample_mesh = primary_mesh
                source_for_spawn = proxy_by_source.get(sample_mesh, sample_mesh) if use_proxy_mode else sample_mesh
                if not _safe_exists(source_for_spawn):
                    source_for_spawn = sample_mesh
                new_obj = cmds.duplicate(source_for_spawn, rr=True, name="{}{}{:03d}".format(CURVE_PREVIEW_PREFIX, name_prefix, i + 1))[0]

            blocked_attrs = unlock_transform_channels(new_obj)
            if blocked_attrs and not warned_locked_channels:
                warned_locked_channels = True
                cmds.warning(
                    "Some transform channels are locked or driven on preview objects: {}. "
                    "Scale/rotation/translation updates may be limited.".format(", ".join(blocked_attrs))
                )

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
                if center_on_bbox and not use_proxy_mode:
                    local_center_offset = local_center_offsets.get(sample_mesh, (0.0, 0.0, 0.0))
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
                if center_on_bbox and not use_proxy_mode:
                    local_center_offset = local_center_offsets.get(sample_mesh, (0.0, 0.0, 0.0))
                    cmds.move(
                        -local_center_offset[0],
                        -local_center_offset[1],
                        -local_center_offset[2],
                        new_obj,
                        r=True,
                        os=True
                    )

            if not apply_uniform_scale_safe(new_obj, final_uniform_scale):
                cmds.warning("Failed to apply scale on {}".format(new_obj))

            results.append(new_obj)
    finally:
        for proxy_mesh in proxy_meshes_to_delete:
            if proxy_mesh and _safe_exists(proxy_mesh):
                _safe_delete(proxy_mesh)

    if clear_existing:
        _CURVE_STATE["preview_objects"] = results
    else:
        _CURVE_STATE["preview_objects"].extend(results)
    add_nodes_to_active_isolate_sets(results)
    return results


def curve_bake_distribution(group_result=True):
    previews = [obj for obj in _CURVE_STATE.get("preview_objects", []) if _safe_exists(obj)]
    meshes = [m for m in _CURVE_STATE.get("meshes", []) if _safe_exists(m)]
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

    if meshes:
        cmds.select(meshes, r=True)
    elif mesh and _safe_exists(mesh):
        cmds.select(mesh, r=True)

    if group_result and final_objects:
        result = cmds.group(final_objects, name=CURVE_RESULT_GROUP_NAME)
        add_nodes_to_active_isolate_sets([result])
        cmds.select(result, r=True)
        return result
    else:
        add_nodes_to_active_isolate_sets(final_objects)
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
                if isinstance(widget, QtWidgets.QDoubleSpinBox):
                    widget.setValue(parse_float_flexible(raw, default_value))
                else:
                    widget.setValue(parse_int_flexible(raw, default_value))
            elif isinstance(widget, (QtWidgets.QCheckBox, QtWidgets.QRadioButton)):
                if isinstance(raw, bool):
                    widget.setChecked(raw)
                else:
                    widget.setChecked(str(raw).lower() in ("1", "true", "yes"))
            elif isinstance(widget, QtWidgets.QComboBox):
                idx = widget.findText(str(raw))
                if idx >= 0:
                    widget.setCurrentIndex(idx)
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
            elif isinstance(widget, QtWidgets.QComboBox):
                settings.setValue(key, widget.currentText())
        settings.endGroup()

    def reset_settings(self):
        for widget, default_value in self._settings_defaults:
            if isinstance(widget, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
                widget.setValue(default_value)
            elif isinstance(widget, (QtWidgets.QCheckBox, QtWidgets.QRadioButton)):
                widget.setChecked(default_value)
            elif isinstance(widget, QtWidgets.QComboBox):
                idx = widget.findText(str(default_value))
                if idx >= 0:
                    widget.setCurrentIndex(idx)

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
        self.select_frame.setVisible(True)
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
        self._curves_hidden_in_viewport = True

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
        count_max = parse_int_flexible(settings.value("count_max", 15), 15)
        self._set_count_max(count_max)
        for widget, default_value in self._settings_defaults:
            key = widget.property("settings_key")
            if not key:
                continue
            raw = settings.value(key, default_value)
            if isinstance(widget, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
                if isinstance(widget, QtWidgets.QDoubleSpinBox):
                    widget.setValue(parse_float_flexible(raw, default_value))
                else:
                    widget.setValue(parse_int_flexible(raw, default_value))
            elif isinstance(widget, (QtWidgets.QCheckBox, QtWidgets.QRadioButton)):
                if isinstance(raw, bool):
                    widget.setChecked(raw)
                else:
                    widget.setChecked(str(raw).lower() in ("1", "true", "yes"))
            elif isinstance(widget, QtWidgets.QComboBox):
                idx = widget.findText(str(raw))
                if idx >= 0:
                    widget.setCurrentIndex(idx)
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
            elif isinstance(widget, QtWidgets.QComboBox):
                settings.setValue(key, widget.currentText())
        settings.endGroup()

    def reset_settings(self):
        for widget, default_value in self._settings_defaults:
            if isinstance(widget, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
                widget.setValue(default_value)
            elif isinstance(widget, (QtWidgets.QCheckBox, QtWidgets.QRadioButton)):
                widget.setChecked(default_value)
            elif isinstance(widget, QtWidgets.QComboBox):
                idx = widget.findText(str(default_value))
                if idx >= 0:
                    widget.setCurrentIndex(idx)
        self._set_count_max(15)

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

    def _add_separator(self, layout):
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setFrameShadow(QtWidgets.QFrame.Sunken)
        sep.setStyleSheet("QFrame { color: #3f3f3f; }")
        sep.setFixedHeight(6)
        layout.addWidget(sep)

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
        self.status_label.setStyleSheet(
            "QLabel#statusLabel { background-color: #292929; border: 1px solid #3f3f3f; border-radius: 3px; color: #b6b6b6; }"
        )
        layout.addWidget(self.status_label)

        self.select_frame = QtWidgets.QFrame()
        self.select_frame.setVisible(True)
        self.select_frame.setMinimumHeight(36)
        self.select_frame.setMaximumHeight(36)

        select_layout = QtWidgets.QHBoxLayout(self.select_frame)
        select_layout.setContentsMargins(0, 2, 0, 2)
        select_layout.setSpacing(6)

        select_layout.addStretch()

        self.btn_select_mesh = QtWidgets.QPushButton()
        self.btn_select_mesh.setFixedHeight(28)
        self.btn_select_mesh.setMinimumWidth(74)
        self.btn_select_mesh.setObjectName("selectMeshBtn")
        self.btn_select_mesh.setToolTip("Capture selected mesh(es) as input. If none selected, reselect current input.")
        self.btn_select_mesh.clicked.connect(self._on_select_mesh)
        select_layout.addWidget(self.btn_select_mesh)

        self.btn_select_curve = QtWidgets.QPushButton()
        self.btn_select_curve.setFixedHeight(28)
        self.btn_select_curve.setMinimumWidth(74)
        self.btn_select_curve.setObjectName("selectCurveBtn")
        self.btn_select_curve.setToolTip("Capture selected curve(s)/edge loop(s). If none selected, reselect current curves.")
        self.btn_select_curve.clicked.connect(self._on_select_curve)
        select_layout.addWidget(self.btn_select_curve)

        self.btn_toggle_curve_outliner = QtWidgets.QPushButton()
        self.btn_toggle_curve_outliner.setFixedSize(28, 28)
        self.btn_toggle_curve_outliner.setObjectName("selectCurveBtn")
        self.btn_toggle_curve_outliner.setToolTip("Toggle curve visibility in viewport (outliner stays visible)")
        self.btn_toggle_curve_outliner.clicked.connect(self._on_toggle_curve_visibility)
        select_layout.addWidget(self.btn_toggle_curve_outliner)

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

        self.mesh_label = QtWidgets.QLabel("Mesh(es)  : -")
        layout.addWidget(self.mesh_label)

        self.curve_label = QtWidgets.QLabel("Curve(s)  : -")
        layout.addWidget(self.curve_label)

        self.curve_length_label = QtWidgets.QLabel("Length     : -")
        layout.addWidget(self.curve_length_label)

        layout.addSpacing(2)
        self._add_separator(layout)

        dist_label = QtWidgets.QLabel("DISTRIBUTION")
        dist_label.setObjectName("sectionLabel")
        layout.addWidget(dist_label)

        self.count_slider, self.count_spin = self._add_slider(layout, "Count", 0, 1000, 10, 0, label_width=110)
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
        self.chk_lock_no_overlap.setChecked(False)
        self.chk_lock_no_overlap.toggled.connect(self._on_rebuild)
        self.chk_lock_no_overlap.setProperty("settings_key", "lock_no_overlap")
        self._register_default(self.chk_lock_no_overlap, False)
        layout.addWidget(self.chk_lock_no_overlap)

        self.chk_trim_ends = QtWidgets.QCheckBox("Trim Ends")
        self.chk_trim_ends.setChecked(False)
        self.chk_trim_ends.toggled.connect(self._on_rebuild)
        self.chk_trim_ends.setProperty("settings_key", "trim_ends")
        self._register_default(self.chk_trim_ends, False)
        layout.addWidget(self.chk_trim_ends)

        self.chk_even_length = QtWidgets.QCheckBox("Even Spacing by Curve Length")
        self.chk_even_length.setChecked(True)
        self.chk_even_length.toggled.connect(self._on_rebuild)
        self.chk_even_length.setProperty("settings_key", "even_length")
        self._register_default(self.chk_even_length, True)
        layout.addWidget(self.chk_even_length)

        self.padding_slider, self.padding_spin = self._add_slider(layout, "Padding", -10.0, 50.0, 0.0, 3, label_width=110)
        self.safety_slider, self.safety_spin = self._add_slider(layout, "Fit Safety", 0.5, 3.0, 1.0, 3, label_width=110)
        self.auto_max_slider, self.auto_max_spin = self._add_slider(layout, "Max Count", 1, 5000, 1000, 0, label_width=110)
        self.auto_max_spin.setToolTip("Global hard cap for generated preview count")
        self.auto_max_slider.setToolTip("Global hard cap for generated preview count")

        self.mesh_order_combo = QtWidgets.QComboBox()
        self.mesh_order_combo.addItems(["Cycle", "Every N", "Random"])
        self.mesh_order_combo.currentTextChanged.connect(self._on_rebuild)
        self.mesh_order_combo.setProperty("settings_key", "mesh_order_mode")
        self._register_default(self.mesh_order_combo, "Cycle")

        mesh_order_row = QtWidgets.QHBoxLayout()
        mesh_order_row.setSpacing(4)
        mesh_order_lbl = QtWidgets.QLabel("Mesh Order")
        mesh_order_lbl.setFixedWidth(110)
        mesh_order_row.addWidget(mesh_order_lbl)
        mesh_order_row.addWidget(self.mesh_order_combo)
        mesh_order_row.addStretch()
        layout.addLayout(mesh_order_row)

        self.mesh_every_n_slider, self.mesh_every_n_spin = self._add_slider(layout, "Every N", 1, 10, 1, 0, label_width=110)

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
        self._add_separator(layout)

        offset_pos_label = QtWidgets.QLabel("OFFSET POSITION")
        offset_pos_label.setObjectName("sectionLabel")
        layout.addWidget(offset_pos_label)

        self.offset_xyz_widget = XYZSliderWidget(self, "Offset XYZ", -100.0, 100.0, 0.0, decimals=3)
        self.offset_xyz_widget.set_on_change_callback(self._on_rebuild)
        layout.addWidget(self.offset_xyz_widget)

        layout.addSpacing(2)

        rot_label = QtWidgets.QLabel("BASE ROTATION")
        rot_label.setObjectName("sectionLabel")
        layout.addWidget(rot_label)

        self.base_rot_xyz_widget = XYZSliderWidget(self, "Rotate XYZ", -360.0, 360.0, 0.0, decimals=2)
        self.base_rot_xyz_widget.set_on_change_callback(self._on_rebuild)
        layout.addWidget(self.base_rot_xyz_widget)
        self._add_quick_rotation_buttons(layout)

        layout.addSpacing(2)
        self._add_separator(layout)

        rand_rot_label = QtWidgets.QLabel("RANDOM ROTATION")
        rand_rot_label.setObjectName("sectionLabel")
        layout.addWidget(rand_rot_label)

        self.rand_rot_xyz_widget = XYZSliderWidget(self, "Random XYZ", 0.0, 360.0, 0.0, decimals=2)
        self.rand_rot_xyz_widget.set_on_change_callback(self._on_rebuild)
        layout.addWidget(self.rand_rot_xyz_widget)

        layout.addSpacing(2)

        scale_label = QtWidgets.QLabel("SCALE")
        scale_label.setObjectName("sectionLabel")
        layout.addWidget(scale_label)

        self.scale_slider, self.scale_spin = self._add_slider(layout, "Scale", 0.001, 10.0, 1.0, 3, label_width=110)
        self.rand_scale_slider, self.rand_scale_spin = self._add_slider(layout, "Random Scale", 0.0, 5.0, 0.0, 3, label_width=110)

        layout.addSpacing(2)
        self._add_separator(layout)

        seed_label = QtWidgets.QLabel("RANDOM")
        seed_label.setObjectName("sectionLabel")
        layout.addWidget(seed_label)

        self.seed_slider, self.seed_spin = self._add_slider(layout, "Seed", 1, 9999, 1, 0, label_width=110)

        layout.addSpacing(4)
        self._add_separator(layout)

        opt_label = QtWidgets.QLabel("OPTIONS")
        opt_label.setObjectName("sectionLabel")
        layout.addWidget(opt_label)

        self.chk_orient = QtWidgets.QCheckBox("Orient to Curve")
        self.chk_orient.setChecked(True)
        self.chk_orient.toggled.connect(self._on_rebuild)
        layout.addWidget(self.chk_orient)

        orient_mode_row = QtWidgets.QVBoxLayout()
        orient_mode_row.setSpacing(2)
        orient_mode_row.setContentsMargins(16, 0, 0, 0)
        orient_mode_lbl = QtWidgets.QLabel("Orient Mode")
        orient_mode_lbl.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        orient_mode_row.addWidget(orient_mode_lbl)
        self.orient_mode_world_up = QtWidgets.QRadioButton("Tangent + WorldUp")
        self.orient_mode_world_up.setChecked(True)
        self.orient_mode_curve_normal = QtWidgets.QRadioButton("Tangent + Curve Normal")

        self.orient_mode_world_up.toggled.connect(self._on_rebuild)
        self.orient_mode_curve_normal.toggled.connect(self._on_rebuild)

        orient_mode_row.addWidget(self.orient_mode_world_up)
        orient_mode_row.addWidget(self.orient_mode_curve_normal)
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
            (self.safety_spin, "fit_safety", 1.0),
            (self.auto_max_spin, "auto_max", 1000),
            (self.mesh_every_n_spin, "mesh_every_n", 1),
            (self.offset_xyz_widget.spinbox, "off_xyz", 0.0),
            (self.base_rot_xyz_widget.spinbox, "rot_xyz", 0.0),
            (self.rand_rot_xyz_widget.spinbox, "rand_rot_xyz", 0.0),
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
            (self.mesh_order_combo, "mesh_order_mode", "Cycle"),
        ]
        for widget, key, default_value in controls:
            widget.setProperty("settings_key", key)
            self._register_default(widget, default_value)

    def _set_count_max(self, max_value):
        max_value = int(clamp(max_value, 50, 5000))
        self.count_spin.setMaximum(max_value)
        current = int(self.count_spin.value())
        if current > max_value:
            self.count_spin.setValue(max_value)
        self._sync_count_slider_with_spin()

    def _sync_count_slider_with_spin(self):
        current_min = float(self.count_spin.minimum())
        current_max = float(self.count_spin.maximum())
        value = float(self.count_spin.value())
        ratio = (value - current_min) / (current_max - current_min) if (current_max - current_min) > 0 else 0.0
        self.count_slider.blockSignals(True)
        self.count_slider.setValue(int(clamp(ratio, 0.0, 1.0) * 1000.0))
        self.count_slider.blockSignals(False)

    def _sync_count_preset_from_range(self):
        max_v = int(self.count_spin.maximum())
        if max_v <= 50:
            self.count_preset_combo.setCurrentText("0-50")
        elif max_v <= 100:
            self.count_preset_combo.setCurrentText("0-100")
        elif max_v <= 250:
            self.count_preset_combo.setCurrentText("0-250")
        elif max_v <= 500:
            self.count_preset_combo.setCurrentText("0-500")
        else:
            self.count_preset_combo.setCurrentText("0-1000+")

    def _change_count_range(self, delta):
        step = 50 if self.count_spin.maximum() < 300 else 100
        self._set_count_max(self.count_spin.maximum() + (delta * step))
        self._sync_count_preset_from_range()
        self._on_rebuild()

    def _on_count_preset_changed(self, text):
        mapping = {"0-50": 50, "0-100": 100, "0-250": 250, "0-500": 500, "0-1000+": 1000}
        self._set_count_max(mapping.get(text, 100))
        self._on_rebuild()

    def _add_count_range_controls(self, parent_layout):
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        row.setContentsMargins(0, 0, 0, 0)

        lbl = QtWidgets.QLabel("Count Range")
        lbl.setFixedWidth(110)
        row.addWidget(lbl)

        self.count_preset_combo = QtWidgets.QComboBox()
        self.count_preset_combo.addItems(["0-50", "0-100", "0-250", "0-500", "0-1000+"])
        self.count_preset_combo.setCurrentIndex(1)
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

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(3)

        buttons = [
            ("X+45", "x", 45.0),
            ("X+90", "x", 90.0),
            ("Y+45", "y", 45.0),
            ("Y+90", "y", 90.0),
            ("Z+45", "z", 45.0),
            ("Z+90", "z", 90.0),
            ("Reset", None, 0.0),
        ]

        for text, axis, delta in buttons:
            btn = QtWidgets.QPushButton(text)
            btn.setFixedHeight(18)
            btn.setMinimumWidth(40 if axis is not None else 46)
            btn.setStyleSheet("font-size: 10px; padding: 1px 4px;")
            if text.startswith("X"):
                btn.setStyleSheet("font-size: 10px; padding: 1px 4px; background-color: %s;" % AXIS_X_COLOR)
            elif text.startswith("Y"):
                btn.setStyleSheet("font-size: 10px; padding: 1px 4px; background-color: %s;" % AXIS_Y_COLOR)
            elif text.startswith("Z"):
                btn.setStyleSheet("font-size: 10px; padding: 1px 4px; background-color: %s;" % AXIS_Z_COLOR)
            if axis is None:
                btn.clicked.connect(self._reset_rotation_offsets)
            else:
                btn.clicked.connect(lambda _, a=axis, d=delta: self._increment_rotation_axis(a, d))
            row.addWidget(btn)

        container.addLayout(row)
        container.addStretch()
        parent_layout.addLayout(container)

    def _increment_rotation_axis(self, axis, delta):
        axis = str(axis).lower()
        vec = self.base_rot_xyz_widget.get_vector_values()
        axis_index = {"x": 0, "y": 1, "z": 2}.get(axis, 0)
        current = vec[axis_index]
        target = clamp(current + float(delta), self.base_rot_xyz_widget.spinbox.minimum(), self.base_rot_xyz_widget.spinbox.maximum())
        self.base_rot_xyz_widget.set_axis_value(axis, target)

    def _reset_rotation_offsets(self):
        self.base_rot_xyz_widget.reset()

    def _setup_icons(self):
        self.btn_select_mesh.setText("Mesh")
        self.btn_select_curve.setText("Curve")
        self.btn_refresh.setText("\u27f3")
        self._update_curve_outliner_toggle_ui()

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
        requested_meshes = _CURVE_STATE.get("meshes", []) or []
        meshes = _unique_in_order([m for m in requested_meshes if _safe_exists(m)])
        mesh = _CURVE_STATE.get("mesh")
        curves = _CURVE_STATE.get("curves", [])
        if meshes:
            if len(meshes) == 1:
                self.mesh_label.setText("Mesh(es)  : {}".format(meshes[0].split("|")[-1]))
            else:
                self.mesh_label.setText("Mesh(es)  : {} selected".format(len(meshes)))
        else:
            self.mesh_label.setText("Mesh(es)  : {}".format(mesh.split("|")[-1] if mesh else "-"))
        if curves:
            if len(curves) == 1:
                self.curve_label.setText("Curve(s)  : {}".format(curves[0].split("|")[-1]))
            else:
                self.curve_label.setText("Curve(s)  : {} selected".format(len(curves)))
        else:
            self.curve_label.setText("Curve(s)  : -")

        if curves:
            total_length = sum(get_curve_length(c) for c in curves if _safe_exists(c))
            self.curve_length_label.setText("Length     : {:.3f}".format(total_length))
        else:
            self.curve_length_label.setText("Length     : -")

    def _update_curve_outliner_toggle_ui(self):
        if self._curves_hidden_in_viewport:
            self.btn_toggle_curve_outliner.setText("H")
            self.btn_toggle_curve_outliner.setToolTip("Curves hidden in viewport (click to show)")
        else:
            self.btn_toggle_curve_outliner.setText("S")
            self.btn_toggle_curve_outliner.setToolTip("Curves visible in viewport (click to hide)")

    def _set_curves_hidden_in_viewport(self, hidden):
        curves = [c for c in _CURVE_STATE.get("curves", []) if _safe_exists(c)]
        set_nodes_hidden_in_outliner(curves, hidden=False)
        set_nodes_viewport_visibility(curves, visible=(not bool(hidden)))
        self._curves_hidden_in_viewport = bool(hidden)
        self._update_curve_outliner_toggle_ui()

    def _capture_meshes_from_selection(self):
        selection = cmds.ls(sl=True, long=True, fl=True) or []
        meshes, _ = extract_mesh_and_curve_transforms_from_selection(selection)
        return [m for m in meshes if _safe_exists(m)]

    def _capture_curves_from_selection(self):
        selection = cmds.ls(sl=True, long=True, fl=True) or []
        _, curves = extract_mesh_and_curve_transforms_from_selection(selection)
        curves = [c for c in curves if _safe_exists(c)]

        generated_curves, warnings = _edge_loops_to_curves_from_selection()
        for msg in warnings[:3]:
            cmds.warning(msg)
        if len(warnings) > 3:
            cmds.warning("{} additional conversion warnings.".format(len(warnings) - 3))

        temp_curves = []
        for c in generated_curves:
            if c and _safe_exists(c) and is_curve_transform(c):
                curves.append(c)
                temp_curves.append(c)
        return _unique_in_order(curves), temp_curves

    def _setup_live_callbacks(self):
        for job_id in _CURVE_STATE.get("script_jobs", []):
            try:
                if cmds.scriptJob(exists=job_id):
                    cmds.scriptJob(kill=job_id, force=True)
            except:
                pass
        _CURVE_STATE["script_jobs"] = []

        meshes = [m for m in _CURVE_STATE.get("meshes", []) if _safe_exists(m)]
        curves = _CURVE_STATE.get("curves", [])
        ui = _CURVE_STATE.get("ui_instance")
        if not ui:
            return

        for mesh in meshes:
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
        meshes = [m for m in _CURVE_STATE.get("meshes", []) if _safe_exists(m)]
        curves = [c for c in _CURVE_STATE.get("curves", []) if _safe_exists(c)]
        temp_curves = [c for c in _CURVE_STATE.get("temp_curves", []) if _safe_exists(c)]

        selection = cmds.ls(sl=True, long=True, fl=True) or []
        if not meshes:
            meshes, _ = extract_mesh_and_curve_transforms_from_selection(selection)
            meshes = [m for m in meshes if _safe_exists(m)]
        if not curves:
            captured_curves, generated_temp_curves = self._capture_curves_from_selection()
            curves = [c for c in captured_curves if _safe_exists(c)]
            temp_curves = [c for c in generated_temp_curves if _safe_exists(c)]

        if not meshes:
            cmds.warning("Sélectionne au moins un mesh transform.")
            self.status_label.setText("Need at least one mesh")
            return

        if not curves:
            cmds.warning("Sélectionne une ou plusieurs curves, ou des edge loops convertibles.")
            self.status_label.setText("Need curve(s) or edge loops")
            return

        _CURVE_STATE["mesh"] = meshes[0]
        _CURVE_STATE["meshes"] = meshes
        _CURVE_STATE["curves"] = curves
        _CURVE_STATE["temp_curves"] = temp_curves
        _CURVE_STATE["started"] = True
        _CURVE_STATE["baked"] = False
        self._set_curves_hidden_in_viewport(True)
        self._setup_live_callbacks()

        self._update_start_button()
        self._update_info_labels()
        self.status_label.setText("Started - {} mesh(es), {} curve(s)".format(len(meshes), len(curves)))

        self._do_rebuild()
        self._request_parent_resize()

    def _do_rebuild(self):
        if not _CURVE_STATE.get("started"):
            return

        requested_meshes = _CURVE_STATE.get("meshes", []) or []
        meshes = _unique_in_order([m for m in requested_meshes if _safe_exists(m)])
        mesh = _CURVE_STATE.get("mesh")
        curves = _CURVE_STATE.get("curves", [])

        valid_curves = [c for c in curves if _safe_exists(c)]
        if (not meshes and not _safe_exists(mesh)) or not valid_curves:
            self.status_label.setText("Mesh or curve(s) missing")
            return
        if not meshes and _safe_exists(mesh):
            meshes = [mesh]
        elif len(requested_meshes) > 1 and len(meshes) <= 1:
            cmds.warning(
                "Only {} valid mesh input remains. Verify deleted/renamed nodes and recapture mesh selection.".format(
                    len(meshes)
                )
            )

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
            mesh_every_n = int(self.mesh_every_n_spin.value())
            mesh_order_mode_text = self.mesh_order_combo.currentText().strip().lower()
            mesh_order_mode = "cycle"
            if mesh_order_mode_text == "every n":
                mesh_order_mode = "every_n"
            elif mesh_order_mode_text == "random":
                mesh_order_mode = "random"
            trim_ends = self.chk_trim_ends.isChecked()
            auto_fit = self.chk_auto_fit.isChecked()
            lock_no_overlap = self.chk_lock_no_overlap.isChecked()
            even_by_length = self.chk_even_length.isChecked()

            safe_limits = []
            for crv in valid_curves:
                safe_limits.append(
                    compute_auto_fit_count_multi(
                        meshes=meshes,
                        curve=crv,
                        start_u=start_u,
                        end_u=end_u,
                        axis_mode=fit_axis,
                        padding=padding,
                        base_scale=base_scale,
                        random_scale=rand_scale,
                        trim_ends=trim_ends,
                        safety_multiplier=fit_safety
                    )
                )
            safe_count_limit = min(safe_limits) if safe_limits else 1
            hard_cap_limit = max(1, max_count)

            if auto_fit:
                auto_counts = []
                for crv in valid_curves:
                    auto_counts.append(
                        compute_auto_fit_count_multi(
                            meshes=meshes,
                            curve=crv,
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
                    )
                count = min(auto_counts) if auto_counts else 1
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
            # "Max Count" is treated as a global hard-cap in all modes.
            count = min(count, hard_cap_limit)
            if not auto_fit and count != int(self.count_spin.value()):
                self.count_spin.blockSignals(True)
                self.count_spin.setValue(count)
                self.count_spin.blockSignals(False)

            per_curve_counts = [count for _ in valid_curves]
            if len(valid_curves) > 1 and not auto_fit:
                per_curve_counts = compute_curve_count_allocation(
                    curves=valid_curves,
                    total_count=count,
                    start_u=start_u,
                    end_u=end_u
                )
            if lock_no_overlap and not auto_fit:
                per_curve_counts = [
                    min(per_curve_counts[idx], safe_limits[idx] if idx < len(safe_limits) else per_curve_counts[idx])
                    for idx in range(len(per_curve_counts))
                ]
                count = sum(per_curve_counts)
                if int(self.count_spin.value()) != count:
                    self.count_spin.blockSignals(True)
                    self.count_spin.setValue(count)
                    self.count_spin.blockSignals(False)

            if count <= 0:
                curve_cleanup_preview()
                self.status_label.setText("0 preview | increase Count")
                cmds.refresh(force=False)
                return

            offset_pos = self.offset_xyz_widget.get_vector_values()
            offset_rot = self.base_rot_xyz_widget.get_vector_values()
            rand_rot = self.rand_rot_xyz_widget.get_vector_values()

            orient = self.chk_orient.isChecked()
            orient_mode = "curve_normal" if self.orient_mode_curve_normal.isChecked() else "world_up"
            center_on_bbox = self.chk_center_bbox.isChecked()
            use_instance = self.chk_instance.isChecked()
            random_seed = int(self.seed_spin.value())

            all_results = []
            usable_len = 0.0
            sample_index_offset = 0
            for idx, curve in enumerate(valid_curves):
                curve_count = per_curve_counts[idx] if idx < len(per_curve_counts) else count
                if auto_fit:
                    curve_count = compute_auto_fit_count_multi(
                        meshes=meshes,
                        curve=curve,
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
                result = build_curve_distribution(
                    mesh=mesh,
                    curve=curve,
                    count=curve_count,
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
                    center_on_bbox=center_on_bbox,
                    even_by_length=even_by_length,
                    meshes=meshes,
                    mesh_pick_mode=mesh_order_mode,
                    mesh_pick_step=mesh_every_n,
                    sample_index_start=sample_index_offset
                )
                all_results.extend(result)
                sample_index_offset += max(0, int(curve_count))
                usable_len += get_curve_length(curve) * max(0.0, (end_u - start_u))

            step_est = estimate_mesh_spacing_multi(
                meshes,
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
                        "{} preview | usable {:.3f} | safe max {} | hard cap {}".format(
                        len(all_results), usable_len, safe_count_limit, hard_cap_limit
                    )
                )
            else:
                self.status_label.setText(
                    "{} preview | usable {:.3f} | hard cap {}".format(len(all_results), usable_len, hard_cap_limit)
                )

            cmds.refresh(force=False)

        except Exception as e:
            cmds.warning("Rebuild failed: {}".format(str(e)))
            self.status_label.setText("Rebuild failed")
        finally:
            _CURVE_STATE["is_processing"] = False

    def _on_select_mesh(self):
        selected_meshes = self._capture_meshes_from_selection()
        if selected_meshes:
            _CURVE_STATE["meshes"] = selected_meshes
            _CURVE_STATE["mesh"] = selected_meshes[0]
            self._update_info_labels()
            if not _CURVE_STATE.get("started"):
                self.status_label.setText("Captured mesh input ({} mesh(es))".format(len(selected_meshes)))
                return
            self._setup_live_callbacks()
            self._on_rebuild()
            self.status_label.setText("Updated mesh input ({} mesh(es))".format(len(selected_meshes)))
            return
        meshes = [m for m in _CURVE_STATE.get("meshes", []) if _safe_exists(m)]
        if meshes:
            cmds.select(meshes, r=True)
            self.status_label.setText("No new mesh selection — reselected current input")

    def _on_select_curve(self):
        selected_curves, temp_curves = self._capture_curves_from_selection()
        if selected_curves:
            _CURVE_STATE["curves"] = selected_curves
            current_temp = [c for c in _CURVE_STATE.get("temp_curves", []) if _safe_exists(c)]
            for old_curve in current_temp:
                if old_curve not in selected_curves:
                    _safe_delete(old_curve)
            _CURVE_STATE["temp_curves"] = _unique_in_order([c for c in temp_curves if _safe_exists(c)])
            self._set_curves_hidden_in_viewport(self._curves_hidden_in_viewport)
            self._update_info_labels()
            if not _CURVE_STATE.get("started"):
                self.status_label.setText("Captured curve input ({} curve(s))".format(len(selected_curves)))
                return
            self._setup_live_callbacks()
            self._on_rebuild()
            self.status_label.setText("Updated curve input ({} curve(s))".format(len(selected_curves)))
            return
        curves = [c for c in _CURVE_STATE.get("curves", []) if _safe_exists(c)]
        if curves:
            cmds.select(curves, r=True)
            self.status_label.setText("No new curve selection — reselected current input")

    def _on_toggle_curve_visibility(self):
        if not _CURVE_STATE.get("started"):
            cmds.warning("Start d'abord pour gérer les curves.")
            return
        self._set_curves_hidden_in_viewport(not self._curves_hidden_in_viewport)
        if self._curves_hidden_in_viewport:
            self.status_label.setText("Curves hidden in viewport")
        else:
            self.status_label.setText("Curves visible in viewport")

    def _on_refresh(self):
        if _CURVE_STATE.get("started"):
            self._do_rebuild()
            self.status_label.setText("Refreshed")

    def _on_cancel(self):
        self._rebuild_timer.stop()
        curve_full_cleanup()
        self._update_start_button()
        self.mesh_label.setText("Mesh(es) : -")
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
        self._ui_scale_percent = 100
        self._ui_scale_factor = 1.0
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
        top_row.setSpacing(6)
        self.ui_scale_label = QtWidgets.QLabel("UI Scale")
        top_row.addWidget(self.ui_scale_label)
        self.ui_scale_spin = QtWidgets.QSpinBox()
        self.ui_scale_spin.setRange(30, 150)
        self.ui_scale_spin.setSingleStep(5)
        self.ui_scale_spin.setSuffix("%")
        self.ui_scale_spin.setFixedWidth(74)
        self.ui_scale_spin.valueChanged.connect(self._on_ui_scale_changed)
        top_row.addWidget(self.ui_scale_spin)

        self.ui_scale_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.ui_scale_slider.setRange(30, 150)
        self.ui_scale_slider.setSingleStep(1)
        self.ui_scale_slider.setPageStep(5)
        self.ui_scale_slider.setFixedWidth(120)
        self.ui_scale_slider.valueChanged.connect(self._on_ui_scale_changed)
        top_row.addWidget(self.ui_scale_slider)

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
        # Requested behavior: every new UI opening starts from clean defaults.
        self.tab_proarray.reset_settings()
        self.tab_curve.reset_settings()
        self._ui_scale_percent = 100
        self.ui_scale_spin.blockSignals(True)
        self.ui_scale_slider.blockSignals(True)
        self.ui_scale_spin.setValue(self._ui_scale_percent)
        self.ui_scale_slider.setValue(self._ui_scale_percent)
        self.ui_scale_spin.blockSignals(False)
        self.ui_scale_slider.blockSignals(False)
        self._apply_ui_scale(self._ui_scale_percent)
        self.tabs.setCurrentIndex(0)

    def _save_settings(self):
        # Keep method for compatibility, but don't persist runtime values.
        return

    def _on_reset_all_settings(self):
        self._settings.clear()
        self.tab_proarray.reset_settings()
        self.tab_curve.reset_settings()
        # Requested behavior: preserve current UI scale when resetting tool settings.
        current_scale = int(clamp(self._ui_scale_percent, 30, 150))
        self.ui_scale_spin.blockSignals(True)
        self.ui_scale_slider.blockSignals(True)
        self.ui_scale_spin.setValue(current_scale)
        self.ui_scale_slider.setValue(current_scale)
        self.ui_scale_spin.blockSignals(False)
        self.ui_scale_slider.blockSignals(False)
        self._apply_ui_scale(current_scale)
        self.request_resize()

    def _on_ui_scale_changed(self, value):
        value = int(clamp(value, 30, 150))
        self._ui_scale_percent = value
        sender = self.sender()
        if sender is self.ui_scale_spin:
            self.ui_scale_slider.blockSignals(True)
            self.ui_scale_slider.setValue(value)
            self.ui_scale_slider.blockSignals(False)
        elif sender is self.ui_scale_slider:
            self.ui_scale_spin.blockSignals(True)
            self.ui_scale_spin.setValue(value)
            self.ui_scale_spin.blockSignals(False)
        self._apply_ui_scale(value)
        self.request_resize()

    def _apply_ui_scale(self, percent):
        scale = max(0.3, min(1.5, float(percent) / 100.0))
        self._ui_scale_factor = scale
        base_font = self.font()
        base_point_size = 9.0
        scaled_font = QtGui.QFont(base_font)
        scaled_font.setPointSizeF(max(7.0, base_point_size * scale))
        self.setFont(scaled_font)
        apply_shared_style(self)
        button_h = int(round(24 * scale))
        spin_h = int(round(22 * scale))
        slider_h = int(round(16 * scale))
        handle_w = int(round(10 * scale))
        tab_pad_v = int(round(6 * scale))
        tab_pad_h = int(round(12 * scale))
        font_px = int(round(11 * scale))
        self.setStyleSheet(
            self.styleSheet() +
            (
                "\nQWidget { font-size: %dpx; }"
                "\nQPushButton { min-height: %dpx; }"
                "\nQAbstractSpinBox, QComboBox { min-height: %dpx; }"
                "\nQSlider:horizontal { min-height: %dpx; }"
                "\nQSlider::handle:horizontal { width: %dpx; }"
                "\nQTabBar::tab { padding: %dpx %dpx; }"
            ) % (font_px, button_h, spin_h, slider_h, handle_w, tab_pad_v, tab_pad_h)
        )
        self.adjustSize()

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

        scale = max(0.3, min(1.5, float(getattr(self, "_ui_scale_factor", 1.0))))
        if index == 0:
            min_w = int(round(self._min_width_proarray * scale))
            target_w = max(min_w, int(round(content_hint.width() + (40 * scale))))
            target_h = int(round(content_hint.height() + (90 * scale)))
            target_h = min(target_h, int(round(self._max_auto_height * scale)))

            self.setMinimumWidth(min_w)
            self.setMinimumHeight(int(round(250 * scale)))
            self.setMaximumWidth(16777215)
            self.setMaximumHeight(16777215)

        else:
            min_w = int(round(self._min_width_curve * scale))
            target_w = max(min_w, int(round(content_hint.width() + (40 * scale))))
            target_h = int(round(content_hint.height() + (90 * scale)))
            target_h = max(int(round(620 * scale)), min(target_h, int(round(self._max_auto_height * scale))))

            self.setMinimumWidth(min_w)
            self.setMinimumHeight(int(round(620 * scale)))
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
