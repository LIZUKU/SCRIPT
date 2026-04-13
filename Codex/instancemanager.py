# -*- coding: utf-8 -*-
"""
=============================================================================
INSTANCE ORGANIZER PRO v1.1
-----------------------------------------------------------------------------
Tool Maya pour organiser un mesh source + ses instances liées.
Pensé pour les gros assets, paneling, répétitions, modular kits.
=============================================================================
"""

import contextlib
import math

import maya.cmds as cmds

# Qt imports - Maya 2022-2024 use PySide2, Maya 2025+ uses PySide6
try:
    from PySide2 import QtWidgets, QtCore
except ImportError:
    from PySide6 import QtWidgets, QtCore

try:
    from shiboken2 import wrapInstance
except ImportError:
    from shiboken6 import wrapInstance

import maya.OpenMayaUI as omui


WINDOW_TITLE = "Instance Organizer Pro"
ACCENT_RED_BG = "#5a2a2a"
ACCENT_RED_BORDER = "#e84d4d"
ACCENT_RED_TEXT = "#e84d4d"

SOURCE_COLOR_INDEX = 13   # red
INSTANCE_COLOR_INDEX = 18  # cyan


# ============================================================
# HELPERS
# ============================================================
def get_maya_main_window():
    try:
        main_window_ptr = omui.MQtUtil.mainWindow()
        return wrapInstance(int(main_window_ptr), QtWidgets.QWidget)
    except Exception:
        return None


def _safe_exists(obj):
    if not obj:
        return False
    try:
        return cmds.objExists(obj)
    except Exception:
        return False


def _safe_delete(obj):
    if not obj:
        return
    try:
        if cmds.objExists(obj):
            cmds.delete(obj)
    except Exception:
        pass


def _short(obj):
    return obj.split("|")[-1] if obj else ""


def _warning(msg):
    cmds.warning(msg)
    try:
        cmds.inViewMessage(amg=msg, pos='botLeft', fade=True)
    except Exception:
        pass


def _info(msg):
    try:
        cmds.inViewMessage(amg=msg, pos='botLeft', fade=True)
    except Exception:
        pass


def _is_transform_with_shape(obj):
    if not _safe_exists(obj):
        return False
    if cmds.nodeType(obj) != "transform":
        return False
    shapes = cmds.listRelatives(obj, s=True, f=True) or []
    return bool(shapes)


def _find_first_valid_transform(selection):
    for obj in selection:
        if _is_transform_with_shape(obj):
            return obj
        if _safe_exists(obj) and cmds.nodeType(obj) == "transform":
            return obj
    return None


def _set_override_color(transform, color_index):
    if not _safe_exists(transform):
        return
    shapes = cmds.listRelatives(transform, s=True, f=True) or []
    for shape in shapes:
        try:
            cmds.setAttr(shape + ".overrideEnabled", 1)
            cmds.setAttr(shape + ".overrideRGBColors", 0)
            cmds.setAttr(shape + ".overrideColor", color_index)
        except Exception:
            pass


def _lock_transform_channels(transform, lock=True):
    if not _safe_exists(transform):
        return
    for attr in ["tx", "ty", "tz", "rx", "ry", "rz"]:
        plug = "{}.{}".format(transform, attr)
        try:
            cmds.setAttr(plug, lock=lock, keyable=not lock, channelBox=not lock)
        except Exception:
            pass


def _safe_name(base_name):
    candidate = base_name
    if not _safe_exists(candidate):
        return candidate
    i = 1
    while _safe_exists("{}_{}".format(base_name, i)):
        i += 1
    return "{}_{}".format(base_name, i)


def _ensure_attr(node, attr, attr_type="bool", default_value=None):
    if not _safe_exists(node):
        return
    if cmds.attributeQuery(attr, node=node, exists=True):
        return
    if attr_type == "string":
        cmds.addAttr(node, ln=attr, dt="string")
        if default_value is not None:
            cmds.setAttr("{}.{}".format(node, attr), str(default_value), type="string")
    elif attr_type == "bool":
        cmds.addAttr(node, ln=attr, at="bool", dv=1 if default_value else 0)
    else:
        cmds.addAttr(node, ln=attr, at=attr_type)


def _tag_node(node, is_source=False, is_instance=False, asset_name=""):
    _ensure_attr(node, "isSource", "bool", is_source)
    _ensure_attr(node, "isInstance", "bool", is_instance)
    _ensure_attr(node, "assetName", "string", asset_name)
    try:
        cmds.setAttr("{}.isSource".format(node), 1 if is_source else 0)
        cmds.setAttr("{}.isInstance".format(node), 1 if is_instance else 0)
        cmds.setAttr("{}.assetName".format(node), asset_name, type="string")
    except Exception:
        pass


@contextlib.contextmanager
def _undo_chunk():
    try:
        cmds.undoInfo(openChunk=True)
        yield
    finally:
        try:
            cmds.undoInfo(closeChunk=True)
        except Exception:
            pass


# ============================================================
# STYLE
# ============================================================
def apply_shared_style(widget):
    widget.setStyleSheet(f"""
        QWidget {{
            background-color: #2d2d2d;
            color: #b0b0b0;
            font-size: 11px;
        }}

        QDialog {{
            background-color: #2d2d2d;
            border: 1px solid #3d3d3d;
            border-radius: 4px;
        }}

        QLabel {{
            color: #b0b0b0;
            font-size: 11px;
        }}

        QLabel#statusLabel {{
            color: {ACCENT_RED_TEXT};
            font-size: 10px;
            font-weight: bold;
            padding: 2px;
        }}

        QLabel#sectionLabel {{
            color: #707070;
            font-size: 9px;
            font-weight: bold;
            padding-top: 4px;
            border-top: 1px solid #3a3a3a;
            margin-top: 4px;
        }}

        QPushButton {{
            background-color: #3a3a3a;
            color: #b0b0b0;
            border: 1px solid #4a4a4a;
            border-radius: 3px;
            font-size: 11px;
            padding: 4px 8px;
        }}

        QPushButton:hover {{
            background-color: #454545;
        }}

        QPushButton#primaryBtn {{
            background-color: {ACCENT_RED_BG};
            color: #ffffff;
            border: 1px solid {ACCENT_RED_BORDER};
            font-weight: bold;
        }}

        QPushButton#selectBtn {{
            background-color: #3a4a5a;
            border: 1px solid #4a5a6a;
            border-radius: 4px;
            font-size: 15px;
            font-weight: bold;
        }}

        QPushButton#selectBtn:hover {{
            background-color: #4a5a6a;
        }}

        QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
            background-color: #252525;
            color: #b0b0b0;
            border: 1px solid #3a3a3a;
            border-radius: 2px;
            padding: 2px;
        }}

        QCheckBox {{
            color: #b0b0b0;
            font-size: 11px;
            spacing: 6px;
        }}

        QCheckBox::indicator {{
            width: 14px;
            height: 14px;
            border: 1px solid #4a4a4a;
            background-color: #252525;
            border-radius: 3px;
        }}

        QCheckBox::indicator:checked {{
            background-color: {ACCENT_RED_BG};
            border-color: {ACCENT_RED_BORDER};
        }}
    """)


class SliderMixin(object):
    def _add_slider(self, parent_layout, label, min_val, max_val, default, decimals, label_width=115):
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        row.setContentsMargins(0, 0, 0, 0)

        lbl = QtWidgets.QLabel(label)
        lbl.setFixedWidth(label_width)
        row.addWidget(lbl)

        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setMinimum(0)
        slider.setMaximum(1000)
        row.addWidget(slider)

        if decimals > 0:
            spinbox = QtWidgets.QDoubleSpinBox()
            spinbox.setDecimals(decimals)
            spinbox.setMinimum(min_val)
            spinbox.setMaximum(max_val)
            step = (max_val - min_val) / 1000.0 if max_val > min_val else 0.01
            spinbox.setSingleStep(step)
        else:
            spinbox = QtWidgets.QSpinBox()
            spinbox.setMinimum(int(min_val))
            spinbox.setMaximum(int(max_val))

        spinbox.setFixedWidth(82)
        spinbox.setFixedHeight(20)
        spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        spinbox.setValue(default if decimals > 0 else int(default))
        row.addWidget(spinbox)

        def update_spinbox(val):
            ratio = val / 1000.0
            real_val = min_val + ratio * (max_val - min_val)
            spinbox.blockSignals(True)
            if decimals > 0:
                spinbox.setValue(real_val)
            else:
                spinbox.setValue(int(round(real_val)))
            spinbox.blockSignals(False)

        def update_slider(val):
            clamped = max(min_val, min(max_val, float(val)))
            ratio = (clamped - min_val) / (max_val - min_val) if (max_val - min_val) > 0 else 0
            slider.blockSignals(True)
            slider.setValue(int(ratio * 1000))
            slider.blockSignals(False)

        slider.valueChanged.connect(update_spinbox)
        spinbox.valueChanged.connect(update_slider)

        ratio = (float(default) - min_val) / (max_val - min_val) if (max_val - min_val) > 0 else 0
        slider.setValue(int(ratio * 1000))

        parent_layout.addLayout(row)
        return slider, spinbox


# ============================================================
# CORE MANAGER
# ============================================================
class InstanceAssetManager(object):
    def __init__(self):
        self.asset_name = "Panel"
        self.source_transform = None

    def _sanitize_asset_name(self, text):
        text = (text or "Asset").strip()
        return text.replace(" ", "_")

    def set_asset_name(self, text):
        self.asset_name = self._sanitize_asset_name(text)

    def auto_pick_source_from_selection(self):
        sel = cmds.ls(sl=True, long=True, transforms=True) or []
        valid = [obj for obj in sel if _find_first_valid_transform([obj])]
        if not valid:
            return None, "Sélectionne un mesh ou un transform valide."
        if len(valid) > 1:
            self.source_transform = valid[0]
            return valid[0], "Plusieurs objets détectés, 1er utilisé comme source."
        self.source_transform = valid[0]
        return valid[0], None

    def set_source_from_selection(self):
        src, warning_msg = self.auto_pick_source_from_selection()
        if not src:
            _warning("Sélectionne un mesh ou un groupe transform valide.")
            return None, warning_msg
        return src, warning_msg

    def get_asset_nodes(self):
        n = self.asset_name
        return {
            "asset_grp": "{}_ASSET_GRP".format(n),
            "base_grp": "{}_BASE_GRP".format(n),
            "inst_grp": "{}_INSTANCES_GRP".format(n),
            "baked_grp": "{}_BAKED_GRP".format(n),
            "export_grp": "{}_EXPORT_GRP".format(n),
        }

    def structure_exists(self):
        return _safe_exists(self.get_asset_nodes()["asset_grp"])

    def get_source_in_base(self):
        nodes = self.get_asset_nodes()
        if not _safe_exists(nodes["base_grp"]):
            return None
        children = cmds.listRelatives(nodes["base_grp"], c=True, f=True) or []
        return children[0] if children else None

    def create_structure(self, center_world=False):
        if not self.source_transform or not _safe_exists(self.source_transform):
            _warning("Choisis d'abord un source mesh.")
            return None

        nodes = self.get_asset_nodes()
        if _safe_exists(nodes["asset_grp"]):
            _warning("La structure existe déjà pour cet asset.")
            return nodes

        with _undo_chunk():
            asset_grp = cmds.group(em=True, n=_safe_name(nodes["asset_grp"]))
            base_grp = cmds.group(em=True, n=_safe_name(nodes["base_grp"]), p=asset_grp)
            cmds.group(em=True, n=_safe_name(nodes["inst_grp"]), p=asset_grp)

            try:
                new_source = cmds.parent(self.source_transform, base_grp)[0]
            except Exception:
                new_source = self.source_transform

            self.source_transform = new_source
            if center_world:
                cmds.xform(self.source_transform, ws=True, t=(0, 0, 0))

            _set_override_color(self.source_transform, SOURCE_COLOR_INDEX)
            _tag_node(self.source_transform, is_source=True, asset_name=self.asset_name)

        _info("Structure créée : <hl>{}</hl>".format(self.asset_name))
        return self.get_asset_nodes()

    def ensure_structure(self, center_world=False):
        if self.structure_exists():
            return self.get_asset_nodes()
        return self.create_structure(center_world=center_world)

    def _make_duplicate(self, source, duplicate_mode):
        if duplicate_mode == "duplicate":
            return cmds.duplicate(source, rr=True)[0]
        return cmds.instance(source)[0]

    def create_instance(self, duplicate_mode="instance"):
        nodes = self.get_asset_nodes()
        if not _safe_exists(nodes["inst_grp"]):
            _warning("Crée d'abord la structure.")
            return None

        source = self.get_source_in_base()
        if not source or not _safe_exists(source):
            _warning("Source introuvable dans BASE_GRP.")
            return None

        inst = self._make_duplicate(source, duplicate_mode)
        inst = cmds.parent(inst, nodes["inst_grp"])[0]
        inst = self.rename_instance(inst)
        _set_override_color(inst, INSTANCE_COLOR_INDEX)
        _tag_node(inst, is_instance=True, asset_name=self.asset_name)
        return inst

    def create_instances_count(self, count, duplicate_mode="instance"):
        created = []
        for _ in range(max(1, int(count))):
            inst = self.create_instance(duplicate_mode=duplicate_mode)
            if inst:
                created.append(inst)
        return created

    def _apply_offset(self, node, pos=(0, 0, 0), rot=(0, 0, 0), scl=(1, 1, 1)):
        try:
            cmds.xform(node, r=True, t=pos)
            cmds.xform(node, r=True, ro=rot)
            cmds.xform(node, r=True, s=scl)
        except Exception:
            pass

    def create_instances_on_selected(self, targets, duplicate_mode="instance", offset_pos=(0, 0, 0), offset_rot=(0, 0, 0), offset_scl=(1, 1, 1)):
        nodes = self.get_asset_nodes()
        source = self.get_source_in_base()
        if not source:
            _warning("Source introuvable.")
            return []

        created = []
        for target in targets:
            if not _safe_exists(target):
                continue
            inst = self._make_duplicate(source, duplicate_mode)
            inst = cmds.parent(inst, nodes["inst_grp"])[0]
            try:
                m = cmds.xform(target, q=True, ws=True, m=True)
                cmds.xform(inst, ws=True, m=m)
            except Exception:
                pass
            self._apply_offset(inst, offset_pos, offset_rot, offset_scl)
            inst = self.rename_instance(inst)
            _set_override_color(inst, INSTANCE_COLOR_INDEX)
            _tag_node(inst, is_instance=True, asset_name=self.asset_name)
            created.append(inst)
        return created

    def create_instances_pattern(self, mode, count=5, spacing=1.0, radius=5.0, duplicate_mode="instance"):
        created = self.create_instances_count(count, duplicate_mode=duplicate_mode)
        if not created:
            return []

        if mode == "Line":
            for i, inst in enumerate(created):
                cmds.xform(inst, ws=True, t=(i * spacing, 0, 0))
        elif mode == "Grid":
            side = int(math.ceil(math.sqrt(len(created))))
            for i, inst in enumerate(created):
                x = (i % side) * spacing
                z = (i // side) * spacing
                cmds.xform(inst, ws=True, t=(x, 0, z))
        elif mode == "Circle":
            n = max(1, len(created))
            for i, inst in enumerate(created):
                a = (float(i) / n) * math.pi * 2.0
                cmds.xform(inst, ws=True, t=(math.cos(a) * radius, 0, math.sin(a) * radius))
        return created

    def rename_instance(self, inst):
        nodes = self.get_asset_nodes()
        children = cmds.listRelatives(nodes["inst_grp"], c=True, f=True) or []
        idx = len(children)
        target_name = _safe_name("{}_INST_{:03d}".format(self.asset_name, idx))
        try:
            inst = cmds.rename(inst, target_name)
        except Exception:
            pass
        return inst

    def rename_all_instances(self):
        nodes = self.get_asset_nodes()
        if not _safe_exists(nodes["inst_grp"]):
            return 0
        children = cmds.listRelatives(nodes["inst_grp"], c=True, f=True) or []
        count = 0
        for i, child in enumerate(children, 1):
            try:
                cmds.rename(child, _safe_name("{}_INST_{:03d}".format(self.asset_name, i)))
                count += 1
            except Exception:
                pass
        return count

    def get_all_instances(self):
        nodes = self.get_asset_nodes()
        if not _safe_exists(nodes["inst_grp"]):
            return []
        return cmds.listRelatives(nodes["inst_grp"], c=True, f=True) or []

    def select_source(self):
        src = self.get_source_in_base()
        if src and _safe_exists(src):
            cmds.select(src, r=True)
            return src
        _warning("Source introuvable.")
        return None

    def select_instances(self):
        inst = self.get_all_instances()
        if inst:
            cmds.select(inst, r=True)
            return inst
        _warning("Aucune instance trouvée.")
        return []

    def select_asset_group(self):
        nodes = self.get_asset_nodes()
        if _safe_exists(nodes["asset_grp"]):
            cmds.select(nodes["asset_grp"], r=True)
            return nodes["asset_grp"]
        _warning("Asset group introuvable.")
        return None

    def toggle_source_lock(self, lock=True):
        src = self.get_source_in_base()
        if not src:
            _warning("Source introuvable.")
            return
        _lock_transform_channels(src, lock=lock)

    def toggle_visibility(self, node):
        if not _safe_exists(node):
            return None
        try:
            state = cmds.getAttr(node + ".visibility")
            cmds.setAttr(node + ".visibility", not state)
            return not state
        except Exception:
            return None

    def toggle_base_visibility(self):
        return self.toggle_visibility(self.get_asset_nodes()["base_grp"])

    def toggle_instances_visibility(self):
        return self.toggle_visibility(self.get_asset_nodes()["inst_grp"])

    def show_all(self):
        nodes = self.get_asset_nodes()
        for key in ["base_grp", "inst_grp"]:
            if _safe_exists(nodes[key]):
                try:
                    cmds.setAttr(nodes[key] + ".visibility", 1)
                except Exception:
                    pass

    def center_source(self):
        src = self.get_source_in_base()
        if not src:
            _warning("Source introuvable.")
            return
        cmds.xform(src, ws=True, t=(0, 0, 0))
        cmds.select(src, r=True)

    def freeze_source(self):
        src = self.get_source_in_base()
        if not src:
            _warning("Source introuvable.")
            return
        cmds.makeIdentity(src, apply=True, t=1, r=1, s=1, n=0)
        cmds.select(src, r=True)

    def delete_selected_instances(self):
        sel = cmds.ls(sl=True, long=True, transforms=True) or []
        inst_set = set(self.get_all_instances())
        to_delete = [obj for obj in sel if obj in inst_set]
        if not to_delete:
            _warning("Sélectionne des instances du groupe INSTANCES.")
            return 0
        cmds.delete(to_delete)
        return len(to_delete)

    def bake_instances_to_real_meshes(self, freeze=True, delete_history=True, combine=False):
        nodes = self.get_asset_nodes()
        instances = self.get_all_instances()
        if not instances:
            _warning("Aucune instance à baker.")
            return []

        if not _safe_exists(nodes["baked_grp"]):
            cmds.group(em=True, n=_safe_name(nodes["baked_grp"]), p=nodes["asset_grp"] if _safe_exists(nodes["asset_grp"]) else None)

        baked = []
        for i, inst in enumerate(instances, 1):
            try:
                dup = cmds.duplicate(inst, rr=True, n=_safe_name("{}_BAKED_{:03d}".format(self.asset_name, i)))[0]
                dup = cmds.parent(dup, nodes["baked_grp"])[0]
                if freeze:
                    cmds.makeIdentity(dup, apply=True, t=1, r=1, s=1, n=0)
                if delete_history:
                    cmds.delete(dup, ch=True)
                baked.append(dup)
            except Exception:
                pass

        if combine and baked:
            try:
                combined = cmds.polyUnite(baked, n=_safe_name("{}_BAKED_COMBINED".format(self.asset_name)), ch=False)[0]
                combined = cmds.parent(combined, nodes["baked_grp"])[0]
                for mesh in baked:
                    _safe_delete(mesh)
                baked = [combined]
            except Exception:
                pass

        if baked:
            cmds.select(baked, r=True)
        return baked

    def ensure_export_group(self):
        nodes = self.get_asset_nodes()
        grp = nodes["export_grp"]
        if _safe_exists(grp):
            return grp
        parent = nodes["asset_grp"] if _safe_exists(nodes["asset_grp"]) else None
        return cmds.group(em=True, n=_safe_name(grp), p=parent)


# ============================================================
# UI
# ============================================================
class InstanceOrganizerUI(QtWidgets.QDialog, SliderMixin):
    _instance = None

    def __init__(self, parent=get_maya_main_window()):
        super(InstanceOrganizerUI, self).__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.WindowCloseButtonHint)
        self.manager = InstanceAssetManager()
        self._build_ui()
        apply_shared_style(self)
        self.resize(430, 760)
        self._auto_source_on_open()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(5)

        title = QtWidgets.QLabel("INSTANCE ORGANIZER")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setObjectName("sectionLabel")
        layout.addWidget(title)

        self.status_label = QtWidgets.QLabel("Select a source mesh to begin")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(QtCore.Qt.AlignCenter)
        self.status_label.setFixedHeight(18)
        layout.addWidget(self.status_label)

        input_label = QtWidgets.QLabel("INPUT")
        input_label.setObjectName("sectionLabel")
        layout.addWidget(input_label)

        asset_row = QtWidgets.QHBoxLayout()
        asset_lbl = QtWidgets.QLabel("Asset Name")
        asset_lbl.setFixedWidth(115)
        asset_row.addWidget(asset_lbl)
        self.asset_name_edit = QtWidgets.QLineEdit("Panel")
        self.asset_name_edit.textChanged.connect(self._on_asset_name_changed)
        asset_row.addWidget(self.asset_name_edit)
        layout.addLayout(asset_row)

        self.source_label = QtWidgets.QLabel("Source : -")
        layout.addWidget(self.source_label)

        select_row = QtWidgets.QHBoxLayout()
        self.btn_pick_source = QtWidgets.QPushButton("Pick / Auto Source")
        self.btn_pick_source.setObjectName("primaryBtn")
        self.btn_pick_source.clicked.connect(self._on_pick_source)
        select_row.addWidget(self.btn_pick_source)

        self.btn_select_source = QtWidgets.QPushButton("▣")
        self.btn_select_source.setObjectName("selectBtn")
        self.btn_select_source.setFixedSize(28, 28)
        self.btn_select_source.setToolTip("Select source mesh")
        self.btn_select_source.clicked.connect(self._on_select_source)
        select_row.addWidget(self.btn_select_source)
        layout.addLayout(select_row)

        self.chk_lock_source = QtWidgets.QCheckBox("Lock source TR after structure creation")
        self.chk_lock_source.setChecked(True)
        self.chk_lock_source.toggled.connect(self._on_toggle_lock_mode)
        layout.addWidget(self.chk_lock_source)

        self.chk_edit_mode = QtWidgets.QCheckBox("Edit Mode (unlock source)")
        self.chk_edit_mode.setChecked(False)
        self.chk_edit_mode.toggled.connect(self._on_toggle_edit_mode)
        layout.addWidget(self.chk_edit_mode)

        setup_label = QtWidgets.QLabel("SETUP")
        setup_label.setObjectName("sectionLabel")
        layout.addWidget(setup_label)

        self.chk_center_on_create = QtWidgets.QCheckBox("Center source to world on structure creation")
        self.chk_center_on_create.setChecked(False)
        layout.addWidget(self.chk_center_on_create)

        self.chk_auto_select_new = QtWidgets.QCheckBox("Auto select new instance")
        self.chk_auto_select_new.setChecked(True)
        layout.addWidget(self.chk_auto_select_new)

        dup_row = QtWidgets.QHBoxLayout()
        dup_lbl = QtWidgets.QLabel("Create mode")
        dup_lbl.setFixedWidth(115)
        dup_row.addWidget(dup_lbl)
        self.duplicate_mode_combo = QtWidgets.QComboBox()
        self.duplicate_mode_combo.addItems(["instance", "duplicate"])
        dup_row.addWidget(self.duplicate_mode_combo)
        layout.addLayout(dup_row)

        self.btn_create_structure = QtWidgets.QPushButton("Create Outliner Structure")
        self.btn_create_structure.clicked.connect(self._on_create_structure)
        layout.addWidget(self.btn_create_structure)

        inst_label = QtWidgets.QLabel("INSTANCE CREATION")
        inst_label.setObjectName("sectionLabel")
        layout.addWidget(inst_label)

        self.instance_count_slider, self.instance_count_spin = self._add_slider(layout, "Instance Count", 1, 200, 1, 0)

        pattern_row = QtWidgets.QHBoxLayout()
        pat_lbl = QtWidgets.QLabel("Pattern")
        pat_lbl.setFixedWidth(115)
        pattern_row.addWidget(pat_lbl)
        self.pattern_combo = QtWidgets.QComboBox()
        self.pattern_combo.addItems(["None", "Line", "Grid", "Circle"])
        pattern_row.addWidget(self.pattern_combo)
        layout.addLayout(pattern_row)

        self.pattern_spacing_slider, self.pattern_spacing_spin = self._add_slider(layout, "Pattern Spacing", 0.1, 50.0, 2.0, 2)
        self.pattern_radius_slider, self.pattern_radius_spin = self._add_slider(layout, "Circle Radius", 0.1, 100.0, 5.0, 2)

        self.offset_px_slider, self.offset_px_spin = self._add_slider(layout, "Offset Pos X", -50.0, 50.0, 0.0, 2)
        self.offset_py_slider, self.offset_py_spin = self._add_slider(layout, "Offset Pos Y", -50.0, 50.0, 0.0, 2)
        self.offset_pz_slider, self.offset_pz_spin = self._add_slider(layout, "Offset Pos Z", -50.0, 50.0, 0.0, 2)
        self.offset_rx_slider, self.offset_rx_spin = self._add_slider(layout, "Offset Rot X", -180.0, 180.0, 0.0, 2)
        self.offset_ry_slider, self.offset_ry_spin = self._add_slider(layout, "Offset Rot Y", -180.0, 180.0, 0.0, 2)
        self.offset_rz_slider, self.offset_rz_spin = self._add_slider(layout, "Offset Rot Z", -180.0, 180.0, 0.0, 2)
        self.offset_scale_slider, self.offset_scale_spin = self._add_slider(layout, "Offset Uniform Scale", 0.01, 5.0, 1.0, 2)

        self.btn_create_instances = QtWidgets.QPushButton("Create Instance(s)")
        self.btn_create_instances.setObjectName("primaryBtn")
        self.btn_create_instances.clicked.connect(self._on_create_instances)
        layout.addWidget(self.btn_create_instances)

        self.btn_create_on_selected = QtWidgets.QPushButton("Create on Selected Objects")
        self.btn_create_on_selected.clicked.connect(self._on_create_on_selected)
        layout.addWidget(self.btn_create_on_selected)

        utility_label = QtWidgets.QLabel("VISIBILITY / CLEANUP / EXPORT")
        utility_label.setObjectName("sectionLabel")
        layout.addWidget(utility_label)

        row1 = QtWidgets.QHBoxLayout()
        self.btn_sel_source = QtWidgets.QPushButton("Select Source")
        self.btn_sel_source.clicked.connect(self._on_select_source)
        row1.addWidget(self.btn_sel_source)
        self.btn_sel_instances = QtWidgets.QPushButton("Select All Instances")
        self.btn_sel_instances.clicked.connect(self._on_select_instances)
        row1.addWidget(self.btn_sel_instances)
        layout.addLayout(row1)

        row2 = QtWidgets.QHBoxLayout()
        self.btn_rename_instances = QtWidgets.QPushButton("Rename Instances")
        self.btn_rename_instances.clicked.connect(self._on_rename_instances)
        row2.addWidget(self.btn_rename_instances)
        self.btn_delete_selected_instances = QtWidgets.QPushButton("Delete Selected Instances")
        self.btn_delete_selected_instances.clicked.connect(self._on_delete_selected_instances)
        row2.addWidget(self.btn_delete_selected_instances)
        layout.addLayout(row2)

        row3 = QtWidgets.QHBoxLayout()
        self.btn_toggle_base = QtWidgets.QPushButton("Toggle Base")
        self.btn_toggle_base.clicked.connect(self._on_toggle_base)
        row3.addWidget(self.btn_toggle_base)
        self.btn_toggle_inst = QtWidgets.QPushButton("Toggle Instances")
        self.btn_toggle_inst.clicked.connect(self._on_toggle_instances)
        row3.addWidget(self.btn_toggle_inst)
        layout.addLayout(row3)

        self.chk_bake_freeze = QtWidgets.QCheckBox("Bake: Freeze transforms")
        self.chk_bake_freeze.setChecked(True)
        layout.addWidget(self.chk_bake_freeze)
        self.chk_bake_history = QtWidgets.QCheckBox("Bake: Delete history")
        self.chk_bake_history.setChecked(True)
        layout.addWidget(self.chk_bake_history)
        self.chk_bake_combine = QtWidgets.QCheckBox("Bake: Combine output")
        self.chk_bake_combine.setChecked(False)
        layout.addWidget(self.chk_bake_combine)

        row4 = QtWidgets.QHBoxLayout()
        self.btn_bake = QtWidgets.QPushButton("Bake Instances")
        self.btn_bake.setObjectName("primaryBtn")
        self.btn_bake.clicked.connect(self._on_bake)
        row4.addWidget(self.btn_bake)

        self.btn_export_grp = QtWidgets.QPushButton("Create EXPORT_GRP")
        self.btn_export_grp.clicked.connect(self._on_create_export_group)
        row4.addWidget(self.btn_export_grp)
        layout.addLayout(row4)

        self.stats_label = QtWidgets.QLabel("Instances : 0")
        layout.addWidget(self.stats_label)

    def _set_status(self, text):
        self.status_label.setText(text)
        self._refresh_stats()

    def _refresh_source_label(self):
        src = self.manager.source_transform or self.manager.get_source_in_base()
        self.source_label.setText("Source : {}".format(_short(src) if src else "-"))

    def _refresh_stats(self):
        count = len(self.manager.get_all_instances())
        self.stats_label.setText("Instances : {}".format(count))

    def _auto_source_on_open(self):
        src, warning_msg = self.manager.auto_pick_source_from_selection()
        if src:
            self._refresh_source_label()
            if warning_msg:
                self._set_status(warning_msg)
                _warning(warning_msg)
            else:
                self._set_status("Source auto-detected: {}".format(_short(src)))

    def _on_asset_name_changed(self, text):
        self.manager.set_asset_name(text)

    def _on_pick_source(self):
        src, warning_msg = self.manager.set_source_from_selection()
        if src:
            self._refresh_source_label()
            if warning_msg:
                _warning(warning_msg)
                self._set_status(warning_msg)
            else:
                self._set_status("Source selected: {}".format(_short(src)))

    def _on_create_structure(self):
        self.manager.set_asset_name(self.asset_name_edit.text())
        result = self.manager.create_structure(center_world=self.chk_center_on_create.isChecked())
        if result:
            self._refresh_source_label()
            self._set_status("Structure created for {}".format(self.manager.asset_name))
            if self.chk_lock_source.isChecked() and not self.chk_edit_mode.isChecked():
                self.manager.toggle_source_lock(lock=True)

    def _duplicate_mode(self):
        return self.duplicate_mode_combo.currentText().strip().lower()

    def _offset_values(self):
        pos = (self.offset_px_spin.value(), self.offset_py_spin.value(), self.offset_pz_spin.value())
        rot = (self.offset_rx_spin.value(), self.offset_ry_spin.value(), self.offset_rz_spin.value())
        s = self.offset_scale_spin.value()
        scl = (s, s, s)
        return pos, rot, scl

    def _on_create_instances(self):
        with _undo_chunk():
            self.manager.set_asset_name(self.asset_name_edit.text())
            self.manager.ensure_structure(center_world=self.chk_center_on_create.isChecked())
            count = int(self.instance_count_spin.value())
            mode = self.pattern_combo.currentText()
            duplicate_mode = self._duplicate_mode()

            if mode == "None":
                created = self.manager.create_instances_count(count, duplicate_mode=duplicate_mode)
            else:
                created = self.manager.create_instances_pattern(
                    mode=mode,
                    count=count,
                    spacing=float(self.pattern_spacing_spin.value()),
                    radius=float(self.pattern_radius_spin.value()),
                    duplicate_mode=duplicate_mode,
                )

            pos, rot, scl = self._offset_values()
            for node in created:
                self.manager._apply_offset(node, pos, rot, scl)

            if created:
                if self.chk_auto_select_new.isChecked():
                    cmds.select(created, r=True)
                self._set_status("{} object(s) created in {} mode".format(len(created), duplicate_mode))
            else:
                self._set_status("No object created")

    def _on_create_on_selected(self):
        with _undo_chunk():
            self.manager.set_asset_name(self.asset_name_edit.text())
            self.manager.ensure_structure(center_world=self.chk_center_on_create.isChecked())
            targets = cmds.ls(sl=True, long=True, transforms=True) or []
            if not targets:
                _warning("Sélectionne les objets cibles.")
                return
            source = self.manager.get_source_in_base()
            targets = [t for t in targets if t != source]
            pos, rot, scl = self._offset_values()
            created = self.manager.create_instances_on_selected(
                targets,
                duplicate_mode=self._duplicate_mode(),
                offset_pos=pos,
                offset_rot=rot,
                offset_scl=scl,
            )
            if created:
                cmds.select(created, r=True)
                self._set_status("{} object(s) created on selected targets".format(len(created)))

    def _on_select_source(self):
        src = self.manager.select_source()
        if src:
            self._set_status("Source selected")

    def _on_select_instances(self):
        inst = self.manager.select_instances()
        if inst:
            self._set_status("{} instance(s) selected".format(len(inst)))

    def _on_rename_instances(self):
        count = self.manager.rename_all_instances()
        self._set_status("{} instance(s) renamed".format(count))

    def _on_toggle_base(self):
        self.manager.toggle_base_visibility()
        self._set_status("Base visibility toggled")

    def _on_toggle_instances(self):
        self.manager.toggle_instances_visibility()
        self._set_status("Instances visibility toggled")

    def _on_delete_selected_instances(self):
        count = self.manager.delete_selected_instances()
        self._set_status("{} instance(s) deleted".format(count))

    def _on_bake(self):
        with _undo_chunk():
            baked = self.manager.bake_instances_to_real_meshes(
                freeze=self.chk_bake_freeze.isChecked(),
                delete_history=self.chk_bake_history.isChecked(),
                combine=self.chk_bake_combine.isChecked(),
            )
            if baked:
                self._set_status("{} baked mesh(es) created".format(len(baked)))

    def _on_create_export_group(self):
        grp = self.manager.ensure_export_group()
        self._set_status("Export group ready: {}".format(_short(grp)))

    def _on_toggle_lock_mode(self, checked):
        if self.chk_edit_mode.isChecked():
            return
        self.manager.toggle_source_lock(lock=checked)

    def _on_toggle_edit_mode(self, checked):
        self.manager.toggle_source_lock(lock=not checked and self.chk_lock_source.isChecked())
        self._set_status("Edit mode ON" if checked else "Edit mode OFF")

    @classmethod
    def show_ui(cls):
        if cls._instance:
            try:
                cls._instance.close()
                cls._instance.deleteLater()
            except Exception:
                pass
        cls._instance = cls()
        cls._instance.show()
        return cls._instance


def show_ui():
    return InstanceOrganizerUI.show_ui()


if __name__ == "__main__":
    show_ui()
