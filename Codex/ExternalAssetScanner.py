# -*- coding: utf-8 -*-
"""
External Asset Scanner  v8  -  Maya 2022  (PySide2 / Qt)

Features:
- Auto-detect current asset from scene path (e.g. RELIC_DENDO_TEMPLE_B)
- Scan Standard / KIT / Orphan / Textures tabs
- Orphan tab: Standard assets with no known prefix (likely unclassified)
- Load assets:  QDLoad.by_catalog(name, category).update_status(b_recursive=True)
- Check texture existence in scene before load
- Auto-assign shaders: QDS_* / Blinn / Phong / Lambert  ->  QDM_*
  with namespace + numeric suffix support
- Detailed logs: OK / SKIP / FAIL per operation
"""

import re
import ast
import os

import maya.cmds as cmds
import maya.OpenMayaUI as omui

from PySide2 import QtWidgets, QtCore, QtGui
from PySide2.QtCore import Qt
from shiboken2 import wrapInstance


# ============================================================
#  CONFIG
# ============================================================

WINDOW_TITLE  = "External Asset Scanner  v8"
WINDOW_OBJ    = "ExtAssetScannerV8"

CACHE_MAIN    = "extAssetScanner_main_v8"
CACHE_KIT     = "extAssetScanner_kit_v8"
CACHE_ORPHAN  = "extAssetScanner_orphan_v8"

DEFAULT_PREFIXES = ["ACC", "ARC", "QDD", "AGRA", "RELIC"]

BLINN_PHONG_LAMBERT = {"blinn", "phong", "phongE", "lambert", "surfaceShader"}

# ============================================================
#  STYLE
# ============================================================

QSS = """
QWidget {
    background-color: #2b2b2b;
    color: #d4d4d4;
    font-family: 'Consolas', monospace;
    font-size: 12px;
}
QLabel { color: #aaaaaa; }
QLabel#title {
    font-size: 14px;
    font-weight: bold;
    color: #e8e8e8;
    padding: 4px 0;
}
QLabel#asset_badge {
    font-size: 11px;
    color: #7ec8e3;
    padding: 2px 6px;
    background: #1a3a4a;
    border-radius: 3px;
}
QLineEdit, QTextEdit {
    background-color: #1e1e1e;
    border: 1px solid #444;
    border-radius: 3px;
    padding: 3px 6px;
    color: #d4d4d4;
}
QTextEdit#log {
    font-family: 'Consolas', monospace;
    font-size: 11px;
    background-color: #191919;
    color: #c8c8c8;
}
QPushButton {
    background-color: #3c3c3c;
    border: 1px solid #555;
    border-radius: 4px;
    padding: 5px 14px;
    color: #d4d4d4;
}
QPushButton:hover  { background-color: #4a4a4a; border-color: #888; }
QPushButton:pressed { background-color: #282828; }
QPushButton#scan   { background-color: #3a3a3a; border-color: #666; }
QPushButton#load   { background-color: #1e3d1e; border-color: #3a7a3a; color: #88cc88; }
QPushButton#load:hover { background-color: #265226; }
QPushButton#loadmaya { background-color: #1a2e42; border-color: #2f6095; color: #7ab8e8; }
QPushButton#loadmaya:hover { background-color: #1e3a55; }
QPushButton#shader { background-color: #3d2510; border-color: #8a4d1a; color: #e8a060; }
QPushButton#shader:hover { background-color: #512e14; }
QPushButton#tex    { background-color: #2d1e3d; border-color: #6040a0; color: #b088e8; }
QPushButton#tex:hover { background-color: #3a2655; }
QTabWidget::pane  { border: 1px solid #444; background: #252525; }
QTabBar::tab {
    background: #333; color: #aaa;
    padding: 5px 16px; border-radius: 3px 3px 0 0;
    margin-right: 2px;
}
QTabBar::tab:selected { background: #252525; color: #e8e8e8; }
QListWidget {
    background-color: #1e1e1e;
    border: 1px solid #444;
    alternate-background-color: #222;
    outline: none;
}
QListWidget::item { padding: 2px 6px; }
QListWidget::item:selected { background-color: #2d4a6a; color: #ffffff; }
QListWidget::item:hover { background-color: #2a2a2a; }
QCheckBox { color: #aaaaaa; }
QCheckBox::indicator { width: 14px; height: 14px; }
QSplitter::handle { background: #444; }
QGroupBox {
    border: 1px solid #444;
    border-radius: 4px;
    margin-top: 10px;
    padding-top: 8px;
    font-size: 11px;
    color: #888;
}
QGroupBox::title { subcontrol-origin: margin; left: 8px; color: #aaa; }
QRadioButton { color: #aaaaaa; spacing: 6px; }
QRadioButton::indicator { width: 13px; height: 13px; }
QScrollBar:vertical {
    background: #222; width: 10px; border-radius: 5px;
}
QScrollBar::handle:vertical {
    background: #555; border-radius: 5px; min-height: 20px;
}
"""


# ============================================================
#  UTILS
# ============================================================

def maya_main_window():
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget)


def short_name(node):
    return node.split("|")[-1]


def strip_ns(name):
    return name.split(":")[-1]


def _safe_literal_eval(raw, fallback):
    try:
        return ast.literal_eval(raw)
    except Exception:
        return fallback


def detect_scene_asset():
    """
    Reads the current Maya scene path and extracts the asset name.
    Example: .../RELIC_DENDO_TEMPLE_B_BakeTextures... -> RELIC_DENDO_TEMPLE_B
    """
    path = cmds.file(q=True, sceneName=True) or ""
    if not path:
        return ""
    basename = os.path.splitext(os.path.basename(path))[0]
    parts = basename.split("_")
    upper = []
    for p in parts:
        if re.match(r"^[A-Z0-9]+$", p):
            upper.append(p)
        else:
            break
    if len(upper) >= 3:
        # stop after single-letter segment (e.g. _B)
        result = []
        for p in upper:
            result.append(p)
            if re.match(r"^[A-Z]$", p):
                return "_".join(result)
        return "_".join(upper)
    return basename


# ============================================================
#  DETECTION
# ============================================================

def extract_main_name(node_name):
    name = strip_ns(short_name(node_name))
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


def extract_kit_name(node_name):
    name = strip_ns(short_name(node_name))
    parts = name.split("_")
    upper = []
    for p in parts:
        if re.match(r"^[A-Z0-9]+$", p):
            upper.append(p)
        else:
            break
    if not upper or "KIT" not in upper or len(upper) < 3:
        return None
    if extract_main_name(name):
        return None
    return "_".join(upper)


def find_external_assets(scan_all=True, include_kits=True, known_prefixes=None):
    main    = {}
    kit     = {}
    orphan  = {}

    prefixes = [p.strip().upper() for p in (known_prefixes or DEFAULT_PREFIXES) if p.strip()]

    nodes = (cmds.ls(type="transform", long=True) if scan_all
             else cmds.ls(assemblies=True, long=True)) or []

    for node in nodes:
        main_name = extract_main_name(node)
        if main_name:
            # check if it starts with a known prefix
            has_prefix = any(main_name.upper().startswith(p + "_") for p in prefixes)
            if has_prefix:
                main.setdefault(main_name, []).append(node)
            else:
                orphan.setdefault(main_name, []).append(node)
            continue

        if include_kits:
            kit_name = extract_kit_name(node)
            if kit_name:
                kit.setdefault(kit_name, []).append(node)

    return main, kit, orphan


# ============================================================
#  CACHE
# ============================================================

def set_cache(main, kit, orphan):
    cmds.optionVar(sv=(CACHE_MAIN,   repr(main)))
    cmds.optionVar(sv=(CACHE_KIT,    repr(kit)))
    cmds.optionVar(sv=(CACHE_ORPHAN, repr(orphan)))


def get_cache_main():
    return _safe_literal_eval(cmds.optionVar(q=CACHE_MAIN), {}) if cmds.optionVar(exists=CACHE_MAIN) else {}


def get_cache_kit():
    return _safe_literal_eval(cmds.optionVar(q=CACHE_KIT), {}) if cmds.optionVar(exists=CACHE_KIT) else {}


def get_cache_orphan():
    return _safe_literal_eval(cmds.optionVar(q=CACHE_ORPHAN), {}) if cmds.optionVar(exists=CACHE_ORPHAN) else {}


# ============================================================
#  SHADER UTILS
# ============================================================

def _meshes_under(nodes):
    meshes = []
    for n in nodes:
        meshes.extend(cmds.listRelatives(n, ad=True, type="mesh", fullPath=True) or [])
    return list(set(meshes))


def _ses_on_mesh(mesh):
    return list(set(cmds.listConnections(mesh, type="shadingEngine") or []))


def _surface_shader(se):
    shaders = cmds.listConnections(se + ".surfaceShader", s=True, d=False) or []
    return shaders[0] if shaders else None


def _shader_type(shader):
    try:
        return cmds.nodeType(shader)
    except Exception:
        return ""


def _all_qdm_candidates():
    out = []
    for n in (cmds.ls() or []):
        s = strip_ns(n)
        if s.startswith("QDM_"):
            try:
                if cmds.attributeQuery("outColor", node=n, exists=True):
                    out.append(n)
            except Exception:
                pass
    return sorted(out, key=lambda x: strip_ns(x).lower())


def _strip_trailing_digits(name):
    return re.sub(r"\d+$", "", name)


def _find_qdm(qds_name):
    short_qds = strip_ns(qds_name)
    if not short_qds.startswith("QDS_"):
        return None
    target_short = "QDM_" + short_qds[4:]
    target_low   = target_short.lower()
    candidates   = _all_qdm_candidates()

    for n in candidates:
        if strip_ns(n) == target_short:
            return n
    for n in candidates:
        if strip_ns(n).lower() == target_low:
            return n

    numbered = []
    pattern = re.compile(r"^" + re.escape(target_short) + r"(\d+)$", re.IGNORECASE)
    for n in candidates:
        m = pattern.match(strip_ns(n))
        if m:
            numbered.append((int(m.group(1)), n))
    if numbered:
        return sorted(numbered)[0][1]

    stripped_target = _strip_trailing_digits(target_short).lower()
    soft = []
    for n in candidates:
        short_n = strip_ns(n)
        if _strip_trailing_digits(short_n).lower() == stripped_target:
            m2 = re.search(r"(\d+)$", short_n)
            soft.append((int(m2.group(1)) if m2 else 0, n))
    if soft:
        return sorted(soft)[0][1]

    return None


def _find_or_create_sg(shader):
    connected = cmds.listConnections(shader, s=False, d=True, type="shadingEngine") or []
    if connected:
        return connected[0]
    base = strip_ns(shader) + "SG"
    sg = base
    i = 1
    while cmds.objExists(sg):
        sg = "{}_{}".format(base, i); i += 1
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=sg)
    if cmds.attributeQuery("outColor", node=shader, exists=True):
        cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
    else:
        raise RuntimeError("No outColor on '{}'".format(shader))
    return sg


def _assign_shader(mesh, shader):
    sg = _find_or_create_sg(shader)
    cmds.sets(mesh, e=True, forceElement=sg)


# ============================================================
#  LOAD UTILS
# ============================================================

def build_catalog_candidates(asset_name, category, prefixes):
    candidates = [asset_name]
    if re.match(r"^[A-Z]{3,5}_", asset_name):
        return candidates
    ordered = (["ACC"] + [p for p in prefixes if p != "ACC"]
               if category.lower() == "props" and "ACC" in prefixes
               else prefixes)
    seen = {asset_name}
    for prefix in ordered:
        c = "{}_{}".format(prefix, asset_name)
        if c not in seen:
            seen.add(c); candidates.append(c)
    return candidates


def _do_load(asset_name, category, prefixes, log_fn):
    try:
        from qdTools.qdAssembly.qdUtils.qdLoad import QDLoad
    except ImportError:
        log_fn("  [FAIL] Cannot import QDLoad")
        return False
    candidates = build_catalog_candidates(asset_name, category, prefixes)
    last_error = None
    for catalog_name in candidates:
        try:
            QDLoad.by_catalog(catalog_name, category).update_status(b_recursive=True)
            log_fn("  [OK]   {} -> {}".format(asset_name, catalog_name))
            return True
        except Exception as e:
            last_error = e
            log_fn("  [SKIP] {} -> {} : {}".format(asset_name, catalog_name, e))
    log_fn("  [FAIL] {} : {}".format(asset_name, last_error))
    return False


# ============================================================
#  MAIN WINDOW
# ============================================================

class ExternalAssetScanner(QtWidgets.QDialog):

    def __init__(self, parent=maya_main_window()):
        super(ExternalAssetScanner, self).__init__(parent)
        self.setObjectName(WINDOW_OBJ)
        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumSize(760, 860)
        self.setStyleSheet(QSS)
        self.setWindowFlags(self.windowFlags() | Qt.Window)

        self._cache_main   = {}
        self._cache_kit    = {}
        self._cache_orphan = {}

        self._build_ui()
        self._refresh()

    # ----------------------------------------------------------
    #  UI BUILD
    # ----------------------------------------------------------

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        # --- Header ---
        hdr = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("External Asset Scanner")
        title.setObjectName("title")
        hdr.addWidget(title)
        hdr.addStretch()

        self.lbl_scene_asset = QtWidgets.QLabel("")
        self.lbl_scene_asset.setObjectName("asset_badge")
        self.lbl_scene_asset.setVisible(False)
        hdr.addWidget(self.lbl_scene_asset)
        root.addLayout(hdr)

        self.lbl_summary = QtWidgets.QLabel("Standard: 0   KIT: 0   Orphans: 0")
        self.lbl_summary.setStyleSheet("color:#888; font-size:11px;")
        root.addWidget(self.lbl_summary)

        # --- Scan options ---
        scan_grp = QtWidgets.QGroupBox("Scan Options")
        scan_lay = QtWidgets.QHBoxLayout(scan_grp)
        scan_lay.setSpacing(16)
        self.cb_scan_all = QtWidgets.QCheckBox("All transforms")
        self.cb_scan_all.setChecked(True)
        self.cb_kits = QtWidgets.QCheckBox("Include KIT")
        self.cb_kits.setChecked(True)
        btn_scan = QtWidgets.QPushButton("⟳  Scan Scene")
        btn_scan.setObjectName("scan")
        btn_scan.clicked.connect(self._refresh)
        btn_sel_all = QtWidgets.QPushButton("Select All")
        btn_sel_all.clicked.connect(self._select_all)
        scan_lay.addWidget(self.cb_scan_all)
        scan_lay.addWidget(self.cb_kits)
        scan_lay.addStretch()
        scan_lay.addWidget(btn_scan)
        scan_lay.addWidget(btn_sel_all)
        root.addWidget(scan_grp)

        # --- Selected field ---
        sel_lay = QtWidgets.QHBoxLayout()
        sel_lay.addWidget(QtWidgets.QLabel("Selected:"))
        self.field_selected = QtWidgets.QLineEdit()
        self.field_selected.setReadOnly(True)
        sel_lay.addWidget(self.field_selected)
        btn_copy = QtWidgets.QPushButton("Copy")
        btn_copy.clicked.connect(self._copy_name)
        btn_sel_nodes = QtWidgets.QPushButton("Select Nodes")
        btn_sel_nodes.clicked.connect(self._select_nodes)
        btn_detail = QtWidgets.QPushButton("Print Detail")
        btn_detail.clicked.connect(self._print_detail)
        sel_lay.addWidget(btn_copy)
        sel_lay.addWidget(btn_sel_nodes)
        sel_lay.addWidget(btn_detail)
        root.addLayout(sel_lay)

        # --- Tabs: Standard / KIT / Orphans / Textures ---
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setFixedHeight(220)

        self.list_std    = self._make_list()
        self.list_kit    = self._make_list()
        self.list_orphan = self._make_list()
        self.list_tex    = self._make_list()

        self.tabs.addTab(self._wrap(self.list_std),    "Standard")
        self.tabs.addTab(self._wrap(self.list_kit),    "KIT")
        self.tabs.addTab(self._wrap(self.list_orphan), "Orphans ⚠")
        self.tabs.addTab(self._wrap(self.list_tex),    "Textures")
        root.addWidget(self.tabs)

        # --- Load section ---
        load_grp = QtWidgets.QGroupBox("Load Assets  (QDLoad.by_catalog)")
        load_lay = QtWidgets.QVBoxLayout(load_grp)
        load_lay.setSpacing(4)

        r1 = QtWidgets.QHBoxLayout()
        r1.addWidget(QtWidgets.QLabel("Category:"))
        self.field_cat = QtWidgets.QLineEdit("Props")
        self.field_cat.setFixedWidth(130)
        r1.addWidget(self.field_cat)
        r1.addWidget(QtWidgets.QLabel("  Prefixes:"))
        self.field_prefixes = QtWidgets.QLineEdit("ACC, ARC, QDD, AGRA, RELIC")
        r1.addWidget(self.field_prefixes)
        load_lay.addLayout(r1)

        r2 = QtWidgets.QHBoxLayout()
        btn_load_list = QtWidgets.QPushButton("Load List Selection  (all if empty)")
        btn_load_list.setObjectName("load")
        btn_load_list.clicked.connect(self._load_list)
        btn_load_maya = QtWidgets.QPushButton("Load Maya Selection")
        btn_load_maya.setObjectName("loadmaya")
        btn_load_maya.clicked.connect(self._load_maya)

        # Textures load
        btn_load_tex = QtWidgets.QPushButton("Load Textures (check scene first)")
        btn_load_tex.setObjectName("tex")
        btn_load_tex.clicked.connect(self._load_textures)
        r2.addWidget(btn_load_list)
        r2.addWidget(btn_load_maya)
        r2.addWidget(btn_load_tex)
        load_lay.addLayout(r2)
        root.addWidget(load_grp)

        # --- Shader section ---
        shader_grp = QtWidgets.QGroupBox("Auto-Assign Shaders  (QDS_ / Blinn / Phong / Lambert  ->  QDM_)")
        shader_lay = QtWidgets.QVBoxLayout(shader_grp)
        shader_lay.setSpacing(4)

        mode_row = QtWidgets.QHBoxLayout()
        self.rb_shader_maya = QtWidgets.QRadioButton("Maya Selection")
        self.rb_shader_maya.setChecked(True)
        self.rb_shader_list = QtWidgets.QRadioButton("List Selection")
        self.rb_shader_all  = QtWidgets.QRadioButton("All Scanned")
        mode_row.addWidget(self.rb_shader_maya)
        mode_row.addWidget(self.rb_shader_list)
        mode_row.addWidget(self.rb_shader_all)
        mode_row.addStretch()
        shader_lay.addLayout(mode_row)

        btn_row = QtWidgets.QHBoxLayout()
        btn_assign = QtWidgets.QPushButton("Auto-Assign Shaders")
        btn_assign.setObjectName("shader")
        btn_assign.clicked.connect(self._auto_assign_shaders)
        btn_preview = QtWidgets.QPushButton("Preview QDM_ candidates")
        btn_preview.clicked.connect(self._preview_shaders)
        btn_row.addWidget(btn_assign)
        btn_row.addWidget(btn_preview)
        shader_lay.addLayout(btn_row)
        root.addWidget(shader_grp)

        # --- Log ---
        log_grp = QtWidgets.QGroupBox("Log")
        log_lay = QtWidgets.QVBoxLayout(log_grp)
        self.log_box = QtWidgets.QTextEdit()
        self.log_box.setObjectName("log")
        self.log_box.setReadOnly(True)
        self.log_box.setFixedHeight(170)
        log_btn_row = QtWidgets.QHBoxLayout()
        btn_clear = QtWidgets.QPushButton("Clear Log")
        btn_clear.clicked.connect(self.log_box.clear)
        log_btn_row.addStretch()
        log_btn_row.addWidget(btn_clear)
        log_lay.addWidget(self.log_box)
        log_lay.addLayout(log_btn_row)
        root.addWidget(log_grp)

    def _make_list(self):
        lst = QtWidgets.QListWidget()
        lst.setAlternatingRowColors(True)
        lst.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        lst.itemSelectionChanged.connect(self._on_select)
        lst.itemDoubleClicked.connect(self._on_double_click)
        return lst

    def _wrap(self, widget):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(widget)
        return w

    # ----------------------------------------------------------
    #  HELPERS
    # ----------------------------------------------------------

    def _log(self, msg):
        self.log_box.append(msg)
        self.log_box.verticalScrollBar().setValue(
            self.log_box.verticalScrollBar().maximum()
        )
        print(msg)

    def _active_list(self):
        idx = self.tabs.currentIndex()
        return [self.list_std, self.list_kit, self.list_orphan, self.list_tex][idx]

    def _selected_assets(self):
        items = self._active_list().selectedItems()
        return [i.data(Qt.UserRole) for i in items]

    def _selected_data(self):
        idx = self.tabs.currentIndex()
        return [self._cache_main, self._cache_kit, self._cache_orphan, {}][idx]

    def _get_prefixes(self):
        raw = self.field_prefixes.text()
        vals = [x.strip() for x in raw.split(",") if x.strip()]
        return vals if vals else list(DEFAULT_PREFIXES)

    def _get_category(self):
        return self.field_cat.text().strip() or "Props"

    # ----------------------------------------------------------
    #  SCAN
    # ----------------------------------------------------------

    def _refresh(self):
        self.list_std.clear()
        self.list_kit.clear()
        self.list_orphan.clear()
        self.list_tex.clear()

        # Auto-detect scene asset
        scene_asset = detect_scene_asset()
        if scene_asset:
            self.lbl_scene_asset.setText("Scene asset: {}".format(scene_asset))
            self.lbl_scene_asset.setVisible(True)
        else:
            self.lbl_scene_asset.setVisible(False)

        prefixes = self._get_prefixes()
        main, kit, orphan = find_external_assets(
            scan_all=self.cb_scan_all.isChecked(),
            include_kits=self.cb_kits.isChecked(),
            known_prefixes=prefixes
        )

        self._cache_main   = main
        self._cache_kit    = kit
        self._cache_orphan = orphan
        set_cache(main, kit, orphan)

        def _fill(lst, data):
            for name in sorted(data.keys()):
                item = QtWidgets.QListWidgetItem("{:<52} [{}]".format(name, len(data[name])))
                item.setData(Qt.UserRole, name)
                lst.addItem(item)

        _fill(self.list_std,    main)
        _fill(self.list_kit,    kit)
        _fill(self.list_orphan, orphan)

        # Texture nodes (file nodes in scene)
        tex_nodes = cmds.ls(type="file") or []
        for t in sorted(tex_nodes):
            item = QtWidgets.QListWidgetItem(t)
            item.setData(Qt.UserRole, t)
            self.list_tex.addItem(item)

        occ_m = sum(len(v) for v in main.values())
        occ_k = sum(len(v) for v in kit.values())
        occ_o = sum(len(v) for v in orphan.values())
        self.lbl_summary.setText(
            "Standard: {} ({} occ)   KIT: {} ({} occ)   Orphans: {} ({} occ)   Textures: {}".format(
                len(main), occ_m, len(kit), occ_k, len(orphan), occ_o, len(tex_nodes)
            )
        )
        self.setWindowTitle("{} | {} assets".format(WINDOW_TITLE, len(main)))
        self.field_selected.clear()

        self._log("=" * 60)
        self._log("[Scan] Standard: {}  KIT: {}  Orphans: {}  Textures: {}".format(
            len(main), len(kit), len(orphan), len(tex_nodes)))
        if orphan:
            self._log("[Scan] Orphan assets (no known prefix):")
            for name in sorted(orphan.keys()):
                self._log("  !! {}  [{}]".format(name, len(orphan[name])))
        self._log("=" * 60)

    # ----------------------------------------------------------
    #  SELECTION
    # ----------------------------------------------------------

    def _on_select(self):
        assets = self._selected_assets()
        self.field_selected.setText(assets[0] if assets else "")

    def _on_double_click(self, item):
        name = item.data(Qt.UserRole)
        if name:
            clipboard = QtWidgets.QApplication.clipboard()
            clipboard.setText(name)
            self.field_selected.setText(name)
            self._log("[Copy] {}".format(name))

    def _copy_name(self):
        assets = self._selected_assets()
        if assets:
            clipboard = QtWidgets.QApplication.clipboard()
            clipboard.setText(assets[0])
            self._log("[Copy] {}".format(assets[0]))

    def _select_nodes(self):
        data   = self._selected_data()
        assets = self._selected_assets()
        nodes  = []
        for a in assets:
            nodes.extend(data.get(a, []))
        if not nodes:
            cmds.warning("[Scanner] No asset selected.")
            return
        cmds.select(nodes, r=True)
        self._log("[Select] {} node(s)".format(len(nodes)))

    def _select_all(self):
        nodes = []
        for vals in self._cache_main.values():   nodes.extend(vals)
        for vals in self._cache_kit.values():    nodes.extend(vals)
        for vals in self._cache_orphan.values(): nodes.extend(vals)
        if not nodes:
            cmds.warning("[Scanner] Nothing cached — run scan first.")
            return
        cmds.select(nodes, r=True)
        self._log("[Select All] {} node(s)".format(len(nodes)))

    def _print_detail(self):
        data   = self._selected_data()
        assets = self._selected_assets()
        if not assets:
            cmds.warning("[Scanner] No asset selected.")
            return
        self._log("-" * 60)
        for a in assets:
            nodes = data.get(a, [])
            self._log("{}  [{} occurrence(s)]".format(a, len(nodes)))
            for n in nodes:
                self._log("   {}".format(n))
        self._log("-" * 60)

    # ----------------------------------------------------------
    #  LOAD
    # ----------------------------------------------------------

    def _iter_assets_to_load(self):
        assets = self._selected_assets()
        if assets:
            return assets
        return sorted(list(self._cache_main.keys()) + list(self._cache_kit.keys()))

    def _load_list(self):
        assets   = self._iter_assets_to_load()
        category = self._get_category()
        prefixes = self._get_prefixes()
        if not assets:
            cmds.warning("[Load] No assets. Run scan first.")
            return
        self._log("=" * 60)
        self._log("[Load] {} asset(s)  cat={}".format(len(assets), category))
        ok = err = 0
        for asset in assets:
            if _do_load(asset, category, prefixes, self._log):
                ok += 1
            else:
                err += 1
            try:
                cmds.refresh(force=True)
            except Exception:
                pass
        self._log("[Load] Done -> {} OK  {} FAIL".format(ok, err))
        self._log("=" * 60)

    def _load_maya(self):
        category = self._get_category()
        prefixes = self._get_prefixes()
        sel = cmds.ls(selection=True, long=True) or []
        if not sel:
            cmds.warning("[Load] Nothing selected in Maya.")
            return
        found = set()
        for node in sel:
            n = extract_main_name(node) or extract_kit_name(node)
            if n:
                found.add(n)
        if not found:
            cmds.warning("[Load] No recognizable names in Maya selection.")
            return
        assets = sorted(found)
        self._log("=" * 60)
        self._log("[Load Maya] {} asset(s)  cat={}".format(len(assets), category))
        ok = err = 0
        for asset in assets:
            if _do_load(asset, category, prefixes, self._log):
                ok += 1
            else:
                err += 1
            try:
                cmds.refresh(force=True)
            except Exception:
                pass
        self._log("[Load Maya] Done -> {} OK  {} FAIL".format(ok, err))
        self._log("=" * 60)

    def _load_textures(self):
        """Load texture assets from list selection — skip if file node already exists in scene."""
        assets   = self._selected_assets()
        category = self._get_category()
        prefixes = self._get_prefixes()
        if not assets:
            cmds.warning("[Textures] No texture selected in Textures tab.")
            return
        existing_files = set(cmds.ls(type="file") or [])
        self._log("=" * 60)
        self._log("[Textures] {} selected  ({} file nodes in scene)".format(len(assets), len(existing_files)))
        ok = skip = err = 0
        for asset in assets:
            if asset in existing_files:
                self._log("  [SKIP] {} already in scene".format(asset))
                skip += 1
                continue
            if _do_load(asset, category, prefixes, self._log):
                ok += 1
            else:
                err += 1
        self._log("[Textures] Done -> {} loaded  {} skipped  {} failed".format(ok, skip, err))
        self._log("=" * 60)

    # ----------------------------------------------------------
    #  SHADERS
    # ----------------------------------------------------------

    def _get_shader_nodes(self):
        if self.rb_shader_maya.isChecked():
            nodes = cmds.ls(selection=True, long=True) or []
            if not nodes:
                cmds.warning("[Shader] Nothing selected in Maya.")
            return nodes
        elif self.rb_shader_list.isChecked():
            data   = self._selected_data()
            assets = self._selected_assets()
            nodes  = []
            for a in assets:
                nodes.extend(data.get(a, []))
            if not nodes:
                cmds.warning("[Shader] No asset selected in list.")
            return nodes
        else:
            nodes = []
            for vals in self._cache_main.values():   nodes.extend(vals)
            for vals in self._cache_kit.values():    nodes.extend(vals)
            for vals in self._cache_orphan.values(): nodes.extend(vals)
            if not nodes:
                cmds.warning("[Shader] No scanned assets.")
            return nodes

    def _auto_assign_shaders(self):
        nodes = self._get_shader_nodes()
        if not nodes:
            return

        meshes = _meshes_under(nodes)
        assigned = 0
        skipped  = []
        not_found = set()

        self._log("=" * 60)
        self._log("[Shader] Auto-Assign  mode={}  {} mesh(es)".format(
            "maya" if self.rb_shader_maya.isChecked()
            else "list" if self.rb_shader_list.isChecked() else "all",
            len(meshes)
        ))

        for mesh in meshes:
            ses = _ses_on_mesh(mesh)
            if not ses:
                continue
            for se in ses:
                shader = _surface_shader(se)
                if not shader:
                    continue
                short_sh  = strip_ns(shader)
                sh_type   = _shader_type(shader)
                is_qds    = short_sh.startswith("QDS_")
                is_basic  = sh_type in BLINN_PHONG_LAMBERT

                if not (is_qds or is_basic):
                    continue

                qdm = _find_qdm(shader) if is_qds else None

                if qdm:
                    try:
                        _assign_shader(mesh, qdm)
                        self._log("  [OK]   {} : {} ({}) -> {}".format(
                            short_name(mesh), short_sh, sh_type, strip_ns(qdm)))
                        assigned += 1
                    except Exception as e:
                        self._log("  [FAIL] {} -> {} : {}".format(short_name(mesh), qdm, e))
                        not_found.add(short_sh)
                else:
                    reason = "no QDM_ found" if is_qds else "basic shader ({}) — no QDM_ target".format(sh_type)
                    self._log("  [SKIP] {} : {} -> {}".format(short_name(mesh), short_sh, reason))
                    skipped.append(short_sh)
                    if is_qds:
                        not_found.add(short_sh)

        self._log("")
        self._log("  Assigned : {}".format(assigned))
        self._log("  Skipped  : {}".format(len(skipped)))
        if not_found:
            self._log("  QDM_ NOT FOUND for:")
            for s in sorted(not_found):
                self._log("    {} -> QDM_{} : MISSING".format(s, s[4:] if s.startswith("QDS_") else s))
        self._log("=" * 60)

    def _preview_shaders(self):
        all_qdm = _all_qdm_candidates()
        self._log("[Shader] QDM_ candidates ({}) :".format(len(all_qdm)))
        for n in all_qdm:
            self._log("  {}".format(n))


# ============================================================
#  LAUNCH
# ============================================================

def launch():
    # Close existing instance
    for w in QtWidgets.QApplication.topLevelWidgets():
        if w.objectName() == WINDOW_OBJ:
            w.close()
            w.deleteLater()

    win = ExternalAssetScanner()
    win.show()
    return win


launch()
