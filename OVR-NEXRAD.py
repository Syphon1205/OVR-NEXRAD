import os
import sys
if getattr(sys, 'frozen', False):
    # If running as a frozen exe, add the exe directory to PATH
    exe_dir = os.path.dirname(sys.executable)
    os.environ["PATH"] = exe_dir + os.pathsep + os.environ["PATH"]

import asyncio
import aiohttp
import requests
import tempfile
import json
import glob
import base64
import numpy as np
import matplotlib.pyplot as plt
import xml.etree.ElementTree as ET
import pyart
import re
import io
from datetime import datetime, timedelta, timezone
from shapely.geometry import shape
import csv
import matplotlib.cm as cm
import matplotlib as mpl
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QCheckBox, QDockWidget, QListWidget, QListWidgetItem, QFrame, QStatusBar, QMessageBox,
    QDialog, QSlider, QFileDialog, QStackedWidget, QGroupBox, QLineEdit, QScrollArea, QSizePolicy
)
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import QObject, pyqtSignal, QThread
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtCore import QUrl
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtCore import pyqtSlot
import boto3
from botocore import UNSIGNED
from botocore.config import Config

# --- Add zoneinfo for timezone-aware date handling ---
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

# --- Config ---
DEFAULT_SITE = 'KTLX'
SITES_CSV = 'Nexrad_sites.csv'
NEXRAD_BUCKET = 'unidata-nexrad-level3-chunks'  # Real-time Level 3 bucket
WARNING_TYPES = {
    'Tornado': {'color': '#FF0000', 'alpha': 0.7},
    'Severe Thunderstorm': {'color': '#FFCC00', 'alpha': 0.6},
    'Flash Flood': {'color': '#00FF00', 'alpha': 0.5},
}

def load_nexrad_sites(csv_path):
    sites = {}
    try:
        with open(csv_path, newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                site_id = row['id'].strip()
                name = row['name'].strip()
                lat = float(row['lat'].strip())
                lon = float(row['lon'].strip())
                sites[site_id] = {'name': name, 'lat': lat, 'lon': lon}
    except Exception as e:
        # Fallback: built-in default sites
        sites = {
            'KTLX': {'name': 'Oklahoma City, OK', 'lat': 35.3331, 'lon': -97.2775},
            'KAMA': {'name': 'Amarillo, TX', 'lat': 35.233, 'lon': -101.708},
            'KDVN': {'name': 'Davenport, IA', 'lat': 41.611, 'lon': -90.580},
        }
    if not sites:
        # Fallback: built-in default sites if CSV is empty
        sites = {
            'KTLX': {'name': 'Oklahoma City, OK', 'lat': 35.3331, 'lon': -97.2775},
            'KAMA': {'name': 'Amarillo, TX', 'lat': 35.233, 'lon': -101.708},
            'KDVN': {'name': 'Davenport, IA', 'lat': 41.611, 'lon': -90.580},
        }
    return sites

# --- JS Bridge ---
class JSBridge(QObject):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

    @pyqtSlot(str)
    def selectRadarSite(self, site_id):
        self.main_window.on_site_change(site_id, auto_animate=True)

    @pyqtSlot(str)
    def requestWeatherAlerts(self, area_code):
        self.main_window.send_weather_alerts_to_js(area_code)

    @pyqtSlot()
    def requestLatestRadarFile(self):
        """JS can call this to request the latest radar file (as base64)."""
        self.main_window.send_latest_radar_file_to_js()

# --- Settings Dialog ---
class SettingsDialog(QDialog):
    def __init__(self, parent, settings):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.settings = settings.copy()
        self.main_window = parent
        layout = QVBoxLayout(self)

        # Animation frame count
        layout.addWidget(QLabel("Animation Frame Count:"))
        self.frame_slider = QSlider(Qt.Orientation.Horizontal)
        self.frame_slider.setMinimum(1)
        self.frame_slider.setMaximum(30)
        self.frame_slider.setValue(self.settings.get('animation_frames', 14))
        self.frame_slider.setTickInterval(1)
        self.frame_slider.setTickPosition(QSlider.TicksBelow)
        self.frame_slider.valueChanged.connect(self._on_frame_slider_change)
        layout.addWidget(self.frame_slider)
        self.frame_label = QLabel(f"{self.frame_slider.value()} frames")
        layout.addWidget(self.frame_label)

        # Default site selection
        layout.addWidget(QLabel("Default Radar Site:"))
        self.site_combo = QComboBox()
        for site_id, info in self.main_window.sites.items():
            self.site_combo.addItem(f"{site_id} - {info['name']}", site_id)
        idx = self.site_combo.findData(self.settings.get('default_site', self.main_window.current_site))
        if idx != -1:
            self.site_combo.setCurrentIndex(idx)
        layout.addWidget(self.site_combo)

        # Per-product colormap selection
        layout.addWidget(QLabel("Colormap (Palette) for Each Product:"))
        self.palette_combos = {}
        self.product_map = {
            "Super-Res Reflectivity (N0B)": "Reflectivity",
            "Super-Res Velocity (N0G)": "Velocity",
            "Spectrum Width (NSW)": "Spectrum Width",
            "Differential Reflectivity (N0X)": "Differential Reflectivity",
            "Correlation Coefficient (N0C)": "Correlation Coefficient",
            "Specific Differential Phase (N0K)": "Specific Differential Phase",
            "Hydrometeor Classification (N0H)": "Other",
            "High-Res VIL (DVL)": "Other",
            "Enhanced Echo Tops (EET)": "Other",
            "Storm Total Accum (DTA)": "Other",
            "Digital 1-Hour Accum (DAA)": "Other",
            "Composite Reflectivity (NCZ)": "Reflectivity",
            "Mid-Layer Composite (NML)": "Reflectivity",
            "High-Layer Composite (NHL)": "Reflectivity",
            "1-Hour Precip (N1P)": "Other",
            "3-Hour Precip (N3P)": "Other",
            "Storm Total Precip (NTP)": "Other",
            "Echo Tops (NET)": "Other",
            "Vertically Integrated Liquid (NVL)": "Other",
            "Mesocyclone Detection (NMD)": "Other",
            "Tornado Vortex Signature (NTV)": "Other",
            "Hail Index (NHI)": "Other",
            "Storm Structure (NSS)": "Other",
        }
        # Use display names for palettes
        display_to_internal = self.main_window.palette_display_to_internal
        internal_to_display = self.main_window.palette_internal_to_display
        for prod_name in self.product_map.keys():
            hbox = QHBoxLayout()
            hbox.addWidget(QLabel(prod_name))
            combo = QComboBox()
            # Get palettes for this product type (internal names)
            ptype = self.product_map[prod_name]
            internal_palettes = self.main_window.product_palettes.get(ptype, [])
            display_palettes = [internal_to_display.get(p, p) for p in internal_palettes]
            combo.addItems(display_palettes if display_palettes else ["default"])
            # Set current selection from settings if present
            pal_setting = self.settings.get('product_palettes', {}).get(prod_name, None)
            if pal_setting:
                # Map internal name to display name for selection
                display_name = internal_to_display.get(pal_setting, pal_setting)
                idx = combo.findText(display_name)
                if idx != -1:
                    combo.setCurrentIndex(idx)
            hbox.addWidget(combo)
            layout.addLayout(hbox)
            self.palette_combos[prod_name] = combo

        # Upload palette button (optional)
        self.upload_btn = QPushButton("Upload New Palette (.pal)")
        self.upload_btn.clicked.connect(self.upload_pal_file)
        layout.addWidget(self.upload_btn)

        # Buttons
        btns = QHBoxLayout()
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(ok_btn)
        btns.addWidget(cancel_btn)
        layout.addLayout(btns)

    def _on_frame_slider_change(self, value):
        self.frame_label.setText(f"{value} frames")

    def upload_pal_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select .pal File", "", "Palette Files (*.pal)")
        if path:
            self.main_window.register_custom_palette(path)
            # Refresh palette combos with display names
            display_to_internal = self.main_window.palette_display_to_internal
            internal_to_display = self.main_window.palette_internal_to_display
            for prod_name, combo in self.palette_combos.items():
                ptype = self.product_map[prod_name]
                internal_palettes = self.main_window.product_palettes.get(ptype, [])
                display_palettes = [internal_to_display.get(p, p) for p in internal_palettes]
                combo.clear()
                combo.addItems(display_palettes if display_palettes else ["default"])

    def get_settings(self):
        # Gather settings from UI
        new_settings = self.settings.copy()
        new_settings['animation_frames'] = self.frame_slider.value()
        new_settings['default_site'] = self.site_combo.currentData()
        # Per-product palettes (store internal names)
        pal_dict = {}
        display_to_internal = self.main_window.palette_display_to_internal
        internal_to_display = self.main_window.palette_internal_to_display
        for prod_name, combo in self.palette_combos.items():
            display_name = combo.currentText()
            # Map display name to internal name for storage
            internal_name = display_to_internal.get(display_name, display_name)
            pal_dict[prod_name] = internal_name
        new_settings['product_palettes'] = pal_dict
        return new_settings

# --- Main App ---
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton, QApplication, QMainWindow, QWidget, QHBoxLayout, QComboBox, QCheckBox, QStatusBar, QSlider, QListWidget, QListWidgetItem, QFileDialog
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal

# --- LoadingScreen class ---
class LoadingScreen(QDialog):
    def __init__(self, version, parent=None):
        super().__init__(parent)
        # Add all available Qt window flags for maximum cross-platform compatibility
        # Use getattr for all Qt window flags for maximum cross-platform compatibility
        # Use getattr for all Qt window flags for maximum cross-platform compatibility
        # Use Qt.WindowFlags(0) as a safe fallback for bitwise operations
        self.setWindowFlags(
            Qt.Window |
            Qt.CustomizeWindowHint |
            Qt.WindowTitleHint |
            Qt.WindowMinimizeButtonHint |
            Qt.WindowMaximizeButtonHint |
            Qt.WindowCloseButtonHint
        )
        self.setWindowTitle(f"OVR-NEXRAD v{version}")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setStyleSheet("background-color: #181a1b; color: #f0f0f0; font-family: Arial, 'Segoe UI', 'Liberation Sans', sans-serif;")
        layout = QVBoxLayout(self)
        self.title_label = QLabel(f"<div style='font-size:20px; color:#2d8cf0; font-weight:bold;'>OVR-NEXRAD v{version}</div>")
        # Use Qt.TextFormat and Qt.Alignment as safe fallbacks for enum values
        self.title_label.setTextFormat(Qt.RichText)
        self.title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.title_label)
        disclaimer = (
            "Radar data is provided by NOAA NEXRAD and Unidata/UCAR.<br>"
            "This application is for educational and informational purposes only.<br>"
            "<b>Do not use for life or property protection.</b><br>"
            "Data source: <a href='https://www.unidata.ucar.edu/data/radar.html'>https://www.unidata.ucar.edu/data/radar.html</a>"
        )
        self.disclaimer_label = QLabel(f"<div style='font-size:12px; color:#aaa; margin-top:4px;'>{disclaimer}</div>")
        self.disclaimer_label.setTextFormat(Qt.RichText)
        self.disclaimer_label.setAlignment(Qt.AlignCenter)
        self.disclaimer_label.setWordWrap(True)
        layout.addWidget(self.disclaimer_label)
        self.progress_label = QLabel("Initializing...")
        self.progress_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.progress_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        self.setLayout(layout)
    def set_message(self, msg):
        self.progress_label.setText(msg)
        QApplication.processEvents()
    def set_progress(self, value, max_value=None):
        if max_value is not None:
            self.progress_bar.setMaximum(max_value)
        self.progress_bar.setValue(value)
        QApplication.processEvents()


# --- Main Application Window ---


# --- Worker for threaded Level 3 loading ---
from PyQt5.QtCore import QObject, pyqtSignal, QThread

class Level3LoaderWorker(QObject):
    async def async_download_level3(self, session, url, temp_filepath):
        try:
            async with session.get(url, timeout=30) as resp:
                if resp.status != 200:
                    return False
                with open(temp_filepath, 'wb') as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        f.write(chunk)
            return True
        except Exception as e:
            with open('errorlog.txt', 'a', encoding='utf-8') as f:
                f.write(f"[ASYNC LEVEL3 ERROR] Failed to download {url}: {e}\n")
            return False

    async def async_run(self):
        import tempfile, pyart, numpy as np, io, base64, os, xml.etree.ElementTree as ET
        from pyproj import Geod
        from datetime import datetime, timedelta, timezone
        import matplotlib.pyplot as plt
        overlays = []
        now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
        total_steps = len(self.sites) * len(self.product_map) * self.frame_count
        step = 0
        sem = asyncio.Semaphore(10)  # Limit concurrency
        async with aiohttp.ClientSession() as session:
            tasks = []
            download_info = []
            for site in self.sites:
                site3 = site[-3:] if len(site) == 4 else site
                for product_name, product_code in self.product_map.items():
                    found_files = []
                    for day_offset in [0, 1]:
                        check_date_for_url = now_utc - timedelta(days=day_offset)
                        date_str = check_date_for_url.strftime('%Y%m%d')
                        tds_catalog_url = f"https://thredds.ucar.edu/thredds/catalog/nexrad/level3/{product_code}/{site3}/{date_str}/catalog.xml"
                        try:
                            async with session.get(tds_catalog_url, timeout=10) as response:
                                if response.status != 200:
                                    continue
                                xml_text = await response.text()
                                root = ET.fromstring(xml_text)
                                for dataset_element in root.findall('.//{http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0}dataset'):
                                    name = dataset_element.get('name')
                                    if name and name.endswith('.nids'):
                                        try:
                                            parts = name.split('_')
                                            if len(parts) >= 4:
                                                dt_str = parts[3].replace('.nids','')
                                                t = datetime.strptime(dt_str, '%Y%m%d%H%M')
                                            else:
                                                t = now_utc
                                        except Exception:
                                            t = now_utc
                                        file_url = f"https://thredds.ucar.edu/thredds/fileServer/nexrad/level3/{product_code}/{site3}/{date_str}/{name}"
                                        found_files.append((file_url, t))
                        except Exception as e:
                            with open('errorlog.txt', 'a', encoding='utf-8') as f:
                                f.write(f"[ASYNC THREDDS ERROR] {tds_catalog_url}: {e}\n")
                            continue
                    found_files.sort(key=lambda x: x[1], reverse=True)
                    unique_sorted_files = []
                    seen_urls = set()
                    for url, t in found_files:
                        if url not in seen_urls:
                            unique_sorted_files.append((url, t))
                            seen_urls.add(url)
                    latest_file_urls = [url for url, t in unique_sorted_files[:self.frame_count]][::-1]
                    for idx, url in enumerate(latest_file_urls):
                        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.nids')
                        temp_filepath = temp_file.name
                        temp_file.close()
                        download_info.append((site, product_code, url, temp_filepath))
                        async def download_and_process(site=site, product_code=product_code, url=url, temp_filepath=temp_filepath, idx=idx):
                            async with sem:
                                ok = await self.async_download_level3(session, url, temp_filepath)
                                nonlocal step
                                self.progress.emit(step, f"{site} {product_code}: Downloading {idx+1}/{len(latest_file_urls)}...")
                                if ok:
                                    try:
                                        radar = pyart.io.read_nexrad_level3(temp_filepath)
                                        field_name = list(radar.fields.keys())[0] if radar.fields else None
                                        vmin, vmax = None, None
                                        if field_name:
                                            data = radar.fields[field_name]['data']
                                            vmin = float(np.nanmin(data))
                                            vmax = float(np.nanmax(data))
                                        fig = plt.figure(figsize=(10,10), dpi=300)
                                        ax = fig.add_subplot(111)
                                        display = pyart.graph.RadarMapDisplay(radar)
                                        display.plot_ppi_map(
                                            field_name,
                                            ax=ax,
                                            title='',
                                            vmin=vmin,
                                            vmax=vmax,
                                            cmap=self.selected_ref_palette if self.selected_ref_palette else 'NWSRef',
                                            colorbar_flag=False,
                                            linewidth=0.2,
                                        )
                                        ax.set_axis_off()
                                        fig.patch.set_alpha(0.0)
                                        plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
                                        buf = io.BytesIO()
                                        plt.savefig(buf, format='png', transparent=True, bbox_inches='tight', pad_inches=0, dpi=300)
                                        plt.close(fig)
                                        buf.seek(0)
                                        img_b64 = base64.b64encode(buf.read()).decode('utf-8')
                                        radar_lat = radar.latitude['data'][0]
                                        radar_lon = radar.longitude['data'][0]
                                        max_range_meters = radar.range['data'][-1] if hasattr(radar, 'range') else 230000
                                        geod = Geod(ellps='WGS84')
                                        azimuths = np.linspace(0, 360, 360)
                                        lons_boundary, lats_boundary, _ = geod.fwd(
                                            np.full_like(azimuths, radar_lon),
                                            np.full_like(azimuths, radar_lat),
                                            azimuths,
                                            np.full_like(azimuths, max_range_meters)
                                        )
                                        min_lat, max_lat = np.min(lats_boundary), np.max(lats_boundary)
                                        min_lon, max_lon = np.min(lons_boundary), np.max(lons_boundary)
                                        overlays.append({
                                            'img_b64': img_b64,
                                            'bounds': [[min_lat, min_lon], [max_lat, max_lon]],
                                            'timestamp': str(radar.time['units']) if hasattr(radar, 'time') else '',
                                            'src_url': url,
                                            'radar_obj': radar,
                                            'field_name': field_name,
                                            'vmin': vmin,
                                            'vmax': vmax,
                                            'site': site,
                                            'product_code': product_code
                                        })
                                    except Exception as e:
                                        with open('errorlog.txt', 'a', encoding='utf-8') as f:
                                            f.write(f"[ASYNC LEVEL3 ERROR] Failed to process Level 3 file {url}: {e}\n")
                                if os.path.exists(temp_filepath):
                                    os.remove(temp_filepath)
                                step += 1
                        tasks.append(download_and_process())
            await asyncio.gather(*tasks)
        self.finished.emit(overlays)
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(list)
    def __init__(self, sites, product_map, frame_count, selected_ref_palette):
        super().__init__()
        self.sites = sites
        self.product_map = product_map
        self.frame_count = frame_count
        self.selected_ref_palette = selected_ref_palette
    def run(self):
        # Run the async download logic in a new event loop
        asyncio.run(self.async_run())




# --- Main Application Window ---
class MainWindow(QMainWindow):
    def on_spotter_reports_checkbox_toggled(self, state):
        # Toggle Spotter Reports overlay in JS
        checked = state == 2  # Qt.Checked == 2
        js = f"if(window.toggleSpotterReports) window.toggleSpotterReports({str(checked).lower()});"
        if hasattr(self, 'radar_map') and self.radar_map and self.radar_map.page() is not None:
            self.radar_map.page().runJavaScript(js)

    def on_spotter_positions_checkbox_toggled(self, state):
        # Toggle Spotter Positions overlay in JS
        checked = state == 2  # Qt.Checked == 2
        js = f"if(window.toggleSpotterPositions) window.toggleSpotterPositions({str(checked).lower()});"
        if hasattr(self, 'radar_map') and self.radar_map and self.radar_map.page() is not None:
            self.radar_map.page().runJavaScript(js)
    def fetch_spotter_network_reports(self):
        """
        Fetch Spotter Network reports and positions, parse, and send to JS as markers.
        """
        import requests
        import re
        import xml.etree.ElementTree as ET
        def parse_rss(xml_text):
            features = []
            try:
                root = ET.fromstring(xml_text)
                for item in root.findall('.//item'):
                    title = item.findtext('title', '')
                    desc = item.findtext('description', '')
                    lat = item.findtext('{http://www.w3.org/2003/01/geo/wgs84_pos#}lat')
                    lon = item.findtext('{http://www.w3.org/2003/01/geo/wgs84_pos#}long')
                    # Fallback: try to extract lat/lon from description if not present
                    if not lat or not lon:
                        m = re.search(r'Lat:\s*([\-\d.]+),\s*Lon:\s*([\-\d.]+)', desc)
                        if m:
                            lat, lon = m.group(1), m.group(2)
                    if lat and lon:
                        features.append({
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
                            "properties": {
                                "title": title,
                                "description": desc
                            }
                        })
            except Exception as e:
                print(f"[SPOTTER NETWORK] Failed to parse RSS: {e}")
            return features

        try:
            # Reports
            url_reports = "http://www.spotternetwork.org/feeds/rss-reports.xml"
            resp_reports = requests.get(url_reports, timeout=10)
            resp_reports.raise_for_status()
            features_reports = parse_rss(resp_reports.text)
            # Positions
            url_positions = "http://www.spotternetwork.org/feeds/rss-positions.xml"
            resp_positions = requests.get(url_positions, timeout=10)
            resp_positions.raise_for_status()
            features_positions = parse_rss(resp_positions.text)
            # Send to JS
            js = (
                f"if(window.setSpotterReports) window.setSpotterReports({json.dumps(features_reports)});"
                f"if(window.setSpotterPositions) window.setSpotterPositions({json.dumps(features_positions)});"
            )
            if self.radar_map and self.radar_map.page() is not None:
                self.radar_map.page().runJavaScript(js)
        except Exception as e:
            print(f"[SPOTTER NETWORK] Failed to fetch: {e}")

    def refresh_spotter_network_periodically(self):
        from PyQt5.QtCore import QTimer
        self.fetch_spotter_network_reports()
        if not hasattr(self, '_spotter_timer'):
            self._spotter_timer = QTimer(self)
            self._spotter_timer.timeout.connect(self.fetch_spotter_network_reports)
            self._spotter_timer.start(180000)  # every 3 minutes
    def fetch_nws_alert_polygons(self):
        """
        Fetch active NWS alert polygons from the NWS API and update the map with outlined, non-filled polygons.
        """
        import requests
        import json
        try:
            url = "https://api.weather.gov/alerts/active?status=actual&message_type=alert"
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            polygons = []
            for feature in data.get('features', []):
                props = feature.get('properties', {})
                geometry = feature.get('geometry', {})
                if geometry and geometry.get('type') == 'Polygon':
                    coords = geometry.get('coordinates', [])
                    if coords:
                        polygons.append({
                            'coordinates': coords[0],
                            'event': props.get('event', ''),
                            'severity': props.get('severity', ''),
                            'id': feature.get('id', ''),
                            'headline': props.get('headline', ''),
                            'areaDesc': props.get('areaDesc', ''),
                            'onset': props.get('onset', ''),
                            'ends': props.get('ends', ''),
                            'description': props.get('description', ''),
                            'instruction': props.get('instruction', ''),
                            'color': None  # Color and PDS handled in JS
                        })
            self.nws_alert_polygons = polygons
            self.update_nws_polygons_on_map()
        except Exception as e:
            print(f"[NWS POLYGON] Failed to fetch or parse NWS polygons: {e}")

    def _get_alert_color(self, event):
        # Simple color mapping for outline only (no fill)
        event = (event or '').lower()
        if 'tornado' in event:
            return '#FF0000'
        if 'severe' in event:
            return '#FFD600'
        if 'flood' in event:
            return '#00FF00'
        return '#00BFFF'

    def update_nws_polygons_on_map(self):
        """
        Send the NWS polygons to the JS map as outlined, non-filled polygons (RadarScope style).
        """
        if not hasattr(self, 'nws_alert_polygons') or not self.nws_alert_polygons:
            # Clear polygons if none
            if self.radar_map and self.radar_map.page() is not None:
                js = "if(window.setNWSAlertPolygons) window.setNWSAlertPolygons([]);"
                self.radar_map.page().runJavaScript(js)
            return
        js_polys = []
        for poly in self.nws_alert_polygons:
            js_polys.append({
                'coordinates': poly['coordinates'],
                'color': poly['color'],
                'id': poly['id'],
                'headline': poly['headline'],
                'event': poly['event'],
                'severity': poly['severity']
            })
        js = f"if(window.setNWSAlertPolygons) window.setNWSAlertPolygons({json.dumps(js_polys)});"
        if self.radar_map and self.radar_map.page() is not None:
            self.radar_map.page().runJavaScript(js)

    def refresh_nws_polygons_periodically(self):
        # Call this in __init__ to refresh polygons every 2 minutes
        from PyQt5.QtCore import QTimer
        self.fetch_nws_alert_polygons()
        if not hasattr(self, '_nws_poly_timer'):
            self._nws_poly_timer = QTimer(self)
            self._nws_poly_timer.timeout.connect(self.fetch_nws_alert_polygons)
            self._nws_poly_timer.start(120000)

    def prompt_for_maptiler_key(self):
        """
        Always prompt the user for a MapTiler API key if not set or if the key is empty/invalid.
        Never use a hardcoded or prefilled key.
        """
        key = self.settings.get('maptiler_key', '').strip()
        if not key:
            from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QMessageBox
            import webbrowser
            dlg = QDialog(self)
            dlg.setWindowTitle("MapTiler API Key Required")
            layout = QVBoxLayout(dlg)
            label = QLabel("<b>MapTiler API Key Required</b><br><br>To use the map background, you need a free MapTiler API key.<br>Sign up at <a href='https://cloud.maptiler.com/signup/'>https://cloud.maptiler.com/signup/</a> and paste your key below.")
            label.setOpenExternalLinks(True)
            layout.addWidget(label)
            key_input = QLineEdit()
            key_input.setPlaceholderText("Paste your MapTiler API key here...")
            layout.addWidget(key_input)
            signup_btn = QPushButton("Sign Up for Free Key")
            def open_signup():
                webbrowser.open('https://cloud.maptiler.com/signup/')
            signup_btn.clicked.connect(open_signup)
            layout.addWidget(signup_btn)
            ok_btn = QPushButton("Save Key and Continue")
            def save_key():
                entered = key_input.text().strip()
                if not entered:
                    QMessageBox.warning(dlg, "Missing Key", "Please enter your MapTiler API key.")
                    return
                self.settings['maptiler_key'] = entered
                self.save_settings_to_file()
                dlg.accept()
            ok_btn.clicked.connect(save_key)
            layout.addWidget(ok_btn)
            dlg.setLayout(layout)
            dlg.exec_()
            key = self.settings.get('maptiler_key', '').strip()
        return key
    def show_updates_window(self, on_close_callback=None):
        """
        Show a modal window with the latest updates/fixes if this is the first launch after an update.
        Uses self.VERSION and tracks last shown version in settings.
        """
        last_shown = self.settings.get('last_updates_version_shown', None)
        if last_shown == self.VERSION:
            if on_close_callback:
                on_close_callback()
            return
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QTextEdit
        dlg = QDialog(self)
        dlg.setWindowTitle("What's New in OVR-NEXRAD v{}".format(self.VERSION))
        dlg.setModal(True)
        layout = QVBoxLayout(dlg)
        label = QLabel("<b>Welcome to OVR-NEXRAD v{}</b><br><br>Here's what's new and improved in this version:".format(self.VERSION))
        layout.addWidget(label)
        updates_text = """
<ul>
<li><b>Major UI/UX polish and performance improvements</b></li>
<li>NWS polygons and overlays now use robust, instant toggles (never reappear unless toggled on)</li>
<li>Spotter Network overlays (reports/positions) <b>temporarily disabled</b> for this release (coming back soon!)</li>
<li>Duplicate "Polygons" checkbox removed from UI</li>
<li>Colorbar overlay is now modern, responsive, and always visible</li>
<li>MapTiler API key prompt blocks app start until entered</li>
<li>Bugfixes for animation, palette selection, and overlay reliability</li>
<li>Ready for GitHub launch</li>
</ul>
        """
        updates = QTextEdit()
        updates.setReadOnly(True)
        updates.setHtml(updates_text)
        updates.setStyleSheet("background: #232526; color: #f0f0f0; font-size: 13px; border: none;")
        layout.addWidget(updates)
        btn = QPushButton("Continue")
        btn.clicked.connect(dlg.accept)
        layout.addWidget(btn)
        dlg.setLayout(layout)
        dlg.resize(420, 320)
        dlg.exec_()
        # Mark as shown
        self.settings['last_updates_version_shown'] = self.VERSION
        self.save_settings_to_file()
        if on_close_callback:
            on_close_callback()

    def __init__(self):
        from PyQt5.QtCore import QTimer  # Ensure QTimer is imported at the top of __init__
        super().__init__()
        self.VERSION = "2.1.0"  # July 2025: Major UI/UX polish, robust overlays, Spotter Network temporarily disabled
        self.settings = {
            'window_width': 1200,
            'window_height': 900,
            'dpi': 100,
            'animation_frames': 14,
            'polygon_refresh': 120000,
            'default_site': DEFAULT_SITE,
        }
        self.settings_file = os.path.join(os.getcwd(), 'ovr_nexrad_settings.json')
        self.load_settings_from_file()
        self.setWindowTitle(f"OVR-NEXRAD v{self.VERSION}")
        self.setGeometry(100, 100, self.settings.get('window_width', 1200), self.settings.get('window_height', 900))
        self.setStyleSheet("background-color: #181a1b; color: #f0f0f0; font-family: Arial, 'Segoe UI', 'Liberation Sans', sans-serif;")
        self.sites = load_nexrad_sites(SITES_CSV)
        self.current_site = self.settings.get('default_site', DEFAULT_SITE)
        self.custom_cmaps = {}
        self.selected_ref_palette = None
        self.load_custom_palettes()
        self.ovrkast_polygons = []
        self.nws_alert_polygons = []
        self.animating = False
        self.radar_frames = []
        self.frame_index = 0
        self.last_s3_keys = set()
        self.s3_poll_timer = QTimer()
        self.s3_poll_timer.timeout.connect(self.check_for_new_sweep)
        self.s3_poll_timer.start(self.settings.get('polygon_refresh', 60000))
        self.anim_timer = QTimer(self)
        # Increase interval for smoother UI during animation (reduce lag)
        self.anim_timer.setInterval(250)  # 250ms = 4 FPS, adjust as needed
        self.anim_timer.timeout.connect(self._on_anim_timer)
        self.loading_screen = None
        # Show loading screen before main window
        self.loading_screen = LoadingScreen(self.VERSION)
        self.loading_screen.set_message("Loading initial Level 3 radar data for all US sites...")
        self.loading_screen.set_progress(0, 100)
        self.loading_screen.show()
        QApplication.processEvents()
        # Prompt for MapTiler key before initializing UI
        self.prompt_for_maptiler_key()
        # Now initialize UI (but don't show main window yet)
        self.init_ui()
        # Start periodic NWS polygon refresh
        self.refresh_nws_polygons_periodically()
        # Start periodic Spotter Network refresh
        self.refresh_spotter_network_periodically()

        # Check for updates on GitHub and notify user if available
        QTimer.singleShot(2000, self.check_for_updates)

        # Show updates window if needed, then load data
        def after_updates():
            QTimer.singleShot(100, self._start_initial_load)
        self.show_updates_window(after_updates)

    def check_for_updates(self):
        """
        Check GitHub for the latest release. If a newer version is available, show a popup with version and changelog.
        """
        import threading
        def fetch_and_notify():
            import requests
            try:
                api_url = "https://api.github.com/repos/Syphon1205/OVR-NEXRAD/releases/latest"
                resp = requests.get(api_url, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    latest_version = data.get("tag_name") or data.get("name")
                    if latest_version:
                        # Remove leading 'v' if present
                        latest_version = latest_version.lstrip('vV')
                    changelog = data.get("body", "")
                    if latest_version and self._is_newer_version(latest_version, self.VERSION):
                        self._show_update_popup(latest_version, changelog)
            except Exception as e:
                print(f"[UPDATE CHECK] Failed: {e}")
        threading.Thread(target=fetch_and_notify, daemon=True).start()

    def _is_newer_version(self, latest, current):
        # Compare version strings like '2.1.0' > '2.0.9'
        def parse(v):
            return [int(x) for x in v.split(".") if x.isdigit()]
        try:
            return parse(latest) > parse(current)
        except Exception:
            return False

    def _show_update_popup(self, latest_version, changelog):
        from PyQt5.QtWidgets import QMessageBox
        from PyQt5.QtCore import QTimer
        def show():
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Information)
            msg.setWindowTitle("Update Available!")
            msg.setText(f"A new version of OVR-NEXRAD is available: v{latest_version}")
            msg.setInformativeText("\nWhat's new:\n" + (changelog.strip() or "See GitHub for details."))
            msg.setStandardButtons(QMessageBox.Ok)
            msg.setDetailedText(changelog)
            msg.exec_()
        # Ensure runs on main thread
        QTimer.singleShot(0, show)

    def _start_initial_load(self):
        # Always default to Super-Res Reflectivity (N0B) at startup
        self.current_product_code = "N0B"
        self.current_product_name = "Super-Res Reflectivity (N0B)"
        # Set dropdown to N0B if not already
        idx = self.product_selector.findText("Super-Res Reflectivity (N0B)")
        if idx != -1:
            self.product_selector.setCurrentIndex(idx)
        self.show_loading_screen(f"Loading Level 3 radar data for {self.current_site}...", max_value=100)
        self.load_recent_level3_frames(site_id=self.current_site, frame_count=self.settings.get('animation_frames', 14), product_code="N0B", product_name="Super-Res Reflectivity (N0B)")
        self.hide_loading_screen()
        self.show()
    def show_loading_screen(self, message="Loading...", max_value=100):
        if self.loading_screen is None:
            self.loading_screen = LoadingScreen(self.VERSION)
        self.loading_screen.set_message(message)
        self.loading_screen.set_progress(0, max_value)
        self.loading_screen.show()
        QApplication.processEvents()

    def update_loading_progress(self, value, message=None):
        if self.loading_screen:
            if message:
                self.loading_screen.set_message(message)
            self.loading_screen.set_progress(value)

    def hide_loading_screen(self):
        if self.loading_screen:
            self.loading_screen.close()
            self.loading_screen = None

    def load_custom_palettes(self):
        """
        Recursively load all .pal files from assets/Colormaps and subfolders, and register as matplotlib colormaps.
        Palette names are unique by subfolder path (e.g., 'BV/awips_bv.pal').
        Also, index palettes by product type for filtering in the selector.
        """
        import os
        self.custom_cmaps = {}
        self.product_palettes = {
            'Reflectivity': [],
            'Velocity': [],
            'Spectrum Width': [],
            'Differential Reflectivity': [],
            'Correlation Coefficient': [],
            'Specific Differential Phase': [],
            'Other': []
        }
        self.palette_display_to_internal = {}  # display name -> internal name
        self.palette_internal_to_display = {}  # internal name -> display name
        base_dir = os.path.join(os.getcwd(), 'assets', 'Colormaps')
        for root, dirs, files in os.walk(base_dir):
            for file in files:
                if file.lower().endswith('.pal'):
                    pal_path = os.path.join(root, file)
                    rel_path = os.path.relpath(pal_path, base_dir)
                    name = rel_path.replace('\\', '/').replace(' ', '_')
                    display_name = os.path.splitext(file)[0]
                    orig_display_name = display_name
                    i = 2
                    while display_name in self.palette_display_to_internal:
                        display_name = f"{orig_display_name} ({i})"
                        i += 1
                    self.palette_display_to_internal[display_name] = name
                    self.palette_internal_to_display[name] = display_name
                    points = parse_pal_file(pal_path)
                    if points:
                        cmap = make_colormap_from_points(points, name)
                        self.custom_cmaps[name] = cmap
                        folder = os.path.dirname(rel_path).replace('\\', '/').lower()
                        fname = file.lower()
                        if 'ref' in folder or 'reflect' in folder or 'ref' in fname:
                            self.product_palettes['Reflectivity'].append(name)
                        elif 'bv' in folder or 'velo' in folder or 'vel' in fname:
                            self.product_palettes['Velocity'].append(name)
                        elif 'sw' in folder or 'spectrum' in folder or 'width' in fname:
                            self.product_palettes['Spectrum Width'].append(name)
                        elif 'zdr' in folder or 'differential' in folder or 'zdr' in fname:
                            self.product_palettes['Differential Reflectivity'].append(name)
                        elif 'cc' in folder or 'correlation' in folder or 'cc' in fname:
                            self.product_palettes['Correlation Coefficient'].append(name)
                        elif 'kdp' in folder or 'phase' in folder or 'kdp' in fname:
                            self.product_palettes['Specific Differential Phase'].append(name)
                        else:
                            self.product_palettes['Other'].append(name)

    def register_custom_palette(self, pal_path):
        """Register a new .pal file at runtime (from Settings dialog)."""
        name = os.path.basename(pal_path)
        points = parse_pal_file(pal_path)
        if points:
            cmap = make_colormap_from_points(points, name)
            self.custom_cmaps[name] = cmap
            # No UI palette selector to update; palettes are now per-product in settings
            self.load_custom_palettes()  # Refresh palette lists for settings dialog

    def open_settings_dialog(self):
        dlg = SettingsDialog(self, self.settings)
        # Add Apply button to dialog
        apply_btn = QPushButton("Apply", dlg)
        dlg.layout().addWidget(apply_btn)
        def on_apply():
            new_settings = dlg.get_settings()
            self.apply_settings(new_settings)
            self.save_settings_to_file()
        apply_btn.clicked.connect(on_apply)
        if dlg.exec_():
            new_settings = dlg.get_settings()
            self.apply_settings(new_settings)
            self.save_settings_to_file()

    def apply_settings(self, settings):
        # Apply all settings in realtime, no restart required
        old_width = self.settings.get('window_width', 1200)
        old_height = self.settings.get('window_height', 900)
        old_site = self.settings.get('default_site', self.current_site)
        old_frames = self.settings.get('animation_frames', 14)
        old_palettes = self.settings.get('product_palettes', {})
        self.settings.update(settings)
        # Resize window if needed
        if 'window_width' in self.settings and 'window_height' in self.settings:
            if self.settings['window_width'] != old_width or self.settings['window_height'] != old_height:
                self.resize(self.settings['window_width'], self.settings['window_height'])
        # Update animation frame count
        if 'animation_frames' in self.settings:
            self.frame_slider.setMaximum(max(0, self.settings['animation_frames']-1))
            # If frame count changed, reload frames for current site/product
            if self.settings['animation_frames'] != old_frames:
                self.load_recent_level3_frames(site_id=self.current_site, frame_count=self.settings['animation_frames'], product_code=self.current_product_code, product_name=self.current_product_name)
        # Update polygon refresh interval
        if 'polygon_refresh' in self.settings:
            self.s3_poll_timer.setInterval(self.settings['polygon_refresh'])
        # Update default site if changed
        if 'default_site' in self.settings and self.settings['default_site'] != old_site:
            self.current_site = self.settings['default_site']
            self.change_site_btn.setText(f"Site: {self.current_site}")
            self.load_recent_level3_frames(site_id=self.current_site, frame_count=self.settings.get('animation_frames', 14), product_code=self.current_product_code, product_name=self.current_product_name)
        # Update per-product palettes in realtime
        if 'product_palettes' in self.settings and self.settings['product_palettes'] != old_palettes:
            self.update_colorbar()
            self.update_canvas()
        # Always update canvas for any other changes
        self.update_canvas()
        self.save_settings_to_file()

    def save_settings_to_file(self):
        try:
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                import json
                json.dump(self.settings, f, indent=2)
        except Exception as e:
            with open('errorlog.txt', 'a', encoding='utf-8') as f:
                f.write(f"[SETTINGS ERROR] Failed to save settings: {e}\n")

    def load_settings_from_file(self):
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    import json
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        self.settings.update(loaded)
        except Exception as e:
            with open('errorlog.txt', 'a', encoding='utf-8') as f:
                f.write(f"[SETTINGS ERROR] Failed to load settings: {e}\n")


    def init_ui(self):
        # --- Top Bar ---
        top_bar = QWidget()
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(8, 4, 8, 4)
        top_layout.setSpacing(16)
        # App title
        top_layout.addWidget(QLabel("OVR-NEXRAD"))
        # --- Site Selector Button (RadarOmega style) ---
        self.change_site_btn = QPushButton(f"Site: {self.current_site}")
        self.change_site_btn.clicked.connect(self.open_site_selector_modal)
        top_layout.addWidget(self.change_site_btn)
        # --- Data/Model Dropdown (switches between radar and model controls) ---
        self.data_model_selector = QComboBox()
        self.data_model_selector.addItems(["Radar"])
        self.data_model_selector.setCurrentIndex(0)
        self.data_model_selector.currentIndexChanged.connect(self.on_data_model_change)
        top_layout.addWidget(QLabel("Data:"))
        top_layout.addWidget(self.data_model_selector)

        # --- Polygons Checkbox ---
        self.polygons_checkbox = QCheckBox("Polygons")
        self.polygons_checkbox.setChecked(True)
        self.polygons_checkbox.stateChanged.connect(self.on_polygons_checkbox_toggled)
        top_layout.addWidget(self.polygons_checkbox)

        # --- Spotter Reports Checkbox (hidden for next update) ---
        self.spotter_reports_checkbox = QCheckBox("Spotter Reports")
        self.spotter_reports_checkbox.setChecked(True)
        self.spotter_reports_checkbox.setVisible(False)
        self.spotter_reports_checkbox.stateChanged.connect(self.on_spotter_reports_checkbox_toggled)
        # top_layout.addWidget(self.spotter_reports_checkbox)

        # --- Spotter Positions Checkbox (hidden for next update) ---
        self.spotter_positions_checkbox = QCheckBox("Spotter Positions")
        self.spotter_positions_checkbox.setChecked(True)
        self.spotter_positions_checkbox.setVisible(False)
        self.spotter_positions_checkbox.stateChanged.connect(self.on_spotter_positions_checkbox_toggled)
        # top_layout.addWidget(self.spotter_positions_checkbox)

        # --- Model Data Dropdown (hidden/removed) ---
        self.model_selector = QComboBox()
        self.model_selector.setVisible(False)
        # --- Radar controls group (for easy show/hide) ---
        self.radar_controls = []
        # --- Level 3 product selector removed (green rectangle deleted) ---
        # (Product selector removed entirely)
        # Polygon Infill Toggle (keep, but move if needed)
        self.polygon_infill_checkbox = QCheckBox("Polygon Infill")
        self.polygon_infill_checkbox.setChecked(True)
        self.polygon_infill_checkbox.stateChanged.connect(self.on_polygon_infill_toggle)
        self.radar_controls.append(self.polygon_infill_checkbox)
        # Optionally, move to a different layout if needed, but do not add to top_layout to avoid duplicate "Polygons" checkbox

        # --- Animation Controls (grouped, icon only, with frame slider next to them) ---
        anim_widget = QWidget()
        anim_layout = QHBoxLayout(anim_widget)
        anim_layout.setContentsMargins(0, 0, 0, 0)
        anim_layout.setSpacing(2)
        self.play_btn = QPushButton("▶")
        self.play_btn.setCheckable(True)
        self.play_btn.clicked.connect(self.on_play_pause)
        anim_layout.addWidget(self.play_btn)
        self.step_back_btn = QPushButton("⏮")
        self.step_back_btn.clicked.connect(self.on_step_back)
        anim_layout.addWidget(self.step_back_btn)
        self.step_fwd_btn = QPushButton("⏭")
        self.step_fwd_btn.clicked.connect(self.on_step_fwd)
        anim_layout.addWidget(self.step_fwd_btn)
        # --- Frame slider for animation (move next to play controls) ---
        self.frame_slider = QSlider(Qt.Horizontal)
        self.frame_slider.setMinimum(0)
        self.frame_slider.setMaximum(0)
        self.frame_slider.setValue(0)
        self.frame_slider.setTickInterval(1)
        self.frame_slider.valueChanged.connect(self.on_frame_slider_change)
        self.radar_controls.append(self.frame_slider)
        anim_layout.addWidget(self.frame_slider)
        self.radar_controls.append(anim_widget)
        top_layout.addWidget(anim_widget)

        # --- Main Layout ---
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(top_bar)
        # Radar map area (QWebEngineView)
        self.radar_map = QWebEngineView()
        # JS Bridge for site selection
        self.channel = QWebChannel()
        self.js_bridge = JSBridge(self)
        self.channel.registerObject('backend', self.js_bridge)  # Use 'backend' for JS
        html_path = os.path.abspath(os.path.join("assets", "radar_map.html"))
        with open(html_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        self.radar_map.setHtml(html_content, QUrl.fromLocalFile(html_path))
        self.radar_map.loadFinished.connect(self.on_map_load_finished)
        main_layout.addWidget(self.radar_map, stretch=1)
        self.setCentralWidget(main_widget)
        # Status Bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.setStyleSheet("background: #181a1b; color: #f0f0f0; font-size: 12px; font-family: Arial, 'Segoe UI', 'Liberation Sans', sans-serif;")
        self.status.showMessage("Live radar data loaded.")
        # (Frame slider is now next to play controls)
        # --- Palette selector removed from main UI ---
        # --- Level 2/Level 3 selector ---
        # --- Level selector removed (Level 3 only) ---
        # Now that all widgets are created, update palette selector
        # self.update_palette_selector("reflectivity")
        # --- Level 3 product selector ---
        self.product_selector = QComboBox()
        # Only use the correct, modern Level 3 products (no N0Q/N0U)
        self.product_selector.addItems([
            "Super-Res Reflectivity (N0B)",
            "Super-Res Velocity (N0G)",
            "Spectrum Width (NSW)",
            "Differential Reflectivity (N0X)",
            "Correlation Coefficient (N0C)",
            "Specific Differential Phase (N0K)",
            "Hydrometeor Classification (N0H)",
            "High-Res VIL (DVL)",
            "Enhanced Echo Tops (EET)",
            "Storm Total Accum (DTA)",
            "Digital 1-Hour Accum (DAA)",
            "Composite Reflectivity (NCZ)",
            "Mid-Layer Composite (NML)",
            "High-Layer Composite (NHL)",
            "1-Hour Precip (N1P)",
            "3-Hour Precip (N3P)",
            "Storm Total Precip (NTP)",
            "Echo Tops (NET)",
            "Vertically Integrated Liquid (NVL)",
            "Mesocyclone Detection (NMD)",
            "Tornado Vortex Signature (NTV)",
            "Hail Index (NHI)",
            "Storm Structure (NSS)"
        ])
        self.product_selector.setVisible(True)
        self.product_selector.currentIndexChanged.connect(self.on_product_change)
        self.radar_controls.append(self.product_selector)
        top_layout.addWidget(self.product_selector)
        # --- Settings Button ---
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.clicked.connect(self.open_settings_dialog)
        top_layout.addWidget(self.settings_btn)
        # Add tooltips for main controls
        self.change_site_btn.setToolTip("Change the radar site (station)")
        self.data_model_selector.setToolTip("Switch between Radar and Model data")
        # Level 2 tooltips removed
        self.polygon_infill_checkbox.setToolTip("Toggle polygon infill for warnings")
        self.play_btn.setToolTip("Play/Pause radar animation")
        self.step_back_btn.setToolTip("Step to previous frame")
        self.step_fwd_btn.setToolTip("Step to next frame")
        self.frame_slider.setToolTip("Select animation frame")
        # self.palette_selector.setToolTip("Select radar color palette")
        # self.level_selector.setToolTip("Switch between Level 2 and Level 3 radar data")
        self.product_selector.setToolTip("Select Level 3 radar product")
        self.settings_btn.setToolTip("Open application settings")
        # Add spacing and alignment improvements
        top_layout.setSpacing(18)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)
        # Add a QFrame for the top bar for subtle shadow/border
        top_bar.setObjectName("TopBarFrame")

        # --- Modern Colorbar Overlay (RadarScope style, responsive) ---
        self.colorbar_container = QWidget()
        self.colorbar_container.setAttribute(Qt.WA_TranslucentBackground)
        colorbar_layout = QVBoxLayout(self.colorbar_container)
        colorbar_layout.setContentsMargins(0, 0, 0, 0)
        colorbar_layout.setSpacing(0)
        # Colorbar image
        self.colorbar_label = QLabel()
        self.colorbar_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.colorbar_label.setStyleSheet("background: transparent; border-radius: 8px; margin: 0px;")
        self.colorbar_label.setVisible(True)
        # Min/max labels
        minmax_layout = QHBoxLayout()
        minmax_layout.setContentsMargins(24, 0, 24, 0)
        minmax_layout.setSpacing(0)
        self.colorbar_min_label = QLabel()
        self.colorbar_min_label.setStyleSheet("color: #f0f0f0; font-size: 11px; background: transparent; margin-top: 2px;")
        self.colorbar_min_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.colorbar_max_label = QLabel()
        self.colorbar_max_label.setStyleSheet("color: #f0f0f0; font-size: 11px; background: transparent; margin-top: 2px;")
        self.colorbar_max_label.setAlignment(Qt.AlignRight | Qt.AlignTop)
        minmax_layout.addWidget(self.colorbar_min_label, 1)
        minmax_layout.addWidget(self.colorbar_max_label, 1)
        colorbar_layout.addWidget(self.colorbar_label, 0, Qt.AlignHCenter)
        colorbar_layout.addLayout(minmax_layout)
        # Outer container for centering
        outer_colorbar_container = QWidget()
        outer_layout = QHBoxLayout(outer_colorbar_container)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addStretch(1)
        outer_layout.addWidget(self.colorbar_container)
        outer_layout.addStretch(1)
        main_layout.addWidget(outer_colorbar_container, stretch=0)
        # Responsive colorbar: track window resize
        self._last_colorbar_width = None

    def on_polygons_checkbox_toggled(self, state):
        # This function is called when the "Polygons" checkbox is toggled
        # It will call the JS function togglePolygons() in the web view
        checked = state == 2  # Qt.Checked == 2
        js = f"if(window.togglePolygons) window.togglePolygons({str(checked).lower()});"
        if hasattr(self, 'radar_map') and self.radar_map and self.radar_map.page() is not None:
            self.radar_map.page().runJavaScript(js)
    def resizeEvent(self, event):
        self._on_resize_event(event)
        super().resizeEvent(event)
    def _on_resize_event(self, event):
        # Responsive colorbar: update width and redraw
        self.update_colorbar()
        return super().resizeEvent(event)
    def update_colorbar(self):
        """
        Generate and display a colorbar for the current product and palette.
        """
        import matplotlib.pyplot as plt
        import matplotlib as mpl
        from matplotlib.colors import Normalize
        import io
        product_name = getattr(self, 'current_product_name', None)
        palette_name = None
        vmin, vmax = None, None
        # Try to get palette and vmin/vmax for current product
        if product_name and 'product_palettes' in self.settings:
            palette_name = self.settings['product_palettes'].get(product_name)
        cmap = None
        if palette_name:
            if hasattr(self, 'custom_cmaps') and palette_name in self.custom_cmaps:
                cmap = self.custom_cmaps[palette_name]
            elif palette_name in plt.colormaps():
                cmap = palette_name
        if cmap is None:
            cmap = 'NWSRef'
        # Try to get vmin/vmax from current frame if available
        vmin, vmax = 0, 70
        if self.radar_frames:
            current_code = getattr(self, 'current_product_code', 'N0B')
            filtered_frames = [f for f in self.radar_frames if f.get('product_code') == current_code]
            if filtered_frames:
                frame = filtered_frames[self.frame_index % len(filtered_frames)]
                vmin = frame.get('vmin', vmin)
                vmax = frame.get('vmax', vmax)
        # If cmap is a custom colormap with _ovr_vmin/_ovr_vmax, use those
        if hasattr(cmap, '_ovr_vmin'):
            vmin = getattr(cmap, '_ovr_vmin', vmin)
            vmax = getattr(cmap, '_ovr_vmax', vmax)
        # Responsive width: use a fraction of the window width (e.g., 60%)
        win_width = self.width() if hasattr(self, 'width') else 1200
        bar_width_px = max(240, int(win_width * 0.6))
        bar_height_px = 32
        fig = plt.figure(figsize=(bar_width_px/100, bar_height_px/100), dpi=100)
        ax = fig.add_axes((0.03, 0.2, 0.94, 0.6))
        norm = Normalize(vmin=vmin, vmax=vmax)
        from matplotlib.colorbar import ColorbarBase
        cb = ColorbarBase(ax, cmap=plt.get_cmap(cmap), norm=norm, orientation='horizontal')
        cb.set_label("")
        # Patch: Some matplotlib versions have cb.outline as a Spine, not a callable
        try:
            if hasattr(cb.outline, 'set_linewidth'):
                cb.outline.set_linewidth(1.2)
            if hasattr(cb.outline, 'set_edgecolor'):
                cb.outline.set_edgecolor('#222')
        except Exception:
            pass
        cb.ax.tick_params(labelsize=0, length=0)  # Hide ticks
        cb.set_ticks([])
        # Remove all spines
        for spine in ax.spines.values():
            spine.set_visible(False)
        # Remove axis
        ax.set_axis_off()
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', pad_inches=0.01, transparent=True)
        plt.close(fig)
        buf.seek(0)
        from PyQt5.QtGui import QPixmap
        pixmap = QPixmap()
        pixmap.loadFromData(buf.read())
        self.colorbar_label.setPixmap(pixmap)
        self.colorbar_label.setVisible(True)
        # Set min/max labels
        self.colorbar_min_label.setText(f"{int(round(vmin))}")
        self.colorbar_max_label.setText(f"{int(round(vmax))}")

    def load_and_update_all(self):
        # Minimal stub: just update canvas
        self.update_canvas()

    def download_level3_from_thredds(self, site, product_code, dt):
        # Minimal stub: return None
        return None

    def _get_available_tds_files(self, radar_id, product_code, base_time_utc, num_to_fetch=20):
        # Minimal stub: return empty list
        return []

    def _parse_tds_catalog_xml(self, xml_root, product_code, base_time_utc):
        # Minimal stub: return empty list
        return []

    def _parse_s3_index_html(self, url, site_id):
        """Parse the S3 index HTML for a given URL and return matching Level 2 file keys for the site."""
        import re
        import requests
        from urllib.parse import urljoin
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                print(f"[S3-INDEX] Failed to fetch {url}: HTTP {resp.status_code}")
                return []
            html = resp.text
            # Find all hrefs that look like SITEYYYYMMDD_HHMMSS_V.. files
            pattern = re.compile(rf'href=[\"\"]/([A-Z]{{4}}\d{{8}}_\d{{6}}_V..)')
            matches = pattern.findall(html)
            # Remove duplicates and filter for correct site
            files = [m for m in set(matches) if m.startswith(site_id)]
            print(f"[S3-INDEX] Found {len(files)} files in HTML index for {site_id}")
            return files
        except Exception as e:
            print(f"[S3-INDEX] Error parsing index HTML {url}: {e}")
            return []

    def _parse_iem_index_html(self, url, site_id):
        """Parse the IEM Mesonet HTML directory for Level 2 .ar2v or .ar2v.gz files for the site."""
        import re
        import requests
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            print("[IEM-INDEX] BeautifulSoup4 is required for IEM fallback. Please install with 'pip install beautifulsoup4'.")
            return []
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                print(f"[IEM-INDEX] Failed to fetch {url}: HTTP {resp.status_code}")
                return []
            soup = BeautifulSoup(resp.text, 'html.parser')
            links = soup.find_all('a', href=True)
            # Match .ar2v or .ar2v.gz files for the site
            pattern = re.compile(rf'^{site_id}.*\\.ar2v(\\.gz)?$')
            files = []
            for a in links:
                href = getattr(a, 'href', None)
                if isinstance(href, str) and pattern.match(href):
                    files.append(href)
            print(f"[IEM-INDEX] Found {len(files)} .ar2v files in IEM index for {site_id}")
            return files
        except Exception as e:
            print(f"[IEM-INDEX] Error parsing IEM index HTML {url}: {e}")
            return []


    # load_recent_level2_frames removed (Level 3 only)
            self.frame_slider.setEnabled(True)
            self.play_btn.setEnabled(True)
            self.step_back_btn.setEnabled(True)
            self.step_fwd_btn.setEnabled(True)
            self.update_canvas()
            cleanup()
        def on_error(msg):
            print(msg)
            self.status.showMessage(msg, 5000)
            self.frame_slider.setEnabled(True)
            self.play_btn.setEnabled(True)
            self.step_back_btn.setEnabled(True)
            self.step_fwd_btn.setEnabled(True)
            cleanup()
        self._radar_loader_worker.finished.connect(on_finished)
        self._radar_loader_worker.error.connect(on_error)
        self._radar_loader_thread.started.connect(self._radar_loader_worker.run)
        self._radar_loader_thread.start()

    def _render_radar_png_from_obj(self, radar, field_name, vmin, vmax, cmap_to_use=None):
        """
        Render a radar PNG from a cached radar object and return base64 string.
        Use the per-product palette from settings if available. Robust error handling and fallback.
        """
        import traceback
        product_name = getattr(self, 'current_product_name', None)
        palette_name = None
        if product_name and 'product_palettes' in self.settings:
            palette_name = self.settings['product_palettes'].get(product_name)
        # Use the correct colormap
        cmap = None
        if palette_name:
            # Try custom colormaps first
            if hasattr(self, 'custom_cmaps') and palette_name in self.custom_cmaps:
                cmap = self.custom_cmaps[palette_name]
            # Try matplotlib colormaps
            elif palette_name in plt.colormaps():
                cmap = palette_name
        if cmap is None:
            cmap = cmap_to_use if cmap_to_use else 'NWSRef'
        # Fallback to NWSRef if cmap is still not valid
        if cmap is None or (isinstance(cmap, str) and cmap not in plt.colormaps()):
            cmap = 'NWSRef'
        try:
            # If cmap is a custom colormap with _ovr_vmin/_ovr_vmax, use those
            if hasattr(cmap, '_ovr_vmin'):
                vmin = getattr(cmap, '_ovr_vmin', vmin)
                vmax = getattr(cmap, '_ovr_vmax', vmax)
            fig = plt.figure(figsize=(10, 10), dpi=300)
            ax = fig.add_subplot(111)
            display = pyart.graph.RadarDisplay(radar)
            # Use interpolation for smoother look
            display.plot(
                field_name,
                0,
                ax=ax,
                title='',
                vmin=vmin,
                vmax=vmax,
                cmap=cmap,
                colorbar_flag=False,
                rasterized=False,  # Don't rasterize, keep as vector for smoothness
                linewidth=0.2
            )
            ax.set_axis_off()
            fig.patch.set_alpha(0.0)
            plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
            buf = io.BytesIO()
            plt.savefig(buf, format='png', transparent=True, bbox_inches='tight', pad_inches=0, dpi=300)
            plt.close(fig)
            buf.seek(0)
            img_b64 = base64.b64encode(buf.read()).decode('utf-8')
            return img_b64
        except Exception as e:
            with open('errorlog.txt', 'a', encoding='utf-8') as f:
                f.write(f"[RENDER ERROR] Failed to render radar PNG: {e}\n{traceback.format_exc()}\n")
            return None

    def update_palette_selector(self, product_name=None):
        # No-op: palette selection is now per-product in settings
        pass

    def on_palette_change(self, idx):
        # No-op: palette selection is now per-product in settings
        pass

    def on_site_change(self, site_id, auto_animate=False):
        """
        Change the current radar site, update UI, reload Level 3 data, and center the map on the selected site.
        """
        # Level 3 site IDs must start with 'K' (e.g., KTLX)
        if not site_id.upper().startswith('K'):
            site_id = 'K' + site_id.upper()
        else:
            site_id = site_id.upper()
        if site_id not in self.sites:
            print(f"[SITE] Unknown site: {site_id}")
            return
        self.current_site = site_id
        self.change_site_btn.setText(f"Site: {self.current_site}")
        self.settings['default_site'] = site_id
        # Center the map on the selected site
        site_info = self.sites.get(site_id)
        if site_info and self.radar_map and self.radar_map.page() is not None:
            lat = site_info.get('lat')
            lon = site_info.get('lon')
            js = f"if (window.centerMapToLatLon) window.centerMapToLatLon({lat}, {lon});"
            self.radar_map.page().runJavaScript(js)
        self.show_loading_screen(f"Loading Level 3 radar data for {site_id}...", max_value=100)
        self.load_recent_level3_frames(site_id=site_id, frame_count=self.settings.get('animation_frames', 14), product_code=self.current_product_code, product_name=self.current_product_name)
        self.hide_loading_screen()
        self.update_canvas()

    def check_for_new_sweep(self):
        """
        Periodically check for new sweeps and update frames if new data is found for the current site and product only.
        Only fetch and append the latest frame, not all frames again.
        """
        from datetime import datetime, timedelta, timezone
        import requests, tempfile, os, re, json, numpy as np
        import pyart
        from pyproj import Geod
        now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
        product_code = getattr(self, 'current_product_code', 'N0B')
        product_name = getattr(self, 'current_product_name', 'Super-Res Reflectivity (N0B)')
        site_id = self.current_site
        frame_count = self.settings.get('animation_frames', 14)
        # Always use the correct THREDDS Level 3 directory structure
        site_short = site_id[1:] if site_id.startswith('K') else site_id
        found_urls_with_times = []
        for day_offset in [0, 1]:
            check_date_for_url = now_utc - timedelta(days=day_offset)
            date_str = check_date_for_url.strftime('%Y%m%d')
            tds_catalog_url = f"https://thredds.ucar.edu/thredds/catalog/nexrad/level3/{product_code}/{site_short}/{date_str}/catalog.xml"
            try:
                response = requests.get(tds_catalog_url, timeout=10)
                response.raise_for_status()
                import xml.etree.ElementTree as ET
                root = ET.fromstring(response.content)
                for dataset_element in root.findall('.//{http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0}dataset'):
                    url_path = dataset_element.get('urlPath')
                    if url_path and url_path.endswith('.nids'):
                        url = f"https://thredds.ucar.edu/thredds/fileServer/{url_path}"
                        t = None
                        try:
                            m = re.search(r'(\d{8})_(\d{4})', url_path)
                            if m:
                                dt_str = m.group(1) + m.group(2)
                                t = datetime.strptime(dt_str, '%Y%m%d%H%M').replace(tzinfo=timezone.utc)
                        except Exception:
                            t = now_utc
                        found_urls_with_times.append((url, t))
            except Exception:
                continue
        found_urls_with_times.sort(key=lambda x: x[1] if x[1] else now_utc, reverse=True)
        seen_urls = set()
        unique_sorted_files = []
        for url, t in found_urls_with_times:
            if url not in seen_urls:
                unique_sorted_files.append((url, t))
                seen_urls.add(url)
        if not unique_sorted_files:
            return
        latest_url, latest_time = unique_sorted_files[0]
        # Check if this file is already in the buffer
        if self.radar_frames and any(latest_url == f.get('src_url') for f in self.radar_frames):
            return  # No new sweep
        # Download and append the latest frame only
        with tempfile.NamedTemporaryFile(delete=False, suffix='.nids') as tmp_file:
            temp_filepath = tmp_file.name
        try:
            file_response = requests.get(latest_url, stream=True, timeout=30)
            file_response.raise_for_status()
            if file_response.status_code != 200:
                if os.path.exists(temp_filepath):
                    os.remove(temp_filepath)
                return
            with open(temp_filepath, 'wb') as f:
                for chunk in file_response.iter_content(chunk_size=8192):
                    f.write(chunk)
            radar = pyart.io.read_nexrad_level3(temp_filepath)
            field_name = list(radar.fields.keys())[0] if radar.fields else None
            vmin, vmax = None, None
            if field_name:
                data = radar.fields[field_name]['data']
                vmin = float(np.nanmin(data))
                vmax = float(np.nanmax(data))
            cmap_to_use = None
            try:
                if self.selected_ref_palette:
                    if hasattr(self, 'custom_cmaps') and self.selected_ref_palette in self.custom_cmaps:
                        cmap_to_use = self.custom_cmaps[self.selected_ref_palette]

                    elif self.selected_ref_palette in plt.colormaps():
                        cmap_to_use = self.selected_ref_palette
                    else:
                        cmap_to_use = 'NWSRef'
                else:
                    cmap_to_use = 'NWSRef'
            except Exception:
                cmap_to_use = 'NWSRef'
            img_b64 = None
            if radar and field_name:
                try:
                    img_b64 = self._render_radar_png_from_obj(radar, field_name, vmin, vmax, cmap_to_use)
                except Exception as e:
                    with open('errorlog.txt', 'a', encoding='utf-8') as f:
                        f.write(f"[LEVEL3 ERROR] Failed to render PNG for {latest_url}: {e}\n")
            radar_lat = radar.latitude['data'][0]
            radar_lon = radar.longitude['data'][0]
            if hasattr(radar, 'range'):
                max_range_meters = radar.range['data'][-1]
            else:
                max_range_meters = 230000
            geod = Geod(ellps='WGS84')
            azimuths = np.linspace(0, 360, 720)
            lons_boundary, lats_boundary, _ = geod.fwd(
                np.full_like(azimuths, radar_lon),
                np.full_like(azimuths, radar_lat),
                azimuths,
                np.full_like(azimuths, max_range_meters)
            )
            min_lat, max_lat = np.min(lats_boundary), np.max(lats_boundary)
            min_lon, max_lon = np.min(lons_boundary), np.max(lons_boundary)
            new_frame = {
                'img_b64': img_b64,
                'bounds': [[min_lat, min_lon], [max_lat, max_lon]],
                'timestamp': str(radar.time['units']) if hasattr(radar, 'time') else '',
                'src_url': latest_url,
                'radar_obj': radar,
                'field_name': field_name,
                'vmin': vmin,
                'vmax': vmax,
                'site': site_id,
                'product_code': product_code
            }
            self.radar_frames.append(new_frame)
            # Trim to max frame count
            if len(self.radar_frames) > frame_count:
                self.radar_frames = self.radar_frames[-frame_count:]
            self.frame_index = len(self.radar_frames) - 1
            self.frame_slider.setMaximum(max(0, len(self.radar_frames)-1))
            self.frame_slider.setValue(self.frame_index)
            self.status.showMessage(f"New sweep loaded for {site_id} {product_name}.", 5000)
            if not self.anim_timer.isActive():
                self.update_canvas()
        except Exception as e:
            with open('errorlog.txt', 'a', encoding='utf-8') as f:
                f.write(f"[LEVEL3 ERROR] Failed to process new sweep {latest_url}: {e}\n")
        finally:
            if os.path.exists(temp_filepath):
                os.remove(temp_filepath)

    def _on_anim_timer(self):
        if not self.radar_frames:
            return
        self.frame_index = (self.frame_index + 1) % len(self.radar_frames)
        self.frame_slider.setValue(self.frame_index)
        self.update_canvas()

    def open_site_selector_modal(self):
        """Open a dialog to select a radar site."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Select Radar Site")
        layout = QVBoxLayout(dialog)
        list_widget = QListWidget(dialog)
        for site_id, info in self.sites.items():
            item = QListWidgetItem(f"{site_id} - {info['name']}")
            item.setData(32, site_id)  # 32 is Qt.UserRole
            list_widget.addItem(item)
        layout.addWidget(list_widget)
        def on_item_clicked(item):
            site_id = item.data(32)  # 32 is Qt.UserRole
            self.on_site_change(site_id)
            dialog.accept()
        list_widget.itemClicked.connect(on_item_clicked)
        dialog.setLayout(layout)
        dialog.exec_()

    def on_data_model_change(self, idx):
        # Switch between radar and model controls (future expansion)
        if self.data_model_selector.currentText() == "Radar":
            # self.level2_product_selector.setVisible(True)  # Level 2 removed
            self.tilt_selector.setVisible(True)
            self.polygon_infill_checkbox.setVisible(True)
            self.palette_selector.setVisible(True)
            self.level_selector.setVisible(True)
            self.product_selector.setVisible(False)
        else:
            # self.level2_product_selector.setVisible(False)  # Level 2 removed
            self.tilt_selector.setVisible(False)
            self.polygon_infill_checkbox.setVisible(False)
            self.palette_selector.setVisible(False)
            self.level_selector.setVisible(False)
            self.product_selector.setVisible(False)
        self.update_canvas()

    # Level 2 product and tilt change handlers removed

    def on_polygon_infill_toggle(self, state):
        # Toggle warning polygon infill (redraw overlays)
        self.update_canvas()

    def on_play_pause(self):
        # Start or stop animation
        if self.play_btn.isChecked():
            self.anim_timer.start()
        else:
            self.anim_timer.stop()

    def on_step_back(self):
        # Step to previous frame
        if self.radar_frames:
            self.frame_index = (self.frame_index - 1) % len(self.radar_frames)
            self.frame_slider.setValue(self.frame_index)
            self.update_canvas()

    def on_step_fwd(self):
        # Step to next frame
        if self.radar_frames:
            self.frame_index = (self.frame_index + 1) % len(self.radar_frames)
            self.frame_slider.setValue(self.frame_index)
            self.update_canvas()

    def on_frame_slider_change(self, value):
        # Change displayed frame
        self.frame_index = value
        self.update_canvas()

    # Level selector removed (Level 3 only)



    # Duplicate on_product_change removed (Level 3 only)

    def load_recent_level3_frames(self, site_id=None, frame_count=14, product_code=None, product_name=None):
        """
        Load the most recent Level 3 sweeps for the selected radar site and product only.
        Only loads data for the given site_id and product_code, using the official THREDDS UCAR Level 3 catalog and fileServer URLs.
        """
        import requests, tempfile, pyart, numpy as np, io, base64, os, xml.etree.ElementTree as ET
        from pyproj import Geod
        from datetime import datetime, timedelta, timezone
        import matplotlib.pyplot as plt
        self.status.showMessage(f"Loading Level 3 radar data for {site_id}...")
        # Always clear old frames when switching products
        self.radar_frames = []
        self.frame_index = 0
        # Product map for dropdown (should match dropdown options)
        product_map = {
            "Super-Res Reflectivity (N0B)": "N0B",
            "Super-Res Velocity (N0G)": "N0G",
            "Spectrum Width (NSW)": "NSW",
            "Differential Reflectivity (N0X)": "N0X",
            "Correlation Coefficient (N0C)": "N0C",
            "Specific Differential Phase (N0K)": "N0K",
            "Hydrometeor Classification (N0H)": "N0H",
            "High-Res VIL (DVL)": "DVL",
            "Enhanced Echo Tops (EET)": "EET",
            "Storm Total Accum (DTA)": "DTA",
            "Digital 1-Hour Accum (DAA)": "DAA",
            "Composite Reflectivity (NCZ)": "NCZ",
            "Mid-Layer Composite (NML)": "NML",
            "High-Layer Composite (NHL)": "NHL",
            "1-Hour Precip (N1P)": "N1P",
            "3-Hour Precip (N3P)": "N3P",
            "Storm Total Precip (NTP)": "NTP",
            "Echo Tops (NET)": "NET",
            "Vertically Integrated Liquid (NVL)": "NVL",
            "Mesocyclone Detection (NMD)": "NMD",
            "Tornado Vortex Signature (NTV)": "NTV",
            "Hail Index (NHI)": "NHI",
            "Storm Structure (NSS)": "NSS",
        }
        if site_id is None:
            site_id = self.current_site
        # Only use the correct product code from the dropdown/product_map, never fallback to legacy or alternate codes
        # --- ENFORCE CORRECT PRODUCT CODE ---
        allowed_codes = list(product_map.values())
        if product_code is None or product_code not in allowed_codes:
            product_code = "N0B"
            product_name = "Super-Res Reflectivity (N0B)"
        if product_name is None:
            for k, v in product_map.items():
                if v == product_code:
                    product_name = k
                    break
            if product_name is None:
                self.status.showMessage("Unknown product code. No data loaded.", 5000)
                return
        self.current_product_code = product_code
        self.current_product_name = product_name
        debug_msg = f"[DEBUG] load_recent_level3_frames: Using product_code={product_code}, product_name={product_name}"
        print(debug_msg)
        if hasattr(self, 'status') and self.status:
            self.status.showMessage(debug_msg, 5000)
        with open('errorlog.txt', 'a', encoding='utf-8') as f:
            f.write(debug_msg + '\n')
        now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
        overlays = []
        total_steps = frame_count
        self.show_loading_screen(f"Loading Level 3 {product_name} for {site_id}...", max_value=total_steps)
        self.frame_slider.setEnabled(False)
        self.play_btn.setEnabled(False)
        self.step_back_btn.setEnabled(False)
        self.step_fwd_btn.setEnabled(False)
        step = 0
        all_found_urls_with_times = []
        # Always use the correct THREDDS Level 3 directory structure:
        # https://thredds.ucar.edu/thredds/catalog/nexrad/level3/{product_code}/{site_short}/{date_str}/catalog.xml
        # site_short is always the 3-letter site (e.g., TLX for KTLX)
        site_short = site_id[1:] if site_id.startswith('K') else site_id
        for day_offset in [0, 1]:
            check_date_for_url = now_utc - timedelta(days=day_offset)
            date_str = check_date_for_url.strftime('%Y%m%d')
            tds_catalog_url = f"https://thredds.ucar.edu/thredds/catalog/nexrad/level3/{product_code}/{site_short}/{date_str}/catalog.xml"
            try:
                response = requests.get(tds_catalog_url, timeout=10)
                response.raise_for_status()
                root = ET.fromstring(response.content)
                for dataset_element in root.findall('.//{http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0}dataset'):
                    url_path = dataset_element.get('urlPath')
                    if url_path and url_path.endswith('.nids'):
                        url = f"https://thredds.ucar.edu/thredds/fileServer/{url_path}"
                        t = None
                        try:
                            m = re.search(r'(\d{8})_(\d{4})', url_path)
                            if m:
                                dt_str = m.group(1) + m.group(2)
                                t = datetime.strptime(dt_str, '%Y%m%d%H%M').replace(tzinfo=timezone.utc)
                        except Exception:
                            t = now_utc
                        all_found_urls_with_times.append((url, t))
            except Exception:
                continue
        all_found_urls_with_times.sort(key=lambda x: x[1] if x[1] else now_utc, reverse=True)
        unique_sorted_files = []
        seen_urls = set()
        for url, t in all_found_urls_with_times:
            if url not in seen_urls:
                unique_sorted_files.append((url, t))
                seen_urls.add(url)
        latest_file_urls = [url for url, t in unique_sorted_files[:frame_count]][::-1]
        for idx, url in enumerate(latest_file_urls):
            with tempfile.NamedTemporaryFile(delete=False, suffix='.nids') as tmp_file:
                temp_filepath = tmp_file.name
            try:
                self.update_loading_progress(step, f"{site_id} {product_code}: Downloading {idx+1}/{len(latest_file_urls)}...")
                file_response = requests.get(url, stream=True, timeout=30)
                file_response.raise_for_status()
                if file_response.status_code != 200:
                    if os.path.exists(temp_filepath):
                        os.remove(temp_filepath)
                    step += 1
                    continue
                with open(temp_filepath, 'wb') as f:
                    for chunk in file_response.iter_content(chunk_size=8192):
                        f.write(chunk)
                radar = pyart.io.read_nexrad_level3(temp_filepath)
                field_name = list(radar.fields.keys())[0] if radar.fields else None
                print(f"[DEBUG] {product_code} file {url} field_name: {field_name}")
                vmin, vmax = None, None
                if field_name:
                    data = radar.fields[field_name]['data']
                    vmin = float(np.nanmin(data))
                    vmax = float(np.nanmax(data))
                # Use classic RadarDisplay.plot for Level 3 overlays
                cmap_to_use = None
                try:
                    if self.selected_ref_palette:
                        if hasattr(self, 'custom_cmaps') and self.selected_ref_palette in self.custom_cmaps:
                            cmap_to_use = self.custom_cmaps[self.selected_ref_palette]
                        elif self.selected_ref_palette in plt.colormaps():
                            cmap_to_use = self.selected_ref_palette
                        else:
                            cmap_to_use = 'NWSRef'
                    else:
                        cmap_to_use = 'NWSRef'
                except Exception:
                    cmap_to_use = 'NWSRef'
                img_b64 = None
                if radar and field_name:
                    try:
                        img_b64 = self._render_radar_png_from_obj(radar, field_name, vmin, vmax, cmap_to_use)
                    except Exception as e:
                        with open('errorlog.txt', 'a', encoding='utf-8') as f:
                            f.write(f"[LEVEL3 ERROR] Failed to render PNG for {url}: {e}\n")
                radar_lat = radar.latitude['data'][0]
                radar_lon = radar.longitude['data'][0]
                # Use the true radar dish range for dish clipping (classic look)
                if hasattr(radar, 'range'):
                    max_range_meters = radar.range['data'][-1]
                else:
                    # Fallback to 230km
                    max_range_meters = 230000
                geod = Geod(ellps='WGS84')
                azimuths = np.linspace(0, 360, 720)  # finer for smoother circle
                lons_boundary, lats_boundary, _ = geod.fwd(
                    np.full_like(azimuths, radar_lon),
                    np.full_like(azimuths, radar_lat),
                    azimuths,
                    np.full_like(azimuths, max_range_meters)
                )
                min_lat, max_lat = np.min(lats_boundary), np.max(lats_boundary)
                min_lon, max_lon = np.min(lons_boundary), np.max(lons_boundary)
                overlays.append({
                    'img_b64': img_b64,
                    'bounds': [[min_lat, min_lon], [max_lat, max_lon]],
                    'timestamp': str(radar.time['units']) if hasattr(radar, 'time') else '',
                    'src_url': url,
                    'radar_obj': radar,
                    'field_name': field_name,
                    'vmin': vmin,
                    'vmax': vmax,
                    'site': site_id,
                    'product_code': product_code
                })
            except Exception as e:
                with open('errorlog.txt', 'a', encoding='utf-8') as f:
                    f.write(f"[LEVEL3 ERROR] Failed to process Level 3 file {url}: {e}\n")
            finally:
                if os.path.exists(temp_filepath):
                    os.remove(temp_filepath)
                step += 1
                self.update_loading_progress(step)
        if not overlays:
            self.status.showMessage(f"No Level 3 frames found for {site_id} {product_name} ({product_code})!", 7000)
            print(f"[DEBUG] No Level 3 frames found for {site_id} {product_name} ({product_code})!")
        self.radar_frames = overlays
        self.frame_index = len(self.radar_frames) - 1 if self.radar_frames else 0
        self.frame_slider.setMaximum(max(0, len(self.radar_frames)-1))
        self.frame_slider.setValue(self.frame_index)
        self.status.showMessage(f"{len(self.radar_frames)} Level 3 frames loaded for {site_id} {product_name}.", 5000)
        self.frame_slider.setEnabled(True)
        self.play_btn.setEnabled(True)
        self.step_back_btn.setEnabled(True)
        self.step_fwd_btn.setEnabled(True)
        self.hide_loading_screen()
        self.update_canvas()

    def on_product_change(self, idx):
        """
        Handler for product dropdown change. Loads only the selected product for the current site.
        Always uses the correct product code and THREDDS directory structure, no legacy fallback.
        """
        # Get product name and code from dropdown
        product_name = self.product_selector.currentText()
        # Map product name to code (must match dropdown and THREDDS)
        product_map = {
            "Super-Res Reflectivity (N0B)": "N0B",
            "Super-Res Velocity (N0G)": "N0G",
            "Spectrum Width (NSW)": "NSW",
            "Differential Reflectivity (N0X)": "N0X",
            "Correlation Coefficient (N0C)": "N0C",
            "Specific Differential Phase (N0K)": "N0K",
            "Hydrometeor Classification (N0H)": "N0H",
            "High-Res VIL (DVL)": "DVL",
            "Enhanced Echo Tops (EET)": "EET",
            "Storm Total Accum (DTA)": "DTA",
            "Digital 1-Hour Accum (DAA)": "DAA",
            "Composite Reflectivity (NCZ)": "NCZ",
            "Mid-Layer Composite (NML)": "NML",
            "High-Layer Composite (NHL)": "NHL",
            "1-Hour Precip (N1P)": "N1P",
            "3-Hour Precip (N3P)": "N3P",
            "Storm Total Precip (NTP)": "NTP",
            "Echo Tops (NET)": "NET",
            "Vertically Integrated Liquid (NVL)": "NVL",
            "Mesocyclone Detection (NMD)": "NMD",
            "Tornado Vortex Signature (NTV)": "NTV",
            "Hail Index (NHI)": "NHI",
            "Storm Structure (NSS)": "NSS",
        }
        if product_name not in product_map:
            self.status.showMessage(f"Unknown product: {product_name}. No data loaded.", 5000)
            return
        product_code = product_map[product_name]
        self.current_product_code = product_code
        self.current_product_name = product_name
        # Update palette selector for this product
        self.update_palette_selector(product_name)
        self.load_recent_level3_frames(site_id=self.current_site, frame_count=self.settings.get('animation_frames', 14), product_code=product_code, product_name=product_name)

    def on_map_load_finished(self):
        # Called when the map finishes loading; can be used to initialize overlays
        self.update_canvas()

    # get_selected_level2_field removed (Level 3 only)

    def update_canvas(self):
        """
        Redraw the radar image and overlays on the map.
        Calls setRadarOverlay in JS with robust debug output and saves the PNG for inspection.
        """
        import json
        import base64
        from PyQt5.QtCore import QTimer
        # Only show the overlay for the currently selected product (no merging)
        if not hasattr(self, '_set_radar_overlay_retry_count'):
            self._set_radar_overlay_retry_count = 0
        if not self.radar_frames:
            # Clear overlay if no frames
            if self.radar_map and self.radar_map.page() is not None:
                page = self.radar_map.page()
                if page is not None:
                    js = "if (typeof window.setRadarOverlay === 'function') window.setRadarOverlay(null, null, null, null);"
                    page.runJavaScript(js)
            print("[DEBUG] update_canvas: No radar frames, overlay cleared.")
            if hasattr(self, 'status') and self.status:
                self.status.showMessage("[DEBUG] update_canvas: No radar frames, overlay cleared.", 5000)
            self.update_colorbar()
            return
        # Filter frames for the current product code
        current_code = getattr(self, 'current_product_code', 'N0B')
        filtered_frames = [f for f in self.radar_frames if f.get('product_code') == current_code]
        if not filtered_frames:
            # Clear overlay if no frames for this product
            if self.radar_map and self.radar_map.page() is not None:
                page = self.radar_map.page()
                if page is not None:
                    js = "if (typeof window.setRadarOverlay === 'function') window.setRadarOverlay(null, null, null, null);"
                    page.runJavaScript(js)
            print(f"[DEBUG] update_canvas: No frames for product {current_code}, overlay cleared.")
            if hasattr(self, 'status') and self.status:
                self.status.showMessage(f"[DEBUG] update_canvas: No frames for product {current_code}, overlay cleared.", 5000)
            self.update_colorbar()
            return
        # Use the current frame index for the selected product (for animation)
        frame = filtered_frames[self.frame_index % len(filtered_frames)]
        img_b64 = frame.get('img_b64')
        bounds = frame.get('bounds')
        # Convert all bounds to plain float for JS/Leaflet compatibility
        if bounds:
            bounds = [[float(b[0]), float(b[1])] for b in bounds]
        label = frame.get('product_code', None)
        colormapImgUrl = None
        # Save PNG for debug if present
        if img_b64:
            try:
                with open('debug_radar_overlay.png', 'wb') as f:
                    f.write(base64.b64decode(img_b64))
                print('[DEBUG] update_canvas: Saved debug_radar_overlay.png for inspection.')
            except Exception as e:
                print(f'[DEBUG] update_canvas: Failed to save debug_radar_overlay.png: {e}')
        # --- Level 3 overlay logic ---
        if img_b64 and bounds:
            if self.radar_map and self.radar_map.page() is not None:
                page = self.radar_map.page()
                if page is not None:
                    img_url = f"data:image/png;base64,{img_b64}"
                    js_call = f"window.setRadarOverlay('{img_url}', {json.dumps(bounds)}, '{label}', {json.dumps(colormapImgUrl)})"
                    print(f"[DEBUG] update_canvas: Sending overlay to JS. Bounds: {bounds}, Label: {label}, img_b64 present: {bool(img_b64)}")
                    if hasattr(self, 'status') and self.status:
                        self.status.showMessage(f"[DEBUG] update_canvas: Sending overlay to JS. Bounds: {bounds}, Label: {label}", 5000)
                    page.runJavaScript(js_call)
        # Always update colorbar after canvas update
        self.update_colorbar()
        # Always update NWS polygons after canvas update
        self.update_nws_polygons_on_map()
        # If no valid overlay image or bounds for current frame, print debug
        if not (img_b64 and bounds):
            print("[DEBUG] update_canvas: No valid overlay image or bounds for current frame.")
            if hasattr(self, 'status') and self.status:
                self.status.showMessage("[DEBUG] update_canvas: No valid overlay image or bounds for current frame.", 5000)

    def set_app_icons(self):
        """Set the window, taskbar, and app icon to the new radar_icon.ico everywhere."""
        icon_path = os.path.join(os.path.dirname(__file__), "assets", "radar_icon.ico")
        app_icon = QIcon(icon_path)
        self.setWindowIcon(app_icon)
        QApplication.setWindowIcon(app_icon)

def get_classic_reflectivity_points():
    # Example: returns a list of (value, r, g, b) tuples for classic reflectivity
    # Replace with your actual color table if needed
    return [
        (-30, 0, 0, 0),
        (0, 100, 100, 100),
        (5, 0, 200, 0),
        (10, 0, 150, 0),
        (20, 0, 100, 0),
        (25, 255, 255, 0),
        (30, 255, 150, 0),
        (40, 255, 0, 0),
        (50, 150, 0, 0),
        (60, 255, 0, 255),
        (70, 150, 0, 150)
    ]

import matplotlib.colors as mcolors

def parse_pal_file(pal_path):
    """
    Parse a .pal file into points (value, r, g, b).
    Supports WxTools, NCEI, and other real-world .pal formats.
    Handles 7/8-value 'Color:' lines for smooth color transitions (WxTools/NWS style).
    Returns a list of (value, r, g, b) tuples.
    """
    import re
    points = []
    try:
        with open(pal_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        i = 0
        prev_stop = None
        while i < len(lines):
            line = lines[i].strip()
            if not line or line.startswith('#') or line.startswith(';'):
                i += 1
                continue
            lcline = line.lower()
            if lcline.startswith('units:') or lcline.startswith('step:') or lcline.startswith('product:'):
                i += 1
                continue
            # NCEI: Color4: -25 0 0 0 255
            m = re.match(r'color4:\s*([\-\d.]+)\s+(\d+)\s+(\d+)\s+(\d+)\s+\d+', lcline)
            if m:
                val = float(m.group(1))
                r = int(m.group(2))
                g = int(m.group(3))
                b = int(m.group(4))
                points.append((val, r, g, b))
                i += 1
                continue
            # WxTools: SolidColor: 10 216 226 243
            m = re.match(r'solidcolor:\s*([\d.]+)\s+(\d+)\s+(\d+)\s+(\d+)', lcline)
            if m:
                val = float(m.group(1))
                r = int(m.group(2))
                g = int(m.group(3))
                b = int(m.group(4))
                points.append((val, r, g, b))
                i += 1
                continue
            # WxTools/NWS: Color: dBZ1 r1 g1 b1 dBZ2 r2 g2 b2 (8 values)
            m = re.match(r'color:\s*([\-\d.]+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\-\d.]+)\s+(\d+)\s+(\d+)\s+(\d+)', lcline)
            if m:
                val1 = float(m.group(1))
                r1 = int(m.group(2))
                g1 = int(m.group(3))
                b1 = int(m.group(4))
                val2 = float(m.group(5))
                r2 = int(m.group(6))
                g2 = int(m.group(7))
                b2 = int(m.group(8))
                if prev_stop is None:
                    points.append((val1, r1, g1, b1))
                points.append((val2, r2, g2, b2))
                prev_stop = (val2, r2, g2, b2)
                i += 1
                continue
            # 7-value Color: Color: dBZ1 r1 g1 b1 r2 g2 b2 (current dBZ, r1, g1, b1, r2, g2, b2)
            m = re.match(r'color:\s*([\-\d.]+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)', lcline)
            if m:
                val1 = float(m.group(1))
                r1 = int(m.group(2))
                g1 = int(m.group(3))
                b1 = int(m.group(4))
                r2 = int(m.group(5))
                g2 = int(m.group(6))
                b2 = int(m.group(7))
                val2 = val1 + 1
                if prev_stop is None:
                    points.append((val1, r1, g1, b1))
                points.append((val2, r2, g2, b2))
                prev_stop = (val2, r2, g2, b2)
                i += 1
                continue
            # Fallback: whitespace split, at least 4 values
            parts = line.split()
            if len(parts) >= 4:
                try:
                    val = float(parts[0])
                    r = int(parts[1])
                    g = int(parts[2])
                    b = int(parts[3])
                    points.append((val, r, g, b))
                except Exception:
                    pass
            i += 1
    except Exception as e:
        print(f"[PALETTE] Failed to parse {pal_path}: {e}")
    # Remove duplicate consecutive points (can happen with some .pal files)
    deduped = []
    for pt in points:
        if not deduped or pt != deduped[-1]:
            deduped.append(pt)
    return deduped

def make_colormap_from_points(points, name='custom'):
    """
    Create a matplotlib colormap from a list of (value, r, g, b) tuples.
    Ensures mapping points start at 0 and end at 1.
    Also returns vmin, vmax (palette dBZ range) for correct normalization.
    """
    if not points:
        raise ValueError("No color points provided for colormap.")
    # Sort by value
    points = sorted(points, key=lambda x: x[0])
    vals = [p[0] for p in points]
    rgbs = [(p[1]/255, p[2]/255, p[3]/255) for p in points]
    vmin, vmax = min(vals), max(vals)
    norm_vals = [(v-vmin)/(vmax-vmin) if vmax > vmin else 0 for v in vals]
    # Ensure first is at 0 and last at 1
    if norm_vals[0] > 0:
        norm_vals = [0.0] + norm_vals
        rgbs = [rgbs[0]] + rgbs
    if norm_vals[-1] < 1:
        norm_vals = norm_vals + [1.0]
        rgbs = rgbs + [rgbs[-1]]
    cdict = {'red': [], 'green': [], 'blue': []}
    for i, nv in enumerate(norm_vals):
        r, g, b = rgbs[i]
        cdict['red'].append((nv, r, r))
        cdict['green'].append((nv, g, g))
        cdict['blue'].append((nv, b, b))
    # Only use keys that are valid for LinearSegmentedColormap
    valid_keys = ['red', 'green', 'blue', 'alpha']
    # Ensure each value is a list of 3-tuples of floats, and type matches what LinearSegmentedColormap expects
    from typing import Sequence
    filtered_cdict = {k: [tuple(map(float, t)) for t in v] for k, v in cdict.items() if k in valid_keys}
    # Only use keys that are valid for LinearSegmentedColormap (must be exactly 'red', 'green', 'blue', 'alpha')
    import matplotlib.colors as mcolors
    filtered_cdict_typed = {k: v for k, v in filtered_cdict.items() if k in ('red', 'green', 'blue', 'alpha')}
    # Cast keys to Literal for type checkers (runtime is fine)
    cmap = mcolors.LinearSegmentedColormap(name, filtered_cdict_typed)  # type: ignore
    # Attach vmin/vmax as custom attributes if needed (safely)
    if vmin is not None:
        setattr(cmap, '_ovr_vmin', vmin)
    if vmax is not None:
        setattr(cmap, '_ovr_vmax', vmax)
    return cmap

# --- Main entry point ---
import traceback
import datetime
import os

def write_crash_report(exc_type, exc_value, exc_tb):
    try:
        errorlog_path = os.path.join(os.getcwd(), 'errorlog.txt')
        with open(errorlog_path, 'a', encoding='utf-8') as f:
            f.write(f"\n--- Crash Report: {datetime.datetime.now().isoformat()} ---\n")
            traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
    except Exception as e:
        print(f"[CRASH REPORT] Failed to write error log: {e}")

def excepthook(exc_type, exc_value, exc_tb):
    write_crash_report(exc_type, exc_value, exc_tb)
    # Optionally, show a message box if possible
    try:
        from PyQt5.QtWidgets import QMessageBox
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Critical)
        msg.setWindowTitle("OVR-NEXRAD Crash Report")
        msg.setText("The application has encountered an unexpected error and must close.\nA crash report has been saved to errorlog.txt.")
        msg.setDetailedText(''.join(traceback.format_exception(exc_type, exc_value, exc_tb)))
        msg.exec_()
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc_value, exc_tb)

if __name__ == "__main__":
    import sys
    sys.excepthook = excepthook
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    try:
        sys.exit(app.exec_())
    except Exception:
        # This should be rare, but catch anything not handled by excepthook
        exc_type, exc_value, exc_tb = sys.exc_info()
        write_crash_report(exc_type, exc_value, exc_tb)