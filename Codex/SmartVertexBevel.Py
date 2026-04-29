import __main__
import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.mel as mel


_current_selection = cmds.ls(sl=True, fl=True)

if hasattr(__main__, "MULTIFILLET_ACTIVE_INSTANCE"):
    try:
        if __main__.MULTIFILLET_ACTIVE_INSTANCE:
            __main__.MULTIFILLET_ACTIVE_INSTANCE.validate(clear_selection=False)
    except:
        pass

__main__.MULTIFILLET_ACTIVE_INSTANCE = None

if _current_selection:
    cmds.select(_current_selection, r=True)


class MultiFilletFinalFix:
    def __init__(self):
        self.mesh_name = ""
        self.backup_mesh = ""
        self.vtx_data = []
        self.is_initialized = False

        self.size = 5.0
        self.divs = 6

        self.drag_ctx = "MultiFilletDragContext"
        self.drag_start_x = 0.0
        self.drag_start_size = 0.0
        self.drag_start_divs = 1

        self.tool_job = None
        self.is_validating = False

        self._temp_sets = ["MF_Border_Set", "MF_ToVerif_Set"]

        __main__.MULTIFILLET_ACTIVE_INSTANCE = self
        self.init_tool()

    def _cleanup_temp_sets(self):
        for s in self._temp_sets:
            if cmds.objExists(s):
                try:
                    cmds.delete(s)
                except:
                    pass

    def clear_active_instance(self):
        if hasattr(__main__, "MULTIFILLET_ACTIVE_INSTANCE"):
            if __main__.MULTIFILLET_ACTIVE_INSTANCE is self:
                __main__.MULTIFILLET_ACTIVE_INSTANCE = None

    def _get_dag_and_fn(self):
        sel_list = om.MGlobal.getSelectionListByName(self.mesh_name)
        dag = sel_list.getDagPath(0)
        mesh_fn = om.MFnMesh(dag)
        return dag, mesh_fn

    def _closest_vertex_to_pos(self, target_pos):
        if not cmds.objExists(self.mesh_name):
            return None

        try:
            dag, mesh_fn = self._get_dag_and_fn()
        except:
            return None

        best_id = None
        best_dist = float("inf")

        for i in range(mesh_fn.numVertices):
            p = om.MVector(mesh_fn.getPoint(i, om.MSpace.kWorld))
            d = (p - target_pos).length()

            if d < best_dist:
                best_dist = d
                best_id = i

        return best_id

    def is_border_vertex(self, dag, v_id):
        it_vtx = om.MItMeshVertex(dag)
        it_vtx.setIndex(v_id)

        edges = list(it_vtx.getConnectedEdges())
        mesh_fn = om.MFnMesh(dag)

        for e_id in edges:
            edge_vertices = mesh_fn.getEdgeVertices(e_id)
            connected_faces = []

            it_poly = om.MItMeshPolygon(dag)
            while not it_poly.isDone():
                poly_vertices = list(it_poly.getVertices())

                if edge_vertices[0] in poly_vertices and edge_vertices[1] in poly_vertices:
                    connected_faces.append(it_poly.index())

                it_poly.next()

            if len(connected_faces) < 2:
                return True

        return False

    def _find_edge_toward_neighbor(self, orig_id, p_neighbor_target):
        vtx_str = f"{self.mesh_name}.vtx[{orig_id}]"

        current_edges = cmds.ls(
            cmds.polyListComponentConversion(vtx_str, te=True),
            fl=True
        )

        if not current_edges:
            return None

        try:
            dag, mesh_fn = self._get_dag_and_fn()
        except:
            return None

        best_eid = None
        best_dist = float("inf")

        for e_str in current_edges:
            try:
                eid = int(e_str.split("[")[1][:-1])
                vids = mesh_fn.getEdgeVertices(eid)

                if orig_id not in vids:
                    continue

                other = vids[1] if vids[0] == orig_id else vids[0]
                p_other = om.MVector(mesh_fn.getPoint(other, om.MSpace.kWorld))
                dist = (p_other - p_neighbor_target).length()

                if dist < best_dist:
                    best_dist = dist
                    best_eid = eid
            except:
                continue

        return best_eid

    def _split_edge_at_world_pos(self, edge_id, target_world_pos):
        if edge_id is None:
            return None

        try:
            dag, mesh_fn = self._get_dag_and_fn()
            v_ids = mesh_fn.getEdgeVertices(edge_id)
        except:
            return None

        p_start = om.MVector(mesh_fn.getPoint(v_ids[0], om.MSpace.kWorld))
        p_end = om.MVector(mesh_fn.getPoint(v_ids[1], om.MSpace.kWorld))

        edge_vec = p_end - p_start
        edge_len = edge_vec.length()

        if edge_len < 0.0001:
            param = 0.5
        else:
            to_target = target_world_pos - p_start
            param = (to_target * edge_vec) / (edge_len * edge_len)
            param = max(0.001, min(0.999, param))

        v_count_before = cmds.polyEvaluate(self.mesh_name, v=True)

        try:
            mel.eval(f'polySplit -ch off -ep {edge_id} {param} {self.mesh_name}')
        except:
            return None

        v_count_after = cmds.polyEvaluate(self.mesh_name, v=True)

        if v_count_after <= v_count_before:
            return None

        return v_count_after - 1

    def _collect_shared_edge(self, v_a_id, v_b_id):
        v_a_str = f"{self.mesh_name}.vtx[{v_a_id}]"
        v_b_str = f"{self.mesh_name}.vtx[{v_b_id}]"

        edges_a = set(cmds.ls(cmds.polyListComponentConversion(v_a_str, te=True), fl=True))
        edges_b = set(cmds.ls(cmds.polyListComponentConversion(v_b_str, te=True), fl=True))

        shared = list(edges_a & edges_b)

        if shared:
            return shared[0]

        return None

    def run_border_bevel(self, vertex_selection):
        self._cleanup_temp_sets()

        try:
            cmds.select(vertex_selection, r=True)
            vertex_init_selection = cmds.ls(sl=True, fl=True)

            cmds.ConvertSelectionToFaces()
            cmds.ConvertSelectionToContainedEdges()
            cmds.sets(n=self._temp_sets[0])

            cmds.select(vertex_init_selection, r=True)
            cmds.ConvertSelectionToEdges()
            cmds.sets(n=self._temp_sets[1])

            verif = cmds.sets(self._temp_sets[0], sub=self._temp_sets[1])
            cmds.select(verif, r=True)

            edge_sel = cmds.ls(sl=True, fl=True)

            if not edge_sel:
                return

            cmds.polyExtrudeEdge(
                edge_sel,
                kft=True,
                pvx=0,
                pvy=0,
                pvz=0,
                divisions=1,
                twist=0,
                taper=1,
                offset=0,
                thickness=0
            )

            cmds.ConvertSelectionToFaces()
            cmds.ConvertSelectionToContainedEdges()

            edge_to_bevel = cmds.ls(sl=True, fl=True)

            if not edge_to_bevel:
                return

            mel.eval('dR_DoCmd("bevelPress")')

        finally:
            self._cleanup_temp_sets()

    def create_drag_context(self):
        if cmds.draggerContext(self.drag_ctx, exists=True):
            cmds.deleteUI(self.drag_ctx)

        cmds.draggerContext(
            self.drag_ctx,
            pressCommand=self.drag_press,
            dragCommand=self.drag_update,
            releaseCommand=self.drag_release,
            cursor="crossHair",
            undoMode="step"
        )

        cmds.setToolTo(self.drag_ctx)

        if self.tool_job and cmds.scriptJob(exists=self.tool_job):
            cmds.scriptJob(kill=self.tool_job, force=True)

        self.tool_job = cmds.scriptJob(
            event=["ToolChanged", self.on_tool_changed],
            protected=False
        )

    def on_tool_changed(self):
        if self.is_validating:
            return

        try:
            current = cmds.currentCtx()
        except:
            return

        if self.is_initialized and current != self.drag_ctx:
            self.validate(clear_selection=False)

    def drag_press(self):
        anchor = cmds.draggerContext(self.drag_ctx, q=True, anchorPoint=True)

        self.drag_start_x = anchor[0]
        self.drag_start_size = self.size
        self.drag_start_divs = self.divs

    def drag_update(self):
        if not self.is_initialized:
            return

        drag_pos = cmds.draggerContext(self.drag_ctx, q=True, dragPoint=True)
        mods = cmds.getModifiers()

        dx = drag_pos[0] - self.drag_start_x
        shift_is_pressed = bool(mods & 1)

        if shift_is_pressed:
            new_divs = int(round(self.drag_start_divs + dx * 0.05))
            self.divs = max(1, min(40, new_divs))
        else:
            new_size = self.drag_start_size + dx * 0.05
            self.size = max(0.01, min(100.0, new_size))

        self.update_all()

    def drag_release(self):
        pass

    def init_tool(self, *args):
        sel = cmds.ls(sl=True, fl=True)

        if not sel:
            self.clear_active_instance()
            return

        self.mesh_name = sel[0].split(".")[0]
        self.vtx_data = []

        try:
            sel_list = om.MGlobal.getActiveSelectionList()
            dag = sel_list.getDagPath(0)
        except:
            self.clear_active_instance()
            return

        border_sel = []
        normal_sel = []

        for vtx_str in sel:
            if ".vtx[" not in vtx_str:
                continue

            try:
                v_id = int(vtx_str.split("[")[1][:-1])
            except:
                continue

            if self.is_border_vertex(dag, v_id):
                border_sel.append(vtx_str)
            else:
                normal_sel.append(vtx_str)

        if border_sel and not normal_sel:
            self.is_initialized = False
            self.run_border_bevel(border_sel)
            self.clear_active_instance()
            return

        if border_sel and normal_sel:
            sel = normal_sel

        try:
            cmds.delete(self.mesh_name, ch=True)
        except:
            pass

        if self.backup_mesh and cmds.objExists(self.backup_mesh):
            try:
                cmds.delete(self.backup_mesh)
            except:
                pass

        self.backup_mesh = cmds.duplicate(
            self.mesh_name,
            name=f"{self.mesh_name}_BACKUP"
        )[0]

        cmds.setAttr(f"{self.backup_mesh}.visibility", 0)

        try:
            cmds.delete(self.mesh_name, ch=True)
        except:
            pass

        try:
            dag, mesh_fn = self._get_dag_and_fn()
        except:
            self.clear_active_instance()
            return

        for vtx_str in sel:
            if ".vtx[" not in vtx_str:
                continue

            try:
                v_id = int(vtx_str.split("[")[1][:-1])
            except:
                continue

            try:
                it_vtx = om.MItMeshVertex(dag)
                it_vtx.setIndex(v_id)
                edges = list(it_vtx.getConnectedEdges())
            except:
                continue

            if len(edges) != 2:
                continue

            p0 = om.MVector(it_vtx.position(om.MSpace.kWorld))

            def get_dir(e_id):
                v_ids = mesh_fn.getEdgeVertices(e_id)
                other = v_ids[1] if v_ids[0] == v_id else v_ids[0]
                vec = om.MVector(mesh_fn.getPoint(other, om.MSpace.kWorld)) - p0

                if vec.length() < 0.0001:
                    return om.MVector(0, 0, 0)

                return vec.normalize()

            v_ids_e0 = mesh_fn.getEdgeVertices(edges[0])
            v_ids_e1 = mesh_fn.getEdgeVertices(edges[1])

            neighbor_e0 = v_ids_e0[1] if v_ids_e0[0] == v_id else v_ids_e0[0]
            neighbor_e1 = v_ids_e1[1] if v_ids_e1[0] == v_id else v_ids_e1[0]

            p_neighbor_e0 = om.MVector(mesh_fn.getPoint(neighbor_e0, om.MSpace.kWorld))
            p_neighbor_e1 = om.MVector(mesh_fn.getPoint(neighbor_e1, om.MSpace.kWorld))

            d0 = get_dir(edges[0])
            d1 = get_dir(edges[1])

            if d0.length() < 0.0001 or d1.length() < 0.0001:
                continue

            self.vtx_data.append({
                "orig_id": v_id,
                "p0": p0,
                "v1_vec": d0,
                "v2_vec": d1,
                "p_neighbor_v1": p_neighbor_e0,
                "p_neighbor_v2": p_neighbor_e1,
            })

        if not self.vtx_data:
            if cmds.objExists(self.backup_mesh):
                try:
                    cmds.delete(self.backup_mesh)
                except:
                    pass

            self.clear_active_instance()
            return

        self.is_initialized = True
        self.update_all()
        self.create_drag_context()

    def restore_from_backup(self):
        if not cmds.objExists(self.backup_mesh):
            return False

        if not cmds.objExists(self.mesh_name):
            return False

        try:
            src_dag = om.MGlobal.getSelectionListByName(self.backup_mesh).getDagPath(0)
            dst_dag = om.MGlobal.getSelectionListByName(self.mesh_name).getDagPath(0)

            src_fn = om.MFnMesh(src_dag)
            dst_fn = om.MFnMesh(dst_dag)
        except:
            return False

        if src_fn.numVertices != dst_fn.numVertices:
            try:
                b_shape = cmds.listRelatives(self.backup_mesh, s=True)[0]
                m_shape = cmds.listRelatives(self.mesh_name, s=True)[0]

                cmds.connectAttr(f"{b_shape}.outMesh", f"{m_shape}.inMesh", f=True)
                cmds.disconnectAttr(f"{b_shape}.outMesh", f"{m_shape}.inMesh")

                cmds.delete(self.mesh_name, ch=True)
            except:
                return False
        else:
            try:
                points = src_fn.getPoints(om.MSpace.kWorld)
                dst_fn.setPoints(points, om.MSpace.kWorld)
            except:
                return False

        return True

    def update_all(self, *args):
        if not self.is_initialized:
            return

        if not self.restore_from_backup():
            return

        old_sw = cmds.scriptEditorInfo(q=True, suppressWarnings=True)
        old_si = cmds.scriptEditorInfo(q=True, suppressInfo=True)
        old_sr = cmds.scriptEditorInfo(q=True, suppressResults=True)

        cmds.scriptEditorInfo(e=True, suppressWarnings=True)
        cmds.scriptEditorInfo(e=True, suppressInfo=True)
        cmds.scriptEditorInfo(e=True, suppressResults=True)

        try:
            size = self.size
            divs = self.divs

            sorted_vtx = sorted(
                self.vtx_data,
                key=lambda x: x["orig_id"],
                reverse=True
            )

            for data in sorted_vtx:
                orig_id = data["orig_id"]
                p0 = data["p0"]
                v1_vec = data["v1_vec"]
                v2_vec = data["v2_vec"]
                p_nb_v1 = data["p_neighbor_v1"]
                p_nb_v2 = data["p_neighbor_v2"]

                if not cmds.objExists(f"{self.mesh_name}.vtx[{orig_id}]"):
                    continue

                # ------------------------------------------------------------
                # Correction importante :
                # chaque corner limite sa taille selon ses deux edges.
                # Ça évite que l'arc parte dans le mauvais sens.
                # ------------------------------------------------------------

                len_v1 = (p_nb_v1 - p0).length()
                len_v2 = (p_nb_v2 - p0).length()

                safe_size = min(size, len_v1 * 0.45, len_v2 * 0.45)

                if safe_size <= 0.0001:
                    continue

                p_a = p0 + v1_vec * safe_size
                p_b = p0 + v2_vec * safe_size

                # ------------------------------------------------------------
                # Split edge A
                # ------------------------------------------------------------

                edge_a = self._find_edge_toward_neighbor(orig_id, p_nb_v1)

                if edge_a is None:
                    continue

                id_a = self._split_edge_at_world_pos(edge_a, p_a)

                if id_a is None:
                    continue

                # ------------------------------------------------------------
                # Split edge B
                # ------------------------------------------------------------

                edge_b = self._find_edge_toward_neighbor(orig_id, p_nb_v2)

                if edge_b is None:
                    continue

                id_b = self._split_edge_at_world_pos(edge_b, p_b)

                if id_b is None:
                    continue

                # NE PAS forcer setPoint(id_a/id_b) ici.
                # C'était ça qui pouvait créer un pic / arc inversé.

                # ------------------------------------------------------------
                # Delete original corner vertex
                # ------------------------------------------------------------

                vtx_str = f"{self.mesh_name}.vtx[{orig_id}]"

                try:
                    cmds.polyDelVertex(vtx_str, ch=False)
                except:
                    continue

                real_a = self._closest_vertex_to_pos(p_a)
                real_b = self._closest_vertex_to_pos(p_b)

                if real_a is None or real_b is None or real_a == real_b:
                    continue

                v_a_str = f"{self.mesh_name}.vtx[{real_a}]"
                v_b_str = f"{self.mesh_name}.vtx[{real_b}]"

                if not cmds.objExists(v_a_str) or not cmds.objExists(v_b_str):
                    continue

                # ------------------------------------------------------------
                # Connect A / B
                # ------------------------------------------------------------

                shared_edge = self._collect_shared_edge(real_a, real_b)

                if not shared_edge:
                    cmds.select([v_a_str, v_b_str], r=True)

                    try:
                        mel.eval("catchQuiet(`polyConnectComponents`)")
                    except:
                        pass

                    real_a = self._closest_vertex_to_pos(p_a)
                    real_b = self._closest_vertex_to_pos(p_b)

                    if real_a is None or real_b is None or real_a == real_b:
                        continue

                    shared_edge = self._collect_shared_edge(real_a, real_b)

                if not shared_edge:
                    continue

                # ------------------------------------------------------------
                # Subdivide arc edge
                # ------------------------------------------------------------

                v_pre_sub = cmds.polyEvaluate(self.mesh_name, v=True)

                if divs > 1:
                    try:
                        cmds.polySubdivideEdge(shared_edge, dv=divs - 1, ch=False)
                    except:
                        continue

                v_post_sub = cmds.polyEvaluate(self.mesh_name, v=True)
                new_ids = list(range(v_pre_sub, v_post_sub))

                real_a = self._closest_vertex_to_pos(p_a)
                real_b = self._closest_vertex_to_pos(p_b)

                if real_a is None or real_b is None or real_a == real_b:
                    continue

                arc_ids = [real_a, real_b] + new_ids
                arc_ids = list(dict.fromkeys([i for i in arc_ids if i is not None]))

                if len(arc_ids) < 2:
                    continue

                # ------------------------------------------------------------
                # Reposition arc vertices
                # ------------------------------------------------------------

                try:
                    dag_arc, mesh_fn_arc = self._get_dag_and_fn()
                except:
                    continue

                positions = {}

                for vid in arc_ids:
                    try:
                        positions[vid] = om.MVector(mesh_fn_arc.getPoint(vid, om.MSpace.kWorld))
                    except:
                        pass

                if real_a not in positions or real_b not in positions:
                    continue

                pos_a = positions[real_a]
                pos_b = positions[real_b]

                points_to_sort = []

                for vid, pos in positions.items():
                    dist = (pos - pos_a).length()
                    points_to_sort.append((vid, dist))

                points_to_sort.sort(key=lambda x: x[1])

                n = len(points_to_sort)

                if n < 2:
                    continue

                for i, (vtx_id, _) in enumerate(points_to_sort):
                    t = float(i) / float(n - 1)

                    pos = (
                        pos_a * ((1.0 - t) ** 2) +
                        p0 * (2.0 * (1.0 - t) * t) +
                        pos_b * (t ** 2)
                    )

                    try:
                        mesh_fn_arc.setPoint(vtx_id, om.MPoint(pos), om.MSpace.kWorld)
                    except:
                        pass

            cmds.select(cl=True)
            cmds.refresh()

        finally:
            cmds.scriptEditorInfo(e=True, suppressWarnings=old_sw)
            cmds.scriptEditorInfo(e=True, suppressInfo=old_si)
            cmds.scriptEditorInfo(e=True, suppressResults=old_sr)

    def validate(self, clear_selection=True, *args):
        self.is_validating = True
        self.is_initialized = False

        if self.tool_job and cmds.scriptJob(exists=self.tool_job):
            try:
                cmds.scriptJob(kill=self.tool_job, force=True)
            except:
                pass

            self.tool_job = None

        if cmds.draggerContext(self.drag_ctx, exists=True):
            try:
                if cmds.currentCtx() == self.drag_ctx:
                    cmds.setToolTo("selectSuperContext")
            except:
                pass

            try:
                cmds.deleteUI(self.drag_ctx)
            except:
                pass

        if cmds.objExists(self.backup_mesh):
            try:
                cmds.delete(self.backup_mesh)
            except:
                pass

        if self.mesh_name and cmds.objExists(self.mesh_name):
            try:
                cmds.delete(self.mesh_name, ch=True)
            except:
                pass

        self._cleanup_temp_sets()

        if clear_selection:
            cmds.select(cl=True)

        self.clear_active_instance()
        self.is_validating = False

        print("MultiFillet validé.")


MultiFilletFinalFix()
