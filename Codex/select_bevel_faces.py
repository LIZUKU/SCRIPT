# -*- coding: utf-8 -*-
"""
Sélection rapide des faces de bevel à partir d'une face "milieu".

Principe:
- Tu sélectionnes 1 face située au milieu de la bande de bevel.
- Le script lit sa "largeur" via les arêtes les plus courtes.
- Ensuite il parcourt les faces voisines et garde celles qui ont:
    * le même nombre d'arêtes,
    * une largeur similaire,
    * une orientation locale compatible.

Usage dans Maya (Python tab):
    import select_bevel_faces as sbf
    sbf.select_bevel_faces_from_seed()

Optionnel:
    sbf.select_bevel_faces_from_seed(width_tolerance=0.22, normal_tolerance=0.55)
"""

import maya.cmds as cmds
import math


def _flatten(items):
    return cmds.ls(items, fl=True) if items else []


def _vsub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _vlen(v):
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def _vnorm(v):
    l = _vlen(v)
    if l < 1e-8:
        return [0.0, 0.0, 0.0]
    return [v[0] / l, v[1] / l, v[2] / l]


def _vdot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _face_edges(face):
    return _flatten(cmds.polyListComponentConversion(face, ff=True, te=True))


def _edge_vertices(edge):
    return _flatten(cmds.polyListComponentConversion(edge, fe=True, tv=True))


def _vtx_pos(vtx):
    return cmds.pointPosition(vtx, world=True)


def _edge_length(edge):
    verts = _edge_vertices(edge)
    if len(verts) != 2:
        return 0.0
    p1 = _vtx_pos(verts[0])
    p2 = _vtx_pos(verts[1])
    return _vlen(_vsub(p2, p1))


def _face_normal(face):
    info = cmds.polyInfo(face, fn=True)
    if not info:
        return [0.0, 0.0, 0.0]

    parts = info[0].strip().split(":")
    if len(parts) < 2:
        return [0.0, 0.0, 0.0]

    xyz = parts[1].strip().split()
    if len(xyz) < 3:
        return [0.0, 0.0, 0.0]

    return _vnorm([float(xyz[0]), float(xyz[1]), float(xyz[2])])


def _face_data(face):
    edges = _face_edges(face)
    if not edges:
        return None

    lengths = sorted([_edge_length(e) for e in edges if _edge_length(e) > 1e-8])
    if not lengths:
        return None

    # largeur = moyenne des arêtes les plus courtes
    short_count = max(1, int(round(len(lengths) * 0.34)))
    short_avg = sum(lengths[:short_count]) / float(short_count)

    # longueur "dominante" (utile pour éviter de choper des petites faces parasites)
    long_count = max(1, int(round(len(lengths) * 0.34)))
    long_avg = sum(lengths[-long_count:]) / float(long_count)

    return {
        "face": face,
        "edge_count": len(edges),
        "short_avg": short_avg,
        "long_avg": long_avg,
        "normal": _face_normal(face),
    }


def _relative_close(value, target, tolerance):
    base = max(abs(target), 1e-8)
    return abs(value - target) / base <= tolerance


def _face_neighbors(face):
    edges = _face_edges(face)
    result = []
    seen = set()

    for edge in edges:
        connected = _flatten(cmds.polyListComponentConversion(edge, fe=True, tf=True))
        for f in connected:
            if f == face or f in seen:
                continue
            seen.add(f)
            result.append(f)

    return result


def _is_matching_face(candidate_data, seed_data, width_tolerance, long_tolerance, normal_tolerance):
    if not candidate_data:
        return False

    if candidate_data["edge_count"] != seed_data["edge_count"]:
        return False

    if not _relative_close(candidate_data["short_avg"], seed_data["short_avg"], width_tolerance):
        return False

    if not _relative_close(candidate_data["long_avg"], seed_data["long_avg"], long_tolerance):
        return False

    dot = _vdot(candidate_data["normal"], seed_data["normal"])
    if dot < normal_tolerance:
        return False

    return True


def select_bevel_faces_from_seed(width_tolerance=0.20, long_tolerance=0.60, normal_tolerance=0.50):
    """
    Sélectionne automatiquement la bande de faces bevel à partir de la face seed.

    Args:
        width_tolerance (float): Tolérance relative sur la largeur (arêtes courtes).
        long_tolerance (float): Tolérance relative sur la longueur dominante.
        normal_tolerance (float): Dot minimum entre normales (0.5 ≈ 60°).
    """
    selection = _flatten(cmds.ls(sl=True, fl=True))
    faces = cmds.filterExpand(selection, sm=34) or []

    if len(faces) != 1:
        cmds.warning("Sélectionne exactement UNE face au milieu du bevel.")
        return []

    seed = faces[0]
    seed_data = _face_data(seed)
    if not seed_data:
        cmds.warning("Impossible de lire les données de la face seed.")
        return []

    mesh_name = seed.split(".f[")[0]

    to_visit = [seed]
    visited = set()
    selected = []

    while to_visit:
        current = to_visit.pop()
        if current in visited:
            continue
        visited.add(current)

        if not current.startswith(mesh_name + ".f["):
            continue

        cdata = _face_data(current)
        if not _is_matching_face(cdata, seed_data, width_tolerance, long_tolerance, normal_tolerance):
            continue

        selected.append(current)

        for nb in _face_neighbors(current):
            if nb not in visited:
                to_visit.append(nb)

    if not selected:
        cmds.warning("Aucune face bevel trouvée avec ces tolérances.")
        return []

    cmds.select(selected, r=True)
    print("[bevel-select] %d faces sélectionnées." % len(selected))
    return selected
