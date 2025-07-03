import os
import tempfile
import boto3
import pyart
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
from pyart.graph import RadarMapDisplay
from botocore import UNSIGNED
from botocore.config import Config

# AWS S3 NEXRAD bucket
NEXRAD_BUCKET = "noaa-nexrad-level2"
NEXRAD_PREFIX = ""

# Radar site info (lat, lon, range_km)
RADAR_SITES = {
    "KTLX": {"lat": 35.333, "lon": -97.277, "range_km": 230},
    # Add more sites as needed
}

# Use unsigned S3 client with region us-east-1
s3 = boto3.client('s3', region_name='us-east-1', config=Config(signature_version=UNSIGNED))

def get_latest_nexrad_keys(site_id, n_frames=6):
    """Fetch latest NEXRAD Level 2 keys from AWS S3 for a site."""
    now = datetime.utcnow()
    for day_offset in range(2):
        date = now - timedelta(days=day_offset)
        prefix = f"{site_id}/{date:%Y/%m/%d}/"
        resp = s3.list_objects_v2(Bucket=NEXRAD_BUCKET, Prefix=prefix)
        if "Contents" in resp:
            files = [obj["Key"] for obj in resp["Contents"] if obj["Key"].endswith("V06")]
            files.sort(reverse=True)
            if len(files) >= n_frames:
                return files[:n_frames]
    return []

def download_nexrad_file(key, out_dir):
    out_path = os.path.join(out_dir, os.path.basename(key))
    s3.download_file(NEXRAD_BUCKET, key, out_path)
    return out_path

def render_radar_png(nexrad_file, site_id, out_dir):
    radar = pyart.io.read(nexrad_file)
    display = RadarMapDisplay(radar)
    fig = plt.figure(figsize=(6,6))
    ax = fig.add_subplot(111)
    display.plot_ppi("reflectivity", 0, ax=ax, vmin=-20, vmax=75, cmap="pyart_NWSRef", colorbar_flag=False)
    ax.set_axis_off()
    plt.tight_layout(pad=0)
    png_path = os.path.join(out_dir, os.path.basename(nexrad_file) + ".png")
    plt.savefig(png_path, bbox_inches="tight", pad_inches=0, transparent=True)
    plt.close(fig)
    # Calculate bounds (approximate)
    site = RADAR_SITES[site_id]
    lat, lon, r = site["lat"], site["lon"], site["range_km"]
    dlat = r / 111.32
    dlon = r / (111.32 * np.cos(np.radians(lat)))
    bounds = [[lat - dlat, lon - dlon], [lat + dlat, lon + dlon]]
    return png_path, bounds, lat, lon, r

def get_radar_animation_frames(site_id, n_frames=6):
    """Main function: returns list of PNGs, bounds, lat, lon, range_km."""
    keys = get_latest_nexrad_keys(site_id, n_frames)
    if not keys:
        return [], None, None, None, None
    with tempfile.TemporaryDirectory() as tmpdir:
        pngs = []
        for key in reversed(keys):  # oldest to newest
            f = download_nexrad_file(key, tmpdir)
            png, bounds, lat, lon, r = render_radar_png(f, site_id, tmpdir)
            pngs.append(png)
        # For serving, you may want to move/copy PNGs to a static dir and return URLs
        # Here, just return file paths and metadata
        return pngs, bounds, lat, lon, r
