# -*- coding: utf-8 -*-
"""
UnBevel from face selection for Autodesk Maya.

Usage (one-shot):
    import unbevel_from_faces as ub
    ub.run_unbevel_from_faces(strength=1.0)

Interactive mode:
    import unbevel_from_faces as ub
    ub.start_unbevel_dragger()
    # drag mouse left/right to adjust
    # tool finalizes automatically when leaving the context
    # or call:
    ub.finish_unbevel_dragger()

Notes:
- Supports simple bevel strips.
- Automatically splits closed bevel rings / loops into logical sub-groups.
- Intended to collapse selected bevel faces toward reconstructed hard edges.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple, FrozenSet

import maya.cmds as cmds
import maya.api.OpenMaya as om


_FACE_RE = re.compile(r"^(.*)\.f\[(\d+)\]$")
_HUD_NAME = "unbevelFacesHUD"
_CTX_NAME = "unbevelFacesDraggerCtx"
_TOOL_STATE = None

DEBUG = True


def _debug(msg: str):
    if DEBUG:
        print("[UnBevelDebug] {}".format(msg))


# ---------------------------------------------------------
# Data
# ---------------------------------------------------------

@dataclass
class GroupData:
    """Data for one logical unbevel group."""

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


# ---------------------------------------------------------
# Core tool
# ---------------------------------------------------------

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

            connected_groups = _split_connected_face_groups(dag_path, face_ids)
            _debug(
                "Mesh {} -> {} selected faces, {} connected groups".format(
                    shape, len(face_ids), len(connected_groups)
                )
            )

            groups_data: List[GroupData] = []

            for group_faces in connected_groups:
                if not group_faces:
                    continue

                logical_groups = _split_group_by_support_signature(dag_path, group_faces)
                _debug(
                    "Mesh {} connected group {} faces -> {} logical groups".format(
                        shape, len(group_faces), len(logical_groups)
                    )
                )

                for logical_faces in logical_groups:
                    group = _build_single_group_data(shape, dag_path, mfn, logical_faces)
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

        details = (
            "{} | meshes={} groups={} faces={} vertices={} strength={:.3f} "
            "mergeAttempts={} mergeFailures={}"
        ).format(
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


# ---------------------------------------------------------
# Selection / parsing helpers
# ---------------------------------------------------------

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

    ntype = cmds.nodeType(node)

    if ntype == "mesh":
        long_names = cmds.ls(node, l=True) or []
        return long_names[0] if long_names else node

    if ntype == "transform":
        shapes = cmds.listRelatives(node, s=True, ni=True, f=True) or []
        for shp in shapes:
            if cmds.nodeType(shp) == "mesh":
                return shp

    return None


def _dag_path_from_shape(shape: str) -> om.MDagPath:
    sel = om.MSelectionList()
    sel.add(shape)
    return sel.getDagPath(0)


# ---------------------------------------------------------
# Basic mesh helpers
# ---------------------------------------------------------

def _safe_set_face(poly_it: om.MItMeshPolygon, fid: int) -> bool:
    try:
        poly_it.setIndex(fid)
        return True
    except Exception:
        return False


def _safe_set_edge(edge_it: om.MItMeshEdge, eid: int) -> bool:
    try:
        edge_it.setIndex(eid)
        return True
    except Exception:
        return False


def _face_connected_selected_neighbors(dag_path: om.MDagPath, fid: int, selected_faces: Set[int]) -> List[int]:
    poly_it = om.MItMeshPolygon(dag_path)
    if not _safe_set_face(poly_it, fid):
        return []
    return [nf for nf in poly_it.getConnectedFaces() if nf in selected_faces]


def _face_outside_neighbors(dag_path: om.MDagPath, fid: int, selected_faces: Set[int]) -> List[int]:
    poly_it = om.MItMeshPolygon(dag_path)
    if not _safe_set_face(poly_it, fid):
        return []
    return [nf for nf in poly_it.getConnectedFaces() if nf not in selected_faces]


def _face_center(dag_path: om.MDagPath, fid: int) -> Optional[om.MPoint]:
    poly_it = om.MItMeshPolygon(dag_path)
    if not _safe_set_face(poly_it, fid):
        return None
    return poly_it.center(om.MSpace.kObject)


def _face_normal(dag_path: om.MDagPath, fid: int) -> Optional[om.MVector]:
    poly_it = om.MItMeshPolygon(dag_path)
    if not _safe_set_face(poly_it, fid):
        return None
    try:
        n = poly_it.getNormal(om.MSpace.kObject)
    except Exception:
        return None
    if n.length() < 1e-8:
        return None
    n.normalize()
    return n


# ---------------------------------------------------------
# Topology grouping
# ---------------------------------------------------------

def _split_connected_face_groups(dag_path: om.MDagPath, selected_faces: Set[int]) -> List[Set[int]]:
    selected_faces = set(selected_faces)
    groups: List[Set[int]] = []

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

            poly_it = om.MItMeshPolygon(dag_path)
            if not _safe_set_face(poly_it, f):
                continue

            connected = poly_it.getConnectedFaces()
            for nf in connected:
                if nf in selected_faces:
                    queue.append(nf)

        if chunk:
            groups.append(chunk)

    return groups


# ---------------------------------------------------------
# Logical split for loops / rings
# ---------------------------------------------------------

def _angle_deg(v1: om.MVector, v2: om.MVector) -> float:
    if v1.length() < 1e-8 or v2.length() < 1e-8:
        return 180.0
    a = om.MVector(v1)
    b = om.MVector(v2)
    a.normalize()
    b.normalize()
    dot = max(-1.0, min(1.0, a * b))
    return math.degrees(math.acos(dot))


def _cluster_normals(normals: List[om.MVector], threshold_deg: float = 20.0) -> List[om.MVector]:
    """Cluster normals by angle and return representative averaged normals."""
    clusters: List[List[om.MVector]] = []

    for n in normals:
        if n.length() < 1e-8:
            continue

        assigned = False
        for cluster in clusters:
            ref = _average_vector(cluster)
            if ref.length() < 1e-8:
                continue
            ref.normalize()
            if _angle_deg(n, ref) <= threshold_deg:
                cluster.append(n)
                assigned = True
                break

        if not assigned:
            clusters.append([n])

    reps = []
    for cluster in clusters:
        rep = _average_vector(cluster)
        if rep.length() >= 1e-8:
            rep.normalize()
            reps.append(rep)

    return reps


def _signature_from_outside_normals(
    outside_normals: List[om.MVector],
    cluster_reps: List[om.MVector],
    threshold_deg: float = 25.0,
) -> FrozenSet[int]:
    """
    Build a stable signature for one selected face from its outside support normals.
    Signature is a frozenset of support cluster ids.
    """
    result = set()

    for n in outside_normals:
        best_i = None
        best_ang = None

        for i, rep in enumerate(cluster_reps):
            ang = _angle_deg(n, rep)
            if best_ang is None or ang < best_ang:
                best_ang = ang
                best_i = i

        if best_i is not None and best_ang is not None and best_ang <= threshold_deg:
            result.add(best_i)

    return frozenset(result)


def _split_group_by_support_signature(dag_path: om.MDagPath, face_ids: Set[int]) -> List[Set[int]]:
    """
    Split one connected selected group into logical unbevel groups by analyzing
    the normals of neighboring support faces outside the selection.

    This fixes the common case:
    - one topological ring around a face
    - but several geometric unbevel segments (e.g. 4 sides of a cube top bevel)
    """
    if not face_ids:
        return []

    selected = set(face_ids)
    if len(selected) <= 2:
        return [selected]

    # Priority pass: topology/support section signatures.
    # This pass forces a split whenever support sections change, even if angles stay smooth.
    section_split = _split_group_by_support_sections(dag_path, selected)
    if len(section_split) > 1:
        return section_split

    # Gather all outside support normals around this group
    all_outside_normals: List[om.MVector] = []
    face_to_outside_normals: Dict[int, List[om.MVector]] = {}

    for fid in selected:
        normals = []
        for nf in _face_outside_neighbors(dag_path, fid, selected):
            n = _face_normal(dag_path, nf)
            if n is not None:
                normals.append(n)
                all_outside_normals.append(n)
        face_to_outside_normals[fid] = normals

    # If not enough info, keep group as-is
    if len(all_outside_normals) < 2:
        return _maybe_split_closed_loop_by_face_normals(dag_path, selected)

    cluster_reps = _cluster_normals(all_outside_normals, threshold_deg=20.0)
    if len(cluster_reps) <= 2:
        # Simple strip / corner / already coherent enough
        return _maybe_split_closed_loop_by_face_normals(dag_path, selected)

    face_signatures: Dict[int, FrozenSet[int]] = {}
    for fid in selected:
        sig = _signature_from_outside_normals(
            face_to_outside_normals.get(fid, []),
            cluster_reps,
            threshold_deg=25.0,
        )
        face_signatures[fid] = sig

    # BFS split:
    # faces stay together if connected and support signatures are compatible
    # Compatibility rule:
    # - same signature
    # - or one shared support cluster (helps continuity on slightly noisy meshes)
    logical_groups: List[Set[int]] = []
    remaining = set(selected)

    while remaining:
        seed = next(iter(remaining))
        seed_sig = face_signatures.get(seed, frozenset())

        queue = [seed]
        group = set()

        while queue:
            fid = queue.pop()
            if fid not in remaining:
                continue

            remaining.remove(fid)
            group.add(fid)

            sig_a = face_signatures.get(fid, frozenset())

            for nb in _face_connected_selected_neighbors(dag_path, fid, selected):
                if nb not in remaining:
                    continue

                sig_b = face_signatures.get(nb, frozenset())

                compatible = False
                if sig_a == sig_b:
                    compatible = True
                elif sig_a and sig_b and sig_a.intersection(sig_b):
                    compatible = True
                elif not sig_a and not sig_b:
                    compatible = True
                elif sig_b == seed_sig:
                    compatible = True

                if compatible:
                    queue.append(nb)

        if group:
            logical_groups.append(group)

    # Merge tiny accidental fragments back to nearest compatible group
    logical_groups = _merge_tiny_logical_groups(dag_path, logical_groups, face_signatures)

    result = logical_groups if logical_groups else [selected]
    return _merge_oversegmented_groups(dag_path, result, selected)


def _split_group_by_support_sections(
    dag_path: om.MDagPath,
    face_ids: Set[int],
    angle_fallback_deg: float = 35.0,
) -> List[Set[int]]:
    """
    Section-aware segmentation.

    Build a face graph for the selected group, cut adjacency links when local
    section/support topology changes, then rebuild connected components.
    """
    selected = set(face_ids)
    if not selected:
        return []
    if len(selected) <= 2:
        return [selected]

    outside_face_to_region = _build_outside_support_regions(dag_path, selected)
    edge_to_chain, _ = _build_boundary_edge_chains(dag_path, selected)
    if not edge_to_chain:
        return _maybe_split_closed_loop_by_face_normals(dag_path, selected)

    face_signatures: Dict[int, dict] = {}
    for fid in selected:
        face_signatures[fid] = _build_face_section_signature(
            dag_path,
            fid,
            selected,
            edge_to_chain,
            outside_face_to_region,
        )

    adjacency: Dict[int, List[int]] = {fid: [] for fid in selected}
    for fid in selected:
        for nb in _face_connected_selected_neighbors(dag_path, fid, selected):
            if nb < fid:
                continue
            cut = _should_cut_between_faces(
                dag_path,
                fid,
                nb,
                face_signatures[fid],
                face_signatures[nb],
                angle_threshold_deg=angle_fallback_deg,
            )
            if not cut:
                adjacency[fid].append(nb)
                adjacency[nb].append(fid)

    groups: List[Set[int]] = []
    remaining = set(selected)

    while remaining:
        seed = next(iter(remaining))
        queue = [seed]
        chunk = set()

        while queue:
            fid = queue.pop()
            if fid not in remaining:
                continue

            remaining.remove(fid)
            chunk.add(fid)

            for nb in adjacency.get(fid, []):
                if nb in remaining:
                    queue.append(nb)

        if chunk:
            groups.append(chunk)

    groups = _merge_tiny_section_groups(dag_path, groups, face_signatures)
    return groups if groups else [selected]


def _build_outside_support_regions(
    dag_path: om.MDagPath,
    selected_faces: Set[int],
) -> Dict[int, int]:
    """
    Build topological outside support regions.
    Region id is based on connected components in the mesh *outside* selection.
    """
    poly_it = om.MItMeshPolygon(dag_path)
    try:
        face_count = poly_it.count()
    except Exception:
        return {}

    outside_faces = set(range(face_count)) - set(selected_faces)
    if not outside_faces:
        return {}

    face_to_region: Dict[int, int] = {}
    remaining = set(outside_faces)
    region_id = 0

    while remaining:
        seed = next(iter(remaining))
        queue = [seed]
        comp = set()

        while queue:
            fid = queue.pop()
            if fid not in remaining:
                continue

            remaining.remove(fid)
            comp.add(fid)

            poly_it2 = om.MItMeshPolygon(dag_path)
            if not _safe_set_face(poly_it2, fid):
                continue
            for nb in poly_it2.getConnectedFaces():
                if nb in remaining:
                    queue.append(nb)

        if comp:
            for ofid in comp:
                face_to_region[ofid] = region_id
            region_id += 1

    return face_to_region


def _build_boundary_edge_chains(
    dag_path: om.MDagPath,
    selected_faces: Set[int],
) -> Tuple[Dict[int, int], List[Set[int]]]:
    selected = set(selected_faces)
    edge_it = om.MItMeshEdge(dag_path)
    poly_it = om.MItMeshPolygon(dag_path)

    boundary_edges: Set[int] = set()

    for fid in selected:
        if not _safe_set_face(poly_it, fid):
            continue
        for eid in poly_it.getEdges():
            if not _safe_set_edge(edge_it, eid):
                continue
            connected_faces = set(edge_it.getConnectedFaces())
            inside = connected_faces.intersection(selected)
            outside = connected_faces - selected
            if inside and outside:
                boundary_edges.add(eid)

    if not boundary_edges:
        return {}, []

    edge_to_vertices: Dict[int, Set[int]] = {}
    for eid in boundary_edges:
        if not _safe_set_edge(edge_it, eid):
            continue
        edge_to_vertices[eid] = {edge_it.vertexId(0), edge_it.vertexId(1)}

    edge_to_chain: Dict[int, int] = {}
    chains: List[Set[int]] = []
    remaining = set(edge_to_vertices.keys())

    while remaining:
        seed = next(iter(remaining))
        stack = [seed]
        chain = set()

        while stack:
            eid = stack.pop()
            if eid not in remaining:
                continue

            remaining.remove(eid)
            chain.add(eid)
            ev = edge_to_vertices[eid]

            for other in list(remaining):
                if ev.intersection(edge_to_vertices[other]):
                    stack.append(other)

        if chain:
            cid = len(chains)
            chains.append(chain)
            for eid in chain:
                edge_to_chain[eid] = cid

    return edge_to_chain, chains


def _build_face_section_signature(
    dag_path: om.MDagPath,
    fid: int,
    selected_faces: Set[int],
    edge_to_chain: Dict[int, int],
    outside_face_to_region: Dict[int, int],
) -> dict:
    selected = set(selected_faces)
    poly_it = om.MItMeshPolygon(dag_path)
    edge_it = om.MItMeshEdge(dag_path)

    if not _safe_set_face(poly_it, fid):
        return {"boundary_count": 0, "chain_ids": tuple(), "region_ids": tuple()}

    infos = []
    for eid in poly_it.getEdges():
        if not _safe_set_edge(edge_it, eid):
            continue
        connected_faces = set(edge_it.getConnectedFaces())
        outside = sorted(f for f in connected_faces if f not in selected)
        if not outside:
            continue

        chain_id = edge_to_chain.get(eid, -1)
        region_ids = sorted(
            set(outside_face_to_region[ofid] for ofid in outside if ofid in outside_face_to_region)
        )
        region_id = region_ids[0] if region_ids else -1
        infos.append((chain_id, region_id))

    infos.sort()
    unique_chain_ids = tuple(sorted(set(x[0] for x in infos)))
    unique_region_ids = tuple(sorted(set(x[1] for x in infos)))
    return {
        "boundary_count": len(infos),
        "chain_ids": tuple(x[0] for x in infos),
        "region_ids": tuple(x[1] for x in infos),
        "unique_chain_ids": unique_chain_ids,
        "unique_region_ids": unique_region_ids,
    }


def _should_cut_between_faces(
    dag_path: om.MDagPath,
    f1: int,
    f2: int,
    sig1: dict,
    sig2: dict,
    angle_threshold_deg: float = 35.0,
) -> bool:
    # Strong topological/section cuts first.
    bc1 = int(sig1.get("boundary_count", 0))
    bc2 = int(sig2.get("boundary_count", 0))

    # Junction / local-role change: only enforce hard cut on clear topology events.
    # (avoid over-cut on clean strips where boundary edge count can vary by tessellation)
    if (bc1 > 2) != (bc2 > 2):
        return True

    ureg1 = tuple(sig1.get("unique_region_ids", tuple()))
    ureg2 = tuple(sig2.get("unique_region_ids", tuple()))
    if ureg1 and ureg2 and ureg1 != ureg2:
        return True

    uch1 = tuple(sig1.get("unique_chain_ids", tuple()))
    uch2 = tuple(sig2.get("unique_chain_ids", tuple()))
    if uch1 and uch2 and uch1 != uch2:
        return True

    # Angle as fallback only.
    d1 = _estimate_face_flow_direction(dag_path, f1)
    d2 = _estimate_face_flow_direction(dag_path, f2)
    if d1 is not None and d2 is not None:
        if _angle_deg(d1, d2) > angle_threshold_deg:
            return True
    return False


def _estimate_face_flow_direction(dag_path: om.MDagPath, fid: int) -> Optional[om.MVector]:
    poly_it = om.MItMeshPolygon(dag_path)
    if not _safe_set_face(poly_it, fid):
        return None
    try:
        verts = poly_it.getPoints(om.MSpace.kObject)
    except Exception:
        return None
    if len(verts) < 2:
        return None
    axis = _principal_axis(list(verts))
    if axis.length() < 1e-8:
        return None
    axis.normalize()
    return axis


def _merge_tiny_section_groups(
    dag_path: om.MDagPath,
    groups: List[Set[int]],
    face_signatures: Dict[int, dict],
    tiny_size: int = 1,
) -> List[Set[int]]:
    if len(groups) <= 1:
        return groups

    big_groups = [set(g) for g in groups if len(g) > tiny_size]
    small_groups = [set(g) for g in groups if len(g) <= tiny_size]

    if not big_groups or not small_groups:
        return groups

    all_faces = set().union(*groups)

    def dominant_signature(group: Set[int]) -> Tuple[int, Tuple[int, ...], Tuple[int, ...]]:
        freq: Dict[Tuple[int, Tuple[int, ...], Tuple[int, ...]], int] = {}
        for fid in group:
            sig = face_signatures.get(fid, {})
            key = (
                int(sig.get("boundary_count", 0)),
                tuple(sig.get("unique_chain_ids", sig.get("chain_ids", tuple()))),
                tuple(sig.get("unique_region_ids", sig.get("region_ids", tuple()))),
            )
            freq[key] = freq.get(key, 0) + 1
        if not freq:
            return 0, tuple(), tuple()
        return max(freq.items(), key=lambda x: x[1])[0]

    big_sigs = [dominant_signature(g) for g in big_groups]

    for small in small_groups:
        best_idx = None
        best_score = None
        s_sig = dominant_signature(small)

        for i, big in enumerate(big_groups):
            shared_border = 0
            for fid in small:
                for nb in _face_connected_selected_neighbors(dag_path, fid, all_faces):
                    if nb in big:
                        shared_border += 1

            same_sig = 1 if s_sig == big_sigs[i] else 0
            score = (same_sig, shared_border, len(big))
            if best_score is None or score > best_score:
                best_score = score
                best_idx = i

        if best_idx is not None:
            big_groups[best_idx].update(small)
        else:
            big_groups.append(set(small))
            big_sigs.append(s_sig)

    return big_groups


def _merge_oversegmented_groups(
    dag_path: om.MDagPath,
    groups: List[Set[int]],
    selected_faces: Set[int],
    min_size: int = 2,
) -> List[Set[int]]:
    """
    Conservative anti-oversegmentation:
    merge tiny fragments only if they touch exactly one larger neighbor group.
    """
    if len(groups) <= 1:
        return groups

    large = [set(g) for g in groups if len(g) >= min_size]
    small = [set(g) for g in groups if len(g) < min_size]
    if not large or not small:
        return groups

    all_groups = [set(g) for g in groups]
    merged = [set(g) for g in large]

    for frag in small:
        touching = []
        for i, target in enumerate(merged):
            found_touch = False
            for fid in frag:
                for nb in _face_connected_selected_neighbors(dag_path, fid, selected_faces):
                    if nb in target:
                        found_touch = True
                        break
                if found_touch:
                    break
            if found_touch:
                touching.append(i)

        if len(touching) == 1:
            merged[touching[0]].update(frag)
        else:
            merged.append(frag)

    # Keep deterministic order as much as possible using first face id.
    merged.sort(key=lambda g: min(g) if g else 10**9)
    return merged if merged else all_groups


def _maybe_split_closed_loop_by_face_normals(
    dag_path: om.MDagPath,
    face_ids: Set[int],
    seam_angle_deg: float = 22.5,
) -> List[Set[int]]:
    """
    Extra split pass for closed bevel loops (e.g. top bevel ring on a cube).

    Signature-based split can keep the whole loop together when there are only two
    support planes (top + sides). In that case, collapsing all faces as one group
    can project everything toward a single axis. We detect closed loops and split
    them at sharp normal changes so each side/ring segment unbevels independently.
    """
    selected = set(face_ids)
    if len(selected) < 4:
        return [selected]

    if not _is_closed_face_loop(dag_path, selected):
        return [selected]

    face_normals: Dict[int, om.MVector] = {}
    for fid in selected:
        n = _face_normal(dag_path, fid)
        if n is not None and n.length() > 1e-8:
            n.normalize()
            face_normals[fid] = n

    if len(face_normals) < len(selected):
        return [selected]

    remaining = set(selected)
    split_groups: List[Set[int]] = []

    while remaining:
        seed = next(iter(remaining))
        queue = [seed]
        group = set()

        while queue:
            fid = queue.pop()
            if fid not in remaining:
                continue

            remaining.remove(fid)
            group.add(fid)

            n1 = face_normals.get(fid)
            if n1 is None:
                continue

            for nb in _face_connected_selected_neighbors(dag_path, fid, selected):
                if nb not in remaining:
                    continue
                n2 = face_normals.get(nb)
                if n2 is None:
                    continue

                if _angle_deg(n1, n2) <= seam_angle_deg:
                    queue.append(nb)

        if group:
            split_groups.append(group)

    if len(split_groups) <= 1:
        return [selected]

    return split_groups


def _is_closed_face_loop(dag_path: om.MDagPath, face_ids: Set[int]) -> bool:
    """
    Heuristic: closed loop if every selected face has exactly two selected neighbors.
    This matches common bevel rings and avoids splitting open strips.
    """
    selected = set(face_ids)
    if len(selected) < 3:
        return False

    for fid in selected:
        nbs = _face_connected_selected_neighbors(dag_path, fid, selected)
        if len(nbs) != 2:
            return False
    return True


def _merge_tiny_logical_groups(
    dag_path: om.MDagPath,
    groups: List[Set[int]],
    face_signatures: Dict[int, FrozenSet[int]],
    tiny_size: int = 1,
) -> List[Set[int]]:
    if len(groups) <= 1:
        return groups

    big_groups = [set(g) for g in groups if len(g) > tiny_size]
    small_groups = [set(g) for g in groups if len(g) <= tiny_size]

    if not big_groups or not small_groups:
        return groups

    def group_signature(group: Set[int]) -> FrozenSet[int]:
        freq: Dict[FrozenSet[int], int] = {}
        for fid in group:
            sig = face_signatures.get(fid, frozenset())
            freq[sig] = freq.get(sig, 0) + 1
        if not freq:
            return frozenset()
        return max(freq.items(), key=lambda x: x[1])[0]

    big_group_sigs = [group_signature(g) for g in big_groups]

    for sg in small_groups:
        sg_sig = group_signature(sg)

        best_idx = None
        best_score = None

        for i, bg in enumerate(big_groups):
            bg_sig = big_group_sigs[i]

            shared = len(sg_sig.intersection(bg_sig))
            touch = 0
            for fid in sg:
                for nb in _face_connected_selected_neighbors(dag_path, fid, set().union(*big_groups, *small_groups)):
                    if nb in bg:
                        touch += 1

            score = (shared, touch, len(bg))
            if best_score is None or score > best_score:
                best_score = score
                best_idx = i

        if best_idx is not None:
            big_groups[best_idx].update(sg)
        else:
            big_groups.append(set(sg))

    return big_groups


# ---------------------------------------------------------
# Group build
# ---------------------------------------------------------

def _build_single_group_data(
    shape: str,
    dag_path: om.MDagPath,
    mfn: om.MFnMesh,
    face_ids: Set[int],
) -> Optional[GroupData]:
    vertex_ids = set()
    face_centers = []

    poly_it = om.MItMeshPolygon(dag_path)

    for fid in face_ids:
        if not _safe_set_face(poly_it, fid):
            continue
        try:
            vertex_ids.update(poly_it.getVertices())
            face_centers.append(poly_it.center(om.MSpace.kObject))
        except Exception:
            continue

    if not vertex_ids:
        return None

    original_positions = {}
    for vid in vertex_ids:
        try:
            original_positions[vid] = mfn.getPoint(vid, om.MSpace.kObject)
        except Exception:
            pass

    if not original_positions:
        return None

    sample_points = list(original_positions.values())
    axis_origin = _point_average(face_centers) if face_centers else _point_average(sample_points)
    axis_dir = _principal_axis(sample_points)

    # Preferred mode: rebuild from support planes around the selected bevel sub-group.
    corner_line = _compute_corner_line_from_boundaries(dag_path, mfn, face_ids)
    if corner_line:
        axis_origin, axis_dir = corner_line
    elif len(sample_points) >= 3:
        axis_origin = _point_average(sample_points)

    if axis_dir.length() < 1e-8:
        axis_dir = om.MVector(1.0, 0.0, 0.0)
    else:
        axis_dir.normalize()

    target_positions = {}
    for vid, p in original_positions.items():
        target_positions[vid] = _project_point_to_axis(p, axis_origin, axis_dir)

    return GroupData(
        shape=shape,
        dag_path=dag_path,
        face_ids=set(face_ids),
        vertex_ids=set(original_positions.keys()),
        axis_origin=axis_origin,
        axis_dir=axis_dir,
        original_positions=original_positions,
        target_positions=target_positions,
    )


# ---------------------------------------------------------
# Boundary / support plane analysis
# ---------------------------------------------------------

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

    # Choose the pair representing the best support faces:
    # 1) normals least parallel
    # 2) larger boundary coverage
    best_pair = None
    best_key = None

    for i in range(len(candidate_planes)):
        side_a, n1, p1 = candidate_planes[i]
        for j in range(i + 1, len(candidate_planes)):
            side_b, n2, p2 = candidate_planes[j]

            if n1.length() < 1e-8 or n2.length() < 1e-8:
                continue

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
        if not _safe_set_face(poly_it, fid):
            continue

        try:
            edge_ids = poly_it.getEdges()
        except Exception:
            continue

        for eid in edge_ids:
            if not _safe_set_edge(edge_it, eid):
                continue

            try:
                connected_faces = set(edge_it.getConnectedFaces())
            except Exception:
                continue

            selected_on_edge = connected_faces.intersection(selected)
            if not selected_on_edge:
                continue

            outside_faces = connected_faces - selected
            if not outside_faces:
                continue

            try:
                v0 = edge_it.vertexId(0)
                v1 = edge_it.vertexId(1)
            except Exception:
                continue

            data = boundary_edges.setdefault(
                eid,
                {"vertex_ids": set([v0, v1]), "neighbor_face_ids": set()}
            )
            data["neighbor_face_ids"].update(outside_faces)

    if not boundary_edges:
        return []

    # Connected components by shared vertices
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

            linked = [other for other in remaining if edge_to_vertices[eid].intersection(edge_to_vertices[other])]
            queue.extend(linked)

        components.append(
            BoundarySide(
                edge_ids=comp_edges,
                vertex_ids=comp_vertices,
                neighbor_face_ids=comp_neighbors,
            )
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
        if not _safe_set_face(poly_it, fid):
            continue
        try:
            n = poly_it.getNormal(om.MSpace.kObject)
            p = poly_it.center(om.MSpace.kObject)
        except Exception:
            continue

        if n.length() < 1e-8:
            continue

        normals.append(n)
        points.append(p)

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


# ---------------------------------------------------------
# Math helpers
# ---------------------------------------------------------

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


# ---------------------------------------------------------
# Public API - one shot
# ---------------------------------------------------------

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


# ---------------------------------------------------------
# Public API - interactive dragger
# ---------------------------------------------------------

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
        finalize=_on_finalize,   # important : pas finalizeCommand
        cursor="hand",
        undoMode="step",
        space="screen",
    )

    cmds.setToolTo(_CTX_NAME)
    cmds.inViewMessage(
        amg="<hl>UnBevel faces:</hl> drag horizontal to adjust",
        pos="topCenter",
        fade=True,
    )
    return tool


def finish_unbevel_dragger():
    """Manual finish helper (safe to call multiple times)."""
    _on_finalize()


# ---------------------------------------------------------
# Interactive callbacks
# ---------------------------------------------------------

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


# ---------------------------------------------------------
# UI helpers
# ---------------------------------------------------------

def _hud_create_or_update(value):
    txt = "UnBevel Strength: {:.3f}".format(float(value))

    try:
        if cmds.headsUpDisplay(_HUD_NAME, exists=True):
            cmds.headsUpDisplay(_HUD_NAME, edit=True, label=txt)
            return
    except Exception:
        pass

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
        pass


def _hud_remove():
    try:
        if cmds.headsUpDisplay(_HUD_NAME, exists=True):
            cmds.headsUpDisplay(_HUD_NAME, remove=True)
    except Exception:
        pass


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
