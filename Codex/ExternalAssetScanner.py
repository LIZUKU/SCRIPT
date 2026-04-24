# -*- coding: utf-8 -*-

import os
import re
import time
import traceback

import maya.cmds as cmds
import maya.OpenMayaUI as omui

from PySide2 import QtCore, QtGui, QtWidgets
from shiboken2 import wrapInstance


WINDOW_OBJECT_NAME = "qdExternalAssetScannerQt"

DEFAULT_PREFIXES = ["ACC", "ARC", "QDD", "AGRA", "RELIC"]

MAP_SUFFIXES = {
    "ALB", "NOR", "OCC", "RGH", "RME", "MSK", "HGT", "THR",
    "EMI", "MET", "AO", "COL", "COLOR", "ROUGH", "NORMAL"
}

SCENE_SUFFIXES_TO_STRIP = [
    "_BAKETEXTURES",
    "_BAKE_TEXTURES",
    "_BAKE",
    "_TEXTURES",
    "_TEXTURE",
    "_DELIVERY",
    "_WORK",
    "_WIP",
    "_FINAL",
]

TEXTURE_ATTR_CANDIDATES = [
    "fileTextureName",
    "filename",
    "fileName",
    "source",
    "s_source",
    "path",
    "texture",
]


def maya_main_window():
    ptr = omui.MQtUtil.mainWindow()
    if ptr is None:
        return None
    return wrapInstance(int(ptr), QtWidgets.QWidget)


def short_name(node):
    return node.split("|")[-1]


def strip_ns(name):
    return name.split(":")[-1]


def copy_to_clipboard(text):
    if not text:
        return
    try:
        cmds.clipboard(text=text)
    except Exception:
        pass
    QtWidgets.QApplication.clipboard().setText(text)


def scene_path():
    try:
        return cmds.file(q=True, sn=True) or ""
    except Exception:
        return ""


def basename_no_ext(path):
    if not path:
        return ""
    return os.path.splitext(os.path.basename(path))[0]


def clean_scene_base_for_asset(base_name):
    if not base_name:
        return ""
    name = base_name.upper()
    changed = True
    while changed:
        changed = False
        for suffix in SCENE_SUFFIXES_TO_STRIP:
            if name.endswith(suffix):
                name = name[:-len(suffix)]
                changed = True
    return name.strip("_")


def extract_main_name_from_string(name):
    name = strip_ns(short_name(name)).upper()
    parts = name.split("_")
    if len(parts) < 3:
        return None

    collected = []
    for part in parts:
        if re.match(r"^[A-Z0-9]+$", part):
            collected.append(part)
            if re.match(r"^[A-Z]$", part):
                return "_".join(collected) if len(collected) >= 3 else None
        else:
            break
    return None


def extract_asset_name_from_qds_shader_name(name):
    shader_name = strip_ns(short_name(name)).upper()
    if not shader_name.startswith("QDS_"):
        return None

    core = shader_name[4:]
    parts = core.split("_")
    if len(parts) < 3:
        return None

    collected = []
    for part in parts:
        if not re.match(r"^[A-Z0-9]+$", part):
            break
        collected.append(part)
        if re.match(r"^[A-Z]$", part):
            return "_".join(collected) if len(collected) >= 3 else None
    return None


def extract_kit_name_from_string(name):
    name = strip_ns(short_name(name)).upper()
    parts = name.split("_")
    upper = []

    for p in parts:
        if re.match(r"^[A-Z0-9]+$", p):
            upper.append(p)
        else:
            break

    if not upper or "KIT" not in upper or len(upper) < 3:
        return None

    if extract_main_name_from_string(name):
        return None

    return "_".join(upper)


def is_probable_texture_name(asset_name):
    """
    Avoid listing texture-like families as reused assets.
    Example: QDD_CRACK_CONCRETE_ALB, ARC_WALL_NOR_02
    """
    name = (asset_name or "").upper()
    if not name or "_" not in name:
        return False

    parts = name.split("_")
    if len(parts) < 3:
        return False

    tail = parts[-1]
    if tail in MAP_SUFFIXES:
        return True
    if re.match(r"^\d+$", tail) and len(parts) >= 2 and parts[-2] in MAP_SUFFIXES:
        return True
    return False


def is_excluded_reused_asset_name(asset_name):
    return (asset_name or "").upper().startswith("QDD_")


def current_asset_from_scene():
    path = scene_path()
    if not path:
        return None

    base = clean_scene_base_for_asset(basename_no_ext(path))

    direct = extract_main_name_from_string(base)
    if direct:
        return direct

    matches = re.findall(r"([A-Z0-9]+(?:_[A-Z0-9]+)*_[A-Z])(?:_|$)", base)
    if matches:
        matches = sorted(matches, key=len, reverse=True)
        return matches[0]

    return None


def current_asset_category_from_scene():
    path = scene_path().replace("\\", "/").lower()
    if "/graphics/props/" in path:
        return "Props"
    if "/graphics/environment/" in path:
        return "Environment"
    if "/graphics/characters/" in path or "/graphics/chara/" in path:
        return "Characters"
    return "Props"


def read_first_existing_string_attr(node, attr_names):
    for attr in attr_names:
        plug = "{}.{}".format(node, attr)
        try:
            if cmds.objExists(plug):
                val = cmds.getAttr(plug)
                if isinstance(val, str) and val.strip():
                    return val
        except Exception:
            pass
    return ""


def extract_texture_family_from_path(path):
    if not path:
        return None

    base = os.path.splitext(os.path.basename(path))[0].upper()
    parts = base.split("_")
    if len(parts) < 2:
        return None

    if parts and parts[-1] in MAP_SUFFIXES:
        parts = parts[:-1]

    if parts and re.match(r"^\d+$", parts[-1]):
        parts = parts[:-1]

    if not parts or len(parts) < 2:
        return None

    return "_".join(parts)


def list_texture_nodes():
    nodes = []
    available_types = set()
    try:
        available_types = set(cmds.allNodeTypes() or [])
    except Exception:
        pass

    for t in ["file", "qdNodeBitmap"]:
        if available_types and t not in available_types:
            continue
        try:
            nodes.extend(cmds.ls(type=t) or [])
        except Exception:
            pass
    return list(set(nodes))


def scan_textures():
    data = {}

    for node in list_texture_nodes():
        tex_path = read_first_existing_string_attr(node, TEXTURE_ATTR_CANDIDATES)
        family = extract_texture_family_from_path(tex_path)

        if not family:
            node_short = strip_ns(node).upper()
            family = extract_texture_family_from_path(node_short)

        if not family:
            continue

        data.setdefault(family, [])
        data[family].append(node)

    return data


def scan_qdm_materials():
    data = {}
    for node in cmds.ls(materials=True, long=True) or []:
        short = strip_ns(short_name(node)).upper()
        if short.startswith("QDM_"):
            data.setdefault(short, []).append(node)
    return data


def scan_reused_assets(scan_all=True, include_kits=True, current_asset=None):
    reused = {}
    kits = {}

    if scan_all:
        nodes = cmds.ls(type="transform", long=True) or []
    else:
        nodes = cmds.ls(assemblies=True, long=True) or []

    for node in nodes:
        main_name = extract_main_name_from_string(node)
        if main_name:
            if is_probable_texture_name(main_name):
                continue
            if is_excluded_reused_asset_name(main_name):
                continue
            if current_asset and main_name == current_asset:
                continue
            reused.setdefault(main_name, []).append(node)
            continue

        if include_kits:
            kit_name = extract_kit_name_from_string(node)
            if kit_name:
                if is_probable_texture_name(kit_name):
                    continue
                if current_asset and kit_name == current_asset:
                    continue
                kits.setdefault(kit_name, []).append(node)
                continue

    shader_reused = scan_reused_assets_from_shaders(nodes, current_asset=current_asset)
    for asset_name, vals in shader_reused.items():
        reused.setdefault(asset_name, [])
        reused[asset_name].extend(vals)
        reused[asset_name] = list(set(reused[asset_name]))

    return reused, kits


def scan_reused_assets_from_shaders(nodes, current_asset=None):
    reused = {}
    mesh_to_owner = {}

    for node in nodes:
        try:
            for mesh in cmds.listRelatives(node, ad=True, type="mesh", fullPath=True) or []:
                mesh_to_owner[mesh] = node
        except Exception:
            pass

    for mesh, owner in mesh_to_owner.items():
        for sg in shading_engines_on_mesh(mesh):
            shader = surface_shader_from_sg(sg)
            if not shader:
                continue

            asset_name = extract_asset_name_from_qds_shader_name(shader)
            if not asset_name:
                continue
            if is_excluded_reused_asset_name(asset_name):
                continue
            if current_asset and asset_name == current_asset:
                continue

            reused.setdefault(asset_name, []).append(owner)

    return reused


def meshes_under(nodes):
    meshes = []
    for n in nodes:
        try:
            meshes.extend(cmds.listRelatives(n, ad=True, type="mesh", fullPath=True) or [])
        except Exception:
            pass
    return list(set(meshes))


def shading_engines_on_mesh(mesh):
    try:
        return list(set(cmds.listConnections(mesh, type="shadingEngine") or []))
    except Exception:
        return []


def surface_shader_from_sg(sg):
    try:
        shaders = cmds.listConnections(sg + ".surfaceShader", s=True, d=False) or []
        return shaders[0] if shaders else None
    except Exception:
        return None


def all_qdm_candidates():
    out = []
    for n in cmds.ls() or []:
        sn = strip_ns(n)
        if sn.startswith("QDM_"):
            try:
                if cmds.attributeQuery("outColor", node=n, exists=True):
                    out.append(n)
            except Exception:
                pass
    return sorted(out, key=lambda x: strip_ns(x).lower())


def strip_trailing_digits(name):
    return re.sub(r"\d+$", "", name)


def find_qdm_from_qds(qds_name):
    short_qds = strip_ns(qds_name)
    if not short_qds.startswith("QDS_"):
        return None

    target = "QDM_" + short_qds[4:]
    target_low = target.lower()
    candidates = all_qdm_candidates()

    for n in candidates:
        if strip_ns(n) == target:
            return n

    for n in candidates:
        if strip_ns(n).lower() == target_low:
            return n

    numbered = []
    pattern = re.compile(r"^" + re.escape(target) + r"(\d+)$", re.IGNORECASE)
    for n in candidates:
        sn = strip_ns(n)
        m = pattern.match(sn)
        if m:
            numbered.append((int(m.group(1)), n))
    if numbered:
        numbered.sort(key=lambda x: x[0])
        return numbered[0][1]

    stripped_target = strip_trailing_digits(target).lower()
    soft = []
    for n in candidates:
        sn = strip_ns(n)
        if strip_trailing_digits(sn).lower() == stripped_target:
            suffix = re.search(r"(\d+)$", sn)
            idx = int(suffix.group(1)) if suffix else 0
            soft.append((idx, n))
    if soft:
        soft.sort(key=lambda x: x[0])
        return soft[0][1]

    return None


def find_or_create_sg(shader):
    connected = cmds.listConnections(shader, s=False, d=True, type="shadingEngine") or []
    if connected:
        return connected[0]

    base = strip_ns(shader) + "SG"
    sg = base
    i = 1
    while cmds.objExists(sg):
        sg = "{}_{}".format(base, i)
        i += 1

    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=sg)
    cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
    return sg


def assign_shader(mesh, shader):
    sg = find_or_create_sg(shader)
    cmds.sets(mesh, e=True, forceElement=sg)


def build_catalog_candidates(asset_name, category, prefixes):
    asset_name = asset_name.upper().strip()
    candidates = [asset_name]

    if re.match(r"^[A-Z]{3,4}_", asset_name):
        return candidates

    ordered = list(prefixes)
    if category.lower() == "props" and "ACC" in ordered:
        ordered = ["ACC"] + [x for x in ordered if x != "ACC"]

    for prefix in ordered:
        candidates.append("{}_{}".format(prefix.upper(), asset_name))

    out = []
    seen = set()
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


class AssetTree(QtWidgets.QTreeWidget):
    doubleClickedSignal = QtCore.Signal(str)
    itemDoubleClickedSignal = QtCore.Signal(object)

    def __init__(self, parent=None):
        super(AssetTree, self).__init__(parent)
        self.setColumnCount(2)
        self.setHeaderLabels(["Name", "Items"])
        self.setAlternatingRowColors(True)
        self.setRootIsDecorated(False)
        self.setSortingEnabled(True)
        self.sortByColumn(0, QtCore.Qt.AscendingOrder)
        self.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.setColumnWidth(1, 70)
        self.headerItem().setToolTip(1, "Number of Maya nodes detected for this entry.")

    def _on_item_double_clicked(self, item, column):
        self.doubleClickedSignal.emit(item.text(0))
        self.itemDoubleClickedSignal.emit(item)

    def set_data(self, data_dict):
        self.clear()
        for name in sorted(data_dict.keys()):
            count = len(data_dict[name])
            item = QtWidgets.QTreeWidgetItem([name, str(count)])
            item.setTextAlignment(1, QtCore.Qt.AlignCenter)
            self.addTopLevelItem(item)
        self.resizeColumnToContents(0)

    def selected_names(self):
        return [item.text(0) for item in self.selectedItems()]


class ExternalAssetScannerUI(QtWidgets.QDialog):
    def __init__(self, parent=maya_main_window()):
        super(ExternalAssetScannerUI, self).__init__(parent)

        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle("External Asset Scanner")
        self.setMinimumSize(760, 620)
        self.resize(820, 680)

        self.data_reused = {}
        self.data_kits = {}
        self.data_textures = {}
        self.last_shader_assignments = []
        self.shader_assignment_history = []
        self.last_failed_loads = []

        self.current_asset = current_asset_from_scene()
        self.current_category = current_asset_category_from_scene()

        self._build_ui()
        self._apply_style()
        self._reset_ui_before_first_scan()

    def _apply_style(self):
        font = QtGui.QFont("Segoe UI", 9)
        self.setFont(font)

        self.setStyleSheet("""
        QDialog {
            background-color: #2b2b2b;
            color: #d8d8d8;
        }
        QLabel {
            color: #d8d8d8;
        }
        QLineEdit, QPlainTextEdit, QTreeWidget, QComboBox {
            background-color: #202020;
            color: #e6e6e6;
            border: 1px solid #4a4a4a;
            border-radius: 4px;
            padding: 4px;
        }
        QPushButton {
            background-color: #3a3a3a;
            color: #f0f0f0;
            border: 1px solid #555555;
            border-radius: 4px;
            padding: 6px 10px;
            min-height: 24px;
        }
        QPushButton:hover {
            background-color: #4a4a4a;
        }
        QPushButton:disabled {
            color: #7c7c7c;
            background-color: #2d2d2d;
        }
        QTabWidget::pane {
            border: 1px solid #4a4a4a;
            top: -1px;
        }
        QTabBar::tab {
            background: #353535;
            color: #d8d8d8;
            padding: 8px 14px;
            border: 1px solid #4a4a4a;
            border-bottom: none;
            min-width: 120px;
        }
        QTabBar::tab:selected {
            background: #202020;
        }
        QHeaderView::section {
            background-color: #353535;
            color: #e0e0e0;
            border: 1px solid #4a4a4a;
            padding: 4px;
        }
        """)

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        top = QtWidgets.QHBoxLayout()

        self.scene_edit = QtWidgets.QLineEdit()
        self.scene_edit.setReadOnly(True)

        self.current_asset_edit = QtWidgets.QLineEdit()
        self.current_asset_edit.setReadOnly(True)

        self.category_edit = QtWidgets.QLineEdit()
        self.category_edit.setText(self.current_category)

        self.scan_all_cb = QtWidgets.QCheckBox("All Scene")
        self.scan_all_cb.setChecked(True)

        self.kit_cb = QtWidgets.QCheckBox("Include KIT")
        self.kit_cb.setChecked(True)

        self.skip_existing_cb = QtWidgets.QCheckBox("Skip If In Scene")
        self.skip_existing_cb.setChecked(True)
        self.skip_existing_cb.setToolTip(
            "When enabled, loading is skipped for asset names already detected in outliner."
        )
        self.skip_existing_cb.toggled.connect(self._on_skip_existing_toggled)

        self.force_import_cb = QtWidgets.QCheckBox("Force Import")
        self.force_import_cb.setChecked(False)
        self.force_import_cb.setToolTip(
            "When checked, import even if the asset appears to already exist in scene."
        )
        self.force_import_cb.toggled.connect(self._on_force_import_toggled)

        self.scan_btn = QtWidgets.QPushButton("Scan")
        self.scan_btn.clicked.connect(self._scan)

        top.addWidget(self.scene_edit, 5)
        top.addWidget(self.current_asset_edit, 2)
        top.addWidget(self.category_edit, 1)
        top.addWidget(self.scan_all_cb)
        top.addWidget(self.kit_cb)
        top.addWidget(self.skip_existing_cb)
        top.addWidget(self.force_import_cb)
        top.addWidget(self.scan_btn)

        root.addLayout(top)

        prefix_row = QtWidgets.QHBoxLayout()
        prefix_row.addWidget(QtWidgets.QLabel("Prefixes"))
        self.prefixes_edit = QtWidgets.QLineEdit("ACC, ARC, QDD, AGRA, RELIC")
        prefix_row.addWidget(self.prefixes_edit)
        root.addLayout(prefix_row)

        self.hint_label = QtWidgets.QLabel(
            "Click Scan to populate lists. Texture tab lists texture families and all QDM_* materials found in scene."
        )
        self.hint_label.setWordWrap(True)
        root.addWidget(self.hint_label)

        self.tabs = QtWidgets.QTabWidget()
        self.tree_reused = AssetTree()
        self.tree_kit = AssetTree()
        self.tree_texture = AssetTree()

        self.tree_reused.doubleClickedSignal.connect(self._copy_name)
        self.tree_kit.doubleClickedSignal.connect(self._copy_name)
        self.tree_texture.doubleClickedSignal.connect(self._copy_name)
        self.tree_texture.itemDoubleClickedSignal.connect(self._on_texture_item_double_clicked)
        self.tree_reused.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self.tree_kit.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self.tree_texture.itemSelectionChanged.connect(self._on_tree_selection_changed)

        self.tabs.addTab(self.tree_reused, "Reused Assets")
        self.tabs.addTab(self.tree_kit, "KIT")
        self.tabs.addTab(self.tree_texture, "Textures")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        root.addWidget(self.tabs, 1)

        row_a = QtWidgets.QHBoxLayout()

        row_a.addWidget(QtWidgets.QLabel("Asset Name"))
        self.asset_name_edit = QtWidgets.QLineEdit()
        self.asset_name_edit.setPlaceholderText("Click from list or paste/type an asset name")

        self.copy_btn = QtWidgets.QPushButton("Copy")
        self.import_btn = QtWidgets.QPushButton("Import")
        self.select_btn = QtWidgets.QPushButton("Select Nodes")
        self.detail_btn = QtWidgets.QPushButton("Details")
        self.select_current_btn = QtWidgets.QPushButton("Select Current Asset")
        self.select_current_btn.setEnabled(False)

        self.copy_btn.clicked.connect(self.copy_asset_name_field)
        self.import_btn.clicked.connect(self.import_from_asset_name_field)
        self.select_btn.clicked.connect(self.select_selected_nodes)
        self.detail_btn.clicked.connect(self.print_details)
        self.select_current_btn.clicked.connect(self.select_current_asset_nodes)

        row_a.addWidget(self.asset_name_edit, 1)
        row_a.addWidget(self.copy_btn)
        row_a.addWidget(self.import_btn)
        row_a.addWidget(self.select_btn)
        row_a.addWidget(self.detail_btn)
        row_a.addWidget(self.select_current_btn)

        root.addLayout(row_a)

        row_b = QtWidgets.QHBoxLayout()

        self.load_list_btn = QtWidgets.QPushButton("Load From List")
        self.load_sel_btn = QtWidgets.QPushButton("Load From Maya Selection")
        self.load_current_btn = QtWidgets.QPushButton("Load Current Asset")
        self.select_failed_btn = QtWidgets.QPushButton("Select Failed In List")
        self.retry_failed_btn = QtWidgets.QPushButton("Retry Failed")
        self.load_current_btn.setEnabled(False)
        self.select_failed_btn.setEnabled(False)
        self.retry_failed_btn.setEnabled(False)

        self.load_list_btn.clicked.connect(self.load_from_list)
        self.load_sel_btn.clicked.connect(self.load_from_maya_selection)
        self.load_current_btn.clicked.connect(self.load_current_asset)
        self.select_failed_btn.clicked.connect(self.select_failed_assets_in_ui)
        self.retry_failed_btn.clicked.connect(self.retry_failed_assets)

        row_b.addWidget(self.load_list_btn)
        row_b.addWidget(self.load_sel_btn)
        row_b.addWidget(self.load_current_btn)
        row_b.addWidget(self.select_failed_btn)
        row_b.addWidget(self.retry_failed_btn)

        root.addLayout(row_b)

        row_c = QtWidgets.QHBoxLayout()

        row_c.addWidget(QtWidgets.QLabel("Shader Scope"))
        self.shader_scope = QtWidgets.QComboBox()
        self.shader_scope.addItems([
            "Auto",
            "Current Asset",
            "List Selection",
            "All Reused Assets",
            "Maya Selection",
        ])

        self.shader_maya_sel_btn = QtWidgets.QPushButton("Assign From Maya Selection")
        self.shader_assign_btn = QtWidgets.QPushButton("Auto Assign Shaders")
        self.shader_revert_btn = QtWidgets.QPushButton("Revert Last Assign")
        self.shader_revert_btn.setEnabled(False)

        self.shader_maya_sel_btn.clicked.connect(self.assign_shaders_from_maya_selection)
        self.shader_assign_btn.clicked.connect(self.auto_assign_shaders)
        self.shader_revert_btn.clicked.connect(self.revert_last_auto_assign)

        row_c.addWidget(self.shader_scope, 1)
        row_c.addWidget(self.shader_maya_sel_btn)
        row_c.addWidget(self.shader_assign_btn)
        row_c.addWidget(self.shader_revert_btn)

        root.addLayout(row_c)

        self.log_box = QtWidgets.QGroupBox("Log")
        self.log_box.setCheckable(True)
        self.log_box.setChecked(False)
        self.log_box.toggled.connect(self._on_log_toggled)
        log_layout = QtWidgets.QVBoxLayout(self.log_box)
        log_layout.setContentsMargins(6, 12, 6, 6)

        self.report = QtWidgets.QPlainTextEdit()
        self.report.setReadOnly(True)
        log_layout.addWidget(self.report, 1)
        root.addWidget(self.log_box, 1)

        self.status_label = QtWidgets.QLabel("")
        root.addWidget(self.status_label)
        self._on_log_toggled(self.log_box.isChecked())
        self._on_force_import_toggled(self.force_import_cb.isChecked())

    def _reset_ui_before_first_scan(self):
        self.current_asset = current_asset_from_scene()
        self.current_category = current_asset_category_from_scene()
        self.scene_edit.setText(scene_path())
        self.current_asset_edit.setText(self.current_asset or "")
        self.category_edit.setText(self.current_category)
        self.tree_reused.clear()
        self.tree_kit.clear()
        self.tree_texture.clear()
        self.tabs.setTabText(0, "Reused Assets (0)")
        self.tabs.setTabText(1, "KIT (0)")
        self.tabs.setTabText(2, "Textures (0)")
        self._set_status("Ready. Click Scan to analyze scene.")

    def _log(self, text=""):
        self.report.appendPlainText(text)
        sb = self.report.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _set_status(self, text):
        self.status_label.setText(text)

    def _on_log_toggled(self, checked):
        self.report.setVisible(checked)
        if checked:
            self.log_box.setMaximumHeight(16777215)
        else:
            self.log_box.setMaximumHeight(34)

    def _on_force_import_toggled(self, checked):
        if checked:
            self.skip_existing_cb.setChecked(False)

    def _on_skip_existing_toggled(self, checked):
        if checked and self.force_import_cb.isChecked():
            self.force_import_cb.setChecked(False)

    def _prefixes(self):
        raw = self.prefixes_edit.text().strip()
        if not raw:
            return list(DEFAULT_PREFIXES)
        vals = [x.strip().upper() for x in raw.split(",") if x.strip()]
        return vals or list(DEFAULT_PREFIXES)

    def current_tree(self):
        idx = self.tabs.currentIndex()
        if idx == 0:
            return self.tree_reused, self.data_reused, "reused"
        if idx == 1:
            return self.tree_kit, self.data_kits, "kit"
        return self.tree_texture, self.data_textures, "texture"

    def selected_names(self):
        tree, data, kind = self.current_tree()
        return tree.selected_names()

    def _normalized_asset_name_from_field(self):
        return self.asset_name_edit.text().strip().upper()

    def _set_asset_name_field(self, name):
        if not name:
            return
        self.asset_name_edit.setText(name)

    def _on_tree_selection_changed(self):
        names = self.selected_names()
        if names:
            self._set_asset_name_field(names[0])

    def _on_tab_changed(self, index):
        self._on_tree_selection_changed()

    def _copy_name(self, name):
        copy_to_clipboard(name)
        self._set_asset_name_field(name)
        self._set_status("Copied: {}".format(name))

    def _on_texture_item_double_clicked(self, item):
        if self.tabs.currentIndex() != 2:
            return
        if not item:
            return
        item.setSelected(True)
        self.select_selected_nodes()

    def copy_asset_name_field(self):
        name = self._normalized_asset_name_from_field()
        if not name:
            cmds.warning("Asset Name field is empty.")
            return
        self._copy_name(name)

    def import_from_asset_name_field(self):
        name = self._normalized_asset_name_from_field()
        if not name:
            cmds.warning("Asset Name field is empty.")
            return
        category = self.category_edit.text().strip() or "Props"
        self._run_load_batch([name], category, "Import From Asset Name")

    def _scan(self):
        self.current_asset = current_asset_from_scene()
        self.current_category = current_asset_category_from_scene()

        self.scene_edit.setText(scene_path())
        self.current_asset_edit.setText(self.current_asset or "")
        self.category_edit.setText(self.current_category)

        self.data_reused, self.data_kits = scan_reused_assets(
            scan_all=self.scan_all_cb.isChecked(),
            include_kits=self.kit_cb.isChecked(),
            current_asset=self.current_asset
        )
        self.data_textures = scan_textures()
        qdm_data = scan_qdm_materials()
        for key, nodes in qdm_data.items():
            self.data_textures.setdefault(key, [])
            self.data_textures[key].extend(nodes)
            self.data_textures[key] = list(set(self.data_textures[key]))

        self.tree_reused.set_data(self.data_reused)
        self.tree_kit.set_data(self.data_kits)
        self.tree_texture.set_data(self.data_textures)
        self.tabs.setTabText(0, "Reused Assets ({})".format(len(self.data_reused)))
        self.tabs.setTabText(1, "KIT ({})".format(len(self.data_kits)))
        self.tabs.setTabText(2, "Textures ({})".format(len(self.data_textures)))

        current_in_scene = bool(self.current_asset and self._find_nodes_for_asset_name(self.current_asset))
        self.select_current_btn.setEnabled(current_in_scene)
        self.load_current_btn.setEnabled(bool(self.current_asset))

        self._set_status(
            "Reused:{} | KIT:{} | Tex:{} | Current:{} | InScene:{}".format(
                len(self.data_reused),
                len(self.data_kits),
                len(self.data_textures),
                self.current_asset or "-",
                "Yes" if current_in_scene else "No"
            )
        )

        self._log("=" * 80)
        self._log("SCAN")
        self._log("Scene   : {}".format(scene_path()))
        self._log("Current : {}".format(self.current_asset or "-"))
        self._log("Category: {}".format(self.current_category))
        self._log("Reused  : {}".format(len(self.data_reused)))
        self._log("KIT     : {}".format(len(self.data_kits)))
        self._log("Texture : {}".format(len(self.data_textures)))
        self._log("=" * 80)

    def _find_nodes_for_asset_name(self, asset_name):
        nodes = []
        asset_name = (asset_name or "").upper()

        for k, vals in self.data_reused.items():
            if k == asset_name:
                nodes.extend(vals)

        for k, vals in self.data_kits.items():
            if k == asset_name:
                nodes.extend(vals)

        if not nodes:
            raw_nodes = cmds.ls(type="transform", long=True) or []
            for node in raw_nodes:
                detected = extract_main_name_from_string(node) or extract_kit_name_from_string(node)
                if detected == asset_name:
                    nodes.append(node)

        return list(set(nodes))

    def select_current_asset_nodes(self):
        if not self.current_asset:
            cmds.warning("No current asset found from scene path.")
            return

        nodes = self._find_nodes_for_asset_name(self.current_asset)
        if not nodes:
            cmds.warning("Current asset not found in scene.")
            return

        cmds.select(nodes, r=True)
        self._set_status("Selected current asset: {} [{} node(s)]".format(self.current_asset, len(nodes)))
        self._log("SELECT CURRENT {} [{} node(s)]".format(self.current_asset, len(nodes)))

    def _selected_scene_nodes_from_current_tab(self):
        tree, data, kind = self.current_tree()
        names = tree.selected_names()
        nodes = []
        for name in names:
            nodes.extend(data.get(name, []))
        return list(set(nodes))

    def select_selected_nodes(self):
        tree, data, kind = self.current_tree()
        if kind == "texture":
            names = tree.selected_names()
            nodes = []
            for name in names:
                nodes.extend(data.get(name, []))
            nodes = list(set(nodes))
            if not nodes:
                cmds.warning("No texture nodes found.")
                return
            cmds.select(nodes, r=True)
            self._set_status("{} texture node(s) selected".format(len(nodes)))
            self._log("SELECT {} texture node(s)".format(len(nodes)))
            return

        nodes = self._selected_scene_nodes_from_current_tab()
        if not nodes:
            cmds.warning("No nodes found from current selection.")
            return

        cmds.select(nodes, r=True)
        self._set_status("{} node(s) selected".format(len(nodes)))
        self._log("SELECT {} node(s)".format(len(nodes)))

    def print_details(self):
        tree, data, kind = self.current_tree()
        names = tree.selected_names()
        if not names:
            cmds.warning("Nothing selected.")
            return

        self._log("-" * 80)
        self._log("DETAILS [{}]".format(kind.upper()))
        for name in names:
            vals = data.get(name, [])
            self._log("{}  [{}]".format(name, len(vals)))
            for v in vals:
                self._log("    {}".format(v))
        self._log("-" * 80)

    def _scene_known_asset_names(self):
        out = set(self.data_reused.keys())
        out.update(self.data_kits.keys())
        if self.current_asset:
            out.add(self.current_asset)
        return out

    def _scan_asset_names_from_outliner(self):
        names = set()
        for node in cmds.ls(type="transform", long=True) or []:
            detected = extract_main_name_from_string(node) or extract_kit_name_from_string(node)
            if detected:
                names.add(detected.upper())
        return names

    def _is_asset_already_in_scene(self, asset_name):
        asset_name = asset_name.upper()
        existing = {x.upper() for x in self._scene_known_asset_names()}
        existing.update(self._scan_asset_names_from_outliner())
        return asset_name in existing

    def _do_load_one(self, asset_name, category):
        try:
            from qdTools.qdAssembly.qdUtils.qdLoad import QDLoad
        except Exception:
            raise RuntimeError("Cannot import QDLoad")

        candidates = build_catalog_candidates(asset_name, category, self._prefixes())

        self._log("LOAD {}".format(asset_name))
        for candidate in candidates:
            try:
                self._log("    try by_catalog({}, {})".format(repr(candidate), repr(category)))
                obj = QDLoad.by_catalog(candidate, category)
                if obj is None:
                    raise RuntimeError("QDLoad.by_catalog returned None")
                try:
                    obj.update_status(b_recursive=True)
                except Exception as e:
                    self._log("    WARN update_status failed for {} -> {}".format(candidate, e))
                self._log("    OK {}".format(candidate))
                return True, candidate
            except Exception as e:
                self._log("    FAIL {} -> {}".format(candidate, e))

        return False, None

    def _run_load_batch(self, names, category, title):
        if not names:
            cmds.warning("Nothing to load.")
            return

        ok = []
        skipped = []
        failed = []
        self.last_failed_loads = []

        progress = QtWidgets.QProgressDialog("Loading...", "Cancel", 0, len(names), self)
        progress.setWindowTitle(title)
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setMinimumWidth(380)
        progress.setMaximumWidth(460)

        for i, name in enumerate(names):
            progress.setValue(i)
            progress.setLabelText("Loading {}".format(name))
            QtWidgets.QApplication.processEvents()
            time.sleep(0.01)

            if progress.wasCanceled():
                break

            should_skip_existing = self.skip_existing_cb.isChecked() and not self.force_import_cb.isChecked()
            if should_skip_existing and self._is_asset_already_in_scene(name):
                skipped.append(name)
                self._log("SKIP {} (already in scene)".format(name))
                continue

            try:
                result, used = self._do_load_one(name, category)
                if result:
                    ok.append((name, used))
                else:
                    failed.append((name, "No candidate worked"))
            except Exception as e:
                failed.append((name, str(e)))
                self._log("CRASH-PROTECT {} -> {}".format(name, e))

            try:
                cmds.refresh(force=True)
            except Exception:
                pass
            QtWidgets.QApplication.processEvents()

        progress.setValue(len(names))

        self._log("=" * 80)
        self._log(title.upper())
        self._log("OK [{}]".format(len(ok)))
        for src, used in ok:
            self._log("    {} -> {}".format(src, used))
        self._log("SKIP [{}]".format(len(skipped)))
        for s in skipped:
            self._log("    {}".format(s))
        self._log("FAIL [{}]".format(len(failed)))
        for src, err in failed:
            self._log("    {} -> {}".format(src, err))
        self._log("=" * 80)

        self.last_failed_loads = [src for src, _err in failed]
        self.select_failed_btn.setEnabled(bool(self.last_failed_loads))
        self.retry_failed_btn.setEnabled(bool(self.last_failed_loads))
        self._scan()

    def select_failed_assets_in_ui(self):
        if not self.last_failed_loads:
            cmds.warning("No failed assets from previous load.")
            return

        tree, data, kind = self.current_tree()
        if kind == "texture":
            self.tabs.setCurrentIndex(0)
            tree, data, kind = self.current_tree()

        wanted = set(x.upper() for x in self.last_failed_loads)
        tree.clearSelection()
        selected = 0
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            if item.text(0).upper() in wanted:
                item.setSelected(True)
                selected += 1

        self._set_status("Failed assets: {} (found in list: {})".format(len(self.last_failed_loads), selected))
        self._log("SELECT FAILED [{}], found in current list [{}]".format(len(self.last_failed_loads), selected))
        if self.last_failed_loads:
            self.asset_name_edit.setText(", ".join(self.last_failed_loads))

    def retry_failed_assets(self):
        if not self.last_failed_loads:
            cmds.warning("No failed assets to retry.")
            return
        category = self.category_edit.text().strip() or "Props"
        self._run_load_batch(list(self.last_failed_loads), category, "Retry Failed Assets")

    def load_from_list(self):
        tree, data, kind = self.current_tree()
        category = self.category_edit.text().strip() or "Props"

        if kind == "texture":
            cmds.warning("Texture tab is scan only.")
            return

        names = tree.selected_names()
        if not names:
            names = sorted(data.keys())

        self._run_load_batch(names, category, "Load From List")

    def load_from_maya_selection(self):
        try:
            sel = cmds.ls(selection=True, long=True) or []
            category = self.category_edit.text().strip() or "Props"

            if not sel:
                cmds.warning("Nothing selected in Maya.")
                return

            found = []
            seen = set()

            for node in sel:
                try:
                    detected = extract_main_name_from_string(node) or extract_kit_name_from_string(node)
                except Exception:
                    detected = None
                if detected and detected not in seen:
                    seen.add(detected)
                    found.append(detected)

            if not found:
                cmds.warning("No recognizable asset found in Maya selection.")
                return

            self._run_load_batch(found, category, "Load From Maya Selection")
        except Exception as exc:
            self._log("ERROR load_from_maya_selection -> {}".format(exc))
            self._log(traceback.format_exc())
            cmds.warning("Load From Maya Selection failed. Check scanner log.")

    def load_current_asset(self):
        if not self.current_asset:
            cmds.warning("No current asset found from scene path.")
            return
        category = self.category_edit.text().strip() or "Props"
        self._run_load_batch([self.current_asset], category, "Load Current Asset")

    def assign_shaders_from_maya_selection(self):
        prev = self.shader_scope.currentText()
        self.shader_scope.setCurrentText("Maya Selection")
        self._log("[Shader] Scope switched: {} -> Maya Selection".format(prev))
        self.auto_assign_shaders()

    def _shader_target_nodes(self):
        mode = self.shader_scope.currentText()

        if mode == "Current Asset":
            if self.current_asset:
                return self._find_nodes_for_asset_name(self.current_asset)
            return []

        if mode == "List Selection":
            return self._selected_scene_nodes_from_current_tab()

        if mode == "All Reused Assets":
            nodes = []
            for vals in self.data_reused.values():
                nodes.extend(vals)
            for vals in self.data_kits.values():
                nodes.extend(vals)
            return list(set(nodes))

        if mode == "Maya Selection":
            return cmds.ls(selection=True, long=True) or []

        # Auto
        if self.current_asset:
            cur_nodes = self._find_nodes_for_asset_name(self.current_asset)
            if cur_nodes:
                return cur_nodes

        nodes = []
        for vals in self.data_reused.values():
            nodes.extend(vals)
        for vals in self.data_kits.values():
            nodes.extend(vals)
        if nodes:
            return list(set(nodes))

        return cmds.ls(selection=True, long=True) or []

    def auto_assign_shaders(self):
        nodes = self._shader_target_nodes()
        if not nodes:
            cmds.warning("No target nodes found for shader auto-assign.")
            self._log("[Shader] No target nodes found.")
            return

        meshes = meshes_under(nodes)
        converted = []
        missing = []
        seen_missing = set()

        self._log("=" * 80)
        self._log("AUTO ASSIGN SHADERS")
        self._log("Target nodes : {}".format(len(nodes)))
        self._log("Meshes       : {}".format(len(meshes)))

        for mesh in meshes:
            mesh_assigned = False
            for sg in shading_engines_on_mesh(mesh):
                shader = surface_shader_from_sg(sg)
                if not shader:
                    continue

                short_shader = strip_ns(shader)
                if not short_shader.startswith("QDS_"):
                    continue

                qdm = find_qdm_from_qds(shader)

                if qdm:
                    try:
                        previous_sgs = shading_engines_on_mesh(mesh)
                        assign_shader(mesh, qdm)
                        converted.append((mesh, shader, qdm, previous_sgs))
                        mesh_assigned = True
                        break
                    except Exception as e:
                        key = "{} -> ASSIGN FAIL {}".format(shader, e)
                        if key not in seen_missing:
                            seen_missing.add(key)
                            missing.append((shader, "ASSIGN FAIL: {}".format(e)))
                else:
                    if short_shader not in seen_missing:
                        seen_missing.add(short_shader)
                        missing.append((shader, "QDM not found"))
            if mesh_assigned:
                continue

        self._log("CONVERTED [{}]".format(len(converted)))
        for mesh, qds, qdm, _previous_sgs in converted:
            self._log("    {} : {} -> {}".format(short_name(mesh), qds, qdm))

        self._log("MISSING [{}]".format(len(missing)))
        for qds, msg in missing:
            self._log("    {} -> {}".format(qds, msg))

        self._log("=" * 80)

        if not converted and not missing:
            cmds.warning("No QDS shader found on target meshes.")
            return

        self.last_shader_assignments = [
            {"mesh": mesh, "sgs": list(previous_sgs)}
            for mesh, _qds, _qdm, previous_sgs in converted
            if previous_sgs
        ]
        if self.last_shader_assignments:
            self.shader_assignment_history.append(list(self.last_shader_assignments))
            if len(self.shader_assignment_history) > 20:
                self.shader_assignment_history = self.shader_assignment_history[-20:]
        self.shader_revert_btn.setEnabled(bool(self.last_shader_assignments))

    def revert_last_auto_assign(self):
        if not self.shader_assignment_history:
            cmds.warning("No previous auto-assign operation to revert.")
            return
        self.last_shader_assignments = self.shader_assignment_history.pop()

        restored = 0
        failed = 0
        self._log("=" * 80)
        self._log("REVERT LAST AUTO ASSIGN")

        for entry in self.last_shader_assignments:
            mesh = entry.get("mesh")
            sgs = entry.get("sgs") or []
            if not mesh or not sgs:
                continue

            try:
                cmds.sets(mesh, e=True, forceElement=sgs[0])
                restored += 1
                self._log("    RESTORE {} -> {}".format(short_name(mesh), sgs[0]))
            except Exception as e:
                failed += 1
                self._log("    FAIL {} -> {}".format(mesh, e))

        self._log("RESTORED [{}]".format(restored))
        self._log("FAILED   [{}]".format(failed))
        self._log("=" * 80)

        self.last_shader_assignments = []
        self.shader_revert_btn.setEnabled(bool(self.shader_assignment_history))

    def closeEvent(self, event):
        QtWidgets.QDialog.closeEvent(self, event)


def show_external_asset_scanner():
    parent = maya_main_window()
    if parent:
        for w in parent.findChildren(QtWidgets.QDialog, WINDOW_OBJECT_NAME):
            try:
                w.close()
                w.deleteLater()
            except Exception:
                pass

    ui = ExternalAssetScannerUI(parent=parent)
    ui.show()
    ui.raise_()
    ui.activateWindow()
    return ui


class ExternalAssetScanner(ExternalAssetScannerUI):
    pass


def launch():
    return show_external_asset_scanner()


scanner_ui = show_external_asset_scanner()
