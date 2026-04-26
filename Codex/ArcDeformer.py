# -*- coding: utf-8 -*-
"""
=============================================================================
ARC DEFORMER PRO v2.2
=============================================================================
Standalone Arc / Curve Wire Deformer

Changelog v2.2:
- FIX MAJEUR : Suppression des fonctions de "Snap" qui modifiaient la
  géométrie originale. Zéro mouvement de vertex à la création.
- FIX MAJEUR : Le bouton "Done" ne supprime plus le noeud "BaseWire" caché.
  Le deformer reste actif et appliqué à 100%.
- Nettoyage de l'UI (suppression des options destructives).
=============================================================================
"""

import math
import maya.cmds as cmds
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
    "current_curve":      "",
    "stored_edges":       [],
    "wire_node":          "",
    "original_selection": [],
    "started":            False,
    "ui_values":          {},
    "undo_was_open":      False,
    "temp_layer":         "ARC_DEFORMER_PRO_LYR",
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
    except Exception:
        return False

def get_maya_main_window():
    try:
        ptr = omui.MQtUtil.mainWindow()
        return wrapInstance(int(ptr), QtWidgets.QWidget)
    except Exception:
        return None

# ============================================================
# GEOMETRY HELPERS
# ============================================================

def _distance(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))

def _point(component):
    return cmds.pointPosition(component, w=True)

def _ordered_edge_vertices(edges=None):
    """Trie les vertices pour former une chaîne continue le long des edges."""
    sel_edges = edges or cmds.ls(sl=True, fl=True)
    if not sel_edges:
        raise RuntimeError("Sélectionnez des edges d'abord.")

    shape     = cmds.listRelatives(sel_edges[0], fullPath=True, parent=True)[0]
    transform = cmds.listRelatives(shape,         fullPath=True, parent=True)[0]

    all_vtx_ids = []
    for e in sel_edges:
        vts = cmds.ls(cmds.polyListComponentConversion(e, fe=True, tv=True), fl=True)
        all_vtx_ids.extend([v.split("[")[-1][:-1] for v in vts])

    counts    = {v: all_vtx_ids.count(v) for v in set(all_vtx_ids)}
    endpoints = [v for v, c in counts.items() if c == 1]

    is_closed = len(endpoints) == 0
    start_v   = endpoints[0] if not is_closed else all_vtx_ids[0]

    ordered_ids      = [start_v]
    remaining_edges  = list(sel_edges)

    while remaining_edges:
        current_v = ordered_ids[-1]
        found = False
        for e in remaining_edges:
            ev = cmds.ls(cmds.polyListComponentConversion(e, fe=True, tv=True), fl=True)
            ev_ids = [v.split("[")[-1][:-1] for v in ev]
            if current_v in ev_ids:
                next_v = ev_ids[0] if ev_ids[1] == current_v else ev_ids[1]
                if next_v in ordered_ids and is_closed and len(ordered_ids) > 1:
                    remaining_edges.remove(e)
                    found = True
                    break
                ordered_ids.append(next_v)
                remaining_edges.remove(e)
                found = True
                break
        if not found:
            break

    return is_closed, ["{}.vtx[{}]".format(transform, vid) for vid in ordered_ids]

# ============================================================
# PARAMETRIC PATH HELPERS
# ============================================================

def _get_u_params(vertices):
    """Retourne les paramètres U normalisés [0..1] basés sur la distance."""
    points = [_point(v) for v in vertices]
    lengths = [0.0]
    total   = 0.0

    for i in range(len(points) - 1):
        d = _distance(points[i], points[i + 1])
        total += d
        lengths.append(total)

    if total <= 1e-8:
        return [0.0] * len(vertices)

    return [l / total for l in lengths]

def _interpolate_path(t, points, u_params):
    """Trouve la position 3D exacte sur la polyligne."""
    for i in range(len(u_params) - 1):
        if u_params[i] <= t <= u_params[i + 1]:
            denom = u_params[i + 1] - u_params[i]
            if denom <= 1e-8:
                return list(points[i])
            seg_t = (t - u_params[i]) / denom
            p1, p2 = points[i], points[i + 1]
            return [p1[j] + seg_t * (p2[j] - p1[j]) for j in range(3)]
    return list(points[-1])

# ============================================================
# MESH PROTECTION
# ============================================================

def _enable_mesh_protection():
    """Met les meshes en mode Reference (non cliquables)."""
    layer = _STATE["temp_layer"]
    if not cmds.objExists(layer):
        cmds.createDisplayLayer(name=layer, empty=True)

    all_geo   = cmds.ls(type="geometryShape") or []
    transforms = list(set(cmds.listRelatives(all_geo, p=True, f=True) or []))
    to_protect = [obj for obj in transforms if _STATE["current_curve"] not in obj]

    if to_protect:
        cmds.editDisplayLayerMembers(layer, to_protect)

    cmds.setAttr("{}.displayType".format(layer), 2)  # 2 = Reference

def _disable_mesh_protection():
    """Supprime la protection."""
    layer = _STATE["temp_layer"]
    if cmds.objExists(layer):
        cmds.delete(layer)

# ============================================================
# CURVE BUILD
# ============================================================

def _build_arc_curve(vertices, point_count, is_closed, curve_type=1):
    """Construit la courbe sans jamais déplacer la géométrie."""
    points   = [_point(v) for v in vertices]
    u_params = _get_u_params(vertices)

    curve = cmds.curve(d=1, p=points, name="arcDeformer_Curve")
    if is_closed:
        cmds.closeCurve(curve, ch=False, ps=2, rpo=True, bb=0.5, bki=False, p=0.1)

    spans = max(1, int(point_count) - 3 if not is_closed else int(point_count))
    cmds.rebuildCurve(curve, ch=False, rpo=True, rt=0, end=1, kr=0, kcp=0, kep=1, kt=0, s=spans, d=3, tol=0.001)

    cvs = cmds.ls("{}.cv[*]".format(curve), fl=True)
    cv_count = len(cvs)
    for i, cv in enumerate(cvs):
        t = float(i) / max(cv_count - 1, 1)
        pos = _interpolate_path(t, points, u_params)
        cmds.xform(cv, ws=True, t=pos)

    if curve_type == 1:
        cmds.select(curve)
        cmds.nurbsCurveToBezier()
        if is_closed:
            cmds.closeCurve(curve, ch=False, ps=2, rpo=True, bb=0.5, bki=False, p=0.1)

    cmds.delete(curve, ch=True)
    return curve

# ============================================================
# ARC DEFORMER CORE
# ============================================================

def finish_arc_deformer(*args):
    """Bouton Done : valide la déformation et nettoie la scène sans rien casser."""
    _disable_mesh_protection()

    current_curve = _STATE.get("current_curve", "")
    if current_curve and _safe_exists(current_curve):
        cmds.select(current_curve, r=True)

    if _STATE.get("undo_was_open", False):
        try:
            cmds.undoInfo(closeChunk=True)
        except Exception:
            pass
        _STATE["undo_was_open"] = False

    _STATE["current_curve"] = ""
    _STATE["wire_node"]     = ""
    _STATE["started"]       = False

def revert_arc_deformer(*args):
    """Bouton ESC / Cancel : annule tout."""
    _disable_mesh_protection()

    if _STATE.get("undo_was_open", False):
        try:
            cmds.undoInfo(closeChunk=True)
        except Exception:
            pass
        _STATE["undo_was_open"] = False

    try:
        cmds.undo()
    except Exception:
        pass

    original_selection = _STATE.get("original_selection", [])
    valid = [x for x in original_selection if _safe_exists(x.split(".")[0] if "." in x else x)]
    if valid:
        try:
            cmds.select(valid, r=True)
        except Exception:
            pass

    _STATE["current_curve"] = ""
    _STATE["wire_node"]     = ""
    _STATE["started"]       = False

def create_arc_deformer_from_values(values):
    sel_edges = cmds.filterExpand(expand=True, sm=32) or []

    if not sel_edges:
        cmds.warning("Sélectionnez une chaîne d'edges d'abord.")
        return False

    curve_type  = values["curve_type"]
    point_count = values["point_count"]
    dropoff     = values["dropoff"]

    _STATE["stored_edges"]       = sel_edges[:]
    _STATE["original_selection"] = cmds.ls(sl=True, fl=True) or []

    try:
        cmds.undoInfo(openChunk=True, chunkName="arcDeformerCreate")
        _STATE["undo_was_open"] = True
    except Exception:
        _STATE["undo_was_open"] = False

    target_mesh = cmds.ls(sl=True, o=True)
    is_closed, vertices = _ordered_edge_vertices(sel_edges)

    pc = max(4, point_count)
    curve = _build_arc_curve(
        vertices    = vertices,
        point_count = pc,
        is_closed   = is_closed,
        curve_type  = curve_type,
    )

    # Création du Wire Deformer (sans bouger la géométrie !)
    wire_result = cmds.wire(target_mesh, gw=0, en=1, ce=0, li=0, dds=[(0, 1)], dt=1, w=curve)

    _STATE["current_curve"] = curve
    _STATE["wire_node"]     = wire_result[0]
    _STATE["started"]       = True

    cmds.setAttr(wire_result[0] + ".dropoffDistance[0]", dropoff)

    cmds.setToolTo("moveSuperContext")
    try:
        degree = cmds.getAttr(curve + ".degree")
        spans = cmds.getAttr(curve + ".spans")
        cv_count = degree + spans
        if curve_type == 1:
            bezier_main_cvs = ["{}.cv[{}]".format(curve, (i + 1) * 3) for i in range(int(cv_count / 3) - 1)]
            if bezier_main_cvs: cmds.select(bezier_main_cvs, r=True)
            else: cmds.select("{}.cv[*]".format(curve), r=True)
        else:
            cmds.select("{}.cv[*]".format(curve), r=True)
    except Exception:
        cmds.select("{}.cv[*]".format(curve), r=True)

    _enable_mesh_protection()

    # Isolate curve
    for panel_id in range(1, 5):
        panel = "modelPanel%d" % panel_id
        if cmds.modelEditor(panel, exists=True):
            try: cmds.isolateSelect(panel, ado=curve)
            except Exception: pass

    return True

# ============================================================
# UI
# ============================================================

class ArcDeformerUI(QtWidgets.QDialog):
    _instance = None

    def __init__(self, parent=get_maya_main_window()):
        super(ArcDeformerUI, self).__init__(parent)
        self.setWindowTitle("Arc Deformer Pro")
        self.setFixedWidth(340)
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.WindowCloseButtonHint)
        self._build_ui()
        self._restore_ui_values()
        self._apply_style()
        self._update_status()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.btn_start = QtWidgets.QPushButton("Create Deformer")
        self.btn_start.setFixedHeight(35)
        self.btn_start.setObjectName("startBtn")
        self.btn_start.clicked.connect(self._on_create)
        layout.addWidget(self.btn_start)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.status_label)

        self._section(layout, "TYPE")
        row_type = QtWidgets.QHBoxLayout()
        self.type_group = QtWidgets.QButtonGroup(self)
        self.rb_bezier  = QtWidgets.QRadioButton("Bezier")
        self.rb_nurbs   = QtWidgets.QRadioButton("Nurbs")
        self.rb_bezier.setChecked(True)
        self.type_group.addButton(self.rb_bezier, 1)
        self.type_group.addButton(self.rb_nurbs,  2)
        self.rb_bezier.toggled.connect(self._apply_point_rules)
        self.rb_nurbs.toggled.connect(self._apply_point_rules)
        row_type.addWidget(self.rb_bezier)
        row_type.addWidget(self.rb_nurbs)
        row_type.addStretch()
        layout.addLayout(row_type)

        self._section(layout, "SETTINGS")
        self.point_slider, self.point_spin = self._add_slider(layout, "Points", 4, 100, 8, 0)
        self.drop_slider, self.drop_spin = self._add_slider(layout, "DropOff", 0.01, 100.0, 5.0, 2)

        layout.addSpacing(10)

        row_btn = QtWidgets.QHBoxLayout()
        self.btn_esc = QtWidgets.QPushButton("Cancel")
        self.btn_esc.setFixedWidth(60)
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

    def _section(self, layout, text):
        lbl = QtWidgets.QLabel(text)
        lbl.setObjectName("sectionLabel")
        layout.addWidget(lbl)

    def _add_slider(self, parent_layout, label, min_val, max_val, default, decimals):
        row = QtWidgets.QHBoxLayout()
        lbl = QtWidgets.QLabel(label)
        lbl.setFixedWidth(60)
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

        spinbox.setFixedWidth(50)
        spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        spinbox.setValue(default)
        row.addWidget(spinbox)

        def update_spinbox(val):
            ratio = val / 1000.0
            real_val = min_val + ratio * (max_val - min_val)
            spinbox.blockSignals(True)
            if decimals > 0: spinbox.setValue(real_val)
            else: spinbox.setValue(int(round(real_val)))
            spinbox.blockSignals(False)

        def update_slider():
            val = spinbox.value()
            ratio = (val - min_val) / float(max_val - min_val) if (max_val - min_val) != 0 else 0.0
            slider.blockSignals(True)
            slider.setValue(int(ratio * 1000))
            slider.blockSignals(False)

        slider.valueChanged.connect(update_spinbox)
        spinbox.editingFinished.connect(update_slider)

        ratio = (default - min_val) / float(max_val - min_val)
        slider.setValue(int(ratio * 1000))
        parent_layout.addLayout(row)
        return slider, spinbox

    def _collect_values(self):
        return {
            "curve_type":  1 if self.rb_bezier.isChecked() else 2,
            "point_count": int(self.point_spin.value()),
            "dropoff":     float(self.drop_spin.value()),
        }

    def _save_ui_values(self):
        _STATE["ui_values"] = self._collect_values()

    def _restore_ui_values(self):
        values = _STATE.get("ui_values", {})
        if values:
            self.rb_bezier.setChecked(values.get("curve_type", 1) == 1)
            self.rb_nurbs.setChecked(values.get("curve_type", 1) == 2)
            self.point_spin.setValue(values.get("point_count", 8))
            self.drop_spin.setValue(values.get("dropoff", 5.0))
        self._apply_point_rules()

    def _apply_point_rules(self):
        minimum = 4
        if self.point_spin.value() < minimum:
            self.point_spin.setValue(minimum)
        self.point_spin.setMinimum(minimum)

    def _on_create(self):
        try:
            values = self._collect_values()
            ok = create_arc_deformer_from_values(values)
            if ok:
                self._save_ui_values()
                self._update_status()
        except Exception as e:
            cmds.warning("Erreur Arc Deformer : %s" % str(e))

    def _on_done(self):
        finish_arc_deformer()
        self._update_status()

    def _on_revert(self):
        revert_arc_deformer()
        self._update_status()

    def _update_status(self):
        if _STATE.get("started", False):
            self.status_label.setText("Deformer Active - Editing...")
        else:
            self.status_label.setText("Ready")

    def closeEvent(self, event):
        revert_arc_deformer()
        super(ArcDeformerUI, self).closeEvent(event)

    def _apply_style(self):
        self.setStyleSheet("""
            QDialog { background-color: #2d2d2d; border-radius: 8px; }
            QLabel { color: #b0b0b0; font-size: 11px; }
            QLabel#statusLabel { color: %(red)s; font-size: 10px; font-weight: bold; }
            QLabel#sectionLabel { color: #707070; font-size: 9px; font-weight: bold; border-top: 1px solid #3a3a3a; padding-top: 4px; }
            QPushButton { background-color: #3a3a3a; color: #b0b0b0; border: 1px solid #4a4a4a; border-radius: 4px; padding: 4px; }
            QPushButton:hover { background-color: #454545; }
            QPushButton#startBtn, QPushButton#okBtn { background-color: %(bg)s; color: white; border: 1px solid %(border)s; font-weight: bold; }
            QPushButton#startBtn:hover, QPushButton#okBtn:hover { background-color: #6a3333; }
            QSlider::groove:horizontal { height: 4px; background: #1a1a1a; border-radius: 2px; }
            QSlider::handle:horizontal { background: #888; width: 12px; margin: -4px 0; border-radius: 6px; }
            QSpinBox, QDoubleSpinBox { background-color: #252525; color: #b0b0b0; border: 1px solid #3a3a3a; border-radius: 2px; }
            QRadioButton { color: #b0b0b0; font-size: 11px; }
            QRadioButton::indicator { width: 12px; height: 12px; border-radius: 6px; border: 1px solid #4a4a4a; background-color: #252525; }
            QRadioButton::indicator:checked { background-color: %(bg)s; border-color: %(border)s; }
        """ % {"bg": ACCENT_RED_BG, "border": ACCENT_RED_BORDER, "red": ACCENT_RED_TEXT})

# ============================================================
# MAIN ENTRY POINT
# ============================================================

def main():
    global arc_deformer_ui_instance
    try:
        arc_deformer_ui_instance.close()
        arc_deformer_ui_instance.deleteLater()
    except: pass
    arc_deformer_ui_instance = ArcDeformerUI()
    arc_deformer_ui_instance.show()

if __name__ == "__main__":
    main()
