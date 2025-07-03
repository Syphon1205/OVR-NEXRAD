# radar_canvas.py

import numpy as np
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QDialog, QLabel, QPushButton, QTextEdit, QHBoxLayout, QListWidget, QListWidgetItem, QScrollArea, QSizePolicy
from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QDesktopServices
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
import os
from matplotlib.colors import ListedColormap
import boto3
from botocore import UNSIGNED
from botocore.client import Config
from datetime import datetime
import re
from PyQt5.QtCore import QObject, QThread, pyqtSignal
from matplotlib.patches import Polygon as MplPolygon
import requests

def load_pal_colormap(pal_path):
    """
    Load a .pal file (NEXRAD/GR2Analyst/WeatherBell format) and return a matplotlib ListedColormap.
    """
    colors = []
    with open(pal_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith('Color4:'):
                parts = line.split()
                # Format: Color4: value R G B A
                if len(parts) == 6:
                    r, g, b, a = map(int, parts[2:6])
                    colors.append((r/255, g/255, b/255, a/255))
    if not colors:
        raise ValueError(f"No colors found in {pal_path}")
    return ListedColormap(colors, name=os.path.basename(pal_path))

def get_pal_colormap(colormap_name_or_path, search_dirs=None, fallback='viridis'):
    """
    Given a .pal filename or path, search in search_dirs and load as matplotlib colormap.
    If already a colormap object, return as is. If not found, return fallback colormap.
    """
    if isinstance(colormap_name_or_path, ListedColormap):
        return colormap_name_or_path
    if isinstance(colormap_name_or_path, str):
        if colormap_name_or_path.lower().endswith('.pal'):
            # Try as path
            if os.path.exists(colormap_name_or_path):
                return load_pal_colormap(colormap_name_or_path)
            # Try in search_dirs
            if search_dirs:
                for d in search_dirs:
                    candidate = os.path.join(d, colormap_name_or_path)
                    if os.path.exists(candidate):
                        return load_pal_colormap(candidate)
            # Fallback to matplotlib colormap if .pal not found
            import matplotlib.pyplot as plt
            print(f"[WARN] .pal colormap not found: {colormap_name_or_path}, using fallback '{fallback}'")
            return plt.get_cmap(fallback)
    return colormap_name_or_path  # fallback, e.g. matplotlib colormap name

def get_latest_level2_s3_file(site, date=None):
    """
    Return the latest Level 2 NEXRAD file URL and timestamp for a given site and date (UTC).
    If date is None, uses today (UTC).
    """
    if date is None:
        now = datetime.utcnow()
    else:
        now = date
    date_str = now.strftime('%Y/%m/%d')
    s3_prefix = f"{date_str}/{site}/"
    bucket = "noaa-nexrad-level2"
    s3 = boto3.client('s3', config=Config(signature_version=UNSIGNED), region_name='us-east-1')
    paginator = s3.get_paginator('list_objects_v2')
    page_iterator = paginator.paginate(Bucket=bucket, Prefix=s3_prefix)
    files = []
    for page in page_iterator:
        for obj in page.get('Contents', []):
            key = obj['Key']
            fname = key.split('/')[-1]
            if (fname.startswith(site)
                and ("V06" in fname or "V08" in fname)
                and not any(suffix in fname for suffix in ["_MDM", "_STA", "_EVE", "_RDA"])):
                files.append((fname, key))
    def extract_dt(fname):
        m = re.search(r'(\d{8}[_-]\d{6})', fname)
        if m:
            try:
                return datetime.strptime(m.group(1), '%Y%m%d_%H%M%S')
            except ValueError:
                try:
                    return datetime.strptime(m.group(1), '%Y%m%d-%H%M%S')
                except ValueError:
                    return datetime.min
        return datetime.min
    files = sorted(files, key=lambda x: extract_dt(x[0]), reverse=True)
    if not files:
        return None, None
    fname, key = files[0]
    file_url = f"https://noaa-nexrad-level2.s3.amazonaws.com/{key}"
    dt = extract_dt(fname)
    return file_url, dt

class RadarFrameWorker(QObject):
    finished = pyqtSignal(str, int, dict)  # png_path, idx, frame dict
    error = pyqtSignal(str, int, dict)

    def __init__(self, frame, idx, product_code):
        super().__init__()
        self.frame = frame
        self.idx = idx
        self.product_code = product_code

    def run(self):
        import tempfile
        import requests
        import os
        import pyart
        import matplotlib.pyplot as plt
        try:
            # Download file to temp
            resp = requests.get(self.frame['url'], timeout=20)
            resp.raise_for_status()
            with tempfile.NamedTemporaryFile(delete=False, suffix='.ar2') as f:
                f.write(resp.content)
                tmp_path = f.name
            radar = pyart.io.read(tmp_path)
            display = pyart.graph.RadarDisplay(radar)
            fig = plt.figure(figsize=(6,6), dpi=100)
            ax = fig.add_subplot(111)
            display.plot(self.product_code, 0, ax=ax, title="", colorbar_label=self.product_code.upper(), vmin=None, vmax=None)
            ax.set_axis_off()
            plt.tight_layout(pad=0)
            tmp_dir = tempfile.gettempdir()
            png_path = os.path.join(tmp_dir, f"radar_{self.frame['url'].split('/')[-2]}_{self.idx}.png")
            fig.savefig(png_path, bbox_inches='tight', pad_inches=0, transparent=True)
            plt.close(fig)
            os.unlink(tmp_path)
            self.finished.emit(png_path, self.idx, self.frame)
        except Exception as e:
            print(f"[ERROR][Worker] Failed to render Level 2 to PNG: {e}")
            self.error.emit(str(e), self.idx, self.frame)

class RadarFramePreloader(QObject):
    progress = pyqtSignal(int, int)  # current, total
    finished = pyqtSignal(dict)  # {idx: png_path}
    error = pyqtSignal(str, int, dict)

    def __init__(self, frames, product_code):
        super().__init__()
        self.frames = frames
        self.product_code = product_code
        self.cache = {}

    def run(self):
        import tempfile
        import requests
        import os
        import pyart
        import matplotlib.pyplot as plt
        total = len(self.frames)
        for idx, frame in enumerate(self.frames):
            try:
                resp = requests.get(frame['url'], timeout=20)
                resp.raise_for_status()
                with tempfile.NamedTemporaryFile(delete=False, suffix='.ar2') as f:
                    f.write(resp.content)
                    tmp_path = f.name
                radar = pyart.io.read(tmp_path)
                display = pyart.graph.RadarDisplay(radar)
                fig = plt.figure(figsize=(6,6), dpi=100)
                ax = fig.add_subplot(111)
                display.plot(self.product_code, 0, ax=ax, title="", colorbar_label=self.product_code.upper(), vmin=None, vmax=None)
                ax.set_axis_off()
                plt.tight_layout(pad=0)
                tmp_dir = tempfile.gettempdir()
                png_path = os.path.join(tmp_dir, f"radar_{frame['url'].split('/')[-2]}_{idx}.png")
                fig.savefig(png_path, bbox_inches='tight', pad_inches=0, transparent=True)
                plt.close(fig)
                os.unlink(tmp_path)
                self.cache[idx] = png_path
                self.progress.emit(idx+1, total)
            except Exception as e:
                print(f"[ERROR][Preloader] Failed to render Level 2 to PNG: {e}")
                self.error.emit(str(e), idx, frame)
        self.finished.emit(self.cache)

NWS_ALERT_COLORS = {
    'Tornado Warning': '#C80000',
    'Severe Thunderstorm Warning': '#FF8000',
    'Flash Flood Warning': '#00C800',
    'Flood Warning': '#008000',
    'Special Marine Warning': '#FF00FF',
    'Tornado Watch': '#FFCC00',
    'Severe Thunderstorm Watch': '#FFFF00',
    'Flash Flood Watch': '#00FF00',
    'Flood Watch': '#00FF80',
    'Winter Storm Warning': '#8000FF',
    'Winter Weather Advisory': '#8080FF',
    'Winter Storm Watch': '#80FFFF',
    'Blizzard Warning': '#FF00FF',
    'Ice Storm Warning': '#00FFFF',
    'High Wind Warning': '#FF0080',
    'Wind Advisory': '#FF80C0',
    'Red Flag Warning': '#FF0000',
    'Fire Weather Watch': '#FF8000',
    'Heat Advisory': '#FF8000',
    'Excessive Heat Warning': '#FF0000',
    'Dense Fog Advisory': '#808080',
    'Dense Smoke Advisory': '#A0522D',
    'Hurricane Warning': '#FF0000',
    'Hurricane Watch': '#FFA500',
    'Tropical Storm Warning': '#00BFFF',
    'Tropical Storm Watch': '#87CEEB',
    'Storm Surge Warning': '#FF1493',
    'Storm Surge Watch': '#FF69B4',
    # Add more as needed
}

class AlertPopup(QDialog):
    def __init__(self, alert_props, parent=None):
        super().__init__(parent)
        self.setWindowTitle(alert_props.get('event', 'NWS Alert'))
        self.setModal(True)
        self.setMinimumWidth(350)
        layout = QVBoxLayout()
        # Banner
        banner = QLabel(alert_props.get('event', ''))
        banner.setStyleSheet(f"background:{NWS_ALERT_COLORS.get(alert_props.get('event',''), '#FFD700')};color:#222;font-weight:bold;padding:8px;font-size:16px;text-align:center;")
        layout.addWidget(banner)
        # Details
        details = f"<b>{alert_props.get('headline','')}</b><br>"
        details += f"<b>Type:</b> {alert_props.get('event','')}<br>"
        details += f"<b>Area:</b> {alert_props.get('areaDesc','')}<br>"
        details += f"<b>Sent:</b> {alert_props.get('sent','')}<br>"
        details += f"<b>Effective:</b> {alert_props.get('effective','')}<br>"
        details += f"<b>Expires:</b> {alert_props.get('expires','')}<br>"
        details += f"<b>Description:</b> {alert_props.get('description','')}<br>"
        details += f"<b>Sender:</b> {alert_props.get('senderName','')}<br>"
        if alert_props.get('web'):
            details += '<a href="{}">View on weather.gov</a>'.format(alert_props.get('web'))
        text = QLabel(details)
        from PyQt5.QtGui import QTextFormat
        text.setTextFormat(QTextFormat.RichText)
        text.setOpenExternalLinks(True)
        text.setWordWrap(True)
        layout.addWidget(text)
        # Close button
        btn = QPushButton('Close')
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)
        self.setLayout(layout)

class NWSAlertListWindow(QDialog):
    def __init__(self, alert_props_list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Active NWS Alerts")
        self.setMinimumWidth(420)
        self.setMinimumHeight(600)
        layout = QVBoxLayout()
        self.list_widget = QListWidget()
        self.list_widget.setSpacing(6)
        for props in alert_props_list:
            color = NWS_ALERT_COLORS.get(props.get('event',''), '#FFD700')
            item = QListWidgetItem()
            item.setSizeHint(QLabel().sizeHint())
            widget = QWidget()
            vbox = QVBoxLayout()
            # Banner
            banner = QLabel(props.get('event', ''))
            banner.setStyleSheet(f"background:{color};color:#222;font-weight:bold;padding:4px 8px;font-size:15px;")
            vbox.addWidget(banner)
            # Headline
            headline = QLabel(f"<b>{props.get('headline','')}</b>")
            from PyQt5.QtGui import QTextFormat
            headline.setTextFormat(QTextFormat.RichText)
            vbox.addWidget(headline)
            # Area and times
            area = QLabel(f"<b>Area:</b> {props.get('areaDesc','')}")
            area.setTextFormat(QTextFormat.RichText)
            vbox.addWidget(area)
            times = QLabel(f"<b>Expires:</b> {props.get('expires','')}")
            times.setTextFormat(QTextFormat.RichText)
            vbox.addWidget(times)
            widget.setLayout(vbox)
            widget.setStyleSheet("background:#fff;border-radius:6px;padding:6px;margin-bottom:4px;")
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.list_widget)
        close_btn = QPushButton('Close')
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)
        self.setLayout(layout)
        self.alert_props_list = alert_props_list

    def _on_item_clicked(self, item):
        idx = self.list_widget.row(item)
        props = self.alert_props_list[idx]
        popup = AlertPopup(props, self)
        popup.exec_()

class RadarCanvas(QWidget):
    """
    RadarCanvas: A PyQt5 widget for RadarScope-like radar visualization
    Now supports .pal colormap files (NEXRAD/GR2Analyst/WeatherBell format).
    Usage:
        radar_canvas.plot_radar(radar_obj, field='reflectivity', tilt=0, cmap='NCEI.pal')
        # or
        radar_canvas.plot_radar(radar_obj, cmap=load_pal_colormap('NCEI.pal'))
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.figure = Figure()
        self.canvas = FigureCanvas(self.figure)
        self.ax = self.figure.add_subplot(1, 1, 1)
        layout = QVBoxLayout()
        layout.addWidget(self.canvas)
        self.setLayout(layout)
        self.radar = None
        self.nws_alert_patches = []
        self.nws_alerts_visible = False
        self.nws_alert_patch_props = []
        self.dryline_visible = False
        self.dryline_line = None
        self.example_polygon_visible = False
        self.example_polygon_patch = None
        self.canvas.mpl_connect('button_press_event', self._on_alert_click)

    def plot_radar(self, radar, field='reflectivity', tilt=0, cmap=None, pal_search_dirs=None):
        self.ax.clear()
        if radar is None or not hasattr(radar, 'fields') or field is None:
            self.canvas.draw()
            return
        try:
            import pyart
            # Product-based colormap selection
            product_cmap_map = {
                'reflectivity': 'NWSRef',
                'REF': 'NWSRef',
                'dBZ': 'NWSRef',
                'velocity': 'NWSVel',
                'VEL': 'NWSVel',
                'V': 'NWSVel',
                'spectrum_width': 'NWS_SPW',
                'SW': 'NWS_SPW',
                'differential_reflectivity': 'RefDiff',
                'ZDR': 'RefDiff',
                'cross_correlation_ratio': 'BuOr10',
                'RHOHV': 'BuOr10',
                'differential_phase': 'BuOr12',
                'PHIDP': 'BuOr12',
            }
            # Use provided cmap, else product-based, else fallback
            cmap_key = field if field in product_cmap_map else field.upper() if field.upper() in product_cmap_map else None
            cmap_name = cmap or product_cmap_map.get(field) or product_cmap_map.get(cmap_key) or 'viridis'
            cmap_obj = get_pal_colormap(cmap_name, search_dirs=pal_search_dirs)
            display = pyart.graph.RadarDisplay(radar)
            display.plot_ppi(field, sweep=tilt, ax=self.ax, cmap=cmap_obj, colorbar_flag=True)
        except Exception as e:
            print(f"Radar plot error: {e}")
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        self.ax.set_xlabel("")
        self.ax.set_ylabel("")
        self.ax.set_title("")
        for spine in self.ax.spines.values():
            spine.set_visible(False)
        self.canvas.draw()

    def clear(self):
        self.ax.clear()
        self.canvas.draw()

    def fetch_and_draw_nws_alerts(self):
        for patch in self.nws_alert_patches:
            patch.remove()
        self.nws_alert_patches = []
        self.nws_alert_patch_props = []
        try:
            resp = requests.get('https://api.weather.gov/alerts/active', headers={'User-Agent': 'YourAppName (your@email.com)'}, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            for feature in data.get('features', []):
                geom = feature.get('geometry')
                props = feature.get('properties', {})
                color = NWS_ALERT_COLORS.get(props.get('event',''), self._get_alert_color(props.get('severity','')))
                if geom and geom['type'] in ('Polygon', 'MultiPolygon'):
                    if geom['type'] == 'Polygon':
                        coords = geom['coordinates']
                        for ring in coords:
                            poly = MplPolygon(ring, closed=True, edgecolor=color, facecolor=color, alpha=0.35, linewidth=2, zorder=10, picker=True)
                            self.ax.add_patch(poly)
                            self.nws_alert_patches.append(poly)
                            self.nws_alert_patch_props.append(props)
                    elif geom['type'] == 'MultiPolygon':
                        for poly_coords in geom['coordinates']:
                            for ring in poly_coords:
                                poly = MplPolygon(ring, closed=True, edgecolor=color, facecolor=color, alpha=0.35, linewidth=2, zorder=10, picker=True)
                                self.ax.add_patch(poly)
                                self.nws_alert_patches.append(poly)
                                self.nws_alert_patch_props.append(props)
            self.canvas.draw()
        except Exception as e:
            print(f"[NWS Alerts] Failed to fetch or draw alerts: {e}")

    def _on_alert_click(self, event):
        if not self.nws_alerts_visible or event.inaxes != self.ax:
            return
        for patch, props in zip(self.nws_alert_patches, self.nws_alert_patch_props):
            contains, _ = patch.contains(event)
            if contains:
                popup = AlertPopup(props, self)
                popup.exec_()
                break

    def _get_alert_color(self, severity):
        if severity == 'Extreme':
            return '#d73027'
        elif severity == 'Severe':
            return '#fc8d59'
        elif severity == 'Moderate':
            return '#fee08b'
        elif severity == 'Minor':
            return '#d9ef8b'
        return '#4575b4'

    def toggle_nws_alerts(self):
        if self.nws_alerts_visible:
            for patch in self.nws_alert_patches:
                patch.remove()
            self.nws_alert_patches = []
            self.nws_alerts_visible = False
            self.canvas.draw()
        else:
            self.fetch_and_draw_nws_alerts()
            self.nws_alerts_visible = True

    def show_nws_alert_list(self):
        # Fetch alerts if not already loaded
        if not self.nws_alert_patch_props:
            self.fetch_and_draw_nws_alerts()
        if self.nws_alert_patch_props:
            win = NWSAlertListWindow(self.nws_alert_patch_props, self)
            win.exec_()

    def toggle_dryline(self):
        if self.dryline_visible:
            if self.dryline_line:
                self.dryline_line.remove()
                self.dryline_line = None
            self.dryline_visible = False
        else:
            # Example dryline coordinates (lat/lon in degrees, convert to x/y if needed)
            dryline_coords = [
                (-102.0, 32.0), (-101.0, 33.5), (-100.0, 34.5), (-99.0, 35.5), (-98.0, 36.5)
            ]
            # For demo, just plot as x/y (not projected)
            xs, ys = zip(*dryline_coords)
            self.dryline_line, = self.ax.plot(xs, ys, color='orange', linewidth=3, zorder=20, label='Dryline')
            self.dryline_visible = True
        self.canvas.draw()

    def toggle_example_polygon(self):
        if self.example_polygon_visible:
            if self.example_polygon_patch:
                self.example_polygon_patch.remove()
                self.example_polygon_patch = None
            self.example_polygon_visible = False
        else:
            # Example polygon coordinates (lat/lon in degrees, convert to x/y if needed)
            poly_coords = [
                (-101.5, 34.0), (-100.5, 34.5), (-101.0, 35.0), (-102.0, 34.7)
            ]
            xs, ys = zip(*poly_coords)
            from matplotlib.patches import Polygon as MplPolygon
            self.example_polygon_patch = MplPolygon(list(zip(xs, ys)), closed=True, edgecolor='blue', facecolor='blue', alpha=0.3, linewidth=2, zorder=15)
            self.ax.add_patch(self.example_polygon_patch)
            self.example_polygon_visible = True
        self.canvas.draw()

# Example usage (in your main window):
# from radar_canvas import RadarCanvas, load_pal_colormap
# radar_canvas = RadarCanvas()
# radar_canvas.plot_radar(radar_obj, field='reflectivity', tilt=0, cmap='NCEI.pal', pal_search_dirs=['.','/path/to/pal/files'])
# layout.addWidget(radar_canvas)
