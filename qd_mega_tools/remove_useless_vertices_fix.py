# -*- coding: utf-8 -*-
"""
Patch Maya 2023-2027 pour `mesh_remove_useless_vertices`.

Utilisation:
1) Colle `_remove_vertex_safely` + `mesh_remove_useless_vertices` dans ton script.
2) Remplace l'ancienne implémentation de `mesh_remove_useless_vertices`.
"""

import math
import maya.cmds as cmds


def _remove_vertex_safely(vtx):
    """Supprime un vertex de façon compatible Maya 2023+ et 2027."""
    if not cmds.objExists(vtx):
        return False

    # Essai direct (Maya moderne)
    try:
        cmds.polyDelVertex(vtx, constructionHistory=False)
        return True
    except Exception:
        pass

    # Fallback: supprimer une edge connectée avec cleanVertices
    try:
        edges = vert_to_edges(vtx)
        if edges:
            cmds.polyDelEdge(edges[0], cleanVertices=True, constructionHistory=False)
            return True
    except Exception:
        pass

    return False


def mesh_remove_useless_vertices():
    """
    Retire les vertices inutiles:
    - vertices isolés (0 face)
    - vertices internes de valence 2 alignés (colinéaires)

    Compatible Maya 2023 -> 2027.
    """
    meshes = get_all_selected_meshes()
    if not meshes:
        cmds.warning("Sélectionne un mesh!")
        show_inview_message("Aucun mesh sélectionné!", 2.0, "error")
        return

    cmds.undoInfo(openChunk=True, chunkName="RemoveUselessVerts")
    try:
        total_removed = 0

        for mesh in meshes:
            # Plusieurs passes car la topo change après chaque suppression
            for _pass in range(10):
                if not cmds.objExists(mesh):
                    break

                vcount = int(cmds.polyEvaluate(mesh, vertex=True) or 0)
                if vcount <= 0:
                    break

                to_delete = []

                # Reversed => moins de problèmes d'index quand la topo est éditée
                for i in range(vcount - 1, -1, -1):
                    vtx = "{}.vtx[{}]".format(mesh, i)
                    if not cmds.objExists(vtx):
                        continue

                    faces = vert_to_faces(vtx)
                    edges = vert_to_edges(vtx)

                    # Vertex orphelin
                    if len(faces) == 0:
                        to_delete.append(vtx)
                        continue

                    # On évite de toucher aux bords
                    if is_border_vertex(vtx):
                        continue

                    # Candidat dissolve: valence 2 et aligné
                    if len(edges) == 2:
                        p = get_vertex_position(vtx)
                        if not p:
                            continue

                        neighbors = []
                        for e in edges:
                            vs = edge_to_verts(e)
                            for vv in vs:
                                if vv != vtx and vv not in neighbors:
                                    neighbors.append(vv)

                        if len(neighbors) != 2:
                            continue

                        p0 = get_vertex_position(neighbors[0])
                        p1 = get_vertex_position(neighbors[1])
                        if not p0 or not p1:
                            continue

                        v1 = (p[0] - p0[0], p[1] - p0[1], p[2] - p0[2])
                        v2 = (p1[0] - p[0], p1[1] - p[1], p1[2] - p[2])

                        n1 = normalize(v1)
                        n2 = normalize(v2)

                        # Dot proche de 1 => quasi colinéaire (tolérance un peu plus robuste)
                        if abs(dot(n1, n2)) >= 0.999:
                            to_delete.append(vtx)

                if not to_delete:
                    break

                removed_this_pass = 0
                for vtx in to_delete:
                    if _remove_vertex_safely(vtx):
                        removed_this_pass += 1

                total_removed += removed_this_pass

                if removed_this_pass == 0:
                    break

        valid_meshes = [m for m in meshes if cmds.objExists(m)]
        if valid_meshes:
            cmds.select(valid_meshes, r=True)

        show_inview_message(
            "{} vertices supprimés".format(total_removed),
            2.0,
            "success" if total_removed > 0 else "info"
        )

    except Exception as e:
        cmds.warning("RemoveUseless erreur: {}".format(e))
        show_inview_message("Erreur RemoveUseless!", 2.0, "error")
        import traceback
        traceback.print_exc()
    finally:
        cmds.undoInfo(closeChunk=True)
