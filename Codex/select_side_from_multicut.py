# -*- coding: utf-8 -*-
"""
Sélectionner un côté d'un mesh après une coupe (Multi-Cut / Insert Edge Loop).

Idée:
- Tu sélectionnes les arêtes de la coupe (au moins 1 arête de la loop coupée).
- Le script considère ces arêtes comme une "frontière".
- Il sélectionne toutes les faces d'un seul côté de cette frontière.

Usage (Maya Python):
    import select_side_from_multicut as ssm
    ssm.select_side_from_cut(side="A")   # côté A
    # ou
    ssm.select_side_from_cut(side="B")   # côté opposé
    # ou
    ssm.select_side_from_cut(side="small")  # plus petite zone
    ssm.select_side_from_cut(side="large")  # plus grande zone

Notes:
- Pour un résultat fiable, la coupe doit former une frontière continue.
- Si la frontière n'est pas fermée, la sélection peut englober plus de faces que prévu.
"""

import re
from collections import deque

import maya.cmds as cmds


_EDGE_RE = re.compile(r"\.e\[(\d+)\]")
_FACE_RE = re.compile(r"\.f\[(\d+)\]")


def _flatten(items):
    return cmds.ls(items, fl=True) if items else []


def _edge_indices(edges):
    result = set()
    for edge in _flatten(edges):
        match = _EDGE_RE.search(edge)
        if match:
            result.add(int(match.group(1)))
    return result


def _face_indices(faces):
    result = []
    for face in _flatten(faces):
        match = _FACE_RE.search(face)
        if match:
            result.append(int(match.group(1)))
    return result


def _to_edge_selection(selection):
    as_edges = cmds.polyListComponentConversion(selection, te=True)
    return _flatten(as_edges)


def _edge_faces(mesh, edge_id):
    edge_comp = "%s.e[%d]" % (mesh, edge_id)
    faces = cmds.polyListComponentConversion(edge_comp, fe=True, tf=True)
    return _flatten(faces)


def _build_face_adjacency(mesh, cut_edges):
    face_count = cmds.polyEvaluate(mesh, f=True)
    adjacency = {i: set() for i in range(face_count)}

    edge_count = cmds.polyEvaluate(mesh, e=True)
    for edge_id in range(edge_count):
        if edge_id in cut_edges:
            continue

        faces = _edge_faces(mesh, edge_id)
        ids = _face_indices(faces)
        if len(ids) != 2:
            continue

        a, b = ids[0], ids[1]
        adjacency[a].add(b)
        adjacency[b].add(a)

    return adjacency


def _connected_component(start_face, adjacency):
    visited = set()
    queue = deque([start_face])

    while queue:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)

        for nb in adjacency.get(current, []):
            if nb not in visited:
                queue.append(nb)

    return visited


def _mesh_from_component(comp):
    if "." not in comp:
        return None
    return comp.split(".", 1)[0]


def _pick_side(side, comp_a, comp_b):
    s = str(side).strip().lower()
    if s in ("a", "left", "1"):
        return comp_a
    if s in ("b", "right", "2", "opposite"):
        return comp_b
    if s == "small":
        return comp_a if len(comp_a) <= len(comp_b) else comp_b
    if s == "large":
        return comp_a if len(comp_a) >= len(comp_b) else comp_b
    return comp_a


def select_side_from_cut(side="A"):
    """
    Sélectionne toutes les faces d'un seul côté de la coupe.

    Args:
        side (str):
            - "A" (défaut): côté basé sur la première face trouvée.
            - "B" ou "opposite": côté opposé.
            - "small": prend la plus petite zone.
            - "large": prend la plus grande zone.

    Returns:
        list[str]: Liste des faces sélectionnées.
    """
    selection = _flatten(cmds.ls(sl=True, fl=True))
    if not selection:
        cmds.warning("Sélectionne au moins une arête de la coupe.")
        return []

    edge_sel = _to_edge_selection(selection)
    if not edge_sel:
        cmds.warning("La sélection ne contient pas d'arêtes exploitables.")
        return []

    mesh = _mesh_from_component(edge_sel[0])
    if not mesh:
        cmds.warning("Impossible de déterminer le mesh.")
        return []

    cut_edges = _edge_indices(edge_sel)
    if not cut_edges:
        cmds.warning("Impossible de lire les indices d'arêtes.")
        return []

    first_edge_match = _EDGE_RE.search(edge_sel[0])
    if not first_edge_match:
        cmds.warning("Impossible de lire la première arête.")
        return []

    first_edge_id = int(first_edge_match.group(1))
    border_faces = _edge_faces(mesh, first_edge_id)
    border_face_ids = _face_indices(border_faces)

    if len(border_face_ids) < 2:
        cmds.warning("La coupe est sur un bord ouvert (pas deux côtés détectés).")
        return []

    adjacency = _build_face_adjacency(mesh, cut_edges)

    comp_a = _connected_component(border_face_ids[0], adjacency)
    comp_b = _connected_component(border_face_ids[1], adjacency)

    # Si la frontière est incorrecte et que les deux composantes se rejoignent,
    # on évite une sélection ambiguë.
    if comp_a == comp_b:
        cmds.warning(
            "Impossible de séparer les deux côtés. Vérifie que la coupe forme une frontière continue."
        )
        return []

    chosen = _pick_side(side, comp_a, comp_b)
    faces = ["%s.f[%d]" % (mesh, i) for i in sorted(chosen)]

    cmds.select(faces, r=True)
    print("[cut-side] %d faces sélectionnées (%s)." % (len(faces), side))
    return faces
