# -*- coding: utf-8 -*-

import math
import uuid

import maya.cmds as mc
import maya.mel as mel
import maya.OpenMaya as om1
import maya.OpenMayaUI as omui
import maya.api.OpenMaya as om

try:
    import maya.plugin.polyBoolean.booltoolUtils as btUtils
except Exception:
    btUtils = None


# ============================================================
# CONFIG
# ============================================================

CTX = "PlugBoolDragCtx"
TMP = "plugBoolDragTmp_"
CUTTER_GROUP = "_boolean_cutters"
CUTTER_GROUP_PREFIX = "_boolean_cutters_fastbool"
CUTTER_GROUP_OWNED_ATTR = "plugBoolOwnedCutterGroup"
CUTTER_GROUP_SESSION_ATTR = "plugBoolCutterGroupSession"

# Petit offset le long de la normale pour éviter que le cutter soit exactement
# coplanaire avec la surface au moment du booléen.
SURFACE_OUTWARD_OFFSET = 0.01

# IMPORTANT :
# Le duplicata du cutter est physiquement pré-flippé en Y au tout début
# via scaleY = -1 + freeze transform.
# Donc l'état booléen initial reste NON flippé :
# 0 = subtract / différence
# 1 = union
DEFAULT_BOOLEAN_FLIP = False
BOOL_UNION = 1
BOOL_SUBTRACT = 2

FLIP_ATTR = "click2dFlipState"
TWIST_ATTR = "click2dTwistY"
PREFLIP_Y_ATTR = "plugBoolPreFlipY"
INITIAL_NORMAL_REVERSE_ATTR = "plugBoolInitialNormalReverseDone"
ACTIVE_DUP_ATTR = "plugBoolActiveDuplicateToken"
LIVE_BOOL_NODE_ATTR = "plugBoolLiveNode"
LIVE_BOOL_RESULT_ATTR = "plugBoolLiveResult"
LIVE_BOOL_TARGET_ATTR = "plugBoolLiveTarget"
BASE_ROT_ATTRS = ("click2dBaseRotX", "click2dBaseRotY", "click2dBaseRotZ")

VALIDATE_NAME_CMD = "PlugBoolValidateNameCommand"
OPT_V_PRESS = "PlugBool_saved_v_press"
OPT_V_RELEASE = "PlugBool_saved_v_release"

DEBUG = False
EPS = 0.000001


# ============================================================
# GLOBAL STATE - BOOLEAN
# ============================================================

boolean_target_mesh = ""
boolean_cutter_mesh = ""
boolean_result_mesh = ""
last_boolean_node = ""
last_bool_input_index = 1

orientation_flip_enabled = DEFAULT_BOOLEAN_FLIP
boolean_flip_state = 0

tool_job = None
drag_session_active = False
reuse_selected_cutter_once = False
suppress_next_tool_changed = False
current_cutter_reused = False
validate_hotkey_installed = False
last_mmb_duplicate_cutter = ""
active_cutter_group = ""
active_cutter_group_session = ""
pending_restart_cutter = ""


# ============================================================
# BASIC HELPERS
# ============================================================

def dbg(msg):
    if DEBUG:
        print("[PlugBoolDrag] {0}".format(msg))


def safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def exists(node):
    return bool(node and mc.objExists(node))


def base_node(node):
    return node.split(".", 1)[0] if node else ""


def sn(node):
    return node.split("|")[-1] if node else ""


def fp(node):
    node = base_node(node)
    if not node:
        return ""

    if not exists(node):
        short = sn(node)
        if short and exists(short):
            node = short
        else:
            return ""

    found = mc.ls(node, l=True) or []
    return found[0] if found else ""


def ln(node):
    return fp(node)


def parent_path(node):
    node = fp(node)
    return "|".join(node.split("|")[:-1]) if node and "|" in node.strip("|") else ""


def safe_delete(nodes):
    if isinstance(nodes, str):
        nodes = [nodes]

    for node in nodes or []:
        if exists(node):
            try:
                mc.delete(node)
            except Exception as exc:
                dbg(exc)


def is_comp(node):
    return any(x in node for x in [".vtx[", ".e[", ".f[", ".map["])


def shape_from_node(node):
    node = fp(node)
    if not node:
        return ""

    if mc.nodeType(node) == "mesh":
        return node

    shapes = mc.listRelatives(node, s=True, ni=True, f=True, type="mesh") or []
    shapes += mc.listRelatives(node, s=True, ad=True, ni=True, f=True, type="mesh") or []
    return fp(shapes[0]) if shapes else ""


def transform_from_node(node):
    node = fp(node)
    if not node:
        return ""

    if mc.nodeType(node) == "mesh":
        parents = mc.listRelatives(node, p=True, f=True) or []
        return fp(parents[0]) if parents else ""

    return node if shape_from_node(node) else ""


def mesh_shapes(node):
    node = fp(node)
    if not node:
        return []

    if mc.nodeType(node) == "mesh":
        return [node]

    shapes = mc.listRelatives(node, s=True, ni=True, f=True, type="mesh") or []
    shapes += mc.listRelatives(node, s=True, ad=True, ni=True, f=True, type="mesh") or []
    return [fp(s) for s in dict.fromkeys(shapes) if exists(s)]


def mesh_shapes_under(xform):
    return mesh_shapes(xform)


def has_mesh(node):
    return bool(transform_from_node(node))


def is_mesh_xform(node):
    node = fp(node)
    return bool(node and not is_comp(node) and mc.nodeType(node) != "mesh" and mesh_shapes_under(node))


def first_mesh_shape(xform):
    shapes = mesh_shapes_under(xform)
    return shapes[0] if shapes else ""


def shape_parent(shape):
    shape = fp(shape)
    p = mc.listRelatives(shape, parent=True, fullPath=True) or []
    return fp(p[0]) if p else ""


def shape_under(shape, xform):
    shape = fp(shape)
    xform = fp(xform)
    if not shape or not xform:
        return False

    p = shape_parent(shape)
    return p == xform or p.startswith(xform + "|")


def world_matrix(node):
    node = fp(node)
    if not node:
        return None

    try:
        return mc.xform(node, q=True, ws=True, matrix=True)
    except Exception as exc:
        dbg(exc)
        return None


def set_world_matrix(node, matrix):
    node = fp(node)
    if node and matrix:
        try:
            mc.xform(node, ws=True, matrix=matrix)
        except Exception as exc:
            dbg(exc)

    return fp(node)


def parent_keep_world(node, parent_node=""):
    node = fp(node)
    if not node:
        return ""

    wm = world_matrix(node)

    try:
        result = mc.parent(node, parent_node) if parent_node and exists(parent_node) else mc.parent(node, w=True)
        node = fp(result[0]) if result else fp(node)
    except Exception as exc:
        dbg(exc)
        node = fp(node)

    return set_world_matrix(node, wm)


def normalized_vector(vector, fallback):
    if vector.length() < EPS:
        vector = om.MVector(*fallback)

    if vector.length() < EPS:
        vector = om.MVector(1, 0, 0)

    vector.normalize()
    return vector


def matrix_to_euler_deg(matrix):
    euler = om.MTransformationMatrix(matrix).rotation()
    return math.degrees(euler.x), math.degrees(euler.y), math.degrees(euler.z)


def clean_world_rotation(node):
    matrix = world_matrix(node)
    if not matrix:
        try:
            rot = mc.xform(node, q=True, ws=True, ro=True)
            return rot[0], rot[1], rot[2]
        except Exception:
            return 0, 0, 0

    x_axis = normalized_vector(om.MVector(matrix[0], matrix[1], matrix[2]), (1, 0, 0))
    y_axis = normalized_vector(om.MVector(matrix[4], matrix[5], matrix[6]), (0, 1, 0))
    z_axis = normalized_vector(om.MVector(matrix[8], matrix[9], matrix[10]), (0, 0, 1))

    if (x_axis ^ y_axis) * z_axis < 0:
        y_axis *= -1.0

    x_axis = normalized_vector(x_axis, (1, 0, 0))
    y_axis = normalized_vector(y_axis - x_axis * (x_axis * y_axis), (0, 1, 0))
    z_axis = normalized_vector(x_axis ^ y_axis, (0, 0, 1))

    return matrix_to_euler_deg(om.MMatrix([
        x_axis.x, x_axis.y, x_axis.z, 0,
        y_axis.x, y_axis.y, y_axis.z, 0,
        z_axis.x, z_axis.y, z_axis.z, 0,
        0, 0, 0, 1
    ]))


def dag(node):
    shape = shape_from_node(node)
    if not shape:
        raise RuntimeError("No mesh shape found: {0}".format(node))

    sel = om.MSelectionList()
    sel.add(shape)
    return sel.getDagPath(0)


def mesh_fn(node):
    return om.MFnMesh(dag(node))


def distance(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


# ============================================================
# ATTR HELPERS
# ============================================================

def add_attr(node, attr, kind="string", default=0):
    node = transform_from_node(node) or fp(node)
    if not node or mc.objExists(node + "." + attr):
        return

    try:
        if kind == "string":
            mc.addAttr(node, ln=attr, dt="string")
        elif kind == "bool":
            mc.addAttr(node, ln=attr, at="bool", dv=bool(default))
        elif kind == "double":
            mc.addAttr(node, ln=attr, at="double", dv=float(default))
        else:
            mc.addAttr(node, ln=attr, at="long", dv=int(default))
    except Exception:
        pass


def get_attr(node, attr_name, default=None):
    node = transform_from_node(node) or fp(node)
    attr = node + "." + attr_name if node else ""

    if not attr or not mc.objExists(attr):
        return default

    try:
        return mc.getAttr(attr)
    except Exception:
        return default


def set_attr(node, attr_name, value, attr_type="double"):
    node = transform_from_node(node) or fp(node)
    if not node:
        return

    try:
        if attr_type == "bool":
            add_attr(node, attr_name, "bool", bool(value))
            mc.setAttr(node + "." + attr_name, bool(value))
        elif attr_type == "string":
            add_attr(node, attr_name, "string")
            mc.setAttr(node + "." + attr_name, value or "", type="string")
        elif attr_type == "long":
            add_attr(node, attr_name, "long", int(value))
            mc.setAttr(node + "." + attr_name, int(value))
        else:
            add_attr(node, attr_name, "double", float(value))
            mc.setAttr(node + "." + attr_name, float(value))
    except Exception as exc:
        dbg(exc)


def get_bool_attr(node, attr_name, default=False):
    return bool(get_attr(node, attr_name, default))


def set_bool_attr(node, attr_name, value):
    set_attr(node, attr_name, bool(value), "bool")


def get_float_attr(node, attr_name, default=0.0):
    try:
        return float(get_attr(node, attr_name, default))
    except Exception:
        return default


def set_float_attr(node, attr_name, value):
    set_attr(node, attr_name, float(value), "double")


def set_str(node, attr, value):
    set_attr(node, attr, value or "", "string")


def get_str(node, attr):
    node = transform_from_node(node) or fp(node)
    if not node or not mc.objExists(node + "." + attr):
        return ""

    v = safe(lambda: mc.getAttr(node + "." + attr), "")
    return fp(v) if v else ""


def set_int(node, attr, value):
    set_attr(node, attr, int(value), "long")


def get_int(node, attr, default=0):
    node = transform_from_node(node) or fp(node)
    if not node or not mc.objExists(node + "." + attr):
        return default

    return safe(lambda: int(mc.getAttr(node + "." + attr)), default)


def has_base_rot_attrs(node):
    node = transform_from_node(node)
    return bool(node and all(mc.objExists(node + "." + attr) for attr in BASE_ROT_ATTRS))


def get_base_rotation(node, fallback=None):
    if has_base_rot_attrs(node):
        return [get_float_attr(node, attr, 0.0) for attr in BASE_ROT_ATTRS]

    return fallback if fallback is not None else clean_world_rotation(node)


def set_base_rotation(node, rotation):
    if node and rotation:
        for attr, value in zip(BASE_ROT_ATTRS, rotation):
            set_float_attr(node, attr, value)


def set_flip(mesh, state):
    mesh = transform_from_node(mesh) or fp(mesh)
    if not mesh:
        return

    set_bool_attr(mesh, FLIP_ATTR, bool(state))


def get_flip(mesh):
    mesh = transform_from_node(mesh) or fp(mesh)
    if not mesh or not mc.objExists(mesh + "." + FLIP_ATTR):
        return DEFAULT_BOOLEAN_FLIP

    return safe(lambda: bool(mc.getAttr(mesh + "." + FLIP_ATTR)), DEFAULT_BOOLEAN_FLIP)


# ============================================================
# CLEAN MESH BEFORE BOOLEAN
# ============================================================

def prepare_clean_mesh_transform(node, label="mesh"):
    """
    Prépare un mesh transform avant le boolean :
    - sort des groupes / unparent en world
    - freeze transforms
    - delete history

    Utilisé pour le mesh de base sélectionné ET pour le cutter dupliqué.
    """
    node = transform_from_node(node) or fp(node)
    if not node or not exists(node):
        return ""

    try:
        parent = mc.listRelatives(node, parent=True, fullPath=True) or []
        if parent:
            node = mc.parent(node, world=True)[0]
            node = fp(node) or node
    except Exception as e:
        mc.warning("Could not unparent {0}: {1}".format(label, e))

    try:
        mc.makeIdentity(node, apply=True, translate=True, rotate=True, scale=True, normal=False)
    except Exception:
        try:
            mc.select(node, r=True)
            mel.eval("FreezeTransformations;")
        except Exception as e:
            mc.warning("Could not freeze transforms on {0}: {1}".format(label, e))

    try:
        mc.delete(node, ch=True)
    except Exception as e:
        mc.warning("Could not delete history on {0}: {1}".format(label, e))

    return transform_from_node(node) or fp(node) or node


def prepare_fresh_cutter_duplicate(node):
    return prepare_clean_mesh_transform(node, "duplicated cutter")


def nudge_transform_for_boolean_update(node, amount=0.00001):
    """
    Force une micro-évaluation du booléen moderne.

    Maya ne recalcule pas toujours le polyBoolean quand on change seulement
    l'état logique, le flip du rig ou les normales. On fait donc une micro
    translation puis retour immédiat, invisible visuellement, pour salir la
    matrice du cutter et forcer l'update.
    """
    node = transform_from_node(node) or fp(node)
    if not node or not exists(node):
        return ""

    try:
        tx = mc.getAttr(node + ".translateX")
        mc.setAttr(node + ".translateX", tx + amount)
        mc.setAttr(node + ".translateX", tx)
        mc.dgdirty(node)

        for shape in mesh_shapes(node):
            if shape and exists(shape):
                mc.dgdirty(shape)

        if last_boolean_node and mc.objExists(last_boolean_node):
            mc.dgdirty(last_boolean_node)

        mc.refresh(f=True)
    except Exception as exc:
        dbg(exc)

    return node


def reverse_mesh_normals(node, delete_history=False):
    """
    Reverse les normales du mesh.

    Important :
    - Pour le pré-flip initial, on peut delete history après.
    - Pendant un booléen live, on évite de delete history sur le cutter connecté au polyBoolean,
      pour ne pas risquer de casser les connexions du booléen moderne.
    """
    node = transform_from_node(node) or fp(node)
    if not node:
        return ""

    try:
        mc.polyNormal(node, normalMode=0, userNormalMode=0, ch=False)
        if delete_history:
            mc.delete(node, ch=True)
    except Exception:
        try:
            mc.select(node, r=True)
            mel.eval("ReversePolygonNormals;")
            if delete_history:
                mel.eval("DeleteHistory;")
        except Exception as e:
            mc.warning("Could not reverse normals: {0}".format(e))

    return transform_from_node(node) or fp(node) or node


def reverse_initial_cutter_normals_once(cutter):
    """
    Reverse les normales du cutter une seule fois, juste avant la création
    du tout premier booléen.

    Important :
    - Ne se fait pas au début du drag.
    - Ne se fait pas à chaque update.
    - Ne se fait pas sur un cutter déjà connecté à un polyBoolean.
    - Comme le cutter n'est pas encore connecté au booléen, on peut delete history.
    """
    cutter = transform_from_node(cutter) or fp(cutter)
    if not cutter or not exists(cutter):
        return ""

    if get_bool_attr(cutter, INITIAL_NORMAL_REVERSE_ATTR, False):
        return cutter

    reverse_mesh_normals(cutter, delete_history=True)
    set_bool_attr(cutter, INITIAL_NORMAL_REVERSE_ATTR, True)

    return transform_from_node(cutter) or fp(cutter) or cutter

def preflip_cutter_geometry_y(node):
    """
    Retourne physiquement le cutter dupliqué en Y avant le drag.

    But :
    - Le plug pénètre dans la surface dès le premier placement.
    - L'état booléen initial reste 0 = subtract.
    - Le flip interactif Shift+Ctrl reste disponible ensuite pour passer en union.

    Important :
    On garde le scaleY=-1 + freeze, mais on reverse ensuite les normales.
    Sans reverse normals, le mirror physique peut laisser le volume avec un winding / des normales
    inversés, ce qui perturbe le booléen moderne.

    On tag aussi le cutter avec PREFLIP_Y_ATTR, parce qu'après ce pré-flip physique,
    le bon point de contact pour le snapping n'est plus minY mais maxY.
    """
    node = transform_from_node(node) or fp(node)
    if not node:
        return ""

    try:
        mc.setAttr(node + ".scaleY", -1)
        mc.makeIdentity(node, apply=True, translate=False, rotate=False, scale=True, normal=False)
        mc.delete(node, ch=True)
    except Exception:
        try:
            mc.select(node, r=True)
            mc.setAttr(node + ".scaleY", -1)
            mel.eval("FreezeTransformations;")
            mel.eval("DeleteHistory;")
        except Exception as e:
            mc.warning("Could not pre-flip duplicated cutter on Y: {0}".format(e))

    node = transform_from_node(node) or fp(node) or node

    # IMPORTANT : après un mirror par scale négatif, on reverse les normales.
    # C'est ce qui évite que le booléen moderne interprète mal le volume du cutter.
    reverse_mesh_normals(node, delete_history=True)

    set_bool_attr(node, PREFLIP_Y_ATTR, True)
    return node


# ============================================================
# VISIBILITY / SURFACE FILTERING
# ============================================================

def mesh_is_valid_surface(shape):
    shape = fp(shape)
    if not shape or mc.nodeType(shape) != "mesh":
        return False

    try:
        if mc.getAttr(shape + ".intermediateObject") or not mc.getAttr(shape + ".visibility"):
            return False
    except Exception:
        return False

    for node in mc.listRelatives(shape, p=True, f=True) or []:
        try:
            if not mc.getAttr(node + ".visibility"):
                return False
            if mc.getAttr(node + ".overrideEnabled") and mc.getAttr(node + ".overrideDisplayType") != 0:
                return False
        except Exception:
            pass

    try:
        if mc.getAttr(shape + ".overrideEnabled") and mc.getAttr(shape + ".overrideDisplayType") != 0:
            return False
    except Exception:
        pass

    return True


def visible_meshes():
    view = omui.M3dView.active3dView()
    saved = om1.MSelectionList()
    om1.MGlobal.getActiveSelectionList(saved)

    try:
        om1.MGlobal.selectFromScreen(0, 0, view.portWidth(), view.portHeight(), om1.MGlobal.kReplaceList)
        picked = om1.MSelectionList()
        om1.MGlobal.getActiveSelectionList(picked)
    except Exception:
        picked = om1.MSelectionList()
    finally:
        try:
            om1.MGlobal.setActiveSelectionList(saved, om1.MGlobal.kReplaceList)
        except Exception:
            pass

    names = []
    picked.getSelectionStrings(names)

    if not names:
        return []

    shapes = mc.listRelatives(names, s=True, ni=True, f=True, type="mesh") or []
    shapes += mc.listRelatives(names, s=True, ad=True, ni=True, f=True, type="mesh") or []

    visible = set(fp(s) for s in shapes if mesh_is_valid_surface(s))
    return [s for s in mc.ls(type="mesh", l=True) or [] if s in visible]


def set_visible(obj):
    obj = fp(obj)
    if not obj:
        return

    for n in [obj] + (mc.listRelatives(obj, ad=True, fullPath=True) or []):
        if mc.objExists(n + ".visibility"):
            safe(lambda n=n: mc.setAttr(n + ".visibility", 1))

        if mc.objExists(n + ".template"):
            safe(lambda n=n: mc.setAttr(n + ".template", 0))

        # IMPORTANT :
        # Ne jamais forcer intermediateObject à 0 ici.
        # Certains booléens modernes Maya gardent des shapes intermédiaires
        # qui doivent rester invisibles.


def show_live_objects():
    result = boolean_result_mesh or result_from_bool_node(last_boolean_node)

    for obj in [boolean_target_mesh, result, boolean_cutter_mesh]:
        if obj:
            set_visible(obj)


# ============================================================
# BOOLEAN HELPERS
# ============================================================

def cutter_group_session_token():
    return uuid.uuid4().hex[:8]


def tag_owned_cutter_group(group, session_token):
    group = fp(group)
    if not group:
        return ""

    set_bool_attr(group, CUTTER_GROUP_OWNED_ATTR, True)
    set_str(group, CUTTER_GROUP_SESSION_ATTR, session_token or "")
    return group


def is_owned_cutter_group(group):
    group = fp(group)
    return bool(group and mc.objExists(group + "." + CUTTER_GROUP_OWNED_ATTR) and get_bool_attr(group, CUTTER_GROUP_OWNED_ATTR, False))


def is_cutter_group_node(group):
    group = fp(group)
    if not group:
        return False

    short = sn(group)
    return bool(is_owned_cutter_group(group) or short == CUTTER_GROUP or short.startswith(CUTTER_GROUP_PREFIX))


def begin_new_cutter_group_session():
    global active_cutter_group, active_cutter_group_session

    active_cutter_group = ""
    active_cutter_group_session = cutter_group_session_token()
    return active_cutter_group_session


def ensure_group():
    global active_cutter_group, active_cutter_group_session

    group = fp(active_cutter_group)
    if group and exists(group) and is_owned_cutter_group(group):
        return group

    if not active_cutter_group_session:
        active_cutter_group_session = cutter_group_session_token()

    # On crée volontairement un groupe neuf et taggé au lieu de réutiliser
    # _boolean_cutters. Après des undo/redo Maya, un ancien transform vide peut rester
    # dans l'outliner ; le réutiliser rend le contexte ambigu. Le nom unique protège
    # aussi les éventuels groupes utilisateur qui auraient le même ancien nom.
    group = mc.group(empty=True, name=CUTTER_GROUP_PREFIX + "#")
    active_cutter_group = tag_owned_cutter_group(group, active_cutter_group_session)
    return active_cutter_group


def sync_cutter(node=None):
    global boolean_cutter_mesh

    c = transform_from_node(node or boolean_cutter_mesh)
    if not c:
        return ""

    boolean_cutter_mesh = c
    return c


def is_current_active_cutter(cutter):
    cutter = transform_from_node(cutter)
    active = transform_from_node(boolean_cutter_mesh)
    return bool(cutter and active and cutter == active and exists(cutter))


def deferred_select_active_cutter(cutter, remaining_passes=0):
    """Sélection différée protégée contre les anciens callbacks Maya.

    Maya peut encore exécuter une sélection différée créée pour le cutter précédent
    après qu'un nouveau plug a été ajouté. Dans ce cas, on ignore l'ancien cutter au
    lieu de voler la sélection du cutter actif courant.

    On peut aussi répéter quelques passes différées : certaines commandes natives du
    Bool Tool remettent leur ancien input en sélection avec un léger retard, surtout
    juste après addMesh sur un booléen déjà existant. La dernière passe réaffirme le
    cutter actif courant sans jamais sélectionner un cutter devenu obsolète.
    """
    cutter = transform_from_node(cutter)
    if is_current_active_cutter(cutter):
        safe(lambda: mc.select(cutter, r=True))

        if remaining_passes > 0:
            safe(lambda cutter=cutter, remaining_passes=remaining_passes: mc.evalDeferred(
                lambda cutter=cutter, remaining_passes=remaining_passes: deferred_select_active_cutter(
                    cutter,
                    remaining_passes - 1
                ),
                lowestPriority=True
            ))


def select_active_cutter(node=None, deferred=True, deferred_passes=3):
    """
    Sélectionne explicitement le cutter actif.

    Important pour le MMB : certaines commandes du booléen moderne / callbacks Maya
    resélectionnent l'ancien input après l'ajout au polyBoolean. On force donc la sélection
    du nouveau cutter immédiatement, puis une seconde fois en evalDeferred.

    La sélection différée est volontairement gardée : elle ne sélectionne le cutter capturé
    que s'il est toujours le cutter actif. Elle est répétée quelques fois parce que le
    Bool Tool natif peut parfois resélectionner son ancien input après notre première
    evalDeferred, notamment après un Q qui ajoute un nouveau plug au même booléen.
    """
    cutter = sync_cutter(node or boolean_cutter_mesh)
    if not cutter or not exists(cutter):
        return ""

    safe(lambda: mc.select(cutter, r=True))

    if deferred:
        safe(lambda cutter=cutter, deferred_passes=deferred_passes: mc.evalDeferred(
            lambda cutter=cutter, deferred_passes=deferred_passes: deferred_select_active_cutter(
                cutter,
                max(0, int(deferred_passes) - 1)
            ),
            lowestPriority=True
        ))

    return cutter


def find_transform_by_string_attr(attr_name, attr_value):
    if not attr_name or attr_value is None:
        return ""

    for node in mc.ls(type="transform", l=True) or []:
        plug = node + "." + attr_name
        if not mc.objExists(plug):
            continue

        value = safe(lambda plug=plug: mc.getAttr(plug), "")
        if value == attr_value:
            return fp(node)

    return ""


def group_cutter(cutter):
    cutter = sync_cutter(cutter)
    if not cutter:
        return ""

    try:
        grp = ensure_group()
        parent = mc.listRelatives(cutter, parent=True, fullPath=True) or []

        if not parent or fp(parent[0]) != grp:
            cutter = mc.parent(cutter, grp)[0]
            cutter = fp(cutter) or cutter
    except Exception:
        pass

    return sync_cutter(cutter)


def store_live_result_metadata():
    result = transform_from_node(boolean_result_mesh or result_from_bool_node(last_boolean_node))
    if not result:
        return

    if last_boolean_node and mc.objExists(last_boolean_node):
        set_str(result, LIVE_BOOL_NODE_ATTR, last_boolean_node)

    set_str(result, LIVE_BOOL_RESULT_ATTR, result)

    if boolean_target_mesh:
        set_str(result, LIVE_BOOL_TARGET_ATTR, boolean_target_mesh)


def store_metadata(cutter):
    cutter = transform_from_node(cutter)
    if not cutter:
        return

    set_str(cutter, "plugBoolNode", last_boolean_node)
    set_str(cutter, "plugBoolResult", boolean_result_mesh)
    set_str(cutter, "plugBoolTarget", boolean_target_mesh)
    set_int(cutter, "plugBoolInputIndex", last_bool_input_index)

    # 0 = subtract / différence
    # 1 = union
    set_int(cutter, "plugBoolFlipState", 1 if boolean_flip_state else 0)
    set_flip(cutter, bool(orientation_flip_enabled))

    store_live_result_metadata()
    set_boolean_cutter_wire_display(cutter)


def load_metadata(cutter):
    global last_boolean_node, boolean_result_mesh, boolean_target_mesh
    global last_bool_input_index, orientation_flip_enabled, boolean_flip_state

    cutter = transform_from_node(cutter)
    if not cutter:
        return

    last_boolean_node = get_str(cutter, "plugBoolNode") or last_boolean_node
    boolean_result_mesh = get_str(cutter, "plugBoolResult") or boolean_result_mesh
    boolean_target_mesh = get_str(cutter, "plugBoolTarget") or boolean_target_mesh
    last_bool_input_index = get_int(cutter, "plugBoolInputIndex", last_bool_input_index)

    boolean_flip_state = get_int(cutter, "plugBoolFlipState", 1 if get_flip(cutter) else 0)
    boolean_flip_state = 1 if boolean_flip_state else 0

    orientation_flip_enabled = bool(boolean_flip_state)
    set_flip(cutter, orientation_flip_enabled)
    set_boolean_cutter_wire_display(cutter)


def reset_state(keep_cutter=False, keep_target=False, keep_boolean=False):
    global boolean_target_mesh, boolean_result_mesh, last_boolean_node
    global last_bool_input_index, boolean_cutter_mesh
    global boolean_flip_state, orientation_flip_enabled

    if not keep_target:
        boolean_target_mesh = ""

    if not keep_boolean:
        boolean_result_mesh = ""
        last_boolean_node = ""
        last_bool_input_index = 1

    if not keep_cutter:
        boolean_cutter_mesh = ""
        boolean_flip_state = 0
        orientation_flip_enabled = DEFAULT_BOOLEAN_FLIP


def current_op():
    # 0 = subtract
    # 1 = union
    return BOOL_UNION if bool(boolean_flip_state) else BOOL_SUBTRACT


def current_offset_sign():
    # Le duplicata est déjà physiquement pré-flippé en Y.
    # Par défaut, on garde un léger offset dans le sens de la normale.
    # Quand l'utilisateur flippe en union, on inverse l'offset pour rester cohérent.
    return -1.0 if bool(boolean_flip_state) else 1.0


def bool_nodes(result):
    result = transform_from_node(result)
    if not result:
        return []

    return [
        n for n in (mc.listHistory(result, pruneDagObjects=True) or [])
        if mc.objExists(n) and mc.nodeType(n) == "polyBoolean"
    ]


def result_from_bool_node(node):
    if not node or not mc.objExists(node):
        return ""

    for c in mc.listConnections(node, source=False, destination=True, shapes=True) or []:
        if mc.objExists(c) and mc.nodeType(c) == "mesh":
            p = mc.listRelatives(c, parent=True, fullPath=True) or []
            if p and "_boolCutter" not in p[0]:
                return fp(p[0])

    return ""


def input_index_from_plug(plug):
    return safe(lambda: int(plug.split("[")[-1].split("]")[0]), None)


def cutter_input_indices(node, cutter):
    node = node if node and mc.objExists(node) else ""
    cutter = transform_from_node(cutter)

    if not node or not cutter:
        return []

    found = []

    for shape in mesh_shapes_under(cutter):
        cons = mc.listConnections(shape, s=False, d=True, plugs=True, connections=True) or []

        for i in range(0, len(cons), 2):
            dst = cons[i + 1] if i + 1 < len(cons) else ""

            if dst.startswith(node + "."):
                idx = input_index_from_plug(dst)

                if idx is not None and idx not in found:
                    found.append(idx)

    return found


def cutter_is_really_connected(bool_node, cutter):
    return bool(cutter_input_indices(bool_node, cutter))


def bool_nodes_from_cutter(cutter):
    cutter = transform_from_node(cutter)
    if not cutter:
        return []

    nodes = []

    stored = get_str(cutter, "plugBoolNode")
    if stored and mc.objExists(stored):
        nodes.append(stored)

    for shape in mesh_shapes_under(cutter):
        for n in mc.listConnections(shape, source=True, destination=True) or []:
            if mc.objExists(n) and mc.nodeType(n) == "polyBoolean" and n not in nodes:
                nodes.append(n)

    return nodes


def find_live_bool_nodes_for_cutter(cutter):
    cutter = transform_from_node(cutter)
    if not cutter:
        return []

    nodes = []

    stored = get_str(cutter, "plugBoolNode")
    if stored and mc.objExists(stored):
        nodes.append(stored)

    if last_boolean_node and mc.objExists(last_boolean_node) and last_boolean_node not in nodes:
        nodes.append(last_boolean_node)

    for shape in mesh_shapes_under(cutter):
        for n in mc.listConnections(shape, source=True, destination=True, plugs=False) or []:
            if mc.objExists(n) and mc.nodeType(n) == "polyBoolean" and n not in nodes:
                nodes.append(n)

    for n in mc.listHistory(cutter, pruneDagObjects=True) or []:
        if mc.objExists(n) and mc.nodeType(n) == "polyBoolean" and n not in nodes:
            nodes.append(n)

    return nodes


def transforms_connected_to_bool_node(node):
    """Retourne les transforms mesh branchés en entrée du polyBoolean.

    Les booléens modernes Maya gardent les cutters comme meshes d'entrée du node.
    Cette fonction sert uniquement à retrouver les vrais transforms encore connectés,
    sans se fier à la sélection Maya qui peut être stale après un restart du script.
    """
    node = node if node and mc.objExists(node) else ""
    if not node:
        return []

    result = transform_from_node(boolean_result_mesh or result_from_bool_node(node))
    found = []

    for src in mc.listConnections(node, source=True, destination=False, shapes=True) or []:
        xform = transform_from_node(src)
        if not xform or xform == result or xform in found:
            continue

        if is_mesh_xform(xform):
            found.append(xform)

    return found


def is_boolean_cutter_candidate(node, bool_node=""):
    node = transform_from_node(node)
    if not node:
        return False

    if "_boolCutter" in sn(node):
        return True

    parent = parent_path(node)
    if parent and is_cutter_group_node(parent):
        return True

    stored = get_str(node, "plugBoolNode")
    if stored and (not bool_node or stored == bool_node):
        return True

    if bool_node and bool_node in bool_nodes_from_cutter(node):
        return True

    return False


def cutters_for_bool_node(node, include_active=True):
    """Liste tous les cutters live d'un polyBoolean, pas seulement le dernier actif.

    C'est important au bake : avec les duplications MMB, plusieurs cutters peuvent
    rester branchés au même polyBoolean. Si on supprime uniquement boolean_cutter_mesh,
    Maya laisse les anciens cutters dans la scène après V.
    """
    node = node if node and mc.objExists(node) else ""
    if not node:
        return []

    cutters = []

    for candidate in transforms_connected_to_bool_node(node):
        if is_boolean_cutter_candidate(candidate, node) and candidate not in cutters:
            cutters.append(candidate)

    active = transform_from_node(boolean_cutter_mesh) if include_active else ""
    if active and exists(active) and active not in cutters and is_boolean_cutter_candidate(active, node):
        cutters.append(active)

    return cutters


def resolve_live_boolean_context(cutter=None):
    """Retrouve le polyBoolean live même après relance du script / sélection stale.

    Le hotkey V ne doit pas dépendre uniquement de bool_nodes_from_cutter(cutter).
    Quand Maya resélectionne un ancien input ou quand les globals viennent d'être
    réinitialisés par une relance du fichier, les métadonnées / le result peuvent
    quand même permettre de retrouver le node live à baker.
    """
    global last_boolean_node, boolean_result_mesh, last_bool_input_index

    cutter = transform_from_node(cutter or boolean_cutter_mesh)
    nodes = []

    for n in find_live_bool_nodes_for_cutter(cutter):
        if n not in nodes:
            nodes.append(n)

    stored_result = get_str(cutter, "plugBoolResult") if cutter else ""
    for result in [boolean_result_mesh, stored_result]:
        result = transform_from_node(result)
        for n in bool_nodes(result) if result else []:
            if n not in nodes:
                nodes.append(n)

    if last_boolean_node and mc.objExists(last_boolean_node) and last_boolean_node not in nodes:
        nodes.append(last_boolean_node)

    for node in nodes:
        if not node or not mc.objExists(node) or mc.nodeType(node) != "polyBoolean":
            continue

        result = transform_from_node(boolean_result_mesh or result_from_bool_node(node))
        if result:
            boolean_result_mesh = result

        last_boolean_node = node

        if cutter:
            indices = cutter_input_indices(node, cutter)
            if indices:
                last_bool_input_index = indices[0]
                store_metadata(cutter)

        return node

    return ""


def tune_bool_node(node):
    if not node or not mc.objExists(node):
        return

    for attr, value in [
        ("interactiveUpdate", 1),
        ("maya2025", 1),
        ("newInputOperation", current_op())
    ]:
        if mc.objExists(node + "." + attr):
            safe(lambda attr=attr, value=value: mc.setAttr(node + "." + attr, value))


def operation_values(node):
    raw = safe(lambda: mc.getAttr(node + ".operation"), None) if node and mc.objExists(node + ".operation") else None

    if isinstance(raw, (list, tuple)):
        if raw and isinstance(raw[0], (list, tuple)):
            return list(raw[0])
        return list(raw)

    return []


def set_operation_values(node, values):
    if not node or not mc.objExists(node + ".operation"):
        return False

    try:
        mc.setAttr(node + ".operation", values, type="Int32Array")
        return True
    except Exception:
        try:
            mc.setAttr(node + ".operation", len(values), *values, type="Int32Array")
            return True
        except Exception:
            return False


def repair_bool_operation_array(node):
    node = node if node and mc.objExists(node) else ""

    if not node or not mc.objExists(node + ".operation"):
        return False

    values = operation_values(node)

    if not values:
        values = [BOOL_UNION]

    # input[0] is always the base/result mesh. Every cutter lives on input[1+]
    # and gets its own operation through set_bool_operation_for_cutter().
    values[0] = BOOL_UNION
    return set_operation_values(node, values)


def set_bool_operation_for_cutter(node, cutter, op):
    global last_bool_input_index

    node = node if node and mc.objExists(node) else ""
    cutter = transform_from_node(cutter)

    if not node or not cutter:
        return False

    indices = cutter_input_indices(node, cutter) or [get_int(cutter, "plugBoolInputIndex", last_bool_input_index)]
    indices = [i for i in indices if i is not None and i >= 0]

    if not indices:
        return False

    values = operation_values(node)

    if not values:
        values = [BOOL_UNION]

    needed = max(indices + [0])
    while len(values) <= needed:
        values.append(BOOL_SUBTRACT)

    values[0] = BOOL_UNION

    for idx in indices:
        if idx == 0:
            values[idx] = BOOL_UNION
        else:
            values[idx] = int(op)
            last_bool_input_index = idx

    ok = set_operation_values(node, values)

    if mc.objExists(node + ".newInputOperation"):
        try:
            mc.setAttr(node + ".newInputOperation", int(op))
            ok = True
        except Exception:
            pass

    safe(lambda: mc.dgdirty(node))
    safe(lambda: mc.refresh(f=True))
    return ok


def update_current_boolean_operation():
    global last_boolean_node

    cutter = sync_cutter()
    if not cutter:
        return False

    nodes = [n for n in bool_nodes_from_cutter(cutter) if cutter_is_really_connected(n, cutter)]
    if not nodes:
        node = resolve_live_boolean_context(cutter)
        nodes = [node] if node else []

    if not nodes:
        return False

    changed = False

    for n in nodes:
        tune_bool_node(n)
        repair_bool_operation_array(n)

        if set_bool_operation_for_cutter(n, cutter, current_op()):
            last_boolean_node = n
            changed = True

    store_metadata(cutter)
    show_live_objects()
    safe(lambda: mc.select(cutter, r=True))
    return changed


def force_boolean_operation_now(cutter=None):
    global last_boolean_node

    cutter = sync_cutter(cutter or boolean_cutter_mesh)
    if not cutter:
        return False

    nodes = find_live_bool_nodes_for_cutter(cutter)
    if not nodes:
        node = resolve_live_boolean_context(cutter)
        nodes = [node] if node else []

    if not nodes:
        store_metadata(cutter)
        return False

    changed = False

    for node in nodes:
        repair_bool_operation_array(node)

        if set_bool_operation_for_cutter(node, cutter, current_op()):
            last_boolean_node = node
            changed = True

    store_metadata(cutter)
    show_live_objects()
    safe(lambda: mc.refresh(f=True))
    return changed


def run_modern_boolean_command(op):
    """
    Lance le booléen moderne Maya 2024+ / 2027 avec le même chemin que l'UI.
    op : BOOL_SUBTRACT ou BOOL_UNION.
    """
    if op == BOOL_SUBTRACT:
        mel.eval("PolygonBooleanDifference;")
    elif op == BOOL_UNION:
        mel.eval("PolygonBooleanUnion;")
    else:
        btUtils.createBoolTool(op)


def add_cutter_to_live_boolean(cutter, op=None, select_after=True):
    """
    Ajoute un cutter au polyBoolean live existant sans créer une chaîne de nouveaux booléens.

    Important :
    - On garde last_boolean_node / boolean_result_mesh entre les duplications MMB.
    - On édite directement le node polyBoolean existant avec polyBooleanCmd(edit=True, addMesh=...).
    - On ne sélectionne pas result + cutter pour relancer PolygonBooleanDifference/Union,
      sinon Maya crée un nouveau polyBoolean en cascade.
    """
    global boolean_cutter_mesh, boolean_result_mesh, last_boolean_node, last_bool_input_index
    global boolean_flip_state, orientation_flip_enabled

    cutter = sync_cutter(cutter)
    if not cutter:
        return None

    op = current_op() if op is None else int(op)

    boolean_flip_state = 1 if op == BOOL_UNION else 0
    orientation_flip_enabled = bool(boolean_flip_state)
    set_int(cutter, "plugBoolFlipState", boolean_flip_state)
    set_flip(cutter, orientation_flip_enabled)

    node = last_boolean_node if last_boolean_node and mc.objExists(last_boolean_node) else ""

    if not node:
        result = transform_from_node(boolean_result_mesh or result_from_bool_node(last_boolean_node))
        nodes = bool_nodes(result) if result else []
        node = nodes[0] if nodes else ""

    if node and mc.objExists(node):
        try:
            tune_bool_node(node)
            repair_bool_operation_array(node)

            # IMPORTANT : ne jamais ajouter deux fois le même cutter au même polyBoolean.
            # L'AE du booléen affiche sinon des doublons, et Maya garde parfois des entrées
            # courtes/stale qui provoquent : "No object matches name".
            existing_indices = cutter_input_indices(node, cutter)
            if existing_indices:
                last_bool_input_index = existing_indices[0]
                set_bool_operation_for_cutter(node, cutter, op)
                store_metadata(cutter)
                show_live_objects()
                nudge_transform_for_boolean_update(cutter, amount=0.00001)
                if select_after:
                    select_active_cutter(cutter, deferred=True)
                return boolean_result_mesh or result_from_bool_node(node)

            # Un cutter ajouté plus tard doit recevoir le même reverse normals initial
            # que le premier cutter, avec un attribut qui évite le double reverse.
            cutter = reverse_initial_cutter_normals_once(cutter)
            cutter = sync_cutter(cutter)
            if not cutter:
                mc.warning("Could not prepare cutter normals before addMesh.")
                return None

            set_boolean_cutter_wire_display(cutter)

            # Ajout direct du cutter comme input du même polyBoolean.
            mc.polyBooleanCmd(node, edit=True, addMesh=cutter, operation=op)

            last_boolean_node = node
            repair_bool_operation_array(node)
            result = transform_from_node(boolean_result_mesh or result_from_bool_node(node))
            if result:
                boolean_result_mesh = result

            indices = cutter_input_indices(node, cutter)
            if indices:
                last_bool_input_index = indices[0]
                set_bool_operation_for_cutter(node, cutter, op)
            else:
                if mc.objExists(node + ".newInputOperation"):
                    safe(lambda: mc.setAttr(node + ".newInputOperation", int(op)))

            store_metadata(cutter)
            store_live_result_metadata()
            show_live_objects()
            nudge_transform_for_boolean_update(cutter, amount=0.00001)
            if select_after:
                select_active_cutter(cutter, deferred=True)
            return boolean_result_mesh or result_from_bool_node(node)

        except Exception as e:
            mc.warning("Could not add cutter to existing polyBoolean node: {0}".format(e))
            return None

    # Aucun polyBoolean live connu : on crée le premier booléen normalement.
    # Important pour le premier MMB : si on est en train de finaliser l'ancien cutter,
    # il ne doit pas reprendre la sélection après la création initiale du booléen.
    return create_boolean(restart=False, select_after=select_after)


def _find_live_bool_node_in_scene():
    candidates = []

    for node in [last_boolean_node]:
        if node and mc.objExists(node) and node not in candidates:
            candidates.append(node)

    for result in [boolean_result_mesh, boolean_target_mesh]:
        result = transform_from_node(result)
        for node in bool_nodes(result) if result else []:
            if node not in candidates:
                candidates.append(node)

    cutter = transform_from_node(boolean_cutter_mesh)
    if cutter and exists(cutter):
        for node in bool_nodes_from_cutter(cutter):
            if node not in candidates:
                candidates.append(node)

    return candidates


def create_boolean(restart=True, select_after=True):
    global boolean_result_mesh, last_boolean_node, last_bool_input_index
    global boolean_cutter_mesh

    cutter = sync_cutter()
    if not cutter:
        mc.warning("No boolean cutter found.")
        return None

    existing_nodes = [n for n in bool_nodes_from_cutter(cutter) if cutter_is_really_connected(n, cutter)]
    if existing_nodes:
        last_boolean_node = existing_nodes[0]
        update_current_boolean_operation()
        boolean_result_mesh = get_str(cutter, "plugBoolResult") or result_from_bool_node(last_boolean_node)
        store_metadata(cutter)

        if restart:
            restart_drag_on_cutter(group_cutter(cutter))

        return boolean_result_mesh or cutter

    live_candidates = _find_live_bool_node_in_scene()
    if live_candidates:
        live_node = live_candidates[0]
        last_boolean_node = live_node
        live_result = transform_from_node(result_from_bool_node(live_node))
        if live_result and exists(live_result):
            boolean_result_mesh = live_result

        result = add_cutter_to_live_boolean(cutter, current_op(), select_after=not restart)
        if result:
            if restart:
                restart_drag_on_cutter(group_cutter(cutter))
            return result

        mc.warning("Live polyBoolean found, but addMesh failed. New boolean creation blocked to avoid cascade.")
        return None

    target = transform_from_node(boolean_target_mesh)
    if not target:
        mc.warning("No boolean target detected.")
        return None

    target_bool_nodes = bool_nodes(target)
    if target_bool_nodes:
        last_boolean_node = target_bool_nodes[0]
        boolean_result_mesh = target

        result = add_cutter_to_live_boolean(cutter, current_op(), select_after=not restart)
        if result:
            if restart:
                restart_drag_on_cutter(group_cutter(cutter))
            return result

        mc.warning("Target is already a boolean result, but addMesh failed. New boolean creation blocked to avoid cascade.")
        return None

    if target == cutter:
        mc.warning("Target and cutter are the same object.")
        return None

    if btUtils is None:
        mc.warning("Could not import booltoolUtils.")
        return None

    safe(lambda: mc.loadPlugin("polyBoolean") if not mc.pluginInfo("polyBoolean", q=True, loaded=True) else None)

    # IMPORTANT :
    # Premier vrai booléen seulement.
    # Les plugs étant ouverts, on reverse les normales du cutter une seule fois
    # juste avant de créer le polyBoolean.
    # On ne fait pas de scale -1 ici.
    cutter = reverse_initial_cutter_normals_once(cutter)
    cutter = sync_cutter(cutter)

    if not cutter:
        mc.warning("Could not prepare cutter normals before boolean.")
        return None

    try:
        # Même ordre que l'utilisation manuelle Maya :
        # A = target / mesh cible
        # B = cutter
        # Difference = A - B
        mc.select(target, cutter, r=True)

        # On utilise les runtime commands Maya pour coller au comportement UI Maya 2024+ / 2027.
        # Dans le Script Editor, Maya loggue :
        # PolygonBooleanDifference;
        # polyPerformBooleanAction 2 o 0;
        # import maya.plugin.polyBoolean.booltoolUtils as btUtils; btUtils.createBoolTool(2);
        if current_op() == BOOL_SUBTRACT:
            mel.eval("PolygonBooleanDifference;")
        elif current_op() == BOOL_UNION:
            mel.eval("PolygonBooleanUnion;")
        else:
            btUtils.createBoolTool(current_op())

    except Exception as e:
        mc.warning("Modern Bool Tool failed: {0}".format(e))
        return None

    sel = mc.ls(sl=True, l=True) or []
    if not sel:
        mc.warning("Boolean failed.")
        return None

    result = sel[0]

    if mc.objExists(result) and mc.nodeType(result) != "transform":
        p = mc.listRelatives(result, parent=True, fullPath=True) or []
        result = p[0] if p else result

    boolean_result_mesh = transform_from_node(result) or fp(result) or result

    nodes = bool_nodes(boolean_result_mesh)
    if nodes:
        last_boolean_node = nodes[0]
        tune_bool_node(last_boolean_node)
        repair_bool_operation_array(last_boolean_node)

        indices = cutter_input_indices(last_boolean_node, cutter)
        last_bool_input_index = indices[0] if indices else 1
        set_bool_operation_for_cutter(last_boolean_node, cutter, current_op())
        store_metadata(cutter)
        store_live_result_metadata()

    show_live_objects()

    if restart:
        restart_drag_on_cutter(group_cutter(cutter))
    else:
        if select_after:
            select_active_cutter(cutter, deferred=True)

    return boolean_result_mesh






def bake_boolean_result():
    global drag_session_active

    live_node = resolve_live_boolean_context(boolean_cutter_mesh)
    result = transform_from_node(boolean_result_mesh or result_from_bool_node(live_node or last_boolean_node))
    if not result:
        mc.warning("Could not find boolean result. Bake cancelled.")
        return False

    cutters = cutters_for_bool_node(live_node, include_active=True) if live_node else []
    active_cutter = transform_from_node(boolean_cutter_mesh)
    if (
        active_cutter
        and exists(active_cutter)
        and active_cutter not in cutters
        and is_boolean_cutter_candidate(active_cutter, live_node)
    ):
        cutters.append(active_cutter)

    try:
        mc.select(result, r=True)
        mel.eval("DeleteHistory;")
        mel.eval("FreezeTransformations;")
        mel.eval("CenterPivot;")
    except Exception as e:
        mc.warning("Bake boolean failed: {0}".format(e))
        return False

    # Après le bake, tous les cutters branchés au polyBoolean doivent disparaître.
    # Sinon une validation V après plusieurs MMB ne supprimait que le dernier cutter actif.
    safe_delete(sorted(set(cutters), key=lambda n: n.count("|"), reverse=True))

    cleanup_empty_groups()

    if mc.objExists(result):
        mc.select(result, r=True)

    drag_session_active = False
    return True


# ============================================================
# DUPLICATE / SOURCE PREP
# ============================================================

def cutter_source_base_name(node):
    """
    Nettoie un nom de cutter pour éviter les noms imbriqués :
    pPlane18_boolCutter1_boolCutter2 -> pPlane18
    """
    name = sn(node)

    while "_boolCutter" in name:
        name = name.split("_boolCutter", 1)[0]

    return name or sn(node) or "plug"


def is_existing_boolean_cutter(node):
    node = transform_from_node(node)
    if not node:
        return False

    stored = get_str(node, "plugBoolNode")
    if stored and mc.objExists(stored):
        return True

    return bool(bool_nodes_from_cutter(node))


def pick_valid_plug_from_selection(selection, prefer_existing_cutter=True):
    """
    Retourne le bon mesh transform utilisable dans une sélection Maya.

    Important : l'Attribute Editor du polyBoolean peut sélectionner automatiquement :
    - le node polyBoolean lui-même
    - le mesh résultat
    - plusieurs cutters, souvent dans l'ordre ancien -> récent

    Pour continuer un drag live, on préfère donc le dernier cutter connecté sélectionné.
    Ça évite de repartir de pPlane18_boolCutter1 quand le cutter actif est pPlane18_boolCutter3.
    """
    valid_meshes = []
    existing_cutters = []

    for item in selection or []:
        if is_comp(item):
            continue

        node = fp(item)
        if not node or not exists(node):
            continue

        try:
            if mc.nodeType(node) == "polyBoolean":
                continue
        except Exception:
            continue

        xform = transform_from_node(node)
        if xform and is_mesh_xform(xform):
            valid_meshes.append(xform)

            if is_existing_boolean_cutter(xform):
                existing_cutters.append(xform)

    if prefer_existing_cutter and existing_cutters:
        return existing_cutters[-1]

    return valid_meshes[0] if valid_meshes else ""

def duplicate_selected_plug(selection):
    global boolean_cutter_mesh, reuse_selected_cutter_once, current_cutter_reused
    global orientation_flip_enabled, boolean_flip_state

    current_cutter_reused = False

    valid_plug = pick_valid_plug_from_selection(selection)

    if valid_plug and is_existing_boolean_cutter(valid_plug) and not reuse_selected_cutter_once:
        cutter = sync_cutter(valid_plug)
        if cutter:
            current_cutter_reused = True
            load_metadata(cutter)
            return [cutter]

    if selection and reuse_selected_cutter_once:
        reuse_selected_cutter_once = False

        cutter = sync_cutter(valid_plug)
        if cutter:
            current_cutter_reused = True
            load_metadata(cutter)
            return [cutter]

        return []

    if valid_plug and drag_session_active and boolean_cutter_mesh and transform_from_node(valid_plug) == transform_from_node(boolean_cutter_mesh):
        cutter = sync_cutter(valid_plug)
        if cutter:
            current_cutter_reused = True
            load_metadata(cutter)
            return [cutter]

        return []

    if not selection:
        mc.warning("Select one plug mesh.")
        return []

    src = valid_plug

    if not src:
        mc.warning("Select the plug transform, not a component.")
        return []

    # IMPORTANT :
    # Le mesh de base sélectionné est sorti des groupes / unparent world,
    # freeze transforms, delete history avant duplication.
    src = prepare_clean_mesh_transform(src, "base mesh")
    if not src or not is_mesh_xform(src):
        mc.warning("Could not prepare base mesh before duplication.")
        return []

    dup = mc.duplicate(src, rr=True, name=cutter_source_base_name(src) + "_boolCutter#") or []
    if not dup:
        return []

    # IMPORTANT :
    # Le cutter dupliqué est lui aussi sorti des groupes / unparent world,
    # freeze transforms, delete history avant le drag.
    clean_dup = prepare_fresh_cutter_duplicate(dup[0])
    if not clean_dup:
        mc.warning("Could not prepare duplicated cutter.")
        return []

    # IMPORTANT :
    # On ne pré-flip plus physiquement le cutter en Y ici.
    # Avant, le script faisait :
    # clean_dup = preflip_cutter_geometry_y(clean_dup)
    #
    # Maintenant le cutter garde son orientation d'origine.
    # On force aussi PREFLIP_Y_ATTR à False pour que bottom_pivot()
    # utilise le contact normal minY, et non maxY.
    set_bool_attr(clean_dup, PREFLIP_Y_ATTR, False)

    boolean_cutter_mesh = sync_cutter(clean_dup)

    orientation_flip_enabled = DEFAULT_BOOLEAN_FLIP
    boolean_flip_state = 0

    set_flip(boolean_cutter_mesh, orientation_flip_enabled)
    set_int(boolean_cutter_mesh, "plugBoolFlipState", boolean_flip_state)

    mc.select(boolean_cutter_mesh, r=True)
    return [boolean_cutter_mesh]



# ============================================================
# DRAG TOOL - STABLE CLICK2D LOGIC ADAPTED TO BOOLEAN
# ============================================================

class PlugBoolDragTool(object):
    def __init__(self):
        self.reset_all()

    def reset_all(self):
        self.mesh = ""
        self.mesh_name = ""
        self.old_parent = ""
        self.hit_face = ""

        self.mode = "move"
        self.mode_start = [0, 0]

        self.start_scale = [1, 1, 1]
        self.start_rot_y = 0.0

        self.saved_twist_y = 0.0
        self.saved_base_rot = [0.0, 0.0, 0.0]

        self.duplicate_done = False

        self.flip_on = DEFAULT_BOOLEAN_FLIP
        self.baked_flip_on = DEFAULT_BOOLEAN_FLIP
        self.flip_key_down = False
        self.flip_lock = False

        self.cam_pos = [0, 0, 0]
        self.cam_far = 10000

        self.cache = []
        self.tmp = []

        self.picker = ""
        self.flip = ""
        self.rot = ""
        self.offset = ""

    def reset_drag(self):
        self.mesh = ""
        self.mesh_name = ""
        self.old_parent = ""
        self.hit_face = ""

        self.mode = "move"
        self.mode_start = [0, 0]

        self.start_scale = [1, 1, 1]
        self.start_rot_y = 0.0

        self.saved_twist_y = 0.0
        self.saved_base_rot = [0.0, 0.0, 0.0]

        self.duplicate_done = False
        self.flip_key_down = False
        self.flip_lock = False
        self.cache = []

    def tag(self, node):
        node = fp(node)
        if not node:
            return ""

        try:
            if not mc.objExists(node + ".plugBoolDragTemp"):
                mc.addAttr(node, ln="plugBoolDragTemp", at="bool", dv=True)

            mc.setAttr(node + ".plugBoolDragTemp", True)
        except Exception:
            pass

        if node not in self.tmp:
            self.tmp.append(node)

        return node

    def group(self, name):
        return self.tag(mc.group(em=True, n=TMP + name + "#"))

    def clear(self):
        nodes = [
            n for n in self.tmp + (mc.ls(TMP + "*", l=True) or [])
            if exists(n + ".plugBoolDragTemp")
        ]

        safe_delete(sorted(set(nodes), key=lambda n: n.count("|"), reverse=True))

        self.tmp = []
        self.picker = ""
        self.flip = ""
        self.rot = ""
        self.offset = ""
        self.cache = []

    def refresh_rig_paths(self):
        self.picker = fp(self.picker)
        self.flip = fp(self.flip)
        self.rot = fp(self.rot)
        self.offset = fp(self.offset)
        self.mesh = transform_from_node(self.mesh)

    def start(self):
        global drag_session_active

        if self.picker or self.offset:
            self.finalize_drag(select_final=False)

        self.clear()
        self.reset_drag()

        drag_session_active = False

        if mc.draggerContext(CTX, exists=True):
            mc.deleteUI(CTX)

        mc.draggerContext(
            CTX,
            pressCommand=press,
            dragCommand=drag,
            releaseCommand=release,
            name=CTX,
            cursor="crossHair",
            undoMode="step"
        )

        mc.setToolTo(CTX)

    def press(self):
        global drag_session_active
        global boolean_cutter_mesh, orientation_flip_enabled, boolean_flip_state

        self.clear()
        self.reset_drag()

        if not drag_session_active:
            reset_state(False)
            begin_new_cutter_group_session()
            orientation_flip_enabled = DEFAULT_BOOLEAN_FLIP
            boolean_flip_state = 0

        x, y, _ = mc.draggerContext(CTX, q=True, anchorPoint=True)
        self.mode_start = [x, y]

        selected = duplicate_selected_plug(mc.ls(sl=True, fl=True, l=True) or [])
        if not selected:
            return

        self.mesh = sync_cutter(selected[0])
        if not self.mesh:
            return

        drag_session_active = True
        boolean_cutter_mesh = self.mesh

        self.update_camera()

        self.old_parent = parent_path(self.mesh)
        self.mesh_name = sn(self.mesh)

        self.baked_flip_on = get_flip(self.mesh)
        self.flip_on = bool(orientation_flip_enabled)

        boolean_flip_state = 1 if self.flip_on else 0
        set_int(self.mesh, "plugBoolFlipState", boolean_flip_state)
        set_flip(self.mesh, self.flip_on)

        self.saved_twist_y = get_float_attr(self.mesh, TWIST_ATTR, 0.0)
        self.saved_base_rot = get_base_rotation(self.mesh, clean_world_rotation(self.mesh))

        excluded = set(mesh_shapes(self.mesh))

        result = boolean_result_mesh or result_from_bool_node(last_boolean_node)
        if result:
            excluded.update(mesh_shapes(result))

        self.cache = self.make_cache(self.filtered_visible(excluded))

        if not self.cache:
            self.reset_drag()
            return

        self.create_rig(self.bottom_pivot(self.mesh), self.saved_base_rot)

        if self.picker:
            self.cache_start_values()
            store_metadata(self.mesh)
            show_live_objects()
        else:
            self.reset_drag()

    def drag(self):
        if not self.mesh or not self.picker or not self.rot or not exists(self.picker) or not exists(self.rot):
            return

        button = mc.draggerContext(CTX, q=True, button=True)

        if button == 2:
            self.duplicate()
            return

        # IMPORTANT : on réarme le MMB dès que l'utilisateur n'est plus en drag MMB.
        # Sinon self.duplicate_done reste True après le premier MMB, et le MMB suivant
        # sert juste à replacer au lieu de dupliquer, ce qui oblige à cliquer deux fois.
        self.duplicate_done = False

        x, y, _ = mc.draggerContext(CTX, q=True, dragPoint=True)

        if self.update_flip():
            mc.refresh(cv=True, f=True)
            return

        mode = self.mode_from_keys()

        if mode != self.mode:
            self.mode = mode
            self.mode_start = [x, y]
            self.cache_start_values()
            self.apply_flip()
            self.duplicate_done = False
            mc.refresh(cv=True, f=True)
            return

        if self.mode == "move":
            self.place(x, y)
        elif self.mode == "rotate":
            self.rotate(x)
        elif self.mode == "scale":
            self.scale(x)

        mc.refresh(cv=True, f=True)

    def release(self):
        global last_mmb_duplicate_cutter

        # Si on vient de faire un MMB, le cutter à garder actif est le dernier duplicata,
        # pas forcément ce que Maya ou le polyBoolean ont remis en sélection.
        preferred = transform_from_node(last_mmb_duplicate_cutter)

        final_mesh = self.finalize_drag(select_final=False, preferred_mesh=preferred)
        self.reset_drag()

        final_mesh = transform_from_node(preferred) or transform_from_node(final_mesh)
        last_mmb_duplicate_cutter = ""

        if final_mesh and exists(final_mesh):
            # Après un MMB, Maya / polyBoolean peut encore avoir une sélection différée
            # qui remet l'ancien cutter. On sélectionne donc le cutter final immédiatement,
            # puis à nouveau en evalDeferred.
            select_active_cutter(final_mesh, deferred=True)

    def finalize_drag(self, select_final=True, preferred_mesh=""):
        global boolean_cutter_mesh, orientation_flip_enabled, boolean_flip_state

        final_mesh = ""

        try:
            self.refresh_rig_paths()
            preferred_mesh = transform_from_node(preferred_mesh)
            rigged = preferred_mesh if preferred_mesh and exists(preferred_mesh) else self.rigged_mesh()

            if exists(rigged):
                current_twist = self.current_twist()
                final_mesh = parent_keep_world(rigged, self.old_parent)

                if final_mesh:
                    self.mesh = final_mesh
                    self.mesh_name = sn(final_mesh)

                    orientation_flip_enabled = bool(self.flip_on)
                    boolean_flip_state = 1 if self.flip_on else 0

                    set_flip(final_mesh, self.flip_on)
                    set_int(final_mesh, "plugBoolFlipState", boolean_flip_state)
                    set_float_attr(final_mesh, TWIST_ATTR, current_twist)
                    set_base_rotation(final_mesh, self.saved_base_rot)

                    boolean_cutter_mesh = sync_cutter(final_mesh)
                    store_metadata(final_mesh)

        finally:
            self.clear()

        if select_final and exists(final_mesh):
            mc.select(final_mesh, r=True)

        return final_mesh or sync_cutter()

    def update_camera(self):
        view = omui.M3dView.active3dView()
        camera = om1.MDagPath()
        view.getCamera(camera)

        transform = mc.listRelatives(camera.fullPathName(), p=True, type="transform") or []

        if transform:
            self.cam_far = mc.getAttr(transform[0] + ".farClipPlane")
            self.cam_pos = mc.xform(transform[0], q=True, ws=True, rp=True)

    def filtered_visible(self, excluded=None):
        excluded = excluded or set()

        target = transform_from_node(boolean_target_mesh)

        if target:
            shapes = mesh_shapes(target)
        else:
            shapes = visible_meshes()

        cutter = transform_from_node(boolean_cutter_mesh)
        result = transform_from_node(boolean_result_mesh or result_from_bool_node(last_boolean_node))

        if cutter:
            excluded.update(mesh_shapes(cutter))

        if result:
            excluded.update(mesh_shapes(result))

        return [
            shape for shape in (fp(s) for s in shapes)
            if shape and shape not in excluded and mesh_is_valid_surface(shape)
        ]

    def make_cache(self, shapes):
        out = []

        for shape in shapes:
            try:
                out.append((shape, mesh_fn(shape), om.MMeshIsectAccelParams()))
            except Exception as exc:
                dbg(exc)

        return out

    def raycast(self, x, y):
        wp = om1.MPoint()
        wd = om1.MVector()
        omui.M3dView.active3dView().viewToWorld(int(x), int(y), wp, wd)

        source = om.MFloatPoint(wp.x, wp.y, wp.z)
        direction = om.MFloatVector(wd.x, wd.y, wd.z)

        best_shape = ""
        best_hit = [0, 0, 0]
        best_face = 0
        best_dist = self.cam_far

        for shape, fn, accel in self.cache:
            if not exists(shape):
                continue

            try:
                hit = fn.closestIntersection(
                    source,
                    direction,
                    om.MSpace.kWorld,
                    self.cam_far,
                    False,
                    accelParams=accel
                )
            except TypeError:
                try:
                    hit = fn.closestIntersection(
                        source,
                        direction,
                        om.MSpace.kWorld,
                        self.cam_far,
                        False
                    )
                except Exception as exc:
                    dbg(exc)
                    continue
            except Exception as exc:
                dbg(exc)
                continue

            if not hit:
                continue

            point = [hit[0].x, hit[0].y, hit[0].z]
            dist = distance(self.cam_pos, point)

            if dist < best_dist:
                best_shape = shape
                best_hit = point
                best_face = int(hit[2]) if hit[2] is not None else 0
                best_dist = dist

        return best_shape, best_hit, best_face


    def bottom_pivot(self, mesh):
        """
        Point de contact utilisé pour le snapping.

        Logique :
        - On cherche les border edges du mesh.
        - On récupère leurs vertices en espace local.
        - On groupe ces points par hauteur Y.
        - On choisit la hauteur Y qui correspond au border le plus large en X/Z.
        - On place le pivot du cutter au centre de ce border, à cette hauteur.

        C'est adapté aux plugs avec une grande plaque plate ouverte :
        la hauteur du border de la plaque devient le point de contact.
        """
        mesh = transform_from_node(mesh) or fp(mesh)
        if not mesh or not exists(mesh):
            return [0, 0, 0]

        border_points = []

        try:
            mesh_dag = dag(mesh)
            edge_it = om.MItMeshEdge(mesh_dag)

            while not edge_it.isDone():
                if edge_it.onBoundary():
                    p0 = edge_it.point(0, om.MSpace.kObject)
                    p1 = edge_it.point(1, om.MSpace.kObject)
                    border_points.append(p0)
                    border_points.append(p1)

                edge_it.next()

        except Exception as exc:
            dbg(exc)
            border_points = []

        if not border_points:
            try:
                pivot = mc.xform(mesh, q=True, ws=True, rp=True)
                return [pivot[0], pivot[1], pivot[2]]
            except Exception:
                return [0, 0, 0]

        tolerance = 0.001
        slices = {}

        for p in border_points:
            key = round(p.y / tolerance) * tolerance

            if key not in slices:
                slices[key] = []

            slices[key].append(p)

        best_key = None
        best_score = -1.0

        for key, points in slices.items():
            if len(points) < 4:
                continue

            min_x = min(p.x for p in points)
            max_x = max(p.x for p in points)
            min_z = min(p.z for p in points)
            max_z = max(p.z for p in points)

            size_x = max_x - min_x
            size_z = max_z - min_z
            area = size_x * size_z

            score = area * len(points)

            if score > best_score:
                best_score = score
                best_key = key

        if best_key is None:
            best_key = sum(p.y for p in border_points) / float(len(border_points))

        contact_points = slices.get(best_key, border_points)

        min_x = min(p.x for p in contact_points)
        max_x = max(p.x for p in contact_points)
        min_z = min(p.z for p in contact_points)
        max_z = max(p.z for p in contact_points)

        pivot = om.MPoint(
            (min_x + max_x) * 0.5,
            best_key,
            (min_z + max_z) * 0.5
        )

        pivot *= om.MMatrix(mc.xform(mesh, q=True, ws=True, matrix=True))

        mc.move(
            pivot.x,
            pivot.y,
            pivot.z,
            mesh + ".scalePivot",
            mesh + ".rotatePivot",
            ws=True,
            a=True
        )

        return [pivot.x, pivot.y, pivot.z]

    def create_rig(self, pivot, saved_rot):
        mesh_wm = world_matrix(self.mesh)

        self.picker = self.group("picker")
        self.flip = self.group("flip")
        self.rot = self.group("rot")
        self.offset = self.group("offset")

        if not all([self.picker, self.flip, self.rot, self.offset, exists(self.mesh)]):
            self.clear()
            self.mesh = ""
            return

        self.flip = fp((mc.parent(self.flip, self.picker) or [self.flip])[0])
        self.rot = fp((mc.parent(self.rot, self.flip) or [self.rot])[0])
        self.offset = fp((mc.parent(self.offset, self.rot) or [self.offset])[0])
        self.picker = fp(self.picker)

        if not all([self.picker, self.flip, self.rot, self.offset]):
            self.clear()
            self.mesh = ""
            return

        mc.xform(self.picker, ws=True, t=pivot)

        for node in (self.picker, self.flip, self.rot, self.offset):
            mc.xform(node, os=True, piv=(0, 0, 0))

        for node in (self.flip, self.rot, self.offset):
            mc.setAttr(node + ".translate", 0, 0, 0)
            mc.setAttr(node + ".rotate", 0, 0, 0)
            mc.setAttr(node + ".scale", 1, 1, 1)

        mc.setAttr(self.picker + ".rotate", saved_rot[0], saved_rot[1], saved_rot[2])
        mc.setAttr(self.rot + ".rotateY", self.saved_twist_y)

        self.mesh = parent_keep_world(self.mesh, self.offset)

        if mesh_wm:
            self.mesh = set_world_matrix(self.mesh, mesh_wm)

        self.mesh_name = sn(self.mesh) if self.mesh else ""

        if not all([self.mesh, self.picker, self.flip, self.rot, self.offset]):
            self.clear()
            self.mesh = ""
            return

        self.apply_flip()

    def rigged_mesh(self):
        if not self.offset:
            return ""

        direct = self.offset + "|" + self.mesh_name if self.mesh_name else ""
        if exists(direct):
            return direct

        children = mc.listRelatives(self.offset, children=True, type="transform", f=True) or []
        meshes = [child for child in children if has_mesh(child)]

        return meshes[0] if len(meshes) == 1 else ""

    def current_twist(self):
        if self.rot and exists(self.rot):
            try:
                self.saved_twist_y = float(mc.getAttr(self.rot + ".rotateY"))
            except Exception:
                pass

        return self.saved_twist_y

    def write_twist_state(self):
        if self.mesh:
            set_float_attr(self.mesh, TWIST_ATTR, self.saved_twist_y)

    def write_base_state(self):
        if self.mesh:
            set_base_rotation(self.mesh, self.saved_base_rot)

    def cache_start_values(self):
        if not self.rot or not exists(self.rot):
            return

        self.start_scale = [mc.getAttr(self.rot + ".scale" + axis) for axis in "XYZ"]

        try:
            self.start_rot_y = float(mc.getAttr(self.rot + ".rotateY"))
        except Exception:
            self.start_rot_y = self.saved_twist_y

    def face_normal(self, shape, polygon):
        try:
            normal = mesh_fn(shape).getPolygonNormal(int(polygon), om.MSpace.kWorld)
        except Exception:
            normal = om.MVector(0, 1, 0)

        if normal.length() < EPS:
            normal = om.MVector(0, 1, 0)

        normal.normalize()
        return normal

    def align_matrix(self, face):
        shape = fp(face.split(".f[", 1)[0]) if ".f[" in face else ""
        if not shape:
            return om.MMatrix()

        try:
            polygon = int(face.rsplit("[", 1)[-1].rstrip("]"))
            normal = mesh_fn(shape).getPolygonNormal(polygon, om.MSpace.kWorld)
        except Exception as exc:
            dbg(exc)
            return om.MMatrix()

        if normal.length() < EPS:
            normal = om.MVector(0, 1, 0)

        normal.normalize()

        world_up = om.MVector(0, 0, 1)
        if abs(normal * world_up) > 0.99:
            world_up = om.MVector(1, 0, 0)

        x_axis = world_up ^ normal
        if x_axis.length() < EPS:
            x_axis = om.MVector(1, 0, 0)
        x_axis.normalize()

        z_axis = x_axis ^ normal
        if z_axis.length() < EPS:
            z_axis = om.MVector(0, 0, 1)
        z_axis.normalize()

        return om.MMatrix([
            x_axis.x, x_axis.y, x_axis.z, 0,
            normal.x, normal.y, normal.z, 0,
            z_axis.x, z_axis.y, z_axis.z, 0,
            0, 0, 0, 1
        ])

    def face_rotation(self, face):
        return matrix_to_euler_deg(self.align_matrix(face))

    def keys(self):
        mods = mc.getModifiers()
        return bool(mods & 1), bool(mods & 4)

    def mode_from_keys(self):
        shift, ctrl = self.keys()

        if ctrl and shift:
            return self.mode

        if ctrl:
            return "scale"

        if shift:
            return "rotate"

        return "move"

    def update_flip(self):
        global orientation_flip_enabled, boolean_flip_state

        shift, ctrl = self.keys()
        down = shift and ctrl

        if not shift and not ctrl:
            self.flip_lock = False
        elif shift != ctrl:
            self.flip_lock = True

        can_flip = down and not self.flip_key_down and not self.flip_lock and self.mode == "move"

        if can_flip:
            self.flip_on = not self.flip_on

            orientation_flip_enabled = bool(self.flip_on)
            boolean_flip_state = 1 if self.flip_on else 0

            self.apply_flip(write_state=True)
            self.cache_start_values()
            self.hit_face = ""

            # IMPORTANT :
            # Quand on flippe, le signe de SURFACE_OUTWARD_OFFSET change.
            # On replace donc immédiatement le picker au même point souris,
            # sans toucher à la logique des normals.
            try:
                x, y, _ = mc.draggerContext(CTX, q=True, dragPoint=True)
                self.place(x, y)
            except Exception as exc:
                dbg(exc)

            cutter = self.rigged_mesh() or self.mesh
            if cutter:
                # On garde exactement la logique existante des normals.
                reverse_mesh_normals(cutter, delete_history=False)
                nudge_transform_for_boolean_update(cutter, amount=0.00001)

                set_int(cutter, "plugBoolFlipState", boolean_flip_state)
                set_flip(cutter, self.flip_on)
                sync_cutter(cutter)
                force_boolean_operation_now(cutter)

        self.flip_key_down = down

        return can_flip

    def apply_flip(self, write_state=False):
        if self.flip and exists(self.flip):
            mc.setAttr(self.flip + ".rotate", 0, 0, 0)
            mc.setAttr(self.flip + ".scaleX", 1)

            # Logique stable du premier script :
            # Le flip interactif passe par le groupe flip, pas par le cutter.
            # Comme le duplicata a été physiquement pré-flippé avant le drag,
            # l'état initial reste stable : flip_on False / booléen subtract.
            mc.setAttr(self.flip + ".scaleY", -1 if self.flip_on != self.baked_flip_on else 1)
            mc.setAttr(self.flip + ".scaleZ", 1)

        if write_state and self.mesh:
            set_flip(self.mesh, self.flip_on)
            set_int(self.mesh, "plugBoolFlipState", 1 if self.flip_on else 0)

    def place(self, x, y):
        shape, hit, polygon = self.raycast(x, y)
        if not shape or not self.picker:
            return

        remember_target(shape)

        face = shape + ".f[" + str(polygon) + "]"
        normal = self.face_normal(shape, polygon)
        sign = current_offset_sign()

        mc.setAttr(
            self.picker + ".translate",
            hit[0] + normal.x * SURFACE_OUTWARD_OFFSET * sign,
            hit[1] + normal.y * SURFACE_OUTWARD_OFFSET * sign,
            hit[2] + normal.z * SURFACE_OUTWARD_OFFSET * sign
        )

        if face != self.hit_face:
            self.saved_base_rot = list(self.face_rotation(face))
            mc.setAttr(self.picker + ".rotate", *self.saved_base_rot)
            self.write_base_state()
            self.hit_face = face

        self.apply_flip()

    def rotate(self, x):
        if not self.rot:
            return

        step = int((x - self.mode_start[0]) / 4.0) * 15
        self.saved_twist_y = self.start_rot_y + step

        mc.setAttr(self.rot + ".rotateY", self.saved_twist_y)
        self.write_twist_state()
        self.apply_flip()

    def scale(self, x):
        if not self.rot:
            return

        factor = max(0.01, 1 + (x - self.mode_start[0]) * 0.01)

        for axis, start in zip("XYZ", self.start_scale):
            mc.setAttr(self.rot + ".scale" + axis, max(0.01, start * factor))

        self.apply_flip()

    def duplicate(self):
        global boolean_cutter_mesh, last_mmb_duplicate_cutter

        if not self.rot:
            return

        if self.duplicate_done:
            x, y, _ = mc.draggerContext(CTX, q=True, dragPoint=True)
            self.place(x, y)
            select_active_cutter(self.mesh, deferred=True)
            mc.refresh(cv=True, f=True)
            return

        source = self.rigged_mesh()
        if not exists(source):
            return

        duplicate_list = mc.duplicate(
            source,
            rr=True,
            name=cutter_source_base_name(source) + "_boolCutter#"
        ) or []

        duplicate = transform_from_node(duplicate_list[0]) if duplicate_list else ""
        if not duplicate:
            return

        # IMPORTANT : on tagge le nouveau duplicata tout de suite.
        # Pendant le MMB, l'ancien cutter est finalisé puis ajouté au polyBoolean, ce qui peut
        # changer la sélection et parfois rendre le path court ambigu. Ce token permet de retrouver
        # précisément le nouveau cutter après les opérations Maya.
        duplicate_token = "{0}_{1}".format(sn(duplicate), safe(lambda: mc.timerX(), 0.0))
        set_str(duplicate, ACTIVE_DUP_ATTR, duplicate_token)

        try:
            current_twist = self.current_twist()

            # Finalise l'ancien cutter exactement comme le script stable :
            # on le sort du rig sans casser sa world matrix.
            old_cutter = parent_keep_world(source, self.old_parent)

            if old_cutter:
                cutter_op = BOOL_UNION if self.flip_on else BOOL_SUBTRACT

                set_flip(old_cutter, self.flip_on)
                set_int(old_cutter, "plugBoolFlipState", 1 if self.flip_on else 0)
                set_float_attr(old_cutter, TWIST_ATTR, current_twist)
                set_base_rotation(old_cutter, self.saved_base_rot)

                boolean_cutter_mesh = sync_cutter(old_cutter)
                store_metadata(old_cutter)

                # MMB : l'ancien cutter est immédiatement ajouté au booléen live.
                # Cette commande peut resélectionner old_cutter, donc on ne se fie plus
                # à la sélection Maya pour identifier le nouveau cutter ensuite.
                add_cutter_to_live_boolean(old_cutter, cutter_op, select_after=False)

                # On repart proprement pour le nouveau cutter,
                # mais on garde le target ET le résultat booléen afin de continuer
                # à ajouter les cutters suivants au même booléen live.
                reset_state(keep_cutter=False, keep_target=True, keep_boolean=True)

            # Récupération robuste du nouveau duplicata après les opérations sur l'ancien cutter.
            duplicate = find_transform_by_string_attr(ACTIVE_DUP_ATTR, duplicate_token) or transform_from_node(duplicate)
            if not duplicate:
                return

            if parent_path(duplicate) != self.offset:
                duplicate = parent_keep_world(duplicate, self.offset)

        except Exception as exc:
            dbg(exc)
            return

        duplicate = find_transform_by_string_attr(ACTIVE_DUP_ATTR, duplicate_token) or transform_from_node(duplicate)
        if not duplicate:
            return

        self.mesh = duplicate
        self.mesh_name = sn(duplicate)
        last_mmb_duplicate_cutter = self.mesh

        self.baked_flip_on = self.flip_on
        self.saved_twist_y = current_twist

        set_flip(self.mesh, self.flip_on)
        set_int(self.mesh, "plugBoolFlipState", 1 if self.flip_on else 0)
        set_float_attr(self.mesh, TWIST_ATTR, current_twist)
        set_base_rotation(self.mesh, self.saved_base_rot)

        boolean_cutter_mesh = sync_cutter(self.mesh)
        store_metadata(self.mesh)

        # IMPORTANT : le nouveau cutter devient immédiatement un input du booléen live.
        # Avant, il n'était ajouté qu'au MMB suivant, donc il y avait toujours un cutter de retard.
        # On l'ajoute maintenant tout de suite, puis on continue à le dragger : ses transforms
        # restent connectés au polyBoolean et doivent updater le résultat en live.
        if last_boolean_node and mc.objExists(last_boolean_node):
            add_cutter_to_live_boolean(self.mesh, BOOL_UNION if self.flip_on else BOOL_SUBTRACT)
            self.mesh = find_transform_by_string_attr(ACTIVE_DUP_ATTR, duplicate_token) or transform_from_node(self.mesh)
            last_mmb_duplicate_cutter = self.mesh
            boolean_cutter_mesh = sync_cutter(self.mesh)
            store_metadata(self.mesh)

        self.duplicate_done = True
        self.hit_face = ""

        excluded = set(mesh_shapes(self.mesh))
        result = boolean_result_mesh or result_from_bool_node(last_boolean_node)
        if result:
            excluded.update(mesh_shapes(result))

        self.cache = self.make_cache(self.filtered_visible(excluded))

        self.cache_start_values()
        last_mmb_duplicate_cutter = self.mesh
        select_active_cutter(self.mesh, deferred=True)

        x, y, _ = mc.draggerContext(CTX, q=True, dragPoint=True)
        self.place(x, y)

        # Dernier rappel après le placement : place() ne sélectionne rien, mais Maya peut avoir
        # encore une évaluation différée du booléen. On verrouille donc le nouveau cutter actif.
        last_mmb_duplicate_cutter = self.mesh
        select_active_cutter(self.mesh, deferred=True)
        mc.refresh(cv=True, f=True)


TOOL = PlugBoolDragTool()


# ============================================================
# TARGET / RAYCAST INTEGRATION
# ============================================================

def resolve_boolean_from_hit_shape(shape):
    global last_boolean_node, boolean_result_mesh, boolean_target_mesh

    shape = fp(shape)
    if not shape or not exists(shape):
        return False

    parent = transform_from_node(shape_parent(shape))
    if not parent or not exists(parent):
        return False

    nodes = bool_nodes(parent)
    if not nodes:
        return False

    last_boolean_node = nodes[0]
    boolean_result_mesh = parent

    stored_target = get_str(parent, LIVE_BOOL_TARGET_ATTR)
    if stored_target:
        boolean_target_mesh = stored_target

    if not boolean_target_mesh:
        for cutter in cutters_for_bool_node(last_boolean_node, include_active=False):
            target_from_cutter = get_str(cutter, "plugBoolTarget")
            if target_from_cutter:
                boolean_target_mesh = target_from_cutter
                break

    store_live_result_metadata()
    return True


def remember_target(shape):
    global boolean_target_mesh

    shape = fp(shape)
    if not shape or not exists(shape):
        return

    cutter = transform_from_node(boolean_cutter_mesh)
    if cutter and shape_under(shape, cutter):
        return

    if resolve_boolean_from_hit_shape(shape):
        return

    result = transform_from_node(boolean_result_mesh or result_from_bool_node(last_boolean_node))
    if result and shape_under(shape, result):
        return

    # Une fois un booléen existant lancé, on garde le target original.
    if last_boolean_node and boolean_target_mesh:
        return

    parent = shape_parent(shape)
    if parent:
        boolean_target_mesh = parent


# ============================================================
# TOOL LIFECYCLE
# ============================================================

def clear_temp():
    TOOL.clear()
    safe_delete(["pickerAim", "aimLoc", "instPicker"])


def cleanup_empty_groups():
    global active_cutter_group, active_cutter_group_session

    groups = []

    for g in [active_cutter_group, CUTTER_GROUP, "instPicker", "aimLoc", "pickerAim"]:
        g = fp(g)
        if g and g not in groups:
            groups.append(g)

    # Nettoyage sûr des groupes FastBool taggés. On ne supprime que les groupes vides :
    # si un undo Maya a laissé des cutters dedans, ils restent visibles et récupérables.
    for g in mc.ls(CUTTER_GROUP_PREFIX + "*", type="transform", l=True) or []:
        g = fp(g)
        if g and g not in groups and is_cutter_group_node(g):
            groups.append(g)

    for g in groups:
        if mc.objExists(g):
            try:
                is_active_group = bool(fp(active_cutter_group) == fp(g))
                if not (mc.listRelatives(g, children=True, fullPath=True) or []):
                    mc.delete(g)
                    if is_active_group:
                        active_cutter_group = ""
                        active_cutter_group_session = ""
            except Exception:
                pass


def kill_tool_job():
    global tool_job

    if tool_job and mc.scriptJob(exists=tool_job):
        safe(lambda: mc.scriptJob(kill=tool_job, force=True))

    tool_job = None


def kill_stale_jobs():
    global tool_job

    for j in mc.scriptJob(listJobs=True) or []:
        if any(k in j for k in ["PlugBool", "PlugBoolDragCtx", "on_tool_changed"]):
            safe(lambda j=j: mc.scriptJob(kill=int(j.split(":")[0]), force=True))

    tool_job = None


def start_drag():
    global tool_job, drag_session_active

    if TOOL.picker or TOOL.offset:
        TOOL.finalize_drag(select_final=False)

    clear_temp()

    if mc.draggerContext(CTX, exists=True):
        mc.deleteUI(CTX)

    TOOL.start()

    kill_tool_job()
    tool_job = mc.scriptJob(event=["ToolChanged", on_tool_changed], protected=True)

    install_validate_hotkey()


def press():
    TOOL.press()


def drag():
    TOOL.drag()


def release():
    TOOL.release()


def on_tool_changed():
    global drag_session_active, suppress_next_tool_changed

    if suppress_next_tool_changed:
        suppress_next_tool_changed = False
        return

    new_ctx = mc.currentCtx()

    if new_ctx == CTX:
        return

    kill_tool_job()

    if drag_session_active:
        TOOL.finalize_drag(select_final=False)
        clear_temp()

        # IMPORTANT : si l'utilisateur appuie sur W / Move Tool,
        # Maya passe en moveSuperContext. Dans ce cas c'est une sortie volontaire
        # du drag pour ajuster le cutter à la main, donc on ne doit PAS relancer
        # le drag après création/update du booléen.
        if new_ctx == "moveSuperContext":
            create_boolean(restart=False)
            cutter = sync_cutter()
            if cutter and exists(cutter):
                select_active_cutter(cutter, deferred=True)
            safe(lambda: mc.setToolTo("moveSuperContext"))
        else:
            create_boolean(restart=True)

    drag_session_active = False


def restart_drag_on_cutter(cutter):
    global reuse_selected_cutter_once, suppress_next_tool_changed, pending_restart_cutter

    cutter = sync_cutter(cutter)
    if not cutter:
        return

    load_metadata(cutter)
    store_metadata(cutter)

    reuse_selected_cutter_once = True
    suppress_next_tool_changed = True
    pending_restart_cutter = cutter

    set_visible(cutter)
    select_active_cutter(cutter, deferred=True)

    mc.evalDeferred(_deferred_restart_drag, lowestPriority=True)


def _deferred_restart_drag():
    global suppress_next_tool_changed, pending_restart_cutter

    cutter = transform_from_node(pending_restart_cutter)
    if cutter and exists(cutter):
        # Juste avant de réarmer le drag, on réimpose la sélection capturée.
        # Cela évite qu'une sélection native différée du Bool Tool fasse redémarrer
        # le script sur l'ancien cutter quand l'utilisateur vient d'ajouter un nouveau
        # plug au même polyBoolean avec Q.
        sync_cutter(cutter)
        select_active_cutter(cutter, deferred=True)

    pending_restart_cutter = ""
    start_drag()
    suppress_next_tool_changed = False


# ============================================================
# VALIDATE / BAKE HOTKEY
# ============================================================

def install_validate_hotkey():
    global validate_hotkey_installed

    if validate_hotkey_installed:
        return

    try:
        mc.optionVar(sv=(OPT_V_PRESS, mc.hotkey(keyShortcut="v", query=True, name=True) or ""))
        mc.optionVar(sv=(OPT_V_RELEASE, mc.hotkey(keyShortcut="v", query=True, releaseName=True) or ""))
    except Exception:
        pass

    try:
        mc.nameCommand(
            VALIDATE_NAME_CMD,
            ann="Validate Plug Boolean Drag",
            c='python("import __main__; __main__.plug_bool_validate_hotkey()")'
        )
        mc.hotkey(keyShortcut="v", name=VALIDATE_NAME_CMD)
        mc.hotkey(keyShortcut="v", releaseName="")
        validate_hotkey_installed = True
    except Exception:
        validate_hotkey_installed = False


def restore_validate_hotkey():
    global validate_hotkey_installed

    try:
        old_press = mc.optionVar(q=OPT_V_PRESS) if mc.optionVar(exists=OPT_V_PRESS) else ""
        old_release = mc.optionVar(q=OPT_V_RELEASE) if mc.optionVar(exists=OPT_V_RELEASE) else ""

        mc.hotkey(keyShortcut="v", name=old_press or "")
        mc.hotkey(keyShortcut="v", releaseName=old_release or "")
    except Exception:
        pass

    validate_hotkey_installed = False

def plug_bool_validate_hotkey():
    global drag_session_active, suppress_next_tool_changed

    suppress_next_tool_changed = True
    kill_tool_job()

    if drag_session_active:
        TOOL.finalize_drag(select_final=False)

    clear_temp()

    cutter = sync_cutter()
    live_node = resolve_live_boolean_context(cutter)
    has_live_boolean = bool(live_node)

    # IMPORTANT :
    # Si aucun booléen live n'existe encore, V ne doit PAS baker directement.
    # Ça évite de valider par accident quand tu voulais d'abord faire le premier Q.
    # Dans ce cas, V se comporte comme Q : il crée le booléen live et relance le drag.
    if not has_live_boolean:
        if not create_boolean(restart=True):
            mc.warning("Could not create first boolean. Validate cancelled.")
            drag_session_active = False
            restore_validate_hotkey()

            try:
                mel.eval("setToolTo $gSelect;")
            except Exception:
                safe(lambda: mc.setToolTo("selectSuperContext"))

            suppress_next_tool_changed = False
            return

        drag_session_active = False
        return

    # Si un booléen live existe déjà, là V valide/bake vraiment.
    # Si l'opération ne peut pas être réécrite (sélection Maya stale / cutter déjà retiré),
    # on bake quand même le polyBoolean retrouvé au lieu de relancer un second booléen.
    if not update_current_boolean_operation():
        live_node = resolve_live_boolean_context(cutter)
        if not live_node:
            mc.warning("Could not update existing boolean operation. Bake cancelled.")
            drag_session_active = False
            suppress_next_tool_changed = False
            return

    baked = bake_boolean_result()

    drag_session_active = False
    restore_validate_hotkey()

    try:
        mel.eval("setToolTo $gMove;")
    except Exception:
        safe(lambda: mc.setToolTo("moveSuperContext"))

    suppress_next_tool_changed = False

    if baked:
        cleanup_empty_groups()


def plug_bool_stop():
    global drag_session_active, suppress_next_tool_changed

    suppress_next_tool_changed = True

    kill_tool_job()
    TOOL.finalize_drag(select_final=False)
    clear_temp()
    restore_validate_hotkey()
    cleanup_empty_groups()

    drag_session_active = False
    suppress_next_tool_changed = False

    safe(lambda: mel.eval("setToolTo $gSelect;"))


def plug_bool_emergency_restore():
    """
    Fonction de secours à lancer si Maya reste bloqué avec le context custom,
    le hotkey V remplacé, ou des groupes temporaires dans la scène.

    Usage manuel possible dans le Script Editor :
    plug_bool_emergency_restore()
    """
    global drag_session_active, suppress_next_tool_changed
    global reuse_selected_cutter_once, current_cutter_reused, pending_restart_cutter

    suppress_next_tool_changed = True

    kill_tool_job()
    restore_validate_hotkey()

    try:
        if drag_session_active:
            TOOL.finalize_drag(select_final=False)
    except Exception:
        pass

    clear_temp()
    cleanup_empty_groups()

    TOOL.reset_all()

    reuse_selected_cutter_once = False
    current_cutter_reused = False
    pending_restart_cutter = ""
    drag_session_active = False

    try:
        if mc.draggerContext(CTX, exists=True):
            mc.deleteUI(CTX)
    except Exception:
        pass

    suppress_next_tool_changed = False

    safe(lambda: mel.eval("setToolTo $gSelect;"))
    safe(lambda: mc.refresh(f=True))

    mc.warning("Plug Bool emergency restore done.")


# ============================================================
# ENTRY POINT
# ============================================================

def smart_boolean_plug_drag():
    sel = mc.ls(sl=True, fl=True, l=True)

    if not sel:
        mc.warning("Select one plug mesh first.")
        return

    # Garde le comportement edge-loop si l'utilisateur sélectionne explicitement un edge.
    first = sel[0]
    if ".e[" in first:
        mel.eval("SelectEdgeLoopSp;")
        return

    plug = pick_valid_plug_from_selection(sel, prefer_existing_cutter=True)

    if plug:
        mc.select(plug, r=True)
        start_drag()
        return

    mc.warning("Selected object is not a mesh transform.")


kill_stale_jobs()
smart_boolean_plug_drag()
