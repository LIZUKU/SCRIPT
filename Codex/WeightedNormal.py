import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om
import math


class WeightedNormalsTool:
    def __init__(self):
        self.window_name = "WeightedNormalsUI"
        self.build_ui()

    # =========================================================
    # UI
    # =========================================================
    def build_ui(self):
        if cmds.window(self.window_name, exists=True):
            cmds.deleteUI(self.window_name)

        self.window = cmds.window(
            self.window_name,
            title="Weighted Normals",
            widthHeight=(340, 620),
            sizeable=True
        )

        cmds.columnLayout(adjustableColumn=True, rowSpacing=6)

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

        cmds.rowLayout(numberOfColumns=2, adjustableColumn=1, columnWidth2=(150, 150))
        self.weight_radio = cmds.radioCollection()
        self.rb_area = cmds.radioButton(label="Area", align='center')
        self.rb_angle = cmds.radioButton(label="Angle", align='center', select=True)
        cmds.setParent('..')

        self.chk_convex = cmds.checkBox(label="Use Convex Corner Angle", value=True)
        self.chk_snap = cmds.checkBox(label="Snap To Largest Face", value=True, cc=self.update_ui_states)

        self.flt_snap_strength = cmds.floatSliderGrp(
            label="Snap Strength",
            field=True,
            minValue=0.0,
            maxValue=1.0,
            fieldMinValue=0.0,
            fieldMaxValue=1.0,
            value=0.85,
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

        cmds.setParent('..')

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

        self.chk_smooth_grp = cmds.checkBox(
            label="Use Smoothing Groups",
            value=False,
            enable=False
        )

        cmds.rowLayout(numberOfColumns=2, adjustableColumn=2, columnWidth2=(120, 160))
        self.chk_uv_map = cmds.checkBox(
            label="Use UV Map",
            value=False,
            cc=self.update_ui_states
        )
        self.int_uv = cmds.intSliderGrp(
            field=True,
            minValue=1,
            maxValue=8,
            fieldMinValue=1,
            fieldMaxValue=99,
            value=1,
            enable=False
        )
        cmds.setParent('..')

        cmds.rowLayout(numberOfColumns=2, adjustableColumn=2, columnWidth2=(120, 160))
        self.chk_edge_angle = cmds.checkBox(
            label="By Edge Angle",
            value=True,
            cc=self.update_ui_states
        )
        self.flt_edge_angle = cmds.floatSliderGrp(
            field=True,
            minValue=0.0,
            maxValue=180.0,
            fieldMinValue=0.0,
            fieldMaxValue=180.0,
            value=30.0,
            enable=True
        )
        cmds.setParent('..')

        cmds.setParent('..')

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

        self.flt_smooth = cmds.floatSliderGrp(
            label="Smoothing",
            field=True,
            minValue=0.0,
            maxValue=1.0,
            fieldMinValue=0.0,
            fieldMaxValue=1.0,
            value=0.15,
            columnWidth3=(110, 50, 140)
        )

        self.flt_hard_blend = cmds.floatSliderGrp(
            label="Hard Edge Blend",
            field=True,
            minValue=0.0,
            maxValue=1.0,
            fieldMinValue=0.0,
            fieldMaxValue=1.0,
            value=0.5,
            columnWidth3=(110, 50, 140)
        )

        self.int_iterations = cmds.intSliderGrp(
            label="Iterations",
            field=True,
            minValue=1,
            maxValue=50,
            fieldMinValue=1,
            fieldMaxValue=999,
            value=10,
            columnWidth3=(110, 50, 140)
        )

        cmds.setParent('..')

        # -------------------------
        # DISPLAY NORMALS
        # -------------------------
        cmds.frameLayout(
            label="Display Normals",
            collapsable=True,
            collapse=False,
            marginWidth=6,
            marginHeight=6,
            bgc=(0.2, 0.2, 0.2)
        )

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

        cmds.setParent('..')

        cmds.separator(height=10, style='none')

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
        use_uv = cmds.checkBox(self.chk_uv_map, q=True, v=True)
        use_edge = cmds.checkBox(self.chk_edge_angle, q=True, v=True)
        show_normals = cmds.checkBox(self.chk_display, q=True, v=True)
        use_snap = cmds.checkBox(self.chk_snap, q=True, v=True)

        cmds.intSliderGrp(self.int_uv, e=True, enable=use_uv)
        cmds.floatSliderGrp(self.flt_edge_angle, e=True, enable=use_edge)
        cmds.floatSliderGrp(self.flt_display_len, e=True, enable=show_normals)
        cmds.floatSliderGrp(self.flt_snap_strength, e=True, enable=use_snap)

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
        verts = mesh_fn.getPolygonVertices(face_id)
        if vertex_id not in verts:
            return 0.0

        count = len(verts)
        local_idx = list(verts).index(vertex_id)

        prev_id = verts[(local_idx - 1) % count]
        next_id = verts[(local_idx + 1) % count]

        p_current = om.MVector(mesh_fn.getPoint(vertex_id, om.MSpace.kWorld))
        p_prev = om.MVector(mesh_fn.getPoint(prev_id, om.MSpace.kWorld))
        p_next = om.MVector(mesh_fn.getPoint(next_id, om.MSpace.kWorld))

        vec_a = p_next - p_current
        vec_b = p_prev - p_current

        if vec_a.length() < 1e-8 or vec_b.length() < 1e-8:
            return 0.0

        vec_a.normalize()
        vec_b.normalize()

        dot_prod = max(-1.0, min(1.0, vec_a * vec_b))
        angle = math.acos(dot_prod)

        if use_convex:
            try:
                face_normal = om.MVector(mesh_fn.getPolygonNormal(face_id, om.MSpace.kWorld))
                cross = vec_a ^ vec_b
                if (cross * face_normal) < 0.0:
                    angle *= 0.25
            except Exception:
                pass

        return angle

    def get_face_weight(self, mesh_fn, face_id, vertex_id, use_angle, use_convex):
        if use_angle:
            return self.get_corner_angle(mesh_fn, face_id, vertex_id, use_convex=use_convex)
        return self.get_polygon_area(mesh_fn, face_id)

    def unfreeze_normals(self, *args):
        meshes = self.get_selected_meshes()
        if not meshes:
            om.MGlobal.displayWarning("Sélectionne un mesh.")
            return

        for dag_path in meshes:
            try:
                cmds.select(dag_path.fullPathName(), r=True)
                cmds.polyNormalPerVertex(unFreezeNormal=True)
            except Exception:
                pass

        om.MGlobal.displayInfo("Normals déverrouillées.")

    # =========================================================
    # DISPLAY
    # =========================================================
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

    # =========================================================
    # APPLY
    # =========================================================
    def apply_normals(self, *args):
        use_angle = cmds.radioButton(self.rb_angle, q=True, select=True)
        use_convex = cmds.checkBox(self.chk_convex, q=True, v=True)
        use_snap = cmds.checkBox(self.chk_snap, q=True, v=True)

        snap_strength = cmds.floatSliderGrp(self.flt_snap_strength, q=True, v=True)
        blending = cmds.floatSliderGrp(self.flt_blending, q=True, v=True)

        smoothing = cmds.floatSliderGrp(self.flt_smooth, q=True, v=True)
        hard_blend = cmds.floatSliderGrp(self.flt_hard_blend, q=True, v=True)
        iterations = cmds.intSliderGrp(self.int_iterations, q=True, v=True)

        use_edge_angle = cmds.checkBox(self.chk_edge_angle, q=True, v=True)
        edge_angle_limit = cmds.floatSliderGrp(self.flt_edge_angle, q=True, v=True)

        meshes = self.get_selected_meshes()
        if not meshes:
            om.MGlobal.displayWarning("Sélectionne un maillage polygonal d'abord !")
            return

        current_sel = cmds.ls(sl=True, long=True) or []

        for dag_path in meshes:
            try:
                cmds.select(dag_path.fullPathName(), r=True)
                cmds.polyNormalPerVertex(unFreezeNormal=True)
            except Exception as e:
                om.MGlobal.displayWarning("Erreur lors du déverrouillage des normales : {}".format(e))

        for dag_path in meshes:
            mesh_fn = om.MFnMesh(dag_path)
            vert_iter = om.MItMeshVertex(dag_path)

            new_normals = []
            face_ids = []
            vert_ids = []

            while not vert_iter.isDone():
                v_id = vert_iter.index()
                connected_faces = vert_iter.getConnectedFaces()

                if not connected_faces:
                    vert_iter.next()
                    continue

                accumulated_normal = om.MVector(0.0, 0.0, 0.0)
                best_weight = -1.0
                best_face_normal = None
                total_weight = 0.0

                for f_id in connected_faces:
                    try:
                        face_normal = om.MVector(mesh_fn.getPolygonNormal(f_id, om.MSpace.kWorld))
                    except Exception:
                        continue

                    weight = self.get_face_weight(
                        mesh_fn=mesh_fn,
                        face_id=f_id,
                        vertex_id=v_id,
                        use_angle=use_angle,
                        use_convex=use_convex
                    )

                    if weight <= 1e-8:
                        continue

                    total_weight += weight
                    accumulated_normal += (face_normal * weight)

                    if weight > best_weight:
                        best_weight = weight
                        best_face_normal = om.MVector(face_normal)

                if accumulated_normal.length() < 1e-8:
                    vert_iter.next()
                    continue

                accumulated_normal.normalize()

                # -----------------------------------
                # SNAP TO LARGEST FACE
                # -----------------------------------
                if use_snap and best_face_normal is not None:
                    best_face_normal.normalize()

                    dominance = 0.0
                    if total_weight > 1e-8 and best_weight > 0.0:
                        dominance = best_weight / total_weight

                    # Si la face dominante pèse très fort, on snap plus agressivement
                    if dominance >= 0.6:
                        mixed_snap = ((accumulated_normal * (1.0 - snap_strength)) +
                                      (best_face_normal * snap_strength))
                        if mixed_snap.length() > 1e-8:
                            mixed_snap.normalize()
                        accumulated_normal = mixed_snap
                    else:
                        reduced_strength = snap_strength * 0.75
                        mixed_snap = ((accumulated_normal * (1.0 - reduced_strength)) +
                                      (best_face_normal * reduced_strength))
                        if mixed_snap.length() > 1e-8:
                            mixed_snap.normalize()
                        accumulated_normal = mixed_snap

                # -----------------------------------
                # SMOOTHING / STABILISATION
                # -----------------------------------
                final_normal = om.MVector(accumulated_normal)

                # Ici ce n'est pas un "vrai" smoothing topologique :
                # c'est une stabilisation progressive du résultat
                # pour éviter certains écarts trop secs.
                if iterations > 1 and smoothing > 0.0:
                    base_normal = om.MVector(accumulated_normal)

                    for _ in range(iterations - 1):
                        final_normal = (final_normal * (1.0 - smoothing * 0.2)) + (base_normal * (smoothing * 0.2))
                        if final_normal.length() > 1e-8:
                            final_normal.normalize()

                # -----------------------------------
                # APPLICATION PAR FACE-VERTEX
                # -----------------------------------
                for f_id in connected_faces:
                    try:
                        current_normal = om.MVector(mesh_fn.getFaceVertexNormal(f_id, v_id, om.MSpace.kWorld))
                    except Exception:
                        current_normal = om.MVector(final_normal)

                    out_normal = (current_normal * (1.0 - blending)) + (final_normal * blending)

                    # Hard edge blend :
                    # si l'écart avec la face est fort, on peut ré-attirer
                    # un peu vers la normale de face.
                    if use_edge_angle:
                        try:
                            face_normal = om.MVector(mesh_fn.getPolygonNormal(f_id, om.MSpace.kWorld))

                            if face_normal.length() > 1e-8:
                                face_normal.normalize()

                            tmp = om.MVector(final_normal)
                            if tmp.length() > 1e-8:
                                tmp.normalize()

                            dotv = max(-1.0, min(1.0, face_normal * tmp))
                            ang = math.degrees(math.acos(dotv))

                            if ang > edge_angle_limit:
                                out_normal = ((out_normal * (1.0 - hard_blend)) +
                                              (face_normal * hard_blend))
                        except Exception:
                            pass

                    if out_normal.length() > 1e-8:
                        out_normal.normalize()
                    else:
                        out_normal = om.MVector(final_normal)

                    new_normals.append(out_normal)
                    face_ids.append(f_id)
                    vert_ids.append(v_id)

                vert_iter.next()

            if new_normals:
                mesh_fn.setFaceVertexNormals(new_normals, face_ids, vert_ids, om.MSpace.kWorld)

        if current_sel:
            try:
                cmds.select(current_sel, r=True)
            except Exception:
                pass

        om.MGlobal.displayInfo("Weighted Normals appliquées.")

        if cmds.checkBox(self.chk_display, q=True, v=True):
            self.toggle_display()


WeightedNormalsTool()
