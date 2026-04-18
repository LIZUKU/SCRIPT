# -*- coding: utf-8 -*-
"""
UnBevel from face selection for Autodesk Maya.

Usage (paste in Maya Script Editor, Python tab):

    import unbevel_from_faces as ub
    ub.run_unbevel_from_faces(strength=1.0)

Interactive mode:

    import unbevel_from_faces as ub
    ub.start_unbevel_dragger()
    # drag mouse left/right to adjust
    # call ub.finish_unbevel_dragger() when done (or switch tool)

The tool is intentionally pragmatic: it collapses selected bevel faces toward a
central axis per connected face group, then merges vertices per group.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import maya.cmds as cmds
import maya.api.OpenMaya as om


_FACE_RE = re.compile(r"^(.*)\.f\[(\d+)\]$")
_HUD_NAME = "unbevelFacesHUD"
_CTX_NAME = "unbevelFacesDraggerCtx"
_TOOL_STATE = None


@dataclass
class GroupData:
    """Data for one connected face group."""

    shape: str
    dag_path: om.MDagPath
    face_ids: Set[int]
    vertex_ids: Set[int]
    axis_origin: om.MPoint
    axis_dir: om.MVector
    original_positions: Dict[int, om.MPoint]
    target_positions: Dict[int, om.MPoint]


@dataclass
class MeshData:
    """Container for all groups on one mesh shape."""

    shape: str
    dag_path: om.MDagPath
    mfn_mesh: om.MFnMesh
    groups: List[GroupData]


class UnBevelFaceTool(object):
    """Core logic for face-based unbevel collapse."""

    def __init__(self, merge_distance=0.001, merge_always_on_finish=True):
        self.merge_distance = max(float(merge_distance), 1e-6)
        self.merge_always_on_finish = bool(merge_always_on_finish)
        self.meshes: Dict[str, MeshData] = {}
        self.strength = 0.0
        self._collect_from_selection()

    # -------------------------------
    # Selection & grouping
    # -------------------------------

    def _collect_from_selection(self):
        face_components = cmds.filterExpand(cmds.ls(sl=True, fl=True) or [], sm=34) or []
        if not face_components:
            raise RuntimeError("No polygon faces selected.")

        shape_to_faces: Dict[str, Set[int]] = {}
        for comp in face_components:
            shape, face_id = _parse_face_component(comp)
            if not shape:
                continue
            shape_to_faces.setdefault(shape, set()).add(face_id)

        if not shape_to_faces:
            raise RuntimeError("Selection does not contain valid polygon faces.")

        for shape, face_ids in shape_to_faces.items():
            dag_path = _dag_path_from_shape(shape)
            mfn = om.MFnMesh(dag_path)
            groups_face_ids = _split_connected_face_groups(dag_path, face_ids)

            groups_data: List[GroupData] = []
            for group_faces in groups_face_ids:
                if not group_faces:
                    continue
                group = _build_group_data(shape, dag_path, mfn, group_faces)
                if group and group.vertex_ids:
                    groups_data.append(group)

            if groups_data:
                self.meshes[shape] = MeshData(
                    shape=shape,
                    dag_path=dag_path,
                    mfn_mesh=mfn,
                    groups=groups_data,
                )

        if not self.meshes:
            raise RuntimeError("No valid face groups could be built from selection.")

    # -------------------------------
    # Apply / merge
    # -------------------------------

    def apply_strength(self, strength: float):
        self.strength = max(0.0, min(1.0, float(strength)))

        for mesh_data in self.meshes.values():
            points = mesh_data.mfn_mesh.getPoints(om.MSpace.kObject)

            for group in mesh_data.groups:
                for vid in group.vertex_ids:
                    p0 = group.original_positions[vid]
                    pt = group.target_positions[vid]
                    points[vid] = _lerp_point(p0, pt, self.strength)

            mesh_data.mfn_mesh.setPoints(points, om.MSpace.kObject)

    def merge_vertices(self):
        for mesh_data in self.meshes.values():
            for group in mesh_data.groups:
                vtx_components = ["{}.vtx[{}]".format(mesh_data.shape, vid) for vid in sorted(group.vertex_ids)]
                if len(vtx_components) < 2:
                    continue
                try:
                    cmds.polyMergeVertex(
                        vtx_components,
                        d=self.merge_distance,
                        am=True,
                        ch=False,
                    )
                except Exception as exc:
                    cmds.warning("polyMergeVertex failed on {}: {}".format(mesh_data.shape, exc))

    def finish(self):
        if self.merge_always_on_finish:
            self.merge_vertices()


# ---------------------------------
# Geometry helpers
# ---------------------------------


def _parse_face_component(component: str) -> Tuple[Optional[str], Optional[int]]:
    m = _FACE_RE.match(component)
    if not m:
        return None, None

    node, index_str = m.group(1), m.group(2)
    shape = _as_mesh_shape(node)
    if not shape:
        return None, None

    try:
        return shape, int(index_str)
    except ValueError:
        return None, None



def _as_mesh_shape(node: str) -> Optional[str]:
    if not cmds.objExists(node):
        return None

    if cmds.nodeType(node) == "mesh":
        return cmds.ls(node, l=True)[0]

    if cmds.nodeType(node) == "transform":
        shapes = cmds.listRelatives(node, s=True, ni=True, f=True) or []
        for shp in shapes:
            if cmds.nodeType(shp) == "mesh":
                return shp
    return None



def _dag_path_from_shape(shape: str) -> om.MDagPath:
    sel = om.MSelectionList()
    sel.add(shape)
    return sel.getDagPath(0)



def _split_connected_face_groups(dag_path: om.MDagPath, selected_faces: Set[int]) -> List[Set[int]]:
    selected_faces = set(selected_faces)
    groups = []

    while selected_faces:
        seed = next(iter(selected_faces))
        queue = [seed]
        chunk = set()

        while queue:
            f = queue.pop()
            if f not in selected_faces:
                continue
            selected_faces.remove(f)
            chunk.add(f)

            it = om.MItMeshPolygon(dag_path)
            try:
                it.setIndex(f)
            except RuntimeError:
                continue

            connected = it.getConnectedFaces()
            for nf in connected:
                if nf in selected_faces:
                    queue.append(nf)

        if chunk:
            groups.append(chunk)

    return groups



def _build_group_data(shape: str, dag_path: om.MDagPath, mfn: om.MFnMesh, face_ids: Set[int]) -> Optional[GroupData]:
    vertex_ids = set()
    face_centers = []

    poly_it = om.MItMeshPolygon(dag_path)
    for fid in face_ids:
        try:
            poly_it.setIndex(fid)
        except RuntimeError:
            continue
        for vid in poly_it.getVertices():
            vertex_ids.add(vid)
        face_centers.append(poly_it.center(om.MSpace.kObject))

    if not vertex_ids:
        return None

    original_positions = {vid: mfn.getPoint(vid, om.MSpace.kObject) for vid in vertex_ids}

    sample_points = list(original_positions.values())
    if len(sample_points) >= 3:
        axis_origin = _point_average(sample_points)
    else:
        axis_origin = _point_average(face_centers) if face_centers else _point_average(sample_points)

    axis_dir = _principal_axis(sample_points)

    target_positions = {}
    for vid, p in original_positions.items():
        target_positions[vid] = _project_point_to_axis(p, axis_origin, axis_dir)

    return GroupData(
        shape=shape,
        dag_path=dag_path,
        face_ids=set(face_ids),
        vertex_ids=vertex_ids,
        axis_origin=axis_origin,
        axis_dir=axis_dir,
        original_positions=original_positions,
        target_positions=target_positions,
    )



def _point_average(points: List[om.MPoint]) -> om.MPoint:
    if not points:
        return om.MPoint(0.0, 0.0, 0.0)

    sx = sy = sz = 0.0
    for p in points:
        sx += p.x
        sy += p.y
        sz += p.z
    n = float(len(points))
    return om.MPoint(sx / n, sy / n, sz / n)



def _principal_axis(points: List[om.MPoint]) -> om.MVector:
    """Return the major axis from point cloud by covariance + power iteration."""
    if len(points) < 2:
        return om.MVector(1.0, 0.0, 0.0)

    c = _point_average(points)

    xx = xy = xz = yy = yz = zz = 0.0
    for p in points:
        x = p.x - c.x
        y = p.y - c.y
        z = p.z - c.z
        xx += x * x
        xy += x * y
        xz += x * z
        yy += y * y
        yz += y * z
        zz += z * z

    # Degenerate fallback: choose largest bbox extent axis.
    if (xx + yy + zz) < 1e-12:
        return om.MVector(1.0, 0.0, 0.0)

    mat = (
        (xx, xy, xz),
        (xy, yy, yz),
        (xz, yz, zz),
    )

    v = [1.0, 1.0, 1.0]
    for _ in range(16):
        vx = mat[0][0] * v[0] + mat[0][1] * v[1] + mat[0][2] * v[2]
        vy = mat[1][0] * v[0] + mat[1][1] * v[1] + mat[1][2] * v[2]
        vz = mat[2][0] * v[0] + mat[2][1] * v[1] + mat[2][2] * v[2]

        mag = math.sqrt(vx * vx + vy * vy + vz * vz)
        if mag < 1e-12:
            break
        v = [vx / mag, vy / mag, vz / mag]

    axis = om.MVector(v[0], v[1], v[2])
    if axis.length() < 1e-8:
        axis = om.MVector(1.0, 0.0, 0.0)
    else:
        axis.normalize()
    return axis



def _project_point_to_axis(point: om.MPoint, origin: om.MPoint, axis_dir: om.MVector) -> om.MPoint:
    v = om.MVector(point.x - origin.x, point.y - origin.y, point.z - origin.z)
    t = v * axis_dir
    return om.MPoint(
        origin.x + axis_dir.x * t,
        origin.y + axis_dir.y * t,
        origin.z + axis_dir.z * t,
    )



def _lerp_point(a: om.MPoint, b: om.MPoint, t: float) -> om.MPoint:
    return om.MPoint(
        a.x + (b.x - a.x) * t,
        a.y + (b.y - a.y) * t,
        a.z + (b.z - a.z) * t,
    )


# ---------------------------------
# Public API - one shot
# ---------------------------------


def run_unbevel_from_faces(strength=1.0, merge_distance=0.001):
    """One-shot collapse + merge from currently selected polygon faces."""
    try:
        tool = UnBevelFaceTool(merge_distance=merge_distance, merge_always_on_finish=True)
    except RuntimeError as exc:
        cmds.warning("UnBevel cancelled: {}".format(exc))
        return None

    tool.apply_strength(strength)
    tool.finish()
    _select_original_faces(tool)
    cmds.inViewMessage(amg="<hl>UnBevel faces:</hl> done", pos="topCenter", fade=True)
    return tool


# ---------------------------------
# Public API - interactive dragger
# ---------------------------------


def start_unbevel_dragger(merge_distance=0.001, sensitivity=0.004):
    """
    Start an interactive dragger context.

    - Mouse drag left/right: adjust strength (0..1)
    - On context exit: merge is applied per group
    """
    global _TOOL_STATE

    try:
        tool = UnBevelFaceTool(merge_distance=merge_distance, merge_always_on_finish=True)
    except RuntimeError as exc:
        cmds.warning("Cannot start UnBevel dragger: {}".format(exc))
        return None

    _TOOL_STATE = {
        "tool": tool,
        "start_x": 0.0,
        "start_strength": 0.0,
        "sensitivity": float(sensitivity),
        "merged": False,
    }

    tool.apply_strength(0.0)
    _hud_create_or_update(0.0)

    if cmds.draggerContext(_CTX_NAME, exists=True):
        cmds.deleteUI(_CTX_NAME)

    cmds.draggerContext(
        _CTX_NAME,
        pressCommand=_on_press,
        dragCommand=_on_drag,
        finalizeCommand=_on_finalize,
        cursor="hand",
        undoMode="step",
        space="screen",
    )
    cmds.setToolTo(_CTX_NAME)
    cmds.inViewMessage(amg="<hl>UnBevel faces:</hl> drag horizontal to adjust", pos="topCenter", fade=True)
    return tool



def finish_unbevel_dragger():
    """Manual finish helper (safe to call multiple times)."""
    _on_finalize()


# ---------------------------------
# Interactive callbacks
# ---------------------------------


def _on_press():
    global _TOOL_STATE
    if not _TOOL_STATE:
        return

    anchor = cmds.draggerContext(_CTX_NAME, q=True, anchorPoint=True)
    _TOOL_STATE["start_x"] = float(anchor[0]) if anchor else 0.0
    _TOOL_STATE["start_strength"] = _TOOL_STATE["tool"].strength



def _on_drag():
    global _TOOL_STATE
    if not _TOOL_STATE:
        return

    drag = cmds.draggerContext(_CTX_NAME, q=True, dragPoint=True)
    if not drag:
        return

    current_x = float(drag[0])
    delta = current_x - _TOOL_STATE["start_x"]
    s = _TOOL_STATE["start_strength"] + delta * _TOOL_STATE["sensitivity"]
    s = max(0.0, min(1.0, s))

    _TOOL_STATE["tool"].apply_strength(s)
    _hud_create_or_update(s)



def _on_finalize():
    global _TOOL_STATE
    if not _TOOL_STATE:
        _hud_remove()
        return

    if not _TOOL_STATE.get("merged", False):
        _TOOL_STATE["tool"].finish()
        _TOOL_STATE["merged"] = True

    _select_original_faces(_TOOL_STATE["tool"])
    _hud_remove()
    cmds.inViewMessage(amg="<hl>UnBevel faces:</hl> finalized", pos="topCenter", fade=True)
    _TOOL_STATE = None


# ---------------------------------
# UI helpers
# ---------------------------------


def _hud_create_or_update(value):
    txt = "UnBevel Strength: {:.3f}".format(float(value))

    if cmds.headsUpDisplay(_HUD_NAME, exists=True):
        cmds.headsUpDisplay(_HUD_NAME, edit=True, label=txt)
        return

    # Next free block in section 5 usually avoids collisions.
    block = 0
    while cmds.headsUpDisplay("tmp", section=5, block=block, exists=True):
        block += 1
        if block > 25:
            break

    try:
        cmds.headsUpDisplay(
            _HUD_NAME,
            section=5,
            block=block,
            blockSize="small",
            label=txt,
            labelFontSize="small",
            command=lambda: "",
            event="idle",
        )
    except Exception:
        # Fallback if HUD fails in this Maya UI layout.
        pass



def _hud_remove():
    if cmds.headsUpDisplay(_HUD_NAME, exists=True):
        cmds.headsUpDisplay(_HUD_NAME, remove=True)



def _select_original_faces(tool: UnBevelFaceTool):
    faces = []
    for mesh in tool.meshes.values():
        for grp in mesh.groups:
            for fid in sorted(grp.face_ids):
                faces.append("{}.f[{}]".format(mesh.shape, fid))
    if faces:
        cmds.select(faces, r=True)


if __name__ == "__main__":
    run_unbevel_from_faces(strength=1.0)
