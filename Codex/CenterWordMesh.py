# -*- coding: utf-8 -*-
import maya.cmds as cmds
import maya.api.OpenMaya as om2


def get_dag_path(node_name):
    sel = om2.MSelectionList()
    sel.add(node_name)
    return sel.getDagPath(0)


def get_transform_from_selection(node):
    if cmds.nodeType(node) == "transform":
        return node

    parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
    if parents:
        return parents[0]

    return None


def get_world_bbox_center(transform):
    dag_path = get_dag_path(transform)
    fn_dag = om2.MFnDagNode(dag_path)

    bbox = fn_dag.boundingBox
    world_matrix = dag_path.inclusiveMatrix()

    # Convertir en world
    min_pt = bbox.min * world_matrix
    max_pt = bbox.max * world_matrix

    center = om2.MPoint(
        (min_pt.x + max_pt.x) * 0.5,
        (min_pt.y + max_pt.y) * 0.5,
        (min_pt.z + max_pt.z) * 0.5
    )

    return center


def center_selected_to_world():
    selection = cmds.ls(selection=True, long=True) or []
    if not selection:
        cmds.warning("Aucun objet sélectionné.")
        return

    done = set()

    for node in selection:
        transform = get_transform_from_selection(node)
        if not transform or transform in done:
            continue

        # Freeze Transform avant calcul
        try:
            cmds.makeIdentity(
                transform,
                apply=True,
                translate=True,
                rotate=True,
                scale=True,
                normal=False
            )
        except Exception as e:
            cmds.warning("Impossible de freeze '{}' : {}".format(transform, e))
            continue

        center = get_world_bbox_center(transform)

        dag_path = get_dag_path(transform)
        fn_transform = om2.MFnTransform(dag_path)

        current_pos = fn_transform.translation(om2.MSpace.kWorld)

        # Déplacement pour amener le centre à (0,0,0)
        new_pos = om2.MVector(
            current_pos.x - center.x,
            current_pos.y - center.y,
            current_pos.z - center.z
        )

        fn_transform.setTranslation(new_pos, om2.MSpace.kWorld)

        done.add(transform)

    print("Objets recentrés au centre du monde.")


# Run
center_selected_to_world()
