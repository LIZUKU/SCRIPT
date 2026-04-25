# -*- coding: utf-8 -*-

import math



import maya.cmds as cmds

import maya.mel as mel

import maya.api.OpenMaya as om

import maya.OpenMayaUI as omui



try:

    from PySide6 import QtCore, QtWidgets

    from shiboken6 import wrapInstance

except ImportError:

    from PySide2 import QtCore, QtWidgets

    from shiboken2 import wrapInstance





class CollapsibleSection(QtWidgets.QWidget):

    toggled = QtCore.Signal(bool)

    def __init__(self, title, parent=None, expanded=False):

        super(CollapsibleSection, self).__init__(parent)

        self.toggle_button = QtWidgets.QToolButton()

        self.toggle_button.setText(title)

        self.toggle_button.setCheckable(True)

        self.toggle_button.setChecked(expanded)

        self.toggle_button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)

        self.toggle_button.setArrowType(

            QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow

        )



        self.content = QtWidgets.QWidget()

        self.content_layout = QtWidgets.QVBoxLayout(self.content)

        self.content_layout.setContentsMargins(8, 8, 8, 8)

        self.content.setVisible(expanded)



        layout = QtWidgets.QVBoxLayout(self)

        layout.setContentsMargins(0, 0, 0, 0)

        layout.setSpacing(4)

        layout.addWidget(self.toggle_button)

        layout.addWidget(self.content)



        self.toggle_button.toggled.connect(self._on_toggled)



    def _on_toggled(self, checked):

        self.content.setVisible(checked)

        self.toggle_button.setArrowType(QtCore.Qt.DownArrow if checked else QtCore.Qt.RightArrow)

        self.toggled.emit(checked)





class WeightedNormalsTool(QtWidgets.QDialog):

    WINDOW_NAME = "WeightedNormalsUI_PowerSnap"



    def __init__(self, parent=None):

        super(WeightedNormalsTool, self).__init__(parent)

        self.setObjectName(self.WINDOW_NAME)

        self.setWindowTitle("Weighted Normals Pro")

        self.resize(380, 300)

        self.setMinimumWidth(360)

        self.setMinimumHeight(220)

        self._ui_scale = 1.0

        self._base_font_size = max(float(self.font().pointSizeF()), 10.0)

        self._section_widgets = []



        self._build_ui()

        self.update_ui_states()



    # =========================================================

    # UI

    # =========================================================

    def _build_ui(self):

        root = QtWidgets.QVBoxLayout(self)

        root.setContentsMargins(8, 8, 8, 8)

        root.setSpacing(6)

        root.setSizeConstraint(QtWidgets.QLayout.SetMinimumSize)

        self._build_header_controls(root)

        self._build_weighting_section(root)

        self._build_hard_edges_section(root)

        self._build_smoothing_section(root)

        self._build_display_section(root)



        root.addSpacing(4)



        self.btn_apply = QtWidgets.QPushButton("APPLY WEIGHTED NORMALS")

        self.btn_apply.setMinimumHeight(34)

        self.btn_apply.clicked.connect(self.apply_normals)



        self.btn_unfreeze = QtWidgets.QPushButton("UNFREEZE NORMALS")

        self.btn_unfreeze.setMinimumHeight(28)

        self.btn_unfreeze.clicked.connect(self.unfreeze_normals)



        root.addWidget(self.btn_apply)

        root.addWidget(self.btn_unfreeze)

        root.addStretch(1)



        self.setStyleSheet(

            """

            QDialog { background: #1f1f1f; color: #ececec; }

            QToolButton {

                background: #2c2c2c;

                border: 1px solid #434343;

                border-radius: 6px;

                padding: 5px;

                font-weight: 600;

                text-align: left;

            }

            QGroupBox {

                border: 1px solid #3f3f3f;

                border-radius: 8px;

                margin-top: 10px;

                padding-top: 12px;

                background: #242424;

            }

            QLabel { color: #d2d2d2; }

            QCheckBox, QRadioButton, QPushButton { font-size: 11px; }

            QDoubleSpinBox, QSpinBox {

                background: #202020;

                border: 1px solid #4a4a4a;

                border-radius: 4px;

                min-height: 20px;

                padding: 0px 4px;

            }

            QSlider::groove:horizontal { height: 6px; background: #808080; border-radius: 3px; }

            QSlider::handle:horizontal {

                background: #c7c7c7;

                border: 1px solid #9b9b9b;

                width: 12px;

                margin: -4px 0;

                border-radius: 6px;

            }

            QPushButton {

                background: #353535;

                border: 1px solid #505050;

                border-radius: 6px;

                padding: 5px;

            }

            QPushButton:hover { background: #424242; }

            QPushButton#apply_btn { background: #8f2f2f; border-color: #b44a4a; font-weight: 700; }

            QPushButton#apply_btn:hover { background: #9c3737; }

            QPushButton[stepBtn="true"] {

                min-width: 22px;

                max-width: 22px;

                padding: 0px;

                font-weight: 700;

                background: #3b3b3b;

                border: 1px solid #5b5b5b;

            }

            QPushButton[stepBtn="true"]:hover { background: #4a4a4a; }

            QPushButton[modeBtn="true"] { background: #3c3c3c; border: 1px solid #606060; }

            QPushButton[modeBtn="true"]:checked {

                background: #8b2c2c;

                border: 1px solid #b34949;

                color: #ffffff;

                font-weight: 700;

            }

            """

        )

        self.btn_apply.setObjectName("apply_btn")

        self._apply_ui_scale(1.0)



    def _build_header_controls(self, parent_layout):

        header = QtWidgets.QHBoxLayout()

        header.setContentsMargins(0, 0, 0, 0)

        header.addStretch(1)

        scale_label = QtWidgets.QLabel("UI Scale")

        header.addWidget(scale_label)

        self.scale_combo = QtWidgets.QComboBox()

        for pct in (100, 90, 80, 50):

            self.scale_combo.addItem("{}%".format(pct), pct / 100.0)

        self.scale_combo.setCurrentIndex(0)

        self.scale_combo.currentIndexChanged.connect(self._on_ui_scale_changed)

        header.addWidget(self.scale_combo)

        parent_layout.addLayout(header)



    def _build_weighting_section(self, parent_layout):

        section = CollapsibleSection("Weighting", expanded=True)

        section.toggled.connect(self._refresh_dialog_size)

        self._section_widgets.append(section)

        parent_layout.addWidget(section)

        layout = section.content_layout



        mode_row = QtWidgets.QHBoxLayout()

        mode_row.setSpacing(5)

        self.mode_group = QtWidgets.QButtonGroup(self)

        self.mode_group.setExclusive(True)



        self.rb_area = QtWidgets.QPushButton("Area")

        self.rb_angle = QtWidgets.QPushButton("Angle")

        self.rb_both = QtWidgets.QPushButton("Area + Angle")



        for btn, mode in [(self.rb_area, "area"), (self.rb_angle, "angle"), (self.rb_both, "both")]:

            btn.setCheckable(True)

            btn.setProperty("modeBtn", True)

            btn.setProperty("mode", mode)

            self.mode_group.addButton(btn)

            mode_row.addWidget(btn)



        self.rb_area.setChecked(True)

        layout.addLayout(mode_row)



        self.chk_convex = QtWidgets.QCheckBox("Use Convex Corner Angle")

        self.chk_convex.setChecked(True)

        layout.addWidget(self.chk_convex)



        self.chk_snap = QtWidgets.QCheckBox("Snap To Largest Face")

        self.chk_snap.setChecked(True)

        self.chk_snap.toggled.connect(self.update_ui_states)

        layout.addWidget(self.chk_snap)



        self.snap_strength = self._add_slider_row(layout, "Snap Strength", 0.0, 1.0, 0.9, decimals=2)

        self.snap_power = self._add_slider_row(layout, "Snap Power", 1, 128, 15, is_int=True)

        self.blending = self._add_slider_row(layout, "Blending", 0.0, 1.0, 1.0, decimals=2)



    def _build_hard_edges_section(self, parent_layout):

        section = CollapsibleSection("Hard Edge Detection", expanded=False)

        section.toggled.connect(self._refresh_dialog_size)

        self._section_widgets.append(section)

        parent_layout.addWidget(section)

        layout = section.content_layout



        self.chk_edge_angle = QtWidgets.QCheckBox("By Edge Angle")

        self.chk_edge_angle.setChecked(False)

        self.chk_edge_angle.toggled.connect(self.update_ui_states)

        layout.addWidget(self.chk_edge_angle)



        self.edge_angle = self._add_slider_row(layout, "Edge Angle", 0.0, 180.0, 30.0, decimals=1)



    def _build_smoothing_section(self, parent_layout):

        section = CollapsibleSection("Smoothing", expanded=False)

        section.toggled.connect(self._refresh_dialog_size)

        self._section_widgets.append(section)

        parent_layout.addWidget(section)

        layout = section.content_layout



        self.smoothing = self._add_slider_row(layout, "Smoothing", 0.0, 1.0, 0.0, decimals=2)

        self.iterations = self._add_slider_row(layout, "Iterations", 1, 100, 1, is_int=True)



    def _build_display_section(self, parent_layout):

        section = CollapsibleSection("Display Normals", expanded=False)

        section.toggled.connect(self._refresh_dialog_size)

        self._section_widgets.append(section)

        parent_layout.addWidget(section)

        layout = section.content_layout



        self.chk_display = QtWidgets.QCheckBox("Display Normals")

        self.chk_display.setChecked(False)

        self.chk_display.toggled.connect(self.toggle_display)

        layout.addWidget(self.chk_display)



        self.display_length = self._add_slider_row(layout, "Display Length", 0.1, 50.0, 10.0, decimals=2)



    def _add_slider_row(self, parent_layout, label, minimum, maximum, value, is_int=False, decimals=2):

        row = QtWidgets.QHBoxLayout()

        row.setSpacing(6)



        text = QtWidgets.QLabel(label)

        text.setMinimumWidth(96)

        row.addWidget(text)



        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)

        slider.setMinimum(0)

        slider.setMaximum(1000)

        row.addWidget(slider, 1)



        if is_int:

            spin = QtWidgets.QSpinBox()

            spin.setRange(int(minimum), int(maximum))

            spin.setValue(int(value))

        else:

            spin = QtWidgets.QDoubleSpinBox()

            spin.setDecimals(decimals)

            spin.setRange(float(minimum), float(maximum))

            spin.setValue(float(value))



        minus_btn = QtWidgets.QPushButton("-")

        minus_btn.setProperty("stepBtn", True)

        plus_btn = QtWidgets.QPushButton("+")

        plus_btn.setProperty("stepBtn", True)



        spin.setMinimumWidth(70)

        row.addWidget(minus_btn)

        row.addWidget(spin)

        row.addWidget(plus_btn)



        parent_layout.addLayout(row)



        widget = {

            "slider": slider,

            "spin": spin,

            "min": float(minimum),

            "max": float(maximum),

            "is_int": is_int,

        }



        def spin_to_slider(v):

            ratio = 0.0 if widget["max"] <= widget["min"] else (float(v) - widget["min"]) / (widget["max"] - widget["min"])

            slider.blockSignals(True)

            slider.setValue(int(max(0.0, min(1.0, ratio)) * 1000.0))

            slider.blockSignals(False)



        def slider_to_spin(v):

            ratio = float(v) / 1000.0

            out = widget["min"] + ((widget["max"] - widget["min"]) * ratio)

            spin.blockSignals(True)

            if widget["is_int"]:

                spin.setValue(int(round(out)))

            else:

                spin.setValue(float(out))

            spin.blockSignals(False)



        spin.valueChanged.connect(spin_to_slider)

        slider.valueChanged.connect(slider_to_spin)



        def step_spin(delta):

            step = 1 if widget["is_int"] else (10 ** (-spin.decimals()))

            spin.setValue(spin.value() + (step * delta))



        minus_btn.clicked.connect(lambda: step_spin(-1))

        plus_btn.clicked.connect(lambda: step_spin(1))

        spin_to_slider(spin.value())



        return widget



    def _value(self, control):

        return control["spin"].value()



    def _set_enabled(self, control, enabled):

        control["slider"].setEnabled(enabled)

        control["spin"].setEnabled(enabled)



    def update_ui_states(self, *args):

        use_snap = self.chk_snap.isChecked()

        use_edge = self.chk_edge_angle.isChecked()

        show_normals = self.chk_display.isChecked()



        self._set_enabled(self.snap_strength, use_snap)

        self._set_enabled(self.snap_power, use_snap)

        self._set_enabled(self.edge_angle, use_edge)

        self._set_enabled(self.display_length, show_normals)

    def _on_ui_scale_changed(self, index):

        if index < 0:

            return

        factor = self.scale_combo.itemData(index)

        self._apply_ui_scale(float(factor))



    def _apply_ui_scale(self, factor):

        self._ui_scale = max(0.5, min(2.0, float(factor)))

        font = self.font()

        font.setPointSizeF(self._base_font_size * self._ui_scale)

        self.setFont(font)

        self.btn_apply.setMinimumHeight(max(24, int(round(34 * self._ui_scale))))

        self.btn_unfreeze.setMinimumHeight(max(20, int(round(28 * self._ui_scale))))

        self._refresh_dialog_size()



    def _refresh_dialog_size(self, *args):

        self.layout().activate()

        self.adjustSize()



    # =========================================================

    # HELPERS

    # =========================================================

    def get_selected_meshes(self):

        sel = om.MSelectionList()
        try:
            om.MGlobal.getActiveSelectionList(sel, om.MGlobal.kReplaceList)
        except TypeError:
            try:
                om.MGlobal.getActiveSelectionList(sel)
            except TypeError:
                sel = om.MGlobal.getActiveSelectionList()

        meshes = []



        for i in range(sel.length()):

            try:

                dag_path = sel.getDagPath(i)



                if dag_path.hasFn(om.MFn.kTransform):

                    try:

                        dag_path.extendToShape()

                    except Exception:

                        pass



                if dag_path.hasFn(om.MFn.kMesh):

                    meshes.append(dag_path)

            except Exception:

                continue



        return meshes



    def safe_normalize(self, vec):

        out_vec = om.MVector(vec)

        if out_vec.length() > 1e-8:

            out_vec.normalize()

            return out_vec

        return om.MVector(0.0, 1.0, 0.0)



    def nlerp(self, a, b, t):

        t = max(0.0, min(1.0, t))

        out_vec = (a * (1.0 - t)) + (b * t)

        return self.safe_normalize(out_vec)



    def angle_between_normals_deg(self, a, b):

        na = self.safe_normalize(a)

        nb = self.safe_normalize(b)

        dotv = max(-1.0, min(1.0, na * nb))

        return math.degrees(math.acos(dotv))



    def get_polygon_area(self, mesh_fn, face_id):

        verts = mesh_fn.getPolygonVertices(face_id)

        if len(verts) < 3:

            return 0.0



        p0 = om.MVector(mesh_fn.getPoint(verts[0], om.MSpace.kWorld))

        area = 0.0



        for i in range(1, len(verts) - 1):

            p1 = om.MVector(mesh_fn.getPoint(verts[i], om.MSpace.kWorld))

            p2 = om.MVector(mesh_fn.getPoint(verts[i + 1], om.MSpace.kWorld))

            area += ((p1 - p0) ^ (p2 - p0)).length() * 0.5



        return area



    def get_corner_angle(self, mesh_fn, face_id, vertex_id, use_convex=True):

        verts = list(mesh_fn.getPolygonVertices(face_id))

        if vertex_id not in verts:

            return 0.0



        count = len(verts)

        local_idx = verts.index(vertex_id)



        prev_id = verts[(local_idx - 1) % count]

        next_id = verts[(local_idx + 1) % count]



        p_current = om.MVector(mesh_fn.getPoint(vertex_id, om.MSpace.kWorld))

        p_prev = om.MVector(mesh_fn.getPoint(prev_id, om.MSpace.kWorld))

        p_next = om.MVector(mesh_fn.getPoint(next_id, om.MSpace.kWorld))



        vec_a = p_prev - p_current

        vec_b = p_next - p_current



        if vec_a.length() < 1e-8 or vec_b.length() < 1e-8:

            return 0.0



        vec_a = self.safe_normalize(vec_a)

        vec_b = self.safe_normalize(vec_b)



        dotv = max(-1.0, min(1.0, vec_a * vec_b))

        angle = math.acos(dotv)



        if use_convex:

            try:

                face_normal = self.safe_normalize(

                    om.MVector(mesh_fn.getPolygonNormal(face_id, om.MSpace.kWorld))

                )

                cross_vec = vec_a ^ vec_b

                if (cross_vec * face_normal) < 0.0:

                    angle = max(0.0, (2.0 * math.pi) - angle)

                    if angle > math.pi:

                        angle = (2.0 * math.pi) - angle

            except Exception:

                pass



        return max(0.0, angle)



    def get_weight_mode(self):

        checked = self.mode_group.checkedButton()

        if checked is None:

            return "area"

        return checked.property("mode")



    def get_face_weight(self, mesh_fn, face_id, vertex_id, weight_mode, use_convex):

        area = self.get_polygon_area(mesh_fn, face_id)

        angle = self.get_corner_angle(mesh_fn, face_id, vertex_id, use_convex=use_convex)



        if weight_mode == "area":

            return area

        elif weight_mode == "angle":

            return angle

        return area * angle



    def build_face_cache(self, mesh_fn):

        face_count = mesh_fn.numPolygons

        face_normals = {}



        for f_id in range(face_count):

            try:

                face_normals[f_id] = self.safe_normalize(

                    om.MVector(mesh_fn.getPolygonNormal(f_id, om.MSpace.kWorld))

                )

            except Exception:

                continue



        return face_normals



    def apply_power_snap_weight(self, weight, max_weight, snap_strength, snap_power):

        if max_weight <= 1e-8:

            return weight



        ratio = max(0.0, min(1.0, weight / max_weight))

        exponent = 1.0 + (max(0.0, min(1.0, snap_strength)) * float(snap_power))

        boosted = weight * math.pow(ratio, exponent)

        return boosted



    def get_filtered_neighbor_faces(

        self,

        target_face_id,

        connected_faces,

        face_normals,

        use_edge_angle,

        edge_angle_limit,

    ):

        if not use_edge_angle:

            return list(connected_faces)



        target_normal = face_normals.get(target_face_id)

        if target_normal is None:

            return [target_face_id]



        valid_faces = []

        for other_face_id in connected_faces:

            other_normal = face_normals.get(other_face_id)

            if other_normal is None:

                continue



            ang = self.angle_between_normals_deg(target_normal, other_normal)

            if ang <= edge_angle_limit:

                valid_faces.append(other_face_id)



        if not valid_faces:

            valid_faces = [target_face_id]



        return valid_faces



    def apply_soft_smoothing(self, result_normal, ref_normal, smoothing, iterations):

        if smoothing <= 0.0 or iterations <= 1:

            return self.safe_normalize(result_normal)



        out_vec = om.MVector(result_normal)

        blend_step = max(0.0, min(1.0, smoothing)) * 0.15



        for _ in range(iterations - 1):

            out_vec = self.nlerp(out_vec, ref_normal, blend_step)



        return self.safe_normalize(out_vec)



    # =========================================================

    # ACTIONS

    # =========================================================

    def unfreeze_normals(self, *args):

        meshes = self.get_selected_meshes()

        if not meshes:

            om.MGlobal.displayWarning("Sélectionne un mesh.")

            return



        current_sel = cmds.ls(sl=True, long=True) or []



        for dag_path in meshes:

            try:

                cmds.select(dag_path.fullPathName(), r=True)

                cmds.polyNormalPerVertex(unFreezeNormal=True)

            except Exception:

                pass



        if current_sel:

            try:

                cmds.select(current_sel, r=True)

            except Exception:

                pass



        om.MGlobal.displayInfo("Normals déverrouillées.")



    def toggle_display(self, *args):

        self.update_ui_states()

        val = self.chk_display.isChecked()



        length = self._value(self.display_length)

        meshes = self.get_selected_meshes()

        if not meshes:

            return



        current_sel = cmds.ls(sl=True, long=True) or []



        self._apply_display_state(meshes, val, length)



        if current_sel:

            try:

                cmds.select(current_sel, r=True)

            except Exception:

                pass

    def _apply_display_state(self, meshes, enabled, length):

        for dag_path in meshes:

            try:

                mesh_name = dag_path.fullPathName()

                cmds.select(mesh_name, r=True)

                if enabled:

                    mel.eval("polyOptions -displayNormal true -sizeNormal {};".format(length))

                else:

                    mel.eval("polyOptions -displayNormal false;")

            except Exception:

                pass



    def apply_normals(self, *args):

        meshes = self.get_selected_meshes()

        if not meshes:

            om.MGlobal.displayWarning("Sélectionne un maillage polygonal d'abord !")

            return



        weight_mode = self.get_weight_mode()

        use_convex = self.chk_convex.isChecked()

        use_snap = self.chk_snap.isChecked()

        snap_strength = self._value(self.snap_strength)

        snap_power = self._value(self.snap_power)

        blending = self._value(self.blending)



        use_edge_angle = self.chk_edge_angle.isChecked()

        edge_angle_limit = self._value(self.edge_angle)



        smoothing = self._value(self.smoothing)

        iterations = self._value(self.iterations)



        current_sel = cmds.ls(sl=True, long=True) or []



        for dag_path in meshes:

            try:

                cmds.select(dag_path.fullPathName(), r=True)

                cmds.polyNormalPerVertex(unFreezeNormal=True)

            except Exception as e:

                om.MGlobal.displayWarning(

                    "Erreur lors du déverrouillage des normales : {}".format(e)

                )



        for dag_path in meshes:

            mesh_fn = om.MFnMesh(dag_path)

            face_normals = self.build_face_cache(mesh_fn)



            vert_iter = om.MItMeshVertex(dag_path)

            new_normals = []

            face_ids = []

            vert_ids = []



            while not vert_iter.isDone():

                v_id = vert_iter.index()

                connected_faces = list(vert_iter.getConnectedFaces())



                if not connected_faces:

                    vert_iter.next()

                    continue



                base_weights = {}

                for f_id in connected_faces:

                    try:

                        base_weights[f_id] = self.get_face_weight(

                            mesh_fn=mesh_fn,

                            face_id=f_id,

                            vertex_id=v_id,

                            weight_mode=weight_mode,

                            use_convex=use_convex,

                        )

                    except Exception:

                        base_weights[f_id] = 0.0



                for current_face_id in connected_faces:

                    current_face_normal = face_normals.get(current_face_id)

                    if current_face_normal is None:

                        continue



                    valid_faces = self.get_filtered_neighbor_faces(

                        target_face_id=current_face_id,

                        connected_faces=connected_faces,

                        face_normals=face_normals,

                        use_edge_angle=use_edge_angle,

                        edge_angle_limit=edge_angle_limit,

                    )



                    if not valid_faces:

                        valid_faces = [current_face_id]



                    max_weight = max([base_weights.get(fid, 0.0) for fid in valid_faces] or [0.0])



                    weighted_sum = om.MVector(0.0, 0.0, 0.0)

                    ref_sum = om.MVector(0.0, 0.0, 0.0)



                    for other_face_id in valid_faces:

                        other_normal = face_normals.get(other_face_id)

                        if other_normal is None:

                            continue



                        base_w = base_weights.get(other_face_id, 0.0)

                        if base_w <= 1e-8:

                            continue



                        final_w = base_w

                        if use_snap:

                            final_w = self.apply_power_snap_weight(

                                weight=base_w,

                                max_weight=max_weight,

                                snap_strength=snap_strength,

                                snap_power=snap_power,

                            )



                        weighted_sum += (other_normal * final_w)

                        ref_sum += other_normal



                    if weighted_sum.length() <= 1e-8:

                        result_normal = om.MVector(current_face_normal)

                    else:

                        result_normal = self.safe_normalize(weighted_sum)



                    if ref_sum.length() > 1e-8:

                        ref_normal = self.safe_normalize(ref_sum)

                        result_normal = self.apply_soft_smoothing(

                            result_normal=result_normal,

                            ref_normal=ref_normal,

                            smoothing=smoothing,

                            iterations=iterations,

                        )



                    try:

                        current_fv_normal = om.MVector(

                            mesh_fn.getFaceVertexNormal(current_face_id, v_id, om.MSpace.kWorld)

                        )

                    except Exception:

                        current_fv_normal = om.MVector(result_normal)



                    out_normal = self.nlerp(current_fv_normal, result_normal, blending)



                    new_normals.append(self.safe_normalize(out_normal))

                    face_ids.append(current_face_id)

                    vert_ids.append(v_id)



                vert_iter.next()



            if new_normals:

                try:

                    mesh_fn.setFaceVertexNormals(

                        new_normals,

                        face_ids,

                        vert_ids,

                        om.MSpace.kWorld,

                    )

                except Exception as e:

                    om.MGlobal.displayWarning(

                        "Impossible d'appliquer les normales sur {} : {}".format(

                            dag_path.fullPathName(), e

                        )

                    )

                try:

                    cmds.select(dag_path.fullPathName(), r=True)

                    cmds.polyNormalPerVertex(unFreezeNormal=True)

                except Exception as e:

                    om.MGlobal.displayWarning(

                        "Impossible de déverrouiller les normales sur {} : {}".format(

                            dag_path.fullPathName(), e

                        )

                    )



        if current_sel:

            try:

                cmds.select(current_sel, r=True)

            except Exception:

                pass



        om.MGlobal.displayInfo("Weighted Normals Pro appliquées.")



        if self.chk_display.isChecked():

            self._apply_display_state(meshes, True, self._value(self.display_length))





def maya_main_window():

    ptr = omui.MQtUtil.mainWindow()

    if ptr is None:

        return None

    return wrapInstance(int(ptr), QtWidgets.QWidget)





def show_weighted_normals_tool():

    for widget in QtWidgets.QApplication.allWidgets():

        object_name = widget.objectName() if callable(getattr(widget, "objectName", None)) else ""

        if object_name == WeightedNormalsTool.WINDOW_NAME:

            widget.close()

            widget.deleteLater()



    tool = WeightedNormalsTool(parent=maya_main_window())

    tool.show()

    return tool





show_weighted_normals_tool()
