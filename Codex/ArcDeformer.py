# -*- coding: utf-8 -*-
"""
=============================================================================
ARC DEFORMER PRO v1.0
=============================================================================
Standalone Arc / Curve Wire Deformer
UI style inspired by ProBevel

Compatible:
- Maya 2022+ (PySide2 fallback)
- Maya 2027+ (PySide6 preferred)
=============================================================================
"""

import math
import maya.cmds as cmds
import maya.mel as mel
import maya.OpenMayaUI as omui

# ============================================================
# PYSIDE / SHIBOKEN COMPAT
# ============================================================

try:
    from PySide6 import QtWidgets, QtCore, QtGui
    from shiboken6 import wrapInstance
    PYSIDE_VERSION = 6
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui
    from shiboken2 import wrapInstance
    PYSIDE_VERSION = 2


# ============================================================
# GLOBAL STATE
# ============================================================

_STATE = {
    "current_curve": "",
    "stored_edges": [],
    "wire_node": "",
    "original_selection": [],
    "started": False,
    "ui_values": {},
}


# ============================================================
# THEME COLORS
# ============================================================

ACCENT_RED_BG     = "#5a2a2a"
ACCENT_RED_BORDER = "#e84d4d"
ACCENT_RED_TEXT   = "#e84d4d"


# ============================================================
# SAFE HELPERS
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


def _safe_set_attr(attr, *values, **kwargs):
    try:
        node = attr.split(".")[0]
        if cmds.objExists(node):
            cmds.setAttr(attr, *values, **kwargs)
    except:
        pass


def get_maya_main_window():
    try:
        ptr = omui.MQtUtil.mainWindow()
        return wrapInstance(int(ptr), QtWidgets.QWidget)
    except:
        return None


# ============================================================
# GEOMETRY HELPERS
# ============================================================

def _distance(a, b):
    return math.sqrt(
        (a[0] - b[0]) ** 2 +
        (a[1] - b[1]) ** 2 +
        (a[2] - b[2]) ** 2
    )


def _point(component):
    return cmds.pointPosition(component, w=True)


def _ordered_edge_vertices(edges=None):
    """
    Return:
        (is_closed_loop, ordered_vertices)
    """
    sel_edges = edges or cmds.ls(sl=True, fl=True)
    if not sel_edges:
        raise RuntimeError("Select edge(s) first.")

    shape_node = cmds.listRelatives(sel_edges[0], fullPath=True, parent=True)
    transform_node = cmds.listRelatives(shape_node[0], fullPath=True, parent=True)

    edge_ids = []
    for edge in sel_edges:
        chunks = edge.split('.')[1].split('\n')[0].split(' ')
        for c in chunks:
            n = ''.join([x for x in c.split('|')[-1] if x.isdigit()])
            if n:
                edge_ids.append(n)

    all_vertex_ids = []
    for edge in sel_edges:
        ev_info = cmds.polyInfo(edge, ev=True)
        chunks = ev_info[0].split(':')[1].split('\n')[0].split(' ')
        for c in chunks:
            n = ''.join([x for x in c.split('|')[-1] if x.isdigit()])
            if n:
                all_vertex_ids.append(n)

    duplicated = set([x for x in all_vertex_ids if all_vertex_ids.count(x) > 1])
    endpoints = list(set(all_vertex_ids) - duplicated)

    is_closed = 0
    if not endpoints:
        is_closed = 1
        endpoints.append(all_vertex_ids[0])

    ordered_ids = [endpoints[0]]
    count = 0

    while len(duplicated) > 0 and count < 1000:
        current_vtx = transform_node[0] + ".vtx[" + ordered_ids[-1] + "]"
        ve_info = cmds.polyInfo(current_vtx, ve=True)

        connected_edges = []
        chunks = ve_info[0].split(':')[1].split('\n')[0].split(' ')
        for c in chunks:
            n = ''.join([x for x in c.split('|')[-1] if x.isdigit()])
            if n:
                connected_edges.append(n)

        next_edge = None
        for eid in connected_edges:
            if eid in edge_ids:
                next_edge = eid
                break

        if next_edge is None:
            break

        edge_ids.remove(next_edge)

        edge_comp = transform_node[0] + ".e[" + next_edge + "]"
        ev_info = cmds.polyInfo(edge_comp, ev=True)

        edge_vertices = []
        chunks = ev_info[0].split(':')[1].split('\n')[0].split(' ')
        for c in chunks:
            n = ''.join([x for x in c.split('|')[-1] if x.isdigit()])
            if n:
                edge_vertices.append(n)

        next_vertex = None
        for v in edge_vertices:
            if v in duplicated:
                next_vertex = v
                break

        if next_vertex is None:
            break

        duplicated.remove(next_vertex)
        ordered_ids.append(next_vertex)
        count += 1

    if is_closed == 0:
        if len(endpoints) > 1:
            ordered_ids.append(endpoints[1])
    elif len(ordered_ids) > 1 and ordered_ids[0] == ordered_ids[1]:
        ordered_ids = ordered_ids[1:]
    elif len(ordered_ids) > 1 and ordered_ids[0] == ordered_ids[-1]:
        ordered_ids = ordered_ids[:-1]

    ordered_vertices = [
        transform_node[0] + ".vtx[" + vid + "]"
        for vid in ordered_ids
    ]
    return is_closed, ordered_vertices


def _compute_normalized_spacing(vertices):
    lengths = []
    total_length = 0.0

    for i in range(len(vertices) - 1):
        p0 = _point(vertices[i])
        p1 = _point(vertices[i + 1])
        d = _distance(p0, p1)
        lengths.append(d)
        total_length += d

    if total_length <= 1e-8:
        return []

    u_values = []
    accum = 0.0
    for d in lengths:
        accum += d
        u_values.append(accum / total_length)

    return u_values


def _compute_even_spacing(edge_count):
    if edge_count <= 0:
        return []
    return [float(i + 1) / float(edge_count) for i in range(edge_count)]


# ============================================================
# CURVE BUILD
# ============================================================

def _build_curve_from_open_chain(vertices, edges, use_arc=True, curve_type=1, point_count=3):
    if use_arc:
        mid_index = int(len(vertices) / 2)
        mid_index = max(0, min(mid_index, len(edges) - 1))

        try:
            cmds.move(0.01, 0, 0, edges[mid_index], r=True, cs=True, ls=True, wd=True)
        except:
            pass

        p1 = _point(vertices[0])
        p2 = _point(vertices[mid_index])
        p3 = _point(vertices[-1])

        arc_node = cmds.createNode("makeThreePointCircularArc")
        cmds.setAttr(arc_node + ".pt1", p1[0], p1[1], p1[2])
        cmds.setAttr(arc_node + ".pt2", p2[0], p2[1], p2[2])
        cmds.setAttr(arc_node + ".pt3", p3[0], p3[1], p3[2])
        cmds.setAttr(arc_node + ".d", 3)
        cmds.setAttr(arc_node + ".s", len(vertices))

        curve_shape = cmds.createNode("nurbsCurve")
        cmds.connectAttr(arc_node + ".oc", curve_shape + ".cr")
        cmds.delete(ch=True)

        transform = cmds.listRelatives(curve_shape, fullPath=True, parent=True)[0]
        curve = cmds.rename(transform, "arcCurve0")

        if curve_type == 2:
            spans = max(1, int(point_count) - 3)
        else:
            spans = max(1, int(point_count) - 1)

        cmds.rebuildCurve(
            curve,
            ch=True, rpo=True, rt=0, end=1,
            kr=0, kcp=0, kep=1, kt=0,
            s=spans, d=3, tol=0.01
        )
        return curve

    p0 = _point(vertices[0])
    curve = cmds.curve(d=1, p=[p0], name="arcCurve0")

    for i in range(1, len(vertices)):
        pos = _point(vertices[i])
        cmds.curve(curve, a=True, d=1, p=pos)

    spans = max(1, int(point_count) - 1)
    cmds.rebuildCurve(
        curve,
        ch=True, rpo=True, rt=0, end=1,
        kr=0, kcp=0, kep=1, kt=0,
        s=spans, d=1, tol=0.01
    )
    return curve


def _build_curve_from_closed_loop(vertices, point_count=4):
    p0 = _point(vertices[0])
    curve = cmds.curve(d=1, p=[p0], name="arcCurve0")

    for i in range(1, len(vertices)):
        pos = _point(vertices[i])
        cmds.curve(curve, a=True, d=1, p=pos)

    cmds.curve(curve, a=True, d=1, p=p0)
    cmds.closeCurve(curve, ch=False, ps=2, rpo=True, bb=0.5, bki=False, p=0.1)

    point_count = max(4, int(point_count))
    cmds.rebuildCurve(
        curve,
        ch=True, rpo=True, rt=0, end=1,
        kr=0, kcp=0, kep=1, kt=0,
        s=point_count, d=3, tol=0.01
    )
    return curve


# ============================================================
# SNAP HELPERS
# ============================================================

def _snap_vertices_to_curve(vertices, curve, edge_count, even_spacing=True, closed_loop=False):
    if even_spacing:
        u_values = _compute_even_spacing(edge_count)
    else:
        u_values = _compute_normalized_spacing(vertices)

    if not u_values:
        return

    if closed_loop:
        for i, u in enumerate(u_values):
            if i + 1 == len(vertices):
                pos = cmds.pointOnCurve(curve, pr=0, p=True)
                cmds.move(pos[0], pos[1], pos[2], vertices[0], a=True, ws=True)
            else:
                pos = cmds.pointOnCurve(curve, pr=u, p=True)
                cmds.move(pos[0], pos[1], pos[2], vertices[i + 1], a=True, ws=True)
    else:
        for i in range(len(vertices)):
            if i == 0:
                pos = cmds.pointOnCurve(curve, pr=0, p=True)
            else:
                pos = cmds.pointOnCurve(curve, pr=u_values[i - 1], p=True)
            cmds.move(pos[0], pos[1], pos[2], vertices[i], a=True, ws=True)


# ============================================================
# ARC DEFORMER CORE
# ============================================================

def finish_arc_deformer(*args):
    current_curve = _STATE.get("current_curve", "")
    stored_edges = _STATE.get("stored_edges", [])

    curve_list = cmds.ls("arcCurve*", transforms=True) or []
    for curve in curve_list:
        if "BaseWire" in curve:
            continue
        try:
            shape_nodes = cmds.listRelatives(curve, fullPath=True) or []
            if shape_nodes:
                hist = cmds.listConnections(
                    cmds.listConnections(shape_nodes[0], sh=True, d=True),
                    d=True,
                    sh=True
                )
                if hist:
                    cmds.delete(hist, ch=True)
        except:
            pass

    try:
        cmds.delete("arcCurve*")
    except:
        pass

    if current_curve and _safe_exists(current_curve + "BaseWire"):
        _safe_delete(current_curve + "BaseWire")

    if current_curve and _safe_exists(current_curve):
        cmds.select(current_curve, r=True)

    if stored_edges:
        cmds.select(stored_edges, add=True)

    _STATE["current_curve"] = ""
    _STATE["wire_node"] = ""
    _STATE["started"] = False


def revert_arc_deformer(*args):
    finish_arc_deformer()
    original_selection = _STATE.get("original_selection", [])
    valid = [x for x in original_selection if _safe_exists(x.split(".")[0] if "." in x else x)]
    if valid:
        try:
            cmds.select(valid, r=True)
        except:
            pass


def create_arc_deformer_from_values(values):
    sel_edges = cmds.filterExpand(expand=True, sm=32) or []
    sel_curve = cmds.filterExpand(expand=True, sm=9) or []

    if not sel_edges:
        cmds.warning("Select edge chain / edge loop first.")
        return False

    curve_type = values["curve_type"]
    use_arc = values["make_arc"]
    snap_curve = values["snap_curve"]
    even_space = values["even_space"]
    keep_curve = values["keep_curve"]
    point_count = values["point_count"]
    dropoff = values["dropoff"]

    _STATE["stored_edges"] = sel_edges[:]
    _STATE["original_selection"] = cmds.ls(sl=True, fl=True) or []

    if not keep_curve:
        finish_arc_deformer()

    if sel_curve and len(sel_curve) == 1:
        curve = sel_curve[0]
        cmds.select(sel_curve, d=True)
        target_mesh = cmds.ls(sl=True, o=True)
        is_closed, vertices = _ordered_edge_vertices(sel_edges)

        temp_curve = cmds.duplicate(curve, rr=True)[0]
        temp_curve = cmds.rename(temp_curve, "newsnapCurve")

        cmds.rebuildCurve(
            temp_curve,
            ch=True, rpo=True, rt=0, end=1,
            kr=0, kcp=0, kep=1, kt=0,
            s=100, d=1, tol=0.01
        )

        curve_tip = cmds.pointOnCurve(temp_curve, pr=0, p=True)
        d0 = _distance(_point(vertices[0]), curve_tip)
        d1 = _distance(_point(vertices[-1]), curve_tip)

        if d0 > d1:
            vertices.reverse()

        _snap_vertices_to_curve(
            vertices=vertices,
            curve=temp_curve,
            edge_count=len(sel_edges),
            even_spacing=even_space,
            closed_loop=bool(is_closed)
        )

        _safe_delete(temp_curve)

        wire_result = cmds.wire(
            target_mesh,
            gw=0, en=1, ce=0, li=0,
            dds=[(0, 1)], dt=1, w=curve
        )

        _STATE["current_curve"] = curve
        _STATE["wire_node"] = wire_result[0]
        _STATE["started"] = True

        cmds.setAttr(wire_result[0] + ".dropoffDistance[0]", dropoff if snap_curve else 1)
        cmds.select(curve, r=True)
        return True

    target_mesh = cmds.ls(sl=True, o=True)
    is_closed, vertices = _ordered_edge_vertices(sel_edges)

    if is_closed:
        curve = _build_curve_from_closed_loop(vertices, point_count=max(4, point_count))
    else:
        curve = _build_curve_from_open_chain(
            vertices=vertices,
            edges=sel_edges,
            use_arc=use_arc,
            curve_type=curve_type,
            point_count=point_count
        )

    cmds.delete(curve, ch=True)

    if snap_curve:
        _snap_vertices_to_curve(
            vertices=vertices,
            curve=curve,
            edge_count=len(sel_edges),
            even_spacing=even_space,
            closed_loop=bool(is_closed)
        )

    cmds.delete(curve, ch=True)

    if curve_type == 1:
        cmds.select(curve)
        cmds.nurbsCurveToBezier()
        if is_closed:
            cmds.closeCurve(curve, ch=False, ps=2, rpo=True, bb=0.5, bki=False, p=0.1)
            cmds.closeCurve(curve, ch=False, ps=2, rpo=True, bb=0.5, bki=False, p=0.1)

    wire_result = cmds.wire(
        target_mesh,
        gw=0, en=1, ce=0, li=0,
        dds=[(0, 1)], dt=1, w=curve
    )

    _STATE["current_curve"] = curve
    _STATE["wire_node"] = wire_result[0]
    _STATE["started"] = True

    cmds.setAttr(wire_result[0] + ".dropoffDistance[0]", dropoff if snap_curve else 1)

    if not is_closed:
        cmds.setToolTo("moveSuperContext")
        try:
            degree = cmds.getAttr(curve + ".degree")
            spans = cmds.getAttr(curve + ".spans")
            cv_count = degree + spans

            bezier_main_cvs = []
            for i in range(int(cv_count / 3) - 1):
                bezier_main_cvs.append(curve + ".cv[" + str((i + 1) * 3) + "]")

            if bezier_main_cvs:
                cmds.select(bezier_main_cvs, r=True)
        except:
            pass
    else:
        cmds.select(curve + ".cv[*]", r=True)

    for panel_id in range(1, 5):
        panel = "modelPanel%d" % panel_id
        if cmds.modelEditor(panel, exists=True):
            try:
                cmds.isolateSelect(panel, ado=curve)
            except:
                pass

    return True


# ============================================================
# UI
# ============================================================

class ArcDeformerUI(QtWidgets.QDialog):
    _instance = None

    def __init__(self, parent=get_maya_main_window()):
        super(ArcDeformerUI, self).__init__(parent)

        self.setWindowTitle("Arc Deformer Pro")
        self.setFixedWidth(320)
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.WindowCloseButtonHint)

        self._build_ui()
        self._restore_ui_values()
        self._apply_style()
        self._update_status()

    # --------------------------------------------------------
    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.btn_start = QtWidgets.QPushButton("Create")
        self.btn_start.setFixedHeight(28)
        self.btn_start.setObjectName("startBtn")
        self.btn_start.clicked.connect(self._on_create)
        layout.addWidget(self.btn_start)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.status_label)

        layout.addSpacing(2)

        self._section(layout, "TYPE")

        row_type = QtWidgets.QHBoxLayout()
        row_type.setSpacing(12)

        self.type_group = QtWidgets.QButtonGroup(self)

        self.rb_bezier = QtWidgets.QRadioButton("Bezier")
        self.rb_nurbs = QtWidgets.QRadioButton("Nurbs")
        self.rb_bezier.setChecked(True)

        self.type_group.addButton(self.rb_bezier, 1)
        self.type_group.addButton(self.rb_nurbs, 2)

        self.rb_bezier.toggled.connect(self._on_type_or_arc_changed)
        self.rb_nurbs.toggled.connect(self._on_type_or_arc_changed)

        row_type.addWidget(self.rb_bezier)
        row_type.addWidget(self.rb_nurbs)
        row_type.addStretch()

        layout.addLayout(row_type)

        layout.addSpacing(2)

        self._section(layout, "OPTIONS")

        row_opt_1 = QtWidgets.QHBoxLayout()
        row_opt_1.setSpacing(12)

        self.cb_arc = QtWidgets.QCheckBox("Arc")
        self.cb_snap = QtWidgets.QCheckBox("Snap")
        self.cb_even = QtWidgets.QCheckBox("Even")
        self.cb_keep = QtWidgets.QCheckBox("Keep Curve")

        self.cb_arc.setChecked(True)
        self.cb_snap.setChecked(True)
        self.cb_even.setChecked(True)
        self.cb_keep.setChecked(True)

        self.cb_arc.toggled.connect(self._on_type_or_arc_changed)
        self.cb_snap.toggled.connect(self._on_snap_changed)

        row_opt_1.addWidget(self.cb_arc)
        row_opt_1.addWidget(self.cb_snap)
        row_opt_1.addWidget(self.cb_even)
        row_opt_1.addWidget(self.cb_keep)
        row_opt_1.addStretch()

        layout.addLayout(row_opt_1)

        layout.addSpacing(2)

        self._section(layout, "SETTINGS")

        self.point_slider, self.point_spin = self._add_slider(
            layout, "Points", 2, 500, 3, 0
        )
        self.drop_slider, self.drop_spin = self._add_slider(
            layout, "DropOff", 0.01, 10.0, 0.01, 3
        )

        layout.addSpacing(10)

        row_btn = QtWidgets.QHBoxLayout()
        row_btn.setSpacing(8)

        self.btn_esc = QtWidgets.QPushButton("ESC")
        self.btn_esc.setFixedWidth(50)
        self.btn_esc.setFixedHeight(26)
        self.btn_esc.clicked.connect(self._on_revert)
        row_btn.addWidget(self.btn_esc)

        row_btn.addStretch()

        self.btn_done = QtWidgets.QPushButton("Done")
        self.btn_done.setFixedWidth(60)
        self.btn_done.setFixedHeight(26)
        self.btn_done.setObjectName("okBtn")
        self.btn_done.clicked.connect(self._on_done)
        row_btn.addWidget(self.btn_done)

        layout.addLayout(row_btn)

    # --------------------------------------------------------
    def _section(self, layout, text):
        lbl = QtWidgets.QLabel(text)
        lbl.setObjectName("sectionLabel")
        layout.addWidget(lbl)

    # --------------------------------------------------------
    def _add_slider(self, parent_layout, label, min_val, max_val, default, decimals):
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)

        lbl = QtWidgets.QLabel(label)
        lbl.setFixedWidth(70)
        row.addWidget(lbl)

        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setMinimum(0)
        slider.setMaximum(1000)
        row.addWidget(slider)

        if decimals > 0:
            spinbox = QtWidgets.QDoubleSpinBox()
            spinbox.setDecimals(decimals)
            spinbox.setMinimum(min_val)
            spinbox.setMaximum(max_val)
        else:
            spinbox = QtWidgets.QSpinBox()
            spinbox.setMinimum(int(min_val))
            spinbox.setMaximum(int(max_val))

        spinbox.setFixedWidth(60)
        spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        spinbox.setKeyboardTracking(False)
        spinbox.setValue(default)
        row.addWidget(spinbox)

        def update_spinbox_from_slider(val):
            ratio = val / 1000.0
            real_val = min_val + ratio * (max_val - min_val)
            spinbox.blockSignals(True)
            if decimals > 0:
                spinbox.setValue(real_val)
            else:
                spinbox.setValue(int(round(real_val)))
            spinbox.blockSignals(False)

        def update_slider_from_spinbox():
            val = spinbox.value()
            ratio = (val - min_val) / float(max_val - min_val) if (max_val - min_val) != 0 else 0.0
            slider.blockSignals(True)
            slider.setValue(int(ratio * 1000))
            slider.blockSignals(False)

        slider.valueChanged.connect(update_spinbox_from_slider)
        spinbox.editingFinished.connect(update_slider_from_spinbox)

        ratio = (default - min_val) / float(max_val - min_val) if (max_val - min_val) != 0 else 0.0
        slider.setValue(int(ratio * 1000))

        parent_layout.addLayout(row)
        return slider, spinbox

    # --------------------------------------------------------
    def _collect_values(self):
        return {
            "curve_type": 1 if self.rb_bezier.isChecked() else 2,
            "make_arc": self.cb_arc.isChecked(),
            "snap_curve": self.cb_snap.isChecked(),
            "even_space": self.cb_even.isChecked(),
            "keep_curve": self.cb_keep.isChecked(),
            "point_count": int(self.point_spin.value()),
            "dropoff": float(self.drop_spin.value()),
        }

    # --------------------------------------------------------
    def _save_ui_values(self):
        _STATE["ui_values"] = {
            "curve_type": 1 if self.rb_bezier.isChecked() else 2,
            "make_arc": self.cb_arc.isChecked(),
            "snap_curve": self.cb_snap.isChecked(),
            "even_space": self.cb_even.isChecked(),
            "keep_curve": self.cb_keep.isChecked(),
            "point_count": int(self.point_spin.value()),
            "dropoff": float(self.drop_spin.value()),
        }

    def _restore_ui_values(self):
        values = _STATE.get("ui_values", {})
        if not values:
            self._apply_point_rules()
            self._apply_snap_rules()
            return

        curve_type = values.get("curve_type", 1)
        self.rb_bezier.setChecked(curve_type == 1)
        self.rb_nurbs.setChecked(curve_type == 2)

        self.cb_arc.setChecked(values.get("make_arc", True))
        self.cb_snap.setChecked(values.get("snap_curve", True))
        self.cb_even.setChecked(values.get("even_space", True))
        self.cb_keep.setChecked(values.get("keep_curve", True))

        self.point_spin.setValue(values.get("point_count", 3))
        self.drop_spin.setValue(values.get("dropoff", 0.01))

        self._sync_point_slider()
        self._sync_drop_slider()
        self._apply_point_rules()
        self._apply_snap_rules()

    # --------------------------------------------------------
    def _sync_point_slider(self):
        ratio = (self.point_spin.value() - 2) / float(500 - 2)
        self.point_slider.blockSignals(True)
        self.point_slider.setValue(int(ratio * 1000))
        self.point_slider.blockSignals(False)

    def _sync_drop_slider(self):
        ratio = (self.drop_spin.value() - 0.01) / float(10.0 - 0.01)
        self.drop_slider.blockSignals(True)
        self.drop_slider.setValue(int(ratio * 1000))
        self.drop_slider.blockSignals(False)

    # --------------------------------------------------------
    def _apply_point_rules(self):
        if not self.cb_arc.isChecked():
            minimum = 4
            if self.point_spin.value() < minimum:
                self.point_spin.setValue(minimum)
        else:
            if self.rb_bezier.isChecked():
                minimum = 2
                if self.point_spin.value() < 3:
                    self.point_spin.setValue(3)
            else:
                minimum = 4
                if self.point_spin.value() < 4:
                    self.point_spin.setValue(4)

        self.point_spin.setMinimum(minimum)

    def _apply_snap_rules(self):
        self.cb_even.setEnabled(self.cb_snap.isChecked())

    # --------------------------------------------------------
    def _on_type_or_arc_changed(self):
        self._apply_point_rules()

    def _on_snap_changed(self):
        self._apply_snap_rules()

    # --------------------------------------------------------
    def _on_create(self):
        try:
            values = self._collect_values()
            ok = create_arc_deformer_from_values(values)
            if ok:
                self._save_ui_values()
                self._update_status()
        except Exception as e:
            cmds.warning("Arc Deformer failed: %s" % str(e))

    def _on_done(self):
        finish_arc_deformer()
        self._update_status()

    def _on_revert(self):
        revert_arc_deformer()
        self._update_status()

    # --------------------------------------------------------
    def _update_status(self):
        if _STATE.get("started", False):
            self.status_label.setText("Arc deformer active")
        else:
            self.status_label.setText("Ready")

    # --------------------------------------------------------
    def _apply_style(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 8px;
            }

            QLabel {
                color: #b0b0b0;
                font-size: 11px;
            }

            QLabel#statusLabel {
                color: %(red)s;
                font-size: 10px;
                font-weight: bold;
                padding: 2px;
            }

            QLabel#sectionLabel {
                color: #707070;
                font-size: 9px;
                font-weight: bold;
                padding-top: 4px;
                border-top: 1px solid #3a3a3a;
                margin-top: 4px;
            }

            QPushButton {
                background-color: #3a3a3a;
                color: #b0b0b0;
                border: 1px solid #4a4a4a;
                border-radius: 6px;
                font-size: 11px;
                padding: 5px 8px;
            }

            QPushButton:hover {
                background-color: #454545;
            }

            QPushButton:pressed {
                background-color: #2a2a2a;
            }

            QPushButton#startBtn {
                background-color: %(bg)s;
                color: #ffffff;
                border: 1px solid %(border)s;
                border-radius: 6px;
                font-weight: bold;
            }

            QPushButton#startBtn:hover {
                background-color: #6a3333;
            }

            QPushButton#okBtn {
                background-color: %(bg)s;
                color: #ffffff;
                border: 1px solid %(border)s;
                border-radius: 6px;
                font-weight: bold;
            }

            QPushButton#okBtn:hover {
                background-color: #6a3333;
            }

            QSlider::groove:horizontal {
                height: 4px;
                background: #1a1a1a;
                border-radius: 2px;
            }

            QSlider::handle:horizontal {
                background: #888888;
                width: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }

            QSlider::handle:horizontal:hover {
                background: #aaaaaa;
            }

            QSpinBox, QDoubleSpinBox {
                background-color: #252525;
                color: #b0b0b0;
                border: 1px solid #3a3a3a;
                border-radius: 4px;
                padding: 3px;
            }

            QRadioButton {
                color: #b0b0b0;
                font-size: 11px;
                spacing: 6px;
            }

            QRadioButton::indicator {
                width: 14px;
                height: 14px;
                border-radius: 7px;
                border: 1px solid #4a4a4a;
                background-color: #252525;
            }

            QRadioButton::indicator:checked {
                background-color: %(bg)s;
                border-color: %(border)s;
            }

            QCheckBox {
                color: #b0b0b0;
                font-size: 11px;
                spacing: 6px;
            }

            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border-radius: 3px;
                border: 1px solid #4a4a4a;
                background-color: #252525;
            }

            QCheckBox::indicator:checked {
                background-color: %(bg)s;
                border-color: %(border)s;
            }
        """ % {
            "red": ACCENT_RED_TEXT,
            "bg": ACCENT_RED_BG,
            "border": ACCENT_RED_BORDER,
        })

    # --------------------------------------------------------
    def closeEvent(self, event):
        try:
            self._save_ui_values()
        except:
            pass
        ArcDeformerUI._instance = None
        super(ArcDeformerUI, self).closeEvent(event)

    # --------------------------------------------------------
    @classmethod
    def show_ui(cls):
        if cls._instance is not None:
            try:
                cls._instance.close()
            except:
                pass
            cls._instance = None

        inst = cls(parent=get_maya_main_window())
        cls._instance = inst
        inst.show()
        return inst


# ============================================================
# ENTRY
# ============================================================

def show_ui():
    return ArcDeformerUI.show_ui()


show_ui()
