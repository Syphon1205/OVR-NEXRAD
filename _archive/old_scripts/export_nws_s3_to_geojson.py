import os
import tempfile
import json
from datetime import datetime, timedelta
import boto3
from botocore import UNSIGNED
from botocore.config import Config
import pyart
import numpy as np

def fetch_and_export_nws_geojson(site='KTLX', frame_count=6, output_dir='radar-geojson', field='reflectivity',
                                 grid_shape=(1, 500, 500), grid_limits=((0, 10000), (-250000, 250000), (-250000, 250000)),
                                 grid_origin_lat=35.5, grid_origin_lon=-97.5):
    NEXRAD_BUCKET = 'noaa-nexrad-level2'
    os.makedirs(output_dir, exist_ok=True)
    s3 = boto3.client('s3', config=Config(signature_version=UNSIGNED))
    now = datetime.utcnow()
    prefixes = [now.strftime(f"%Y/%m/%d/{site}/")]
    prev = now - timedelta(days=1)
    prefixes.append(prev.strftime(f"%Y/%m/%d/{site}/"))
    found_files = []
    for prefix in prefixes:
        paginator = s3.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=NEXRAD_BUCKET, Prefix=prefix)
        files = [
            (obj['LastModified'], obj['Key'])
            for page in pages
            for obj in page.get('Contents', [])
            if site in obj['Key'] and not obj['Key'].endswith('_MDM')
        ]
        if files:
            found_files.extend(files)
    files = sorted(found_files, reverse=True)[:frame_count]
    manifest = []
    for _, key in reversed(files):
        filename = os.path.join(tempfile.gettempdir(), os.path.basename(key))
        try:
            s3.download_file(NEXRAD_BUCKET, key, filename)
            radar = pyart.io.read_nexrad_archive(filename)
            grid = pyart.map.grid_from_radars(
                radar,
                grid_shape=grid_shape,
                grid_limits=grid_limits,
                fields=[field],
                origin=(grid_origin_lat, grid_origin_lon),
            )
            data_2d = grid.fields[field]['data'][0].flatten()
            lon_coords = grid.get_point_longitude_latitude(0)[0].flatten()
            lat_coords = grid.get_point_longitude_latitude(0)[1].flatten()
            valid_indices = ~np.isnan(data_2d)
            features = [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [float(lon_coords[j]), float(lat_coords[j])]},
                    "properties": {"value": float(data_2d[j])}
                }
                for j in range(len(data_2d)) if valid_indices[j]
            ]
            geojson_output = {"type": "FeatureCollection", "features": features}
            scan_time = pyart.util.datetime_from_radar(radar)
            if hasattr(scan_time, 'strftime'):
                scan_str = scan_time.strftime("%Y%m%d_%H%M%S")
                scan_iso = scan_time.strftime("%Y-%m-%dT%H:%M:%SZ")
            else:
                scan_str = str(scan_time)
                scan_iso = str(scan_time)
            output_filename = os.path.join(
                output_dir,
                f'radar_data_{scan_str}.geojson'
            )
            with open(output_filename, 'w') as f:
                json.dump(geojson_output, f)
            manifest.append({
                "timestamp": scan_iso,
                "url": os.path.basename(output_filename)
            })
            print(f"Generated {output_filename}")
        except Exception as e:
            print(f"Error processing {key}: {e}")
    with open(os.path.join(output_dir, 'manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=2)
    print("All GeoJSON files and manifest generated.")

if __name__ == "__main__":
    fetch_and_export_nws_geojson()
