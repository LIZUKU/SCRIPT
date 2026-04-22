# -*- coding: utf-8 -*-
import math
import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om


class WeightedNormalsTool:
    def __init__(self):
        self.window_name = "WeightedNormalsUI_PowerSnap"
        self.build_ui()

    # =========================================================
    # UI
    # =========================================================
    def build_ui(self):
        if cmds.window(self.window_name, exists=True):
            cmds.deleteUI(self.window_name)

        self.window = cmds.window(
            self.window_name,
            title="Weighted Normals Pro",
            widthHeight=(360, 700),
            sizeable=True
        )

        main_col = cmds.columnLayout(adjustableColumn=True, rowSpacing=8)

        # -------------------------
        # WEIGHTING
        # -------------------------
        cmds.frameLayout(
            label="Weighting",
            collapsable=True,
            collapse=False,
            marginWidth=6,
            marginHeight=6,
            bgc=(0.2, 0.2, 0.2)
        )
        cmds.columnLayout(adjustableColumn=True, rowSpacing=4)

        self.weight_radio = cmds.radioCollection()
        self.rb_area = cmds.radioButton(label="Area", select=True)
        self.rb_angle = cmds.radioButton(label="Angle")
        self.rb_both = cmds.radioButton(label="Area & Angle")

        self.chk_convex = cmds.checkBox(
            label="Use Convex Corner Angle",
            value=True
        )

        self.chk_snap = cmds.checkBox(
            label="Snap To Largest Face",
            value=True,
            cc=self.update_ui_states
        )

        self.flt_snap_strength = cmds.floatSliderGrp(
            label="Snap Strength",
            field=True,
            minValue=0.0,
            maxValue=1.0,
            fieldMinValue=0.0,
            fieldMaxValue=1.0,
            value=0.9,
            columnWidth3=(110, 50, 140)
        )

        self.int_snap_power = cmds.intSliderGrp(
            label="Snap Power",
            field=True,
            minValue=1,
            maxValue=32,
            fieldMinValue=1,
            fieldMaxValue=128,
            value=15,
            columnWidth3=(110, 50, 140)
        )

        self.flt_blending = cmds.floatSliderGrp(
            label="Blending",
            field=True,
            minValue=0.0,
            maxValue=1.0,
            fieldMinValue=0.0,
            fieldMaxValue=1.0,
            value=1.0,
            columnWidth3=(110, 50, 140)
        )

        cmds.setParent(main_col)

        # -------------------------
        # HARD EDGE DETECTION
        # -------------------------
        cmds.frameLayout(
            label="Hard Edge Detection",
            collapsable=True,
            collapse=False,
            marginWidth=6,
            marginHeight=6,
            bgc=(0.2, 0.2, 0.2)
        )
        cmds.columnLayout(adjustableColumn=True, rowSpacing=4)

        self.chk_edge_angle = cmds.checkBox(
            label="By Edge Angle",
            value=False,
            cc=self.update_ui_states
        )

        self.flt_edge_angle = cmds.floatSliderGrp(
            label="Edge Angle",
            field=True,
            minValue=0.0,
            maxValue=180.0,
            fieldMinValue=0.0,
            fieldMaxValue=180.0,
            value=30.0,
            columnWidth3=(110, 50, 140)
        )

        cmds.setParent(main_col)

        # -------------------------
        # SMOOTHING
        # -------------------------
        cmds.frameLayout(
            label="Smoothing",
            collapsable=True,
            collapse=False,
            marginWidth=6,
            marginHeight=6,
            bgc=(0.2, 0.2, 0.2)
        )
        cmds.columnLayout(adjustableColumn=True, rowSpacing=4)

        self.flt_smooth = cmds.floatSliderGrp(
            label="Smoothing",
            field=True,
            minValue=0.0,
            maxValue=1.0,
            fieldMinValue=0.0,
            fieldMaxValue=1.0,
            value=0.0,
            columnWidth3=(110, 50, 140)
        )

        self.int_iterations = cmds.intSliderGrp(
            label="Iterations",
            field=True,
            minValue=1,
            maxValue=20,
            fieldMinValue=1,
            fieldMaxValue=100,
            value=1,
            columnWidth3=(110, 50, 140)
        )

        cmds.setParent(main_col)

        # -------------------------
        # DISPLAY
        # -------------------------
        cmds.frameLayout(
            label="Display Normals",
            collapsable=True,
            collapse=False,
            marginWidth=6,
            marginHeight=6,
            bgc=(0.2, 0.2, 0.2)
        )
        cmds.columnLayout(adjustableColumn=True, rowSpacing=4)

        self.chk_display = cmds.checkBox(
            label="Display Normals",
            value=False,
            changeCommand=self.toggle_display
        )

        self.flt_display_len = cmds.floatSliderGrp(
            label="Display Length",
            field=True,
            minValue=0.1,
            maxValue=50.0,
            fieldMinValue=0.001,
            fieldMaxValue=9999.0,
            value=10.0,
            enable=False,
            columnWidth3=(110, 50, 140)
        )

        cmds.setParent(main_col)

        cmds.separator(height=8, style='none')

        cmds.button(
            label="APPLY WEIGHTED NORMALS",
            height=42,
            bgc=(0.3, 0.5, 0.8),
            command=self.apply_normals
        )

        cmds.separator(height=6, style='none')

        cmds.button(
            label="UNFREEZE NORMALS",
            height=28,
            command=self.unfreeze_normals
        )

        cmds.showWindow(self.window)
        self.update_ui_states()

    def update_ui_states(self, *args):
        use_snap = cmds.checkBox(self.chk_snap, q=True, v=True)
        use_edge = cmds.checkBox(self.chk_edge_angle, q=True, v=True)
        show_normals = cmds.checkBox(self.chk_display, q=True, v=True)

        cmds.floatSliderGrp(self.flt_snap_strength, e=True, enable=use_snap)
        cmds.intSliderGrp(self.int_snap_power, e=True, enable=use_snap)
        cmds.floatSliderGrp(self.flt_edge_angle, e=True, enable=use_edge)
        cmds.floatSliderGrp(self.flt_display_len, e=True, enable=show_normals)

    # =========================================================
    # HELPERS
    # =========================================================
    def get_selected_meshes(self):
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
        if cmds.radioButton(self.rb_area, q=True, select=True):
            return "area"
        elif cmds.radioButton(self.rb_angle, q=True, select=True):
            return "angle"
        return "both"

    def get_face_weight(self, mesh_fn, face_id, vertex_id, weight_mode, use_convex):
        area = self.get_polygon_area(mesh_fn, face_id)
        angle = self.get_corner_angle(mesh_fn, face_id, vertex_id, use_convex=use_convex)

        if weight_mode == "area":
            return area
        elif weight_mode == "angle":
            return angle
        else:
            return area * angle

    def build_face_cache(self, mesh_fn):
        face_count = mesh_fn.numPolygons
        face_normals = {}
        face_vertices = {}

        for f_id in range(face_count):
            try:
                face_normals[f_id] = self.safe_normalize(
                    om.MVector(mesh_fn.getPolygonNormal(f_id, om.MSpace.kWorld))
                )
                face_vertices[f_id] = list(mesh_fn.getPolygonVertices(f_id))
            except Exception:
                continue

        return face_normals, face_vertices

    def apply_power_snap_weight(self, weight, max_weight, snap_strength, snap_power):
        if max_weight <= 1e-8:
            return weight

        ratio = max(0.0, min(1.0, weight / max_weight))

        # snap_strength = 0 => comportement presque normal
        # snap_strength = 1 => comportement très agressif
        exponent = 1.0 + (max(0.0, min(1.0, snap_strength)) * float(snap_power))

        boosted = weight * math.pow(ratio, exponent)
        return boosted

    def get_filtered_neighbor_faces(
        self,
        target_face_id,
        connected_faces,
        face_normals,
        use_edge_angle,
        edge_angle_limit
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
        val = cmds.checkBox(self.chk_display, q=True, v=True)
        cmds.floatSliderGrp(self.flt_display_len, e=True, enable=val)

        length = cmds.floatSliderGrp(self.flt_display_len, q=True, v=True)
        meshes = self.get_selected_meshes()
        if not meshes:
            return

        current_sel = cmds.ls(sl=True, long=True) or []

        for dag_path in meshes:
            try:
                mesh_name = dag_path.fullPathName()
                cmds.select(mesh_name, r=True)

                if val:
                    mel.eval('polyOptions -displayNormal true -sizeNormal {};'.format(length))
                else:
                    mel.eval('polyOptions -displayNormal false;')
            except Exception:
                pass

        if current_sel:
            try:
                cmds.select(current_sel, r=True)
            except Exception:
                pass

    def apply_normals(self, *args):
        meshes = self.get_selected_meshes()
        if not meshes:
            om.MGlobal.displayWarning("Sélectionne un maillage polygonal d'abord !")
            return

        weight_mode = self.get_weight_mode()
        use_convex = cmds.checkBox(self.chk_convex, q=True, v=True)
        use_snap = cmds.checkBox(self.chk_snap, q=True, v=True)
        snap_strength = cmds.floatSliderGrp(self.flt_snap_strength, q=True, v=True)
        snap_power = cmds.intSliderGrp(self.int_snap_power, q=True, v=True)
        blending = cmds.floatSliderGrp(self.flt_blending, q=True, v=True)

        use_edge_angle = cmds.checkBox(self.chk_edge_angle, q=True, v=True)
        edge_angle_limit = cmds.floatSliderGrp(self.flt_edge_angle, q=True, v=True)

        smoothing = cmds.floatSliderGrp(self.flt_smooth, q=True, v=True)
        iterations = cmds.intSliderGrp(self.int_iterations, q=True, v=True)

        current_sel = cmds.ls(sl=True, long=True) or []

        # Déverrouillage
        for dag_path in meshes:
            try:
                cmds.select(dag_path.fullPathName(), r=True)
                cmds.polyNormalPerVertex(unFreezeNormal=True)
            except Exception as e:
                om.MGlobal.displayWarning(
                    "Erreur lors du déverrouillage des normales : {}".format(e)
                )

        # Calcul
        for dag_path in meshes:
            mesh_fn = om.MFnMesh(dag_path)
            face_normals, face_vertices = self.build_face_cache(mesh_fn)

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

                # Poids de base pour toutes les faces autour du vertex
                base_weights = {}
                for f_id in connected_faces:
                    try:
                        base_weights[f_id] = self.get_face_weight(
                            mesh_fn=mesh_fn,
                            face_id=f_id,
                            vertex_id=v_id,
                            weight_mode=weight_mode,
                            use_convex=use_convex
                        )
                    except Exception:
                        base_weights[f_id] = 0.0

                # Calcul par face-vertex
                for current_face_id in connected_faces:
                    current_face_normal = face_normals.get(current_face_id)
                    if current_face_normal is None:
                        continue

                    valid_faces = self.get_filtered_neighbor_faces(
                        target_face_id=current_face_id,
                        connected_faces=connected_faces,
                        face_normals=face_normals,
                        use_edge_angle=use_edge_angle,
                        edge_angle_limit=edge_angle_limit
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
                                snap_power=snap_power
                            )

                        weighted_sum += (other_normal * final_w)
                        ref_sum += other_normal

                    if weighted_sum.length() <= 1e-8:
                        result_normal = om.MVector(current_face_normal)
                    else:
                        result_normal = self.safe_normalize(weighted_sum)

                    # petite stabilisation optionnelle
                    if ref_sum.length() > 1e-8:
                        ref_normal = self.safe_normalize(ref_sum)
                        result_normal = self.apply_soft_smoothing(
                            result_normal=result_normal,
                            ref_normal=ref_normal,
                            smoothing=smoothing,
                            iterations=iterations
                        )

                    # blending final avec la normale actuelle
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
                        om.MSpace.kWorld
                    )
                except Exception as e:
                    om.MGlobal.displayWarning(
                        "Impossible d'appliquer les normales sur {} : {}".format(
                            dag_path.fullPathName(), e
                        )
                    )

        if current_sel:
            try:
                cmds.select(current_sel, r=True)
            except Exception:
                pass

        om.MGlobal.displayInfo("Weighted Normals Pro appliquées.")

        if cmds.checkBox(self.chk_display, q=True, v=True):
            self.toggle_display()


WeightedNormalsTool()
