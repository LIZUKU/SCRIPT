# -*- coding: utf-8 -*-
"""
=============================================================================
MESH TOOLS PRO v2.0 - Séparation & Combinaison optimisées
=============================================================================
UI moderne inspirée de ProArray
Bug de séparation corrigé - plus de perte de meshes
Compatible: Maya 2022 - 2025
=============================================================================
"""

import maya.cmds as cmds
import maya.mel as mel

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
# MAYA MAIN WINDOW
# ============================================================
def get_maya_main_window():
    try:
        main_window_ptr = omui.MQtUtil.mainWindow()
        return wrapInstance(int(main_window_ptr), QtWidgets.QWidget)
    except:
        return None


# ============================================================
# CORE FUNCTIONS
# ============================================================
def separate_all_meshes_optimized(batch_size=200, progress_callback=None):
    """
    Separate all meshes in scene in optimized way.
    Uses polySeparate with proper settings to avoid deletion.
    """
    # Get all mesh transforms
    all_meshes = cmds.ls(type='mesh', long=True)
    
    if not all_meshes:
        return {"total": 0, "separated": 0, "skipped": 0, "errors": 0}
    
    # Filter to get unique transforms
    mesh_transforms = []
    seen = set()
    for mesh in all_meshes:
        parent = cmds.listRelatives(mesh, parent=True, fullPath=True)
        if parent and parent[0] not in seen:
            mesh_transforms.append(parent[0])
            seen.add(parent[0])
    
    total_meshes = len(mesh_transforms)
    separated_count = 0
    skipped_count = 0
    error_count = 0
    
    # Process by batch for optimization
    for batch_start in range(0, total_meshes, batch_size):
        batch_end = min(batch_start + batch_size, total_meshes)
        batch = mesh_transforms[batch_start:batch_end]
        
        if progress_callback:
            progress_callback(batch_start, total_meshes, 
                            f"Batch {batch_start//batch_size + 1}: {batch_start}-{batch_end}/{total_meshes}")
        
        for mesh_transform in batch:
            try:
                # Check if object still exists
                if not cmds.objExists(mesh_transform):
                    skipped_count += 1
                    continue
                
                # Check number of shells
                try:
                    num_shells = cmds.polyEvaluate(mesh_transform, shell=True)
                except:
                    skipped_count += 1
                    continue
                
                # Only separate if multiple shells
                if num_shells and num_shells > 1:
                    try:
                        # EXACT syntax from original working script
                        separated = cmds.polySeparate(
                            mesh_transform,
                            constructionHistory=False,
                            removeShells=False
                        )
                        
                        if separated and len(separated) > 1:
                            separated_count += 1
                        else:
                            skipped_count += 1
                            
                    except Exception as e:
                        error_count += 1
                        print(f"Separation error {mesh_transform}: {str(e)}")
                else:
                    skipped_count += 1
                    
            except Exception as e:
                error_count += 1
                print(f"Processing error {mesh_transform}: {str(e)}")
    
    cmds.select(clear=True)
    
    return {
        "total": total_meshes,
        "separated": separated_count,
        "skipped": skipped_count,
        "errors": error_count
    }


def separate_selected_meshes():
    """Separate only selected meshes."""
    selection = cmds.ls(selection=True, type='transform')
    
    if not selection:
        return {"success": False, "message": "No selection"}
    
    separated_count = 0
    results = []
    
    for obj in selection:
        try:
            num_shells = cmds.polyEvaluate(obj, shell=True)
            
            if num_shells and num_shells > 1:
                # EXACT syntax from original working script
                separated = cmds.polySeparate(
                    obj, 
                    constructionHistory=False, 
                    removeShells=False
                )
                
                if separated and len(separated) > 1:
                    separated_count += 1
                    results.append(f"'{obj}' -> {len(separated)} objects")
                    
        except Exception as e:
            results.append(f"Error: {obj}")
    
    cmds.select(clear=True)
    
    return {
        "success": True,
        "count": separated_count,
        "results": results
    }


def combine_selection(keep_history=False):
    """Combine selection into a single mesh."""
    selection = cmds.ls(selection=True, type='transform')
    
    if not selection or len(selection) < 2:
        return {"success": False, "message": "Select at least 2 meshes"}
    
    try:
        combined = cmds.polyUnite(selection, constructionHistory=keep_history, mergeUVSets=1)
        
        if combined:
            cmds.xform(combined[0], centerPivots=True)
            cmds.select(combined[0], replace=True)
            
            return {
                "success": True,
                "object": combined[0],
                "count": len(selection)
            }
    except Exception as e:
        return {"success": False, "message": str(e)}


def get_scene_stats():
    """Obtient les statistiques de la scène."""
    all_meshes = cmds.ls(type='mesh')
    mesh_transforms = []
    
    for mesh in all_meshes:
        parent = cmds.listRelatives(mesh, parent=True)
        if parent and parent[0] not in mesh_transforms:
            mesh_transforms.append(parent[0])
    
    combined_count = 0
    for mesh in mesh_transforms:
        try:
            num_shells = cmds.polyEvaluate(mesh, shell=True)
            if num_shells and num_shells > 1:
                combined_count += 1
        except:
            pass
    
    selection = cmds.ls(selection=True)
    
    return {
        "total_meshes": len(mesh_transforms),
        "combined_meshes": combined_count,
        "simple_meshes": len(mesh_transforms) - combined_count,
        "selection_count": len(selection)
    }


# ============================================================
# UI
# ============================================================
class MeshToolsUI(QtWidgets.QDialog):
    _instance = None

    def __init__(self, parent=get_maya_main_window()):
        super(MeshToolsUI, self).__init__(parent)

        self.setWindowTitle("Mesh Tools Pro")
        self.setFixedWidth(300)
        self.setWindowFlags(
            QtCore.Qt.Window |
            QtCore.Qt.WindowCloseButtonHint
        )

        self._build_ui()
        self._apply_style()
        self._update_stats()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        # =========================
        # STATS
        # =========================
        self.stats_label = QtWidgets.QLabel("Loading...")
        self.stats_label.setObjectName("statsLabel")
        self.stats_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.stats_label)

        # =========================
        # SEPARATE
        # =========================
        sep_label = QtWidgets.QLabel("SEPARATE")
        sep_label.setObjectName("sectionLabel")
        layout.addWidget(sep_label)

        # Batch size
        batch_layout = QtWidgets.QHBoxLayout()
        batch_layout.setSpacing(6)
        
        batch_lbl = QtWidgets.QLabel("Batch:")
        batch_lbl.setFixedWidth(45)
        batch_layout.addWidget(batch_lbl)
        
        self.batch_spin = QtWidgets.QSpinBox()
        self.batch_spin.setMinimum(50)
        self.batch_spin.setMaximum(1000)
        self.batch_spin.setValue(200)
        self.batch_spin.setSingleStep(50)
        self.batch_spin.setFixedWidth(70)
        batch_layout.addWidget(self.batch_spin)
        
        batch_layout.addStretch()
        layout.addLayout(batch_layout)

        # Buttons
        btn_layout1 = QtWidgets.QHBoxLayout()
        btn_layout1.setSpacing(6)

        self.btn_sep_all = QtWidgets.QPushButton("Separate All")
        self.btn_sep_all.setFixedHeight(26)
        self.btn_sep_all.clicked.connect(self._on_separate_all)
        btn_layout1.addWidget(self.btn_sep_all)

        self.btn_sep_sel = QtWidgets.QPushButton("Separate Selection")
        self.btn_sep_sel.setFixedHeight(26)
        self.btn_sep_sel.clicked.connect(self._on_separate_selected)
        btn_layout1.addWidget(self.btn_sep_sel)

        layout.addLayout(btn_layout1)

        # =========================
        # COMBINE
        # =========================
        comb_label = QtWidgets.QLabel("COMBINE")
        comb_label.setObjectName("sectionLabel")
        layout.addWidget(comb_label)

        self.chk_keep_history = QtWidgets.QCheckBox("Keep History")
        self.chk_keep_history.setChecked(False)
        layout.addWidget(self.chk_keep_history)

        self.btn_combine = QtWidgets.QPushButton("Combine Selection")
        self.btn_combine.setFixedHeight(26)
        self.btn_combine.clicked.connect(self._on_combine)
        layout.addWidget(self.btn_combine)

        # =========================
        # LOG
        # =========================
        log_label = QtWidgets.QLabel("LOG")
        log_label.setObjectName("sectionLabel")
        layout.addWidget(log_label)

        self.log_text = QtWidgets.QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(100)
        self.log_text.setObjectName("logConsole")
        layout.addWidget(self.log_text)

        layout.addStretch()

        self._log("? Ready")

    def _apply_style(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 4px;
            }
            
            QLabel { 
                color: #b0b0b0; 
                font-size: 11px; 
            }
            
            QLabel#sectionLabel {
                color: #707070;
                font-size: 9px;
                font-weight: bold;
                padding-top: 6px;
                padding-bottom: 2px;
                border-top: 1px solid #3a3a3a;
                margin-top: 4px;
            }
            
            QLabel#statsLabel {
                color: #808080;
                font-size: 10px;
                padding: 4px;
            }
            
            QPushButton {
                background-color: #353535;
                color: #b0b0b0;
                border: 1px solid #4a4a4a;
                border-radius: 2px;
                font-size: 11px;
                padding: 4px 8px;
            }
            QPushButton:hover { 
                background-color: #404040; 
                border: 1px solid #5a5a5a;
            }
            QPushButton:pressed { 
                background-color: #2a2a2a; 
            }
            
            QSpinBox {
                background-color: #252525;
                color: #b0b0b0;
                border: 1px solid #3a3a3a;
                border-radius: 2px;
                padding: 3px;
                font-size: 11px;
            }
            
            QCheckBox { 
                color: #b0b0b0; 
                font-size: 11px; 
                spacing: 6px; 
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid #4a4a4a;
                background-color: #252525;
                border-radius: 3px;
            }
            QCheckBox::indicator:checked {
                background-color: #5a2a2a;
                border-color: #e84d4d;
            }
            
            QTextEdit#logConsole {
                background-color: #1a1a1a;
                color: #909090;
                border: 1px solid #2a2a2a;
                border-radius: 2px;
                font-family: 'Courier New', monospace;
                font-size: 9px;
                padding: 4px;
            }
            
            QProgressBar {
                border: 1px solid #3a3a3a;
                border-radius: 2px;
                background-color: #252525;
                text-align: center;
                color: #b0b0b0;
                font-size: 10px;
                height: 18px;
            }
            QProgressBar::chunk {
                background-color: #4a4a4a;
                border-radius: 1px;
            }
        """)

    def _log(self, message):
        """Ajoute un message au log."""
        self.log_text.append(message)
        print(message)

    def _update_stats(self):
        """Update displayed statistics."""
        stats = get_scene_stats()
        text = f"Scene: {stats['total_meshes']} | Combined: {stats['combined_meshes']} | Selected: {stats['selection_count']}"
        self.stats_label.setText(text)

    def _on_separate_all(self):
        """Launch separation of all meshes."""
        self._log("\n" + "="*50)
        self._log("SEPARATE ALL MESHES - OPTIMIZED")
        self._log("="*50)
        
        batch_size = self.batch_spin.value()
        self._log(f"Batch size: {batch_size}")
        
        # Create progress dialog
        progress_dialog = QtWidgets.QProgressDialog(
            "Processing meshes...", 
            "Cancel", 
            0, 100, 
            self
        )
        progress_dialog.setWindowTitle("Separating")
        progress_dialog.setWindowModality(QtCore.Qt.WindowModal)
        progress_dialog.setMinimumDuration(0)
        progress_dialog.setValue(0)
        
        def progress_callback(current, total, message):
            if progress_dialog.wasCanceled():
                return
            percent = int((current / total) * 100)
            progress_dialog.setValue(percent)
            progress_dialog.setLabelText(message)
            QtWidgets.QApplication.processEvents()
        
        # Execute separation
        result = separate_all_meshes_optimized(batch_size, progress_callback)
        
        progress_dialog.setValue(100)
        progress_dialog.close()
        
        # Display results
        self._log(f"\nDONE")
        self._log(f"  Processed: {result['total']}")
        self._log(f"  Separated: {result['separated']}")
        self._log(f"  Skipped: {result['skipped']}")
        self._log(f"  Errors: {result['errors']}")
        self._log("="*50)
        
        self._update_stats()

    def _on_separate_selected(self):
        """Launch separation of selection."""
        self._log(f"\nSeparating selection...")
        
        result = separate_selected_meshes()
        
        if not result["success"]:
            self._log(f"! {result['message']}")
            cmds.warning(result['message'])
            return
        
        for line in result["results"]:
            self._log(f"  {line}")
        
        self._log(f"Done: {result['count']} separated")
        self._update_stats()

    def _on_combine(self):
        """Launch combination of selection."""
        self._log(f"\nCombining selection...")
        
        keep_history = self.chk_keep_history.isChecked()
        result = combine_selection(keep_history)
        
        if not result["success"]:
            self._log(f"! {result['message']}")
            cmds.warning(result['message'])
            return
        
        self._log(f"Done: {result['count']} meshes -> '{result['object']}'")
        self._update_stats()

    def closeEvent(self, event):
        MeshToolsUI._instance = None
        super(MeshToolsUI, self).closeEvent(event)

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
    return MeshToolsUI.show_ui()


# Run
if __name__ == "__main__":
    show_ui()
