"""
boolean_nurbs_curves.py
-----------------------
Boolean Union / Difference / Intersection sur deux NURBS curves 2D fermees dans Maya.
Pipeline : Loft -> nurbsBoolean -> nurbsToPoly -> polyToCurve (degree 1)

UTILISATION :
  - Drag & drop dans le viewport Maya (Maya 2022+)
  - OU : Script Editor -> File > Source Script

Selectionner exactement 2 NURBS curves fermees, cliquer Union / Difference / Intersection.
"""

import maya.cmds as cmds

LOFT_OFFSET = 1.0  # amplitude de l'offset pour le loft temporaire


# ---------------------------------------------------------------------------
# Detection du vecteur normal de la curve (methode de Newell)
# ---------------------------------------------------------------------------

def get_cvs_world(curve_transform):
    shapes = cmds.listRelatives(curve_transform, shapes=True, type="nurbsCurve") or []
    if not shapes:
        return []
    shape = shapes[0]
    num_cvs = cmds.getAttr(shape + ".degree") + cmds.getAttr(shape + ".spans")
    pts = []
    for i in range(num_cvs):
        pos = cmds.xform("{}.cv[{}]".format(curve_transform, i), query=True, worldSpace=True, translation=True)
        pts.append((pos[0], pos[1], pos[2]))
    return pts


def compute_normal_vector(pts):
    """
    Methode de Newell : calcule le vecteur normal d'un polygone quelconque.
    Retourne (nx, ny, nz) normalise.
    """
    n = len(pts)
    nx = ny = nz = 0.0
    for i in range(n):
        cur = pts[i]
        nxt = pts[(i + 1) % n]
        nx += (cur[1] - nxt[1]) * (cur[2] + nxt[2])
        ny += (cur[2] - nxt[2]) * (cur[0] + nxt[0])
        nz += (cur[0] - nxt[0]) * (cur[1] + nxt[1])
    length = (nx * nx + ny * ny + nz * nz) ** 0.5
    if length < 1e-10:
        return (0.0, 1.0, 0.0)
    return (nx / length, ny / length, nz / length)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def check_is_nurbs_curve(transforms):
    for t in transforms:
        sh = cmds.listRelatives(t, shapes=True, type="nurbsCurve") or []
        if not sh:
            raise RuntimeError("'{}' n'est pas une NURBS curve.".format(t))


def loft_curve(curve_transform, normal_vec):
    """
    Duplique la curve avec un offset dans la direction du vecteur normal,
    puis loft entre l'originale et la copie pour creer une surface plane.
    """
    nx, ny, nz = normal_vec
    dx, dy, dz = nx * LOFT_OFFSET, ny * LOFT_OFFSET, nz * LOFT_OFFSET

    dup = cmds.duplicate(curve_transform, returnRootsOnly=True)[0]
    cmds.move(dx, dy, dz, dup, relative=True)

    lofted = cmds.loft(
        curve_transform,
        dup,
        ch=False,
        u=True,
        c=False,
        ar=True,
        d=3,
        ss=1,
        rn=False,
        po=False,
        rsn=True,
    )[0]

    cmds.delete(dup)
    return lofted


def do_nurbs_boolean(surf_a, surf_b, op_int):
    """Lance le nurbsBoolean natif Maya. op: 0=union, 1=difference, 2=intersection."""
    return cmds.nurbsBoolean(surf_a, surf_b, ch=False, nsf=1, op=op_int)


def collect_bool_surfaces(result_nodes, loft_a, loft_b):
    """
    Extrait les transforms nurbsSurface du resultat boolean,
    en excluant les lofts originaux.
    """
    exclude = {loft_a, loft_b}
    surfaces = []

    for node in result_nodes:
        if not cmds.objExists(node):
            continue
        if cmds.nodeType(node) == "transform":
            sh = cmds.listRelatives(node, shapes=True, type="nurbsSurface") or []
            if sh and node not in exclude:
                surfaces.append(node)
        elif cmds.nodeType(node) == "nurbsSurface":
            par = (cmds.listRelatives(node, parent=True) or [None])[0]
            if par and par not in exclude:
                surfaces.append(par)

    # Fallback : scanner la scene
    if not surfaces:
        for t in cmds.ls("nurbsBooleanSurface*", type="transform") or []:
            if t in exclude:
                continue
            sh = cmds.listRelatives(t, shapes=True, type="nurbsSurface") or []
            if sh:
                surfaces.append(t)

    return list(dict.fromkeys(surfaces))


def nurbs_to_poly(surface):
    """Convertit une NURBS surface en mesh (memes params que dans le log Maya)."""
    return cmds.nurbsToPoly(
        surface,
        mnd=1,
        ch=False,
        f=2,
        pt=1,
        pc=200,
        chr=0.1,
        ft=0.01,
        mel=0.001,
        d=0.1,
        ut=1,
        un=1,
        vt=1,
        vn=1,
        uch=False,
        ucr=False,
        cht=0.2,
        es=False,
        ntr=False,
        mrt=False,
        uss=True,
    )[0]


def get_origin_border_edges(mesh, normal_vec, curve_a_pts):
    """
    Parmi les edges de bordure du mesh, retourne ceux qui correspondent
    au cote 'origine' de la curve (pas le cote loft offset).

    Strategie : le bord origine est celui dont la position moyenne
    sur l'axe normal est la plus proche de la position moyenne
    des CVs de la curve originale sur ce meme axe.
    """

    nx, ny, nz = normal_vec

    def proj(pt):
        return pt[0] * nx + pt[1] * ny + pt[2] * nz

    origin_proj = sum(proj(p) for p in curve_a_pts) / len(curve_a_pts)

    # Selectionner les border edges
    cmds.select(mesh)
    cmds.polySelectConstraint(mode=3, type=0x8000, where=1)
    border_edges = cmds.ls(selection=True, flatten=True) or []
    cmds.polySelectConstraint(mode=0)
    cmds.select(clear=True)

    if not border_edges:
        return []

    # Calculer la projection de chaque edge sur l'axe normal
    edge_proj = {}
    for edge in border_edges:
        info = cmds.polyInfo(edge, edgeToVertex=True)
        if not info:
            continue
        tokens = info[0].split()
        vals = []
        for tok in tokens[2:]:
            try:
                vi = int(tok)
                pos = cmds.xform("{}.vtx[{}]".format(mesh, vi), q=True, ws=True, t=True)
                vals.append(proj(pos))
            except Exception:
                pass
        if vals:
            edge_proj[edge] = sum(vals) / len(vals)

    if not edge_proj:
        return border_edges

    # Trouver le bord le plus proche de la projection d'origine
    closest_val = min(edge_proj.values(), key=lambda v: abs(v - origin_proj))
    tol = LOFT_OFFSET * 0.4
    result = [e for e, v in edge_proj.items() if abs(v - closest_val) < tol]
    return result if result else border_edges


# ---------------------------------------------------------------------------
# Pipeline principale
# ---------------------------------------------------------------------------

def run_boolean(operation_str="union"):
    cmds.undoInfo(openChunk=True, chunkName="boolean_nurbs_{}".format(operation_str))
    try:
        _run_boolean_internal(operation_str)
    finally:
        cmds.undoInfo(closeChunk=True)


def _run_boolean_internal(operation_str):
    sel = cmds.ls(selection=True, type="transform")
    if len(sel) != 2:
        cmds.confirmDialog(
            title="Boolean NURBS",
            message="Selectionnez exactement 2 NURBS curves.",
            button=["OK"],
        )
        return

    curve_a, curve_b = sel[0], sel[1]

    try:
        check_is_nurbs_curve([curve_a, curve_b])
    except RuntimeError as e:
        cmds.confirmDialog(title="Boolean NURBS - Erreur", message=str(e), button=["OK"])
        return

    # Vecteur normal reel de la curve A (Newell)
    pts_a = get_cvs_world(curve_a)
    normal_vec = compute_normal_vector(pts_a)
    nx, ny, nz = normal_vec
    print("[Boolean NURBS] Vecteur normal : ({:.3f}, {:.3f}, {:.3f})".format(nx, ny, nz))

    op_int = {"union": 0, "difference": 1, "intersection": 2}[operation_str]
    temp_nodes = []

    try:
        # 1. Loft des deux curves dans la direction normale
        loft_a = loft_curve(curve_a, normal_vec)
        loft_b = loft_curve(curve_b, normal_vec)
        temp_nodes += [loft_a, loft_b]

        # 2. nurbsBoolean natif Maya
        bool_result = do_nurbs_boolean(loft_a, loft_b, op_int)
        bool_surfs = collect_bool_surfaces(bool_result, loft_a, loft_b)

        if not bool_surfs:
            cmds.confirmDialog(
                title="Boolean NURBS",
                message="Le boolean n'a produit aucune surface.\nVerifiez que les courbes se chevauchent.",
                button=["OK"],
            )
            cmds.delete([n for n in temp_nodes if cmds.objExists(n)])
            return

        for s in bool_surfs:
            par = (cmds.listRelatives(s, parent=True) or [None])[0]
            if par:
                temp_nodes.append(par)
            temp_nodes.append(s)

        # 3. nurbsToPoly sur chaque surface boolean
        meshes = []
        for surf in bool_surfs:
            m = nurbs_to_poly(surf)
            meshes.append(m)
            temp_nodes.append(m)

        # 4. Combine + mergeVertex
        if len(meshes) > 1:
            combined = cmds.polyUnite(meshes, ch=False, mergeUVSets=True)[0]
        else:
            combined = meshes[0]
        temp_nodes.append(combined)
        cmds.polyMergeVertex(combined, d=0.001, am=True, ch=False)

        # 5. Border edges du cote origine (pas du cote loft)
        origin_edges = get_origin_border_edges(combined, normal_vec, pts_a)
        if not origin_edges:
            cmds.confirmDialog(
                title="Boolean NURBS",
                message="Impossible de trouver le bord de la courbe resultante.",
                button=["OK"],
            )
            return

        # 6. polyToCurve degree 1
        cmds.select(origin_edges)
        curve_nodes = cmds.polyToCurve(form=2, degree=1, conformToSmoothMeshPreview=True)
        final_curve = cmds.rename(curve_nodes[0], "boolean_{}_#".format(operation_str))

        # 7. Center pivot
        cmds.xform(final_curve, centerPivots=True)

        # 8. Hide les curves originales
        cmds.hide(curve_a)
        cmds.hide(curve_b)

        # 9. Nettoyage de tous les temporaires
        to_del = [n for n in temp_nodes if cmds.objExists(n) and n != final_curve]
        if to_del:
            cmds.delete(to_del)

        for pattern in ["loftedSurface*", "nurbsBooleanSurface*"]:
            for node in cmds.ls(pattern, type="transform") or []:
                if cmds.objExists(node) and node != final_curve:
                    cmds.delete(node)

        cmds.select(final_curve)
        print("[Boolean NURBS] OK -> {}".format(final_curve))

    except Exception as e:
        # Nettoyage d'urgence
        for n in temp_nodes:
            if cmds.objExists(n):
                try:
                    cmds.delete(n)
                except Exception:
                    pass
        cmds.confirmDialog(title="Boolean NURBS - Erreur", message=str(e), button=["OK"])
        import traceback

        traceback.print_exc()


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def open_ui():
    win_id = "booleanNurbsUI"
    if cmds.window(win_id, exists=True):
        cmds.deleteUI(win_id)

    cmds.window(
        win_id,
        title="Boolean NURBS Curves",
        widthHeight=(390, 95),
        sizeable=False,
        resizeToFitChildren=True,
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=6, columnOffset=("both", 14))
    cmds.separator(height=8, style="none")
    cmds.text(label="Selectioner 2 NURBS curves ->", align="left", font="smallBoldLabelFont")
    cmds.text(label="Axe normal detecte automatiquement", align="left", font="tinyBoldLabelFont")
    cmds.separator(height=6, style="none")

    # Les 3 operations boolean cote a cote
    cmds.rowLayout(numberOfColumns=3, columnWidth3=(116, 116, 116), columnAlign3=("center", "center", "center"))
    cmds.button(
        label="Union",
        width=116,
        height=38,
        backgroundColor=(0.22, 0.52, 0.82),
        command=lambda _: run_boolean("union"),
    )
    cmds.button(
        label="Difference (A - B)",
        width=116,
        height=38,
        backgroundColor=(0.80, 0.38, 0.20),
        command=lambda _: run_boolean("difference"),
    )
    cmds.button(
        label="Intersection",
        width=116,
        height=38,
        backgroundColor=(0.28, 0.62, 0.36),
        command=lambda _: run_boolean("intersection"),
    )
    cmds.setParent("..")

    cmds.separator(height=8, style="none")
    cmds.showWindow(win_id)


# ---------------------------------------------------------------------------
# Auto-lancement
# ---------------------------------------------------------------------------

open_ui()
