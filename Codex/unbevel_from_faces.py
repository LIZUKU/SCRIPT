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


def _debug(msg: str):
    print("[UnBevelDebug] {}".format(msg))


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
class BoundarySide:
    """One connected boundary side around a selected face group."""

    edge_ids: Set[int]
    vertex_ids: Set[int]
    neighbor_face_ids: Set[int]


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
        self._merge_attempts = 0
        self._merge_failures = 0
        self._merge_errors: List[str] = []
        self._collect_from_selection()
        _debug(self.build_summary(prefix="Tool initialized"))

    # -------------------------------
    # Selection & grouping
    # -------------------------------

    def _collect_from_selection(self):
        face_components = cmds.filterExpand(cmds.ls(sl=True, fl=True) or [], sm=34) or []
        _debug("Selected face components: {}".format(len(face_components)))
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
            _debug("Mesh {} -> {} selected faces, {} connected groups".format(shape, len(face_ids), len(groups_face_ids)))

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
                _debug("Mesh {} usable groups: {}".format(shape, len(groups_data)))

        if not self.meshes:
            raise RuntimeError("No valid face groups could be built from selection.")

    # -------------------------------
    # Apply / merge
    # -------------------------------

    def apply_strength(self, strength: float):
        self.strength = max(0.0, min(1.0, float(strength)))
        _debug("Applying strength {:.4f}".format(self.strength))

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
                self._merge_attempts += 1
                try:
                    cmds.polyMergeVertex(
                        vtx_components,
                        d=self.merge_distance,
                        am=False,
                        ch=False,
                    )
                except Exception as exc:
                    self._merge_failures += 1
                    self._merge_errors.append("{}: {}".format(mesh_data.shape, exc))
                    cmds.warning("polyMergeVertex failed on {}: {}".format(mesh_data.shape, exc))
        _debug(
            "Merge finished - attempts: {}, failures: {}".format(
                self._merge_attempts, self._merge_failures
            )
        )

    def finish(self):
        if self.merge_always_on_finish:
            self.merge_vertices()
        _debug(self.build_summary(prefix="Finish summary"))

    def build_summary(self, prefix="Run summary") -> str:
        mesh_count = len(self.meshes)
        group_count = sum(len(mesh.groups) for mesh in self.meshes.values())
        face_count = sum(len(grp.face_ids) for mesh in self.meshes.values() for grp in mesh.groups)
        vertex_count = sum(len(grp.vertex_ids) for mesh in self.meshes.values() for grp in mesh.groups)
        details = "{} | meshes={} groups={} faces={} vertices={} strength={:.3f} mergeAttempts={} mergeFailures={}".format(
            prefix,
            mesh_count,
            group_count,
            face_count,
            vertex_count,
            self.strength,
            self._merge_attempts,
            self._merge_failures,
        )
        if self._merge_errors:
            details += " errors=[{}]".format("; ".join(self._merge_errors))
        return details


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
    axis_origin = _point_average(face_centers) if face_centers else _point_average(sample_points)
    axis_dir = _principal_axis(sample_points)

    # Preferred mode: rebuild using the 2 support planes around the selected bevel faces.
    # This better matches a "slide to hard corner" unbevel behavior.
    corner_line = _compute_corner_line_from_boundaries(dag_path, mfn, face_ids)
    if corner_line:
        axis_origin, axis_dir = corner_line
    elif len(sample_points) >= 3:
        axis_origin = _point_average(sample_points)

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


def _compute_corner_line_from_boundaries(
    dag_path: om.MDagPath,
    mfn: om.MFnMesh,
    face_ids: Set[int],
) -> Optional[Tuple[om.MPoint, om.MVector]]:
    sides = _collect_boundary_sides(dag_path, face_ids)
    if len(sides) < 2:
        return None

    candidate_planes: List[Tuple[BoundarySide, om.MVector, om.MPoint]] = []
    for side in sides:
        plane = _fit_support_plane_from_side(dag_path, mfn, side)
        if not plane:
            continue
        n, p = plane
        candidate_planes.append((side, n, p))

    if len(candidate_planes) < 2:
        return None

    # Choose the pair that best represents opposite support faces.
    # We prioritize:
    #   1) normals least parallel (small |dot|)
    #   2) larger boundary support (more edge coverage)
    best_pair: Optional[Tuple[om.MVector, om.MPoint, om.MVector, om.MPoint]] = None
    best_key: Optional[Tuple[float, int]] = None
    for i in range(len(candidate_planes)):
        side_a, n1, p1 = candidate_planes[i]
        for j in range(i + 1, len(candidate_planes)):
            side_b, n2, p2 = candidate_planes[j]
            dot_val = abs(n1 * n2)
            size_score = len(side_a.edge_ids) + len(side_b.edge_ids)
            key = (dot_val, -size_score)
            if best_key is None or key < best_key:
                best_key = key
                best_pair = (n1, p1, n2, p2)

    if not best_pair:
        return None

    n1, p1, n2, p2 = best_pair
    return _intersect_two_planes(n1, p1, n2, p2)


def _collect_boundary_sides(dag_path: om.MDagPath, face_ids: Set[int]) -> List[BoundarySide]:
    selected = set(face_ids)
    edge_it = om.MItMeshEdge(dag_path)
    boundary_edges: Dict[int, Dict[str, Set[int]]] = {}

    for fid in selected:
        poly_it = om.MItMeshPolygon(dag_path)
        try:
            poly_it.setIndex(fid)
        except RuntimeError:
            continue

        for eid in poly_it.getEdges():
            try:
                edge_it.setIndex(eid)
            except RuntimeError:
                continue

            connected_faces = set(edge_it.getConnectedFaces())
            selected_on_edge = connected_faces.intersection(selected)
            if not selected_on_edge:
                continue

            outside_faces = connected_faces - selected
            if not outside_faces:
                continue

            data = boundary_edges.setdefault(
                eid, {"vertex_ids": set(edge_it.vertexId(i) for i in range(2)), "neighbor_face_ids": set()}
            )
            data["neighbor_face_ids"].update(outside_faces)

    if not boundary_edges:
        return []

    # Connected components by shared vertices.
    components: List[BoundarySide] = []
    remaining = set(boundary_edges.keys())
    edge_to_vertices = {eid: set(boundary_edges[eid]["vertex_ids"]) for eid in boundary_edges}

    while remaining:
        seed = next(iter(remaining))
        queue = [seed]
        comp_edges = set()
        comp_vertices = set()
        comp_neighbors = set()

        while queue:
            eid = queue.pop()
            if eid not in remaining:
                continue
            remaining.remove(eid)
            comp_edges.add(eid)
            comp_vertices.update(edge_to_vertices[eid])
            comp_neighbors.update(boundary_edges[eid]["neighbor_face_ids"])

            for other in list(remaining):
                if edge_to_vertices[eid].intersection(edge_to_vertices[other]):
                    queue.append(other)

        components.append(
            BoundarySide(edge_ids=comp_edges, vertex_ids=comp_vertices, neighbor_face_ids=comp_neighbors)
        )

    return components


def _fit_support_plane_from_side(
    dag_path: om.MDagPath,
    mfn: om.MFnMesh,
    side: BoundarySide,
) -> Optional[Tuple[om.MVector, om.MPoint]]:
    if not side.neighbor_face_ids:
        return None

    poly_it = om.MItMeshPolygon(dag_path)
    normals: List[om.MVector] = []
    points: List[om.MPoint] = []

    for fid in side.neighbor_face_ids:
        try:
            poly_it.setIndex(fid)
        except RuntimeError:
            continue
        normals.append(poly_it.getNormal(om.MSpace.kObject))
        points.append(poly_it.center(om.MSpace.kObject))

    if not normals or not points:
        return None

    normal = _average_vector(normals)
    if normal.length() < 1e-8:
        return None
    normal.normalize()
    plane_point = _point_average(points)
    return normal, plane_point


def _intersect_two_planes(
    n1: om.MVector,
    p1: om.MPoint,
    n2: om.MVector,
    p2: om.MPoint,
) -> Optional[Tuple[om.MPoint, om.MVector]]:
    direction = n1 ^ n2
    denom = direction.length() ** 2
    if denom < 1e-12:
        return None

    d1 = n1 * om.MVector(p1.x, p1.y, p1.z)
    d2 = n2 * om.MVector(p2.x, p2.y, p2.z)

    c1 = n2 ^ direction
    c2 = direction ^ n1

    point_vec = ((c1 * d1) + (c2 * d2)) / denom
    point = om.MPoint(point_vec.x, point_vec.y, point_vec.z)

    if direction.length() < 1e-8:
        return None
    direction.normalize()
    return point, direction



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


def _average_vector(vectors: List[om.MVector]) -> om.MVector:
    if not vectors:
        return om.MVector(0.0, 0.0, 0.0)

    sx = sy = sz = 0.0
    for v in vectors:
        sx += v.x
        sy += v.y
        sz += v.z
    n = float(len(vectors))
    return om.MVector(sx / n, sy / n, sz / n)


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
    _debug("run_unbevel_from_faces called (strength={}, merge_distance={})".format(strength, merge_distance))
    try:
        tool = UnBevelFaceTool(merge_distance=merge_distance, merge_always_on_finish=True)
    except RuntimeError as exc:
        cmds.warning("UnBevel cancelled: {}".format(exc))
        return None

    tool.apply_strength(strength)
    tool.finish()
    _select_original_faces(tool)
    _debug(tool.build_summary(prefix="Final summary"))
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

    try:
        if cmds.headsUpDisplay(_HUD_NAME, exists=True):
            cmds.headsUpDisplay(_HUD_NAME, edit=True, label=txt)
            return
    except Exception:
        # In some Maya versions/layouts, querying/editing can throw
        # "invalid flag combination"; fallback to recreate below.
        pass

    # Query Maya for the next free HUD block (avoid invalid exists+section+block flag combos).
    try:
        block = int(cmds.headsUpDisplay(nextFreeBlock=5))
    except Exception:
        block = 0

    try:
        cmds.headsUpDisplay(
            _HUD_NAME,
            section=5,
            block=block,
            label=txt,
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
    _debug("Reselected original faces: {}".format(len(faces)))


if __name__ == "__main__":
    start_unbevel_dragger()
