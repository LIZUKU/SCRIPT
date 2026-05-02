import math
import re

import maya.cmds as mc
import maya.mel as mel
import maya.OpenMaya as om
import maya.OpenMayaUI as omui
import maya.api.OpenMaya as oma


DRAG_CONTEXT = "Click2dTo3dCtx"

hit_face = ""
anchor_x = 0
anchor_y = 0
picked_mesh_transform = []
visible_mesh_shapes = []
camera_position = [0, 0, 0]
camera_far_clip = 10000
original_parent_path = ""
current_mesh_name = ""
current_mesh_path = ""

current_mode = "move"
mode_start_x = 0
mode_start_y = 0
initial_scale_x = 1.0
initial_scale_y = 1.0
initial_scale_z = 1.0
initial_rotate_y = 0.0
duplicate_done = False

orientation_flip_enabled = False
invert_combo_was_down = False
current_orientation_inverted = False


# ------------------------------------------------------------
# Persistent flip state
# ------------------------------------------------------------

def get_mesh_flip_state(mesh_name):
    attr = mesh_name + ".click2dFlipState"

    if mc.objExists(attr):
        return bool(mc.getAttr(attr))

    return False


def set_mesh_flip_state(mesh_name, state):
    attr = mesh_name + ".click2dFlipState"

    if not mc.objExists(attr):
        try:
            mc.addAttr(mesh_name, ln="click2dFlipState", at="bool", dv=False)
        except Exception:
            return

    try:
        mc.setAttr(attr, bool(state))
    except Exception:
        pass


# ------------------------------------------------------------
# Main drag context
# ------------------------------------------------------------

def start_instant_drag():
    clear_temp_nodes()

    global hit_face
    hit_face = ""

    if mc.draggerContext(DRAG_CONTEXT, exists=True):
        mc.deleteUI(DRAG_CONTEXT)

    mc.draggerContext(
        DRAG_CONTEXT,
        pressCommand=on_drag_press,
        dragCommand=on_drag_move,
        releaseCommand=on_drag_release,
        name=DRAG_CONTEXT,
        cursor="crossHair",
        undoMode="step",
    )
    mc.setToolTo(DRAG_CONTEXT)


def clear_temp_nodes():
    for node in ("instPicker", "instFlip", "instRot", "aimLoc", "pickerAim"):
        if mc.objExists(node):
            mc.delete(node)


def on_drag_press():
    global anchor_x, anchor_y
    global visible_mesh_shapes
    global camera_position
    global picked_mesh_transform
    global original_parent_path
    global current_mesh_name
    global current_mesh_path
    global camera_far_clip
    global current_mode
    global mode_start_x, mode_start_y
    global initial_scale_x, initial_scale_y, initial_scale_z
    global initial_rotate_y
    global hit_face
    global duplicate_done
    global orientation_flip_enabled
    global invert_combo_was_down
    global current_orientation_inverted

    hit_face = ""
    current_mode = "move"
    duplicate_done = False

    orientation_flip_enabled = False
    invert_combo_was_down = False
    current_orientation_inverted = False

    vp_x, vp_y, _ = mc.draggerContext(DRAG_CONTEXT, query=True, anchorPoint=True)
    anchor_x = vp_x
    anchor_y = vp_y
    mode_start_x = vp_x
    mode_start_y = vp_y

    selection_before_press = mc.ls(sl=True, fl=True, l=True)

    world_pos = om.MPoint()
    world_dir = om.MVector()
    omui.M3dView().active3dView().viewToWorld(int(vp_x), int(vp_y), world_pos, world_dir)
    ray_source = om.MFloatPoint(world_pos.x, world_pos.y, world_pos.z)

    view = omui.M3dView.active3dView()
    camera_path = om.MDagPath()
    view.getCamera(camera_path)

    camera_shape_path = camera_path.fullPathName()
    camera_transform = mc.listRelatives(camera_shape_path, type="transform", p=True)

    camera_far_clip = mc.getAttr(camera_transform[0] + ".farClipPlane")
    camera_position = mc.xform(camera_transform, q=True, ws=True, rp=True)

    picked_mesh_transform = []
    closest_mesh = []
    closest_distance = camera_far_clip

    visible_mesh_shapes = get_visible_mesh_shapes()

    for mesh_shape in visible_mesh_shapes:
        selection_list = om.MSelectionList()
        selection_list.add(mesh_shape)

        dag_path = om.MDagPath()
        selection_list.getDagPath(0, dag_path)

        fn_mesh = om.MFnMesh(dag_path)

        hit_point = om.MFloatPoint()

        face_util = om.MScriptUtil()
        face_util.createFromInt(0)
        face_ptr = face_util.asIntPtr()

        has_hit = fn_mesh.closestIntersection(
            om.MFloatPoint(ray_source),
            om.MFloatVector(world_dir),
            None,
            None,
            False,
            om.MSpace.kWorld,
            camera_far_clip,
            False,
            None,
            hit_point,
            None,
            face_ptr,
            None,
            None,
            None,
        )

        if has_hit:
            distance = math.sqrt(
                ((float(camera_position[0]) - hit_point.x) ** 2)
                + ((float(camera_position[1]) - hit_point.y) ** 2)
                + ((float(camera_position[2]) - hit_point.z) ** 2)
            )

            if distance < closest_distance:
                closest_distance = distance
                closest_mesh = mesh_shape

    if selection_before_press:
        selected_shapes = mc.listRelatives(selection_before_press, shapes=True, fullPath=True)

        if selected_shapes:
            closest_mesh = selected_shapes

    if len(closest_mesh) == 0:
        return

    picked_mesh_transform = mc.listRelatives(closest_mesh, type="transform", p=True, f=True)

    if not picked_mesh_transform:
        return

    child_nodes = mc.listRelatives(picked_mesh_transform[0], fullPath=True, ad=True) or []

    original_parent_path = "|".join(picked_mesh_transform[0].split("|")[0:-1])
    current_mesh_path = picked_mesh_transform[0]
    current_mesh_name = picked_mesh_transform[0].split("|")[-1]

    # Flip state is per-drag interaction only.
    # A Ctrl+Shift flip should not persist to the next drag.
    orientation_flip_enabled = False
    current_orientation_inverted = False

    saved_rotation = mc.getAttr(current_mesh_path + ".rotate")[0]
    saved_scale = mc.getAttr(current_mesh_path + ".scale")[0]

    selection_list = oma.MSelectionList()
    selection_list.add(picked_mesh_transform[0])

    dag_path = selection_list.getDagPath(0)
    fn_mesh = oma.MFnMesh(dag_path)

    points = fn_mesh.getPoints(oma.MSpace.kObject)

    min_y = min(point.y for point in points)
    bottom_points = [point for point in points if abs(point.y - min_y) < 0.001]

    pivot_x = sum(point.x for point in bottom_points) / len(bottom_points)
    pivot_z = sum(point.z for point in bottom_points) / len(bottom_points)

    world_matrix = oma.MMatrix(mc.xform(current_mesh_path, q=True, ws=True, matrix=True))

    pivot_object = oma.MPoint(pivot_x, min_y, pivot_z)
    pivot_world = pivot_object * world_matrix

    mc.move(
        pivot_world.x,
        pivot_world.y,
        pivot_world.z,
        current_mesh_path + ".scalePivot",
        current_mesh_path + ".rotatePivot",
        ws=True,
        a=True,
    )

    mc.group(empty=True, n="instPicker")

    mc.duplicate("instPicker")
    mc.rename("instFlip")
    mc.parent("instFlip", "instPicker")

    mc.duplicate("instFlip")
    mc.rename("instRot")
    mc.parent("instRot", "instFlip")

    mc.select("instPicker", picked_mesh_transform[0])
    mc.matchTransform(pos=True, rot=True)

    mc.parent(picked_mesh_transform[0], "instRot")

    mc.setAttr("instPicker.rotate", saved_rotation[0], saved_rotation[1], saved_rotation[2])
    mc.setAttr("instFlip.rotate", 0, 0, 0)
    mc.setAttr("instRot.rotate", 0, 0, 0)
    mc.setAttr("instRot.scale", 1, 1, 1)

    # Apply the stored flip state immediately when the temporary rig is created.
    apply_orientation_flip()

    mesh_path = "instPicker|instFlip|instRot|" + current_mesh_name

    mc.setAttr(mesh_path + ".scaleX", saved_scale[0])
    mc.setAttr(mesh_path + ".scaleY", saved_scale[1])
    mc.setAttr(mesh_path + ".scaleZ", saved_scale[2])


    for node in child_nodes:
        if node in visible_mesh_shapes:
            visible_mesh_shapes.remove(node)

    initial_scale_x = mc.getAttr("instRot.scaleX")
    initial_scale_y = mc.getAttr("instRot.scaleY")
    initial_scale_z = mc.getAttr("instRot.scaleZ")

    initial_rotate_y = mc.getAttr("instRot.rotateY")


def on_drag_release():
    global original_parent_path
    global current_mesh_name
    global current_mesh_path
    global current_mode
    global duplicate_done
    global orientation_flip_enabled

    current_mode = "move"
    duplicate_done = False

    if mc.objExists("instPicker"):
        mesh_path = "instPicker|instFlip|instRot|" + current_mesh_name
        full_mesh_path = "|instPicker|instFlip|instRot|" + current_mesh_name

        if len(original_parent_path) == 0:
            if mc.objExists(mesh_path):
                mc.parent(mesh_path, w=True)
        else:
            if mc.objExists(full_mesh_path):
                mc.parent(full_mesh_path, original_parent_path)

    clear_temp_nodes()

    final_path = (original_parent_path + "|" + current_mesh_name).lstrip("|")
    current_mesh_path = "|" + final_path if final_path else "|" + current_mesh_name

    if mc.objExists(final_path):
        mc.select(final_path)
    elif mc.objExists(current_mesh_name):
        mc.select(current_mesh_name)


# ------------------------------------------------------------
# Modes and modifiers
# ------------------------------------------------------------

def get_drag_mode_from_modifiers():
    modifiers = mc.getModifiers()

    is_shift = bool(modifiers & 1)
    is_ctrl = bool(modifiers & 4)
    is_alt = bool(modifiers & 8)

    if is_alt:
        return "aim"

    # Ctrl + Shift is not really a mode.
    # It only toggles the orientation, then we stay in move mode.
    if is_ctrl and is_shift:
        return "move"

    if is_ctrl:
        return "scale"

    if is_shift:
        return "rotate"

    return "move"


def update_orientation_toggle_from_modifiers():
    global orientation_flip_enabled
    global invert_combo_was_down

    modifiers = mc.getModifiers()

    is_shift = bool(modifiers & 1)
    is_ctrl = bool(modifiers & 4)

    combo_is_down = is_ctrl and is_shift

    if combo_is_down and not invert_combo_was_down:
        orientation_flip_enabled = not orientation_flip_enabled
        set_orientation_inverted(orientation_flip_enabled)

    invert_combo_was_down = combo_is_down


def set_orientation_inverted(inverted):
    global current_orientation_inverted
    global initial_rotate_y

    current_orientation_inverted = inverted

    apply_orientation_flip()

    if mc.objExists("instRot"):
        initial_rotate_y = mc.getAttr("instRot.rotateY")


def apply_orientation_flip():
    if not mc.objExists("instFlip"):
        return

    if orientation_flip_enabled:
        mc.setAttr("instFlip.rotateX", 180)
    else:
        mc.setAttr("instFlip.rotateX", 0)


def switch_drag_mode(new_mode, vp_x, vp_y):
    global current_mode
    global mode_start_x, mode_start_y
    global initial_scale_x, initial_scale_y, initial_scale_z
    global initial_rotate_y
    global duplicate_done

    current_mode = new_mode

    mode_start_x = vp_x
    mode_start_y = vp_y

    if mc.objExists("instRot"):
        initial_scale_x = mc.getAttr("instRot.scaleX")
        initial_scale_y = mc.getAttr("instRot.scaleY")
        initial_scale_z = mc.getAttr("instRot.scaleZ")

    if mc.objExists("instRot"):
        initial_rotate_y = mc.getAttr("instRot.rotateY")

    apply_orientation_flip()

    if new_mode != "duplicate":
        duplicate_done = False


# ------------------------------------------------------------
# Drag callbacks
# ------------------------------------------------------------

def on_drag_move():
    global current_mode

    if not picked_mesh_transform:
        return

    if not mc.objExists("instPicker") or not mc.objExists("instRot"):
        return

    mouse_button = mc.draggerContext(DRAG_CONTEXT, query=True, button=True)

    if mouse_button == 2:
        duplicate_and_continue_drag()
        return

    vp_x, vp_y, _ = mc.draggerContext(DRAG_CONTEXT, query=True, dragPoint=True)

    update_orientation_toggle_from_modifiers()

    new_mode = get_drag_mode_from_modifiers()

    if new_mode != current_mode:
        switch_drag_mode(new_mode, vp_x, vp_y)
        mc.refresh(cv=True, f=True)
        return

    if current_mode != "aim" and mc.objExists("pickerAim"):
        mc.delete("pickerAim")

    if current_mode == "move":
        drag_move(vp_x, vp_y)
    elif current_mode == "rotate":
        drag_rotate(vp_x)
    elif current_mode == "scale":
        drag_scale(vp_x)
    elif current_mode == "aim":
        drag_aim(vp_x, vp_y)

    mc.refresh(cv=True, f=True)


def drag_move(vp_x, vp_y):
    global hit_face

    mesh_shape, hit_point, face_id = raycast_surface(vp_x, vp_y)

    if not mesh_shape:
        return

    face_name = mesh_shape + ".f[" + str(face_id) + "]"

    mc.setAttr("instPicker.translate", hit_point[0], hit_point[1], hit_point[2])

    if hit_face != face_name:
        rx, ry, rz = get_face_rotation(face_name)
        mc.setAttr("instPicker.rotate", rx, ry, rz)
        hit_face = face_name

    apply_orientation_flip()


def drag_rotate(vp_x):
    delta_x = vp_x - mode_start_x
    snapped_step = int(delta_x / 4.0) * 15

    mc.setAttr("instRot.rotateY", initial_rotate_y + snapped_step)

    apply_orientation_flip()


def drag_scale(vp_x):
    delta_x = vp_x - mode_start_x
    factor = max(0.01, 1.0 + (delta_x * 0.01))

    if not mc.objExists("instRot"):
        return

    mc.setAttr("instRot.scaleX", max(0.01, initial_scale_x * factor))
    mc.setAttr("instRot.scaleY", max(0.01, initial_scale_y * factor))
    mc.setAttr("instRot.scaleZ", max(0.01, initial_scale_z * factor))


def duplicate_and_continue_drag():
    global current_mesh_name
    global current_mesh_path
    global initial_scale_x, initial_scale_y, initial_scale_z
    global initial_rotate_y
    global hit_face
    global duplicate_done
    global orientation_flip_enabled

    if not picked_mesh_transform:
        return

    if not mc.objExists("instPicker") or not mc.objExists("instRot"):
        return

    if duplicate_done:
        vp_x, vp_y, _ = mc.draggerContext(DRAG_CONTEXT, query=True, dragPoint=True)
        drag_move(vp_x, vp_y)
        mc.refresh(cv=True, f=True)
        return

    original_mesh_path = "|instPicker|instFlip|instRot|" + current_mesh_name

    if not mc.objExists(original_mesh_path):
        return

    duplicated_nodes = mc.duplicate(original_mesh_path, rr=True)
    duplicated_name = duplicated_nodes[0]

    if len(original_parent_path) == 0:
        mc.parent(original_mesh_path, w=True)
    else:
        mc.parent(original_mesh_path, original_parent_path)

    current_mesh_name = duplicated_name.split("|")[-1]
    current_mesh_path = duplicated_name
    duplicate_done = True

    mc.select(current_mesh_name)

    if mc.objExists("instRot"):
        initial_scale_x = mc.getAttr("instRot.scaleX")
        initial_scale_y = mc.getAttr("instRot.scaleY")
        initial_scale_z = mc.getAttr("instRot.scaleZ")

    if mc.objExists("instRot"):
        initial_rotate_y = mc.getAttr("instRot.rotateY")

    hit_face = ""

    vp_x, vp_y, _ = mc.draggerContext(DRAG_CONTEXT, query=True, dragPoint=True)

    drag_move(vp_x, vp_y)

    mc.refresh(cv=True, f=True)


def drag_aim(vp_x, vp_y):
    mesh_shape, hit_point, face_id = raycast_surface(vp_x, vp_y)

    if not mesh_shape:
        return

    if not mc.objExists("aimLoc"):
        mc.spaceLocator(p=[0, 0, 0], n="aimLoc")

    if not mc.objExists("pickerAim"):
        mc.aimConstraint(
            "aimLoc",
            "instPicker",
            offset=[0, 0, 0],
            weight=1,
            aimVector=[0, 1, 0],
            upVector=[0, 1, 0],
            worldUpType="vector",
            worldUpVector=[0, 1, 0],
            n="pickerAim",
        )

    mc.setAttr("aimLoc.translate", hit_point[0], hit_point[1], hit_point[2])


# ------------------------------------------------------------
# Raycast and scene helpers
# ------------------------------------------------------------

def raycast_surface(vp_x, vp_y):
    global camera_far_clip
    global visible_mesh_shapes
    global camera_position

    world_pos = om.MPoint()
    world_dir = om.MVector()

    omui.M3dView().active3dView().viewToWorld(int(vp_x), int(vp_y), world_pos, world_dir)

    ray_source = om.MFloatPoint(world_pos.x, world_pos.y, world_pos.z)

    closest_mesh = ""
    closest_distance = camera_far_clip

    best_hit = [0.0, 0.0, 0.0]
    best_face_id = 0

    for mesh_shape in visible_mesh_shapes:
        selection_list = om.MSelectionList()
        selection_list.add(mesh_shape)

        dag_path = om.MDagPath()
        selection_list.getDagPath(0, dag_path)

        fn_mesh = om.MFnMesh(dag_path)

        hit_point = om.MFloatPoint()

        face_util = om.MScriptUtil()
        face_util.createFromInt(0)
        face_ptr = face_util.asIntPtr()

        has_hit = fn_mesh.closestIntersection(
            om.MFloatPoint(ray_source),
            om.MFloatVector(world_dir),
            None,
            None,
            False,
            om.MSpace.kWorld,
            camera_far_clip,
            False,
            None,
            hit_point,
            None,
            face_ptr,
            None,
            None,
            None,
        )

        if has_hit:
            distance = math.sqrt(
                (float(camera_position[0]) - hit_point.x) ** 2
                + (float(camera_position[1]) - hit_point.y) ** 2
                + (float(camera_position[2]) - hit_point.z) ** 2
            )

            if distance < closest_distance:
                closest_distance = distance
                closest_mesh = mesh_shape
                best_hit = [hit_point.x, hit_point.y, hit_point.z]
                best_face_id = face_util.getInt(face_ptr)

    return closest_mesh, best_hit, best_face_id


def get_visible_mesh_shapes():
    view = omui.M3dView.active3dView()

    saved_selection = om.MSelectionList()
    om.MGlobal.getActiveSelectionList(saved_selection)

    try:
        om.MGlobal.selectFromScreen(
            0,
            0,
            view.portWidth(),
            view.portHeight(),
            om.MGlobal.kReplaceList,
        )

        selected_objects = om.MSelectionList()
        om.MGlobal.getActiveSelectionList(selected_objects)

    except Exception:
        selected_objects = om.MSelectionList()

    finally:
        try:
            om.MGlobal.setActiveSelectionList(saved_selection, om.MGlobal.kReplaceList)
        except Exception:
            pass

    screen_objects = []
    selected_objects.getSelectionStrings(screen_objects)

    visible_shapes = mc.listRelatives(screen_objects, shapes=True, f=True)
    all_mesh_shapes = mc.ls(type="mesh", l=True)

    if all_mesh_shapes and visible_shapes:
        return list(set(all_mesh_shapes) & set(visible_shapes))

    return []


def get_face_rotation(face_name):
    shape_node = mc.listRelatives(face_name, fullPath=True, parent=True)
    transform_node = mc.listRelatives(shape_node[0], fullPath=True, parent=True)

    world_matrix = oma.MMatrix(
        mc.xform(transform_node, query=True, worldSpace=True, matrix=True)
    )

    face_normal_text = mc.polyInfo(face_name, faceNormals=True)[0]
    face_normal_values = [
        float(value)
        for value in re.findall(r"-?\d*\.\d*", face_normal_text)
    ]

    normal_vector = oma.MVector(face_normal_values) * world_matrix

    up_vector = oma.MVector(0, 1, 0)

    quaternion = oma.MQuaternion(up_vector, normal_vector)
    euler_rotation = quaternion.asEulerRotation()

    return (
        math.degrees(euler_rotation.x),
        math.degrees(euler_rotation.y),
        math.degrees(euler_rotation.z),
    )


# ------------------------------------------------------------
# Align to closest edge tool
# ------------------------------------------------------------

def align_to_closest_edge():
    selection = mc.ls(sl=True, fl=True)

    if len(selection) != 1:
        return

    full_path = mc.ls(selection[0], l=True)
    hierarchy_parts = full_path[0].split("|")

    if len(hierarchy_parts) > 2:
        parent_path = "|".join(hierarchy_parts[1:-1])
        mc.parent(selection[0], w=True)
    else:
        parent_path = ""

    for node in ("sampleCurv*", "sampleMes*", "rotationPlan*"):
        if mc.objExists(node):
            mc.delete(node)

    closest_face, hit_point, closest_edge, edge_point = get_closest_edge()

    mc.select(closest_edge)

    edge_vertices = mc.ls(
        mc.polyListComponentConversion(closest_edge, fe=True, tv=True),
        flatten=True,
    )

    vx, vy, vz = mc.pointPosition(edge_vertices[0], w=True)

    mc.polyPlane(
        w=1,
        h=1,
        sx=1,
        sy=1,
        ax=(0, 1, 0),
        cuv=2,
        ch=0,
        n="rotationPlane",
    )

    mc.polyCreateFacet(
        p=[
            (vx, vy, vz),
            (edge_point[0], edge_point[1], edge_point[2]),
            (hit_point[0], hit_point[1], hit_point[2]),
        ]
    )

    mc.rename("sampleMesh")

    mc.select("rotationPlane.vtx[0:2]", "sampleMesh.vtx[0:2]")
    mel.eval("snap3PointsTo3Points(0);")

    mc.parent(selection[0], "rotationPlane")

    for axis in ("X", "Y", "Z"):
        value = mc.getAttr(selection[0] + ".rotate" + axis)

        if value > 0:
            snapped_value = value + 45
        else:
            snapped_value = value - 45

        mc.setAttr(selection[0] + ".rotate" + axis, int(snapped_value / 90) * 90)

    mc.move(hit_point[0], hit_point[1], hit_point[2], selection[0], rpr=True, wd=True)

    mc.select(selection[0])
    mc.parent(w=True)

    if len(hierarchy_parts) > 2 and parent_path:
        mc.parent(selection[0], parent_path)

    for node in ("sampleCurv*", "sampleMes*", "rotationPlan*"):
        if mc.objExists(node):
            mc.delete(node)


def get_closest_edge():
    selected_mesh = mc.ls(sl=True, fl=True)

    face_name, hit_point = get_closest_mesh_hit(selected_mesh[0])

    face_edges = mc.ls(
        mc.polyListComponentConversion(face_name, ff=True, te=True),
        flatten=True,
    )

    closest_edge = ""
    closest_distance = 1000000
    closest_point_on_edge = []

    for edge in face_edges:
        mc.select(edge)
        mc.polyToCurve(form=2, degree=1, conformToSmoothMeshPreview=1)

        curve = mc.ls(sl=True)

        selection_list = om.MSelectionList()
        selection_list.add(curve[0])

        dag_path = om.MDagPath()
        selection_list.getDagPath(0, dag_path)

        curve_fn = om.MFnNurbsCurve(dag_path)

        target_point = om.MPoint(hit_point[0], hit_point[1], hit_point[2])
        closest_point = curve_fn.closestPoint(target_point)

        distance = math.sqrt(
            (closest_point[0] - hit_point[0]) ** 2
            + (closest_point[1] - hit_point[1]) ** 2
            + (closest_point[2] - hit_point[2]) ** 2
        )

        if distance < closest_distance:
            closest_distance = distance
            closest_edge = edge
            closest_point_on_edge = [
                closest_point[0],
                closest_point[1],
                closest_point[2],
            ]

        mc.delete(curve)

    mc.select(closest_edge)

    return face_name, hit_point, closest_edge, closest_point_on_edge


def get_closest_mesh_hit(mesh_transform):
    mesh_shapes = mc.listRelatives(mesh_transform, f=True, ad=True)

    visible_shapes = get_visible_mesh_shapes()
    visible_shapes = list(set(visible_shapes) - set(mesh_shapes))

    pivot_position = mc.xform(mesh_transform, q=True, ws=True, a=True, piv=True)
    source_position = [pivot_position[0], pivot_position[1], pivot_position[2]]

    closest_distance = 10000

    result_face = []
    result_hit_point = []

    for mesh_shape in visible_shapes:
        transform = mc.listRelatives(mesh_shape, p=True, f=True)

        distance, face_name, hit_point = get_closest_point_on_face(
            transform[0],
            source_position,
        )

        if distance < closest_distance:
            closest_distance = distance
            result_face = face_name
            result_hit_point = hit_point

    return result_face, result_hit_point


def get_closest_point_on_face(mesh_transform, position=None):
    if position is None:
        position = [0, 0, 0]

    vector = oma.MVector(position)

    selection_list = oma.MSelectionList()
    selection_list.add(mesh_transform)

    dag_path = selection_list.getDagPath(0)
    mesh_fn = oma.MFnMesh(dag_path)

    closest_point, face_id = mesh_fn.getClosestPoint(
        oma.MPoint(vector),
        space=oma.MSpace.kWorld,
    )

    face_name = mesh_transform + ".f[" + str(face_id) + "]"

    distance = math.sqrt(
        (position[0] - closest_point[0]) ** 2
        + (position[1] - closest_point[1]) ** 2
        + (position[2] - closest_point[2]) ** 2
    )

    return distance, face_name, [closest_point[0], closest_point[1], closest_point[2]]


def get_face_center(face_name):
    mesh_name = face_name.split(".")[0]
    face_info = mc.polyInfo(face_name, fv=True)

    vertex_ids = []

    raw_indices = ((face_info[0].split(":")[1]).split("\n")[0]).split(" ")

    for item in raw_indices:
        number = "".join([char for char in item.split("|")[-1] if char.isdigit()])

        if number:
            vertex_ids.append(number)

    center_x = 0
    center_y = 0
    center_z = 0

    for vertex_id in vertex_ids:
        x, y, z = mc.pointPosition(mesh_name + ".vtx[" + vertex_id + "]", w=True)

        center_x += x
        center_y += y
        center_z += z

    count = len(vertex_ids)

    return center_x / count, center_y / count, center_z / count


# ------------------------------------------------------------
# Hotkey entry point
# ------------------------------------------------------------

def smart_hotkey():
    selection = mc.ls(sl=True, fl=True)

    if not selection:
        return

    if ".e[" in selection[0]:
        mel.eval("SelectEdgeLoopSp;")
        return

    for obj in selection:
        if mc.nodeType(obj) == "mesh":
            start_instant_drag()
            return

        shapes = mc.listRelatives(obj, shapes=True, fullPath=True) or []

        if any(mc.nodeType(shape) == "mesh" for shape in shapes):
            start_instant_drag()
            return


smart_hotkey()
