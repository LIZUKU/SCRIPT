# -*- coding: utf-8 -*-
"""
=============================================================================
INSTANCE ORGANIZER PRO v1.0
-----------------------------------------------------------------------------
Tool Maya pour organiser un mesh source + ses instances liées.
Pensé pour les gros assets, paneling, répétitions, modular kits.

Fonctions:
- Sélection du mesh source
- Création automatique de la structure Outliner
- Création d'instances liées (duplicate special / instance)
- Sélection rapide du source / des instances / du groupe asset
- Hide / Show base et instances
- Recentrage du source au monde
- Rename auto des instances
- Bake des instances en meshes réels
- UI PySide inspirée d'un tool de prod Maya

Compatible: Maya 2022 - 2025
=============================================================================
"""

import maya.cmds as cmds

# Qt imports - Maya 2022-2024 use PySide2, Maya 2025+ uses PySide6
try:
    from PySide2 import QtWidgets, QtCore, QtGui
    PYSIDE_VERSION = 2
except ImportError:
    from PySide6 import QtWidgets, QtCore, QtGui
    PYSIDE_VERSION = 6

try:
    from shiboken2 import wrapInstance
except ImportError:
    from shiboken6 import wrapInstance

import maya.OpenMayaUI as omui


# ============================================================
# GLOBAL CONSTANTS
# ============================================================
WINDOW_TITLE = "Instance Organizer Pro"
ACCENT_RED_BG = "#5a2a2a"
ACCENT_RED_BORDER = "#e84d4d"
ACCENT_RED_TEXT = "#e84d4d"


# ============================================================
# HELPERS
# ============================================================
def get_maya_main_window():
    try:
        main_window_ptr = omui.MQtUtil.mainWindow()
        return wrapInstance(int(main_window_ptr), QtWidgets.QWidget)
    except:
        return None


def _safe_exists(obj):
    if not obj:
        return False
    try:
        return cmds.objExists(obj)
    except:
        return False


def _safe_delete(obj):
    if not obj:
        return
    try:
        if cmds.objExists(obj):
            cmds.delete(obj)
    except:
        pass


def _short(obj):
    return obj.split("|")[-1] if obj else ""


def _warning(msg):
    cmds.warning(msg)
    try:
        cmds.inViewMessage(amg=msg, pos='botLeft', fade=True)
    except:
        pass


def _info(msg):
    try:
        cmds.inViewMessage(amg=msg, pos='botLeft', fade=True)
    except:
        pass


def _is_transform_with_shape(obj):
    if not _safe_exists(obj):
        return False
    if cmds.nodeType(obj) != "transform":
        return False
    shapes = cmds.listRelatives(obj, s=True, f=True) or []
    return len(shapes) > 0


def _find_first_valid_transform(selection):
    for obj in selection:
        if _is_transform_with_shape(obj):
            return obj
        if _safe_exists(obj) and cmds.nodeType(obj) == "transform":
            return obj
    return None


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

        QPushButton:pressed {{
            background-color: #2a2a2a;
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

        QLineEdit, QSpinBox, QDoubleSpinBox {{
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

        QSlider::groove:horizontal {{
            height: 3px;
            background: #1a1a1a;
            border-radius: 1px;
        }}

        QSlider::handle:horizontal {{
            background: #888888;
            width: 12px;
            margin: -5px 0;
            border-radius: 6px;
        }}

        QSlider::handle:horizontal:hover {{
            background: #aaaaaa;
        }}

        QFrame {{
            border: none;
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
        text = text.replace(" ", "_")
        return text

    def set_asset_name(self, text):
        self.asset_name = self._sanitize_asset_name(text)

    def set_source_from_selection(self):
        sel = cmds.ls(sl=True, long=True, transforms=True) or []
        src = _find_first_valid_transform(sel)
        if not src:
            _warning("Sélectionne un mesh ou un groupe transform valide.")
            return None
        self.source_transform = src
        return src

    def get_asset_nodes(self):
        n = self.asset_name
        return {
            "asset_grp": "{}_ASSET_GRP".format(n),
            "base_grp": "{}_BASE_GRP".format(n),
            "inst_grp": "{}_INSTANCES_GRP".format(n),
            "baked_grp": "{}_BAKED_GRP".format(n),
        }

    def structure_exists(self):
        nodes = self.get_asset_nodes()
        return _safe_exists(nodes["asset_grp"])

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

        asset_grp = cmds.group(em=True, n=nodes["asset_grp"])
        base_grp = cmds.group(em=True, n=nodes["base_grp"], p=asset_grp)
        inst_grp = cmds.group(em=True, n=nodes["inst_grp"], p=asset_grp)

        try:
            new_source = cmds.parent(self.source_transform, base_grp)[0]
        except:
            new_source = self.source_transform

        self.source_transform = new_source

        if center_world:
            try:
                cmds.xform(self.source_transform, ws=True, t=(0, 0, 0))
            except:
                pass

        _info("Structure créée : <hl>{}</hl>".format(self.asset_name))
        return nodes

    def ensure_structure(self, center_world=False):
        if self.structure_exists():
            return self.get_asset_nodes()
        return self.create_structure(center_world=center_world)

    def create_instance(self):
        nodes = self.get_asset_nodes()
        if not _safe_exists(nodes["inst_grp"]):
            _warning("Crée d'abord la structure.")
            return None

        source = self.get_source_in_base()
        if not source or not _safe_exists(source):
            _warning("Source introuvable dans BASE_GRP.")
            return None

        inst = cmds.instance(source)[0]
        inst = cmds.parent(inst, nodes["inst_grp"])[0]
        inst = self.rename_instance(inst)
        cmds.select(inst, r=True)
        return inst

    def create_instances_count(self, count):
        created = []
        count = max(1, int(count))
        for _ in range(count):
            inst = self.create_instance()
            if inst:
                created.append(inst)
        return created

    def rename_instance(self, inst):
        nodes = self.get_asset_nodes()
        children = cmds.listRelatives(nodes["inst_grp"], c=True, f=True) or []
        idx = len(children)
        target_name = "{}_INST_{:03d}".format(self.asset_name, idx)
        try:
            inst = cmds.rename(inst, target_name)
        except:
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
                cmds.rename(child, "{}_INST_{:03d}".format(self.asset_name, i))
                count += 1
            except:
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

    def toggle_visibility(self, node):
        if not _safe_exists(node):
            return None
        try:
            state = cmds.getAttr(node + ".visibility")
            cmds.setAttr(node + ".visibility", not state)
            return not state
        except:
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
                except:
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

    def create_display_layer_for_instances(self):
        instances = self.get_all_instances()
        if not instances:
            _warning("Aucune instance trouvée.")
            return None
        layer_name = "{}_instances_LYR".format(self.asset_name)
        if not _safe_exists(layer_name):
            cmds.createDisplayLayer(name=layer_name, empty=True)
        cmds.editDisplayLayerMembers(layer_name, instances, noRecurse=True)
        return layer_name

    def bake_instances_to_real_meshes(self):
        nodes = self.get_asset_nodes()
        instances = self.get_all_instances()
        if not instances:
            _warning("Aucune instance à baker.")
            return None

        if not _safe_exists(nodes["baked_grp"]):
            cmds.group(em=True, n=nodes["baked_grp"], p=nodes["asset_grp"] if _safe_exists(nodes["asset_grp"]) else None)

        baked = []
        for i, inst in enumerate(instances, 1):
            try:
                dup = cmds.duplicate(inst, rr=True, n="{}_BAKED_{:03d}".format(self.asset_name, i))[0]
                dup = cmds.parent(dup, nodes["baked_grp"])[0]
                baked.append(dup)
            except:
                pass
        if baked:
            cmds.select(baked, r=True)
        return baked


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
        self.resize(410, 560)

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
        self.btn_pick_source = QtWidgets.QPushButton("Pick Selected Source")
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

        setup_label = QtWidgets.QLabel("SETUP")
        setup_label.setObjectName("sectionLabel")
        layout.addWidget(setup_label)

        self.chk_center_on_create = QtWidgets.QCheckBox("Center source to world on structure creation")
        self.chk_center_on_create.setChecked(False)
        layout.addWidget(self.chk_center_on_create)

        self.chk_auto_select_new = QtWidgets.QCheckBox("Auto select new instance")
        self.chk_auto_select_new.setChecked(True)
        layout.addWidget(self.chk_auto_select_new)

        self.btn_create_structure = QtWidgets.QPushButton("Create Outliner Structure")
        self.btn_create_structure.clicked.connect(self._on_create_structure)
        layout.addWidget(self.btn_create_structure)

        inst_label = QtWidgets.QLabel("INSTANCE CREATION")
        inst_label.setObjectName("sectionLabel")
        layout.addWidget(inst_label)

        self.instance_count_slider, self.instance_count_spin = self._add_slider(
            layout, "Instance Count", 1, 100, 1, 0
        )

        self.btn_create_instances = QtWidgets.QPushButton("Create Instance(s)")
        self.btn_create_instances.setObjectName("primaryBtn")
        self.btn_create_instances.clicked.connect(self._on_create_instances)
        layout.addWidget(self.btn_create_instances)

        select_tools_label = QtWidgets.QLabel("SELECTION TOOLS")
        select_tools_label.setObjectName("sectionLabel")
        layout.addWidget(select_tools_label)

        row1 = QtWidgets.QHBoxLayout()
        self.btn_sel_source = QtWidgets.QPushButton("Select Source")
        self.btn_sel_source.clicked.connect(self._on_select_source)
        row1.addWidget(self.btn_sel_source)
        self.btn_sel_instances = QtWidgets.QPushButton("Select All Instances")
        self.btn_sel_instances.clicked.connect(self._on_select_instances)
        row1.addWidget(self.btn_sel_instances)
        layout.addLayout(row1)

        row2 = QtWidgets.QHBoxLayout()
        self.btn_sel_asset = QtWidgets.QPushButton("Select Asset Group")
        self.btn_sel_asset.clicked.connect(self._on_select_asset)
        row2.addWidget(self.btn_sel_asset)
        self.btn_rename_instances = QtWidgets.QPushButton("Rename Instances")
        self.btn_rename_instances.clicked.connect(self._on_rename_instances)
        row2.addWidget(self.btn_rename_instances)
        layout.addLayout(row2)

        vis_label = QtWidgets.QLabel("VISIBILITY / CLEANUP")
        vis_label.setObjectName("sectionLabel")
        layout.addWidget(vis_label)

        row3 = QtWidgets.QHBoxLayout()
        self.btn_toggle_base = QtWidgets.QPushButton("Toggle Base")
        self.btn_toggle_base.clicked.connect(self._on_toggle_base)
        row3.addWidget(self.btn_toggle_base)
        self.btn_toggle_inst = QtWidgets.QPushButton("Toggle Instances")
        self.btn_toggle_inst.clicked.connect(self._on_toggle_instances)
        row3.addWidget(self.btn_toggle_inst)
        layout.addLayout(row3)

        row4 = QtWidgets.QHBoxLayout()
        self.btn_show_all = QtWidgets.QPushButton("Show All")
        self.btn_show_all.clicked.connect(self._on_show_all)
        row4.addWidget(self.btn_show_all)
        self.btn_delete_selected_instances = QtWidgets.QPushButton("Delete Selected Instances")
        self.btn_delete_selected_instances.clicked.connect(self._on_delete_selected_instances)
        row4.addWidget(self.btn_delete_selected_instances)
        layout.addLayout(row4)

        utility_label = QtWidgets.QLabel("UTILITY")
        utility_label.setObjectName("sectionLabel")
        layout.addWidget(utility_label)

        row5 = QtWidgets.QHBoxLayout()
        self.btn_center_source = QtWidgets.QPushButton("Center Source")
        self.btn_center_source.clicked.connect(self._on_center_source)
        row5.addWidget(self.btn_center_source)
        self.btn_freeze_source = QtWidgets.QPushButton("Freeze Source")
        self.btn_freeze_source.clicked.connect(self._on_freeze_source)
        row5.addWidget(self.btn_freeze_source)
        layout.addLayout(row5)

        row6 = QtWidgets.QHBoxLayout()
        self.btn_create_layer = QtWidgets.QPushButton("Create Layer for Instances")
        self.btn_create_layer.clicked.connect(self._on_create_layer)
        row6.addWidget(self.btn_create_layer)
        self.btn_bake = QtWidgets.QPushButton("Bake Instances")
        self.btn_bake.setObjectName("primaryBtn")
        self.btn_bake.clicked.connect(self._on_bake)
        row6.addWidget(self.btn_bake)
        layout.addLayout(row6)

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

    def _on_asset_name_changed(self, text):
        self.manager.set_asset_name(text)

    def _on_pick_source(self):
        src = self.manager.set_source_from_selection()
        if src:
            self._refresh_source_label()
            self._set_status("Source selected: {}".format(_short(src)))

    def _on_create_structure(self):
        self.manager.set_asset_name(self.asset_name_edit.text())
        result = self.manager.create_structure(center_world=self.chk_center_on_create.isChecked())
        if result:
            self._refresh_source_label()
            self._set_status("Structure created for {}".format(self.manager.asset_name))

    def _on_create_instances(self):
        self.manager.set_asset_name(self.asset_name_edit.text())
        self.manager.ensure_structure(center_world=self.chk_center_on_create.isChecked())
        count = int(self.instance_count_spin.value())
        created = self.manager.create_instances_count(count)
        if created:
            if self.chk_auto_select_new.isChecked():
                cmds.select(created, r=True)
            self._set_status("{} instance(s) created".format(len(created)))
        else:
            self._set_status("No instance created")

    def _on_select_source(self):
        src = self.manager.select_source()
        if src:
            self._set_status("Source selected")

    def _on_select_instances(self):
        inst = self.manager.select_instances()
        if inst:
            self._set_status("{} instance(s) selected".format(len(inst)))

    def _on_select_asset(self):
        asset = self.manager.select_asset_group()
        if asset:
            self._set_status("Asset group selected")

    def _on_rename_instances(self):
        count = self.manager.rename_all_instances()
        self._set_status("{} instance(s) renamed".format(count))

    def _on_toggle_base(self):
        self.manager.toggle_base_visibility()
        self._set_status("Base visibility toggled")

    def _on_toggle_instances(self):
        self.manager.toggle_instances_visibility()
        self._set_status("Instances visibility toggled")

    def _on_show_all(self):
        self.manager.show_all()
        self._set_status("Base and instances visible")

    def _on_delete_selected_instances(self):
        count = self.manager.delete_selected_instances()
        self._set_status("{} instance(s) deleted".format(count))

    def _on_center_source(self):
        self.manager.center_source()
        self._set_status("Source centered to world")

    def _on_freeze_source(self):
        self.manager.freeze_source()
        self._set_status("Source frozen")

    def _on_create_layer(self):
        layer = self.manager.create_display_layer_for_instances()
        if layer:
            self._set_status("Layer created: {}".format(layer))

    def _on_bake(self):
        baked = self.manager.bake_instances_to_real_meshes()
        if baked:
            self._set_status("{} baked mesh(es) created".format(len(baked)))

    @classmethod
    def show_ui(cls):
        if cls._instance:
            try:
                cls._instance.close()
                cls._instance.deleteLater()
            except:
                pass
        cls._instance = cls()
        cls._instance.show()
        return cls._instance


def show_ui():
    return InstanceOrganizerUI.show_ui()


if __name__ == "__main__":
    show_ui()
