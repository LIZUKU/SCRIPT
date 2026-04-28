import __main__
import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.mel as mel


# ---------------------------------------------------------
# KILL PREVIOUS INSTANCE WITHOUT LOSING CURRENT SELECTION
# ---------------------------------------------------------
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
        self.divs = 12

        self.drag_ctx = "MultiFilletDragContext"
        self.drag_start_x = 0.0
        self.drag_start_size = 0.0
        self.drag_start_divs = 1

        self.tool_job = None
        self.is_validating = False

        __main__.MULTIFILLET_ACTIVE_INSTANCE = self
        self.init_tool()

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

    def run_border_bevel(self, vertex_selection):
        temp_sets = ["Border_Set", "ToVerif_Set"]

        try:
            cmds.select(vertex_selection, r=True)

            VertexInitSelection = cmds.ls(sl=True, fl=True)

            cmds.ConvertSelectionToFaces()
            cmds.ConvertSelectionToContainedEdges()

            if cmds.objExists("Border_Set"):
                cmds.delete("Border_Set")
            cmds.sets(n="Border_Set")

            cmds.select(VertexInitSelection, r=True)
            cmds.ConvertSelectionToEdges()

            if cmds.objExists("ToVerif_Set"):
                cmds.delete("ToVerif_Set")
            cmds.sets(n="ToVerif_Set")

            Verif = cmds.sets("Border_Set", sub="ToVerif_Set")
            cmds.select(Verif, r=True)

            EdgeSel = cmds.ls(sl=True, fl=True)
            if not EdgeSel:
                return

            cmds.polyExtrudeEdge(
                EdgeSel,
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

            edgeToBevel = cmds.ls(sl=True, fl=True)
            if not edgeToBevel:
                return

            mel.eval('dR_DoCmd("bevelPress")')

        finally:
            for s in temp_sets:
                if cmds.objExists(s):
                    cmds.delete(s)

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
            sensitivity_divs = 0.05
            new_divs = int(round(self.drag_start_divs + dx * sensitivity_divs))
            self.divs = max(1, min(40, new_divs))
        else:
            sensitivity_size = 0.05
            new_size = self.drag_start_size + dx * sensitivity_size
            self.size = max(0.01, min(100.0, new_size))

        self.update_all()

    def drag_release(self):
        pass

    def init_tool(self, *args):
        sel = cmds.ls(sl=True, fl=True)
        if not sel:
            self.clear_active_instance()
            return

        self.mesh_name = sel[0].split('.')[0]
        self.vtx_data = []

        sel_list = om.MGlobal.getActiveSelectionList()
        dag = sel_list.getDagPath(0)

        border_sel = []
        normal_sel = []

        for vtx_str in sel:
            if ".vtx[" not in vtx_str:
                continue

            v_id = int(vtx_str.split('[')[1][:-1])

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

        cmds.delete(self.mesh_name, ch=True)

        if self.backup_mesh and cmds.objExists(self.backup_mesh):
            cmds.delete(self.backup_mesh)

        self.backup_mesh = cmds.duplicate(
            self.mesh_name,
            name=f"{self.mesh_name}_BACKUP"
        )[0]

        cmds.setAttr(f"{self.backup_mesh}.visibility", 0)

        sel_list = om.MGlobal.getSelectionListByName(self.mesh_name)
        dag = sel_list.getDagPath(0)
        mesh_fn = om.MFnMesh(dag)

        for vtx_str in sel:
            v_id = int(vtx_str.split('[')[1][:-1])

            it_vtx = om.MItMeshVertex(dag)
            it_vtx.setIndex(v_id)

            edges = list(it_vtx.getConnectedEdges())

            if len(edges) != 2:
                continue

            p0 = om.MVector(it_vtx.position(om.MSpace.kWorld))

            def get_dir(e_id):
                v_ids = mesh_fn.getEdgeVertices(e_id)
                other = v_ids[1] if v_ids[0] == v_id else v_ids[0]
                return (
                    om.MVector(mesh_fn.getPoint(other, om.MSpace.kWorld)) - p0
                ).normalize()

            self.vtx_data.append({
                "orig_id": v_id,
                "p0": p0,
                "v1_vec": get_dir(edges[0]),
                "v2_vec": get_dir(edges[1]),
                "edge_ids": edges
            })

        if not self.vtx_data:
            if cmds.objExists(self.backup_mesh):
                cmds.delete(self.backup_mesh)
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

        b_shape = cmds.listRelatives(self.backup_mesh, s=True)[0]
        m_shape = cmds.listRelatives(self.mesh_name, s=True)[0]

        cmds.connectAttr(f"{b_shape}.outMesh", f"{m_shape}.inMesh", f=True)
        cmds.disconnectAttr(f"{b_shape}.outMesh", f"{m_shape}.inMesh")

        return True

    def update_all(self, *args):
        if not self.is_initialized or not self.restore_from_backup():
            return

        old_sw = cmds.scriptEditorInfo(q=True, suppressWarnings=True)
        old_si = cmds.scriptEditorInfo(q=True, suppressInfo=True)
        old_sr = cmds.scriptEditorInfo(q=True, suppressResults=True)

        cmds.scriptEditorInfo(e=True, suppressWarnings=True)
        cmds.scriptEditorInfo(e=True, suppressInfo=True)
        cmds.scriptEditorInfo(e=True, suppressResults=True)

        try:
            divs = self.divs
            size = self.size

            sorted_vtx = sorted(
                self.vtx_data,
                key=lambda x: x["orig_id"],
                reverse=True
            )

            for data in sorted_vtx:
                p_a = data["p0"] + (data["v1_vec"] * size)
                p_b = data["p0"] + (data["v2_vec"] * size)

                v_count_before = cmds.polyEvaluate(self.mesh_name, v=True)

                mel.eval(
                    f'polySplit -ch off -ep {data["edge_ids"][0]} 0.1 {self.mesh_name}'
                )
                mel.eval(
                    f'polySplit -ch off -ep {data["edge_ids"][1]} 0.1 {self.mesh_name}'
                )

                id_s, id_e = v_count_before, v_count_before + 1

                cmds.polyDelVertex(
                    f"{self.mesh_name}.vtx[{data['orig_id']}]",
                    ch=False
                )

                id_s = id_s - 1 if id_s > data["orig_id"] else id_s
                id_e = id_e - 1 if id_e > data["orig_id"] else id_e

                v_s_str = f"{self.mesh_name}.vtx[{id_s}]"
                v_e_str = f"{self.mesh_name}.vtx[{id_e}]"

                edges_s = set(cmds.ls(
                    cmds.polyListComponentConversion(v_s_str, te=True),
                    fl=True
                ))
                edges_e = set(cmds.ls(
                    cmds.polyListComponentConversion(v_e_str, te=True),
                    fl=True
                ))

                shared = list(edges_s & edges_e)

                if not shared:
                    cmds.select(cl=True)
                    cmds.select([v_s_str, v_e_str], r=True)

                    try:
                        mel.eval('catchQuiet(`polyConnectComponents`)')
                    except:
                        pass

                    edges_s = set(cmds.ls(
                        cmds.polyListComponentConversion(v_s_str, te=True),
                        fl=True
                    ))
                    edges_e = set(cmds.ls(
                        cmds.polyListComponentConversion(v_e_str, te=True),
                        fl=True
                    ))

                    shared = list(edges_s & edges_e)

                if shared:
                    v_pre_sub = cmds.polyEvaluate(self.mesh_name, v=True)

                    if divs > 1:
                        cmds.polySubdivideEdge(
                            shared[0],
                            dv=divs - 1,
                            ch=False
                        )

                    v_post_sub = cmds.polyEvaluate(self.mesh_name, v=True)

                    sel_list = om.MGlobal.getSelectionListByName(self.mesh_name)
                    mesh_fn = om.MFnMesh(sel_list.getDagPath(0))

                    arc_ids = [id_s, id_e] + list(range(v_pre_sub, v_post_sub))

                    points_to_sort = []

                    for v_id in arc_ids:
                        p_curr = om.MVector(
                            mesh_fn.getPoint(v_id, om.MSpace.kWorld)
                        )
                        dist = (p_curr - p_a).length()
                        points_to_sort.append((v_id, dist))

                    points_to_sort.sort(key=lambda x: x[1])

                    for i, (vtx_id, _) in enumerate(points_to_sort):
                        t = (
                            float(i) / (len(points_to_sort) - 1)
                            if len(points_to_sort) > 1
                            else 0
                        )

                        pos = (
                            p_a * ((1 - t) ** 2) +
                            data["p0"] * (2 * (1 - t) * t) +
                            p_b * (t ** 2)
                        )

                        mesh_fn.setPoint(
                            vtx_id,
                            om.MPoint(pos),
                            om.MSpace.kWorld
                        )

            cmds.select(cl=True)
            cmds.refresh()

        finally:
            cmds.scriptEditorInfo(e=True, suppressWarnings=old_sw)
            cmds.scriptEditorInfo(e=True, suppressInfo=old_si)
            cmds.scriptEditorInfo(e=True, suppressResults=old_sr)

    def clear_active_instance(self):
        if hasattr(__main__, "MULTIFILLET_ACTIVE_INSTANCE"):
            if __main__.MULTIFILLET_ACTIVE_INSTANCE is self:
                __main__.MULTIFILLET_ACTIVE_INSTANCE = None

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

        for s in ["Border_Set", "ToVerif_Set"]:
            if cmds.objExists(s):
                try:
                    cmds.delete(s)
                except:
                    pass

        if clear_selection:
            cmds.select(cl=True)

        self.clear_active_instance()
        self.is_validating = False

        print("C'est enfin stable !")


MultiFilletFinalFix()
