from PyQt5.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QLineEdit, QComboBox, QPushButton, QLabel, QSpinBox, QTabWidget, QCheckBox, QTextEdit, QWidget, QHBoxLayout, QListWidget, QStackedWidget, QDialogButtonBox
from PyQt5.QtCore import pyqtSignal
import json
import os

class SettingsDialog(QDialog):
    settings_applied = pyqtSignal(dict)
    def __init__(self, parent=None, settings_path='settings.json', colormaps=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QDialog, QWidget, QLabel, QLineEdit, QTabWidget, QComboBox, QCheckBox, QPushButton, QListWidget {
                color: #f0f0f0;
                background-color: #181a1b;
            }
            QLineEdit, QComboBox, QTabWidget, QCheckBox, QPushButton, QListWidget {
                border: 1px solid #444;
            }
        """)
        self.setWindowTitle("Settings")
        self.settings_path = settings_path
        self.colormaps = colormaps or []
        # Sidebar and stacked pages
        sidebar_layout = QHBoxLayout(self)
        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(160)
        self.sidebar.addItems(["General", "Palette", "Alerts", "Updates"])
        self.pages = QStackedWidget(self)
        # --- General Page ---
        general_page = QWidget()
        general_layout = QFormLayout(general_page)
        self.width_input = QSpinBox()
        self.width_input.setRange(400, 3840)
        general_layout.addRow("Window Width", self.width_input)
        self.height_input = QSpinBox()
        self.height_input.setRange(300, 2160)
        general_layout.addRow("Window Height", self.height_input)
        self.dpi_input = QSpinBox()
        self.dpi_input.setRange(72, 600)
        general_layout.addRow("DPI", self.dpi_input)
        self.frame_count_input = QSpinBox()
        self.frame_count_input.setRange(1, 100)
        general_layout.addRow("Animation Frames", self.frame_count_input)
        self.refresh_input = QSpinBox()
        self.refresh_input.setRange(1000, 600000)
        general_layout.addRow("Polygon Refresh (ms)", self.refresh_input)
        self.site_input = QLineEdit()
        general_layout.addRow("Default Site", self.site_input)
        general_page.setLayout(general_layout)
        self.pages.addWidget(general_page)
        # --- Palette Page ---
        palette_page = QWidget()
        palette_layout = QVBoxLayout(palette_page)
        self.colormap_combo = QComboBox()
        # Add common Py-ART colormaps if not present
        pyart_colormaps = [
            "NWSRef", "NWSVel", "HomeyerRainbow", "LangRainbow12", "SpectralExtended", "balance", "ChaseSpectral",
            "BlueBrown10", "Carbone11", "Carbone17", "Carbone42", "Cat12", "EWilson17", "NWS_SPW", "PD17", "RRate11", "RefDiff", "SCook18", "StepSeq25", "SymGray12", "Theodore16", "Wild25"
        ]
        for cmap in pyart_colormaps:
            if cmap not in self.colormaps:
                self.colormaps.append(cmap)
        self.colormap_combo.addItems(self.colormaps)
        palette_layout.addWidget(QLabel("Colormap:"))
        palette_layout.addWidget(self.colormap_combo)
        self.upload_pal_btn = QPushButton("Upload .pal File")
        palette_layout.addWidget(self.upload_pal_btn)
        self.upload_pal_btn.clicked.connect(self.upload_pal_file)
        palette_page.setLayout(palette_layout)
        self.pages.addWidget(palette_page)
        # --- Alerts Page ---
        alerts_page = QWidget()
        alerts_layout = QVBoxLayout(alerts_page)
        self.alert_types = ["Tornado", "Severe Thunderstorm", "Flash Flood"]
        self.alert_checkboxes = {}
        for alert in self.alert_types:
            cb = QCheckBox(alert)
            alerts_layout.addWidget(cb)
            self.alert_checkboxes[alert] = cb
        alerts_page.setLayout(alerts_layout)
        self.pages.addWidget(alerts_page)
        # --- Updates Page ---
        updates_page = QWidget()
        updates_layout = QVBoxLayout(updates_page)
        self.updates_text = QTextEdit()
        self.updates_text.setReadOnly(True)
        self.updates_text.setPlainText(self.get_updates_text())
        updates_layout.addWidget(QLabel("Latest Updates:"))
        updates_layout.addWidget(self.updates_text)
        updates_page.setLayout(updates_layout)
        self.pages.addWidget(updates_page)
        # Connect sidebar
        self.sidebar.currentRowChanged.connect(self.pages.setCurrentIndex)
        sidebar_layout.addWidget(self.sidebar)
        sidebar_layout.addWidget(self.pages)
        # --- Button Row ---
        btn_layout = QHBoxLayout()
        self.restore_btn = QPushButton("Restore Defaults")
        self.restore_btn.clicked.connect(self.restore_defaults)
        btn_layout.addWidget(self.restore_btn)
        btn_layout.addStretch(1)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self.apply_settings)
        btn_layout.addWidget(self.apply_btn)
        self.ok_btn = QPushButton("OK")
        self.ok_btn.clicked.connect(self.save_settings)
        btn_layout.addWidget(self.ok_btn)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)
        # --- Main Layout ---
        main_layout = QVBoxLayout(self)
        main_layout.addLayout(sidebar_layout)
        main_layout.addLayout(btn_layout)
        self.setLayout(main_layout)
        self.load_settings()

    def upload_pal_file(self):
        from PyQt5.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "Select .pal File", "", "Palette Files (*.pal)")
        if path:
            name = os.path.basename(path)
            if name not in self.colormaps:
                self.colormap_combo.addItem(name)
            self.colormap_combo.setCurrentText(name)

    def get_updates_text(self):
        # This could be loaded from a file or hardcoded
        return (
            "- New tabbed settings dialog\n"
            "- Updates tab shows latest changes\n"
            "- Palette and alert type selection moved to settings\n"
            "- Save radar images and GIFs (coming soon)\n"
            "- UI/UX improvements\n"
            "- Added colormap upload functionality\n"
            "- Improved alert handling\n"
            "- Added DPI and animation frame settings\n"
            "- Added polygon refresh interval setting\n"
            "- Added default site setting\n"
        )

    def load_settings(self):
        """Load settings from file and populate the UI widgets."""
        s = {}
        try:
            if os.path.exists(self.settings_path):
                with open(self.settings_path, 'r', encoding='utf-8') as f:
                    s = json.load(f)
        except Exception as e:
            print(f"[WARNING] Could not load settings: {e}")
        # General
        self.width_input.setValue(s.get('window_width', 1200))
        self.height_input.setValue(s.get('window_height', 900))
        self.dpi_input.setValue(s.get('dpi', 96))
        self.frame_count_input.setValue(s.get('animation_frame_count', 14))
        self.refresh_input.setValue(s.get('polygon_refresh_interval_ms', 120000))
        self.site_input.setText(s.get('default_site', 'KTLX'))
        # Palette
        colormap = s.get('colormap', self.colormap_combo.itemText(0))
        idx = self.colormap_combo.findText(colormap)
        if idx >= 0:
            self.colormap_combo.setCurrentIndex(idx)
        # Alerts
        for alert in self.alert_types:
            checked = s.get(f'alert_{alert.lower().replace(" ", "_")}', True)
            self.alert_checkboxes[alert].setChecked(checked)

    def save_settings(self):
        s = {
            'window_width': self.width_input.value(),
            'window_height': self.height_input.value(),
            'dpi': self.dpi_input.value(),
            'colormap': self.colormap_combo.currentText(),
            'animation_frame_count': self.frame_count_input.value(),
            'polygon_refresh_interval_ms': self.refresh_input.value(),
            'default_site': self.site_input.text()
        }
        # Alerts
        for alert in self.alert_types:
            s[f'alert_{alert.lower().replace(" ", "_") }'] = self.alert_checkboxes[alert].isChecked()
        with open(self.settings_path, 'w') as f:
            json.dump(s, f, indent=4)
        self.settings_applied.emit(s)
        self.accept()

    def apply_settings(self):
        """Apply settings without closing the dialog."""
        s = {
            'window_width': self.width_input.value(),
            'window_height': self.height_input.value(),
            'dpi': self.dpi_input.value(),
            'colormap': self.colormap_combo.currentText(),
            'animation_frame_count': self.frame_count_input.value(),
            'polygon_refresh_interval_ms': self.refresh_input.value(),
            'default_site': self.site_input.text()
        }
        for alert in self.alert_types:
            s[f'alert_{alert.lower().replace(" ", "_") }'] = self.alert_checkboxes[alert].isChecked()
        with open(self.settings_path, 'w') as f:
            json.dump(s, f, indent=4)
        self.settings_applied.emit(s)
        # Do not close the dialog

    def restore_defaults(self):
        """Restore all settings to their default values in the UI."""
        self.width_input.setValue(1280)
        self.height_input.setValue(720)
        self.dpi_input.setValue(96)
        self.frame_count_input.setValue(12)
        self.refresh_input.setValue(60000)
        self.site_input.setText('KTLX')
        self.colormap_combo.setCurrentIndex(0)
        for alert in self.alert_types:
            self.alert_checkboxes[alert].setChecked(True)
