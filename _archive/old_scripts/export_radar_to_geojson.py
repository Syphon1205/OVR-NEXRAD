import pyart
import numpy as np
import json
import os
import datetime
import glob
import boto3
from botocore import UNSIGNED
from botocore.config import Config
from datetime import datetime, timedelta
import time
from concurrent.futures import ThreadPoolExecutor, as_completed # Import for parallelization

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

def to_py_datetime(scan_time):
    # This function is fine as is, but ensure its dependencies (numpy, pandas) are optimized if they slow down
    import datetime
    try:
        import numpy as np
        import pandas as pd
    except ImportError:
        np = None
        pd = None
    if isinstance(scan_time, datetime.datetime):
        return scan_time
    if pd and isinstance(scan_time, pd.Timestamp):
        return scan_time.to_pydatetime()
    if np and isinstance(scan_time, np.datetime64):
        return pd.Timestamp(scan_time).to_pydatetime() if pd else scan_time.astype('M8[ms]').astype('O')
    if isinstance(scan_time, (list, tuple)) and len(scan_time) > 0:
        return to_py_datetime(scan_time[0])
    if hasattr(scan_time, 'tolist') and not isinstance(scan_time, (list, tuple)):
        val = scan_time.tolist()
        if isinstance(val, (list, tuple)) and len(val) > 0:
            return to_py_datetime(val[0])
        return to_py_datetime(val)
    return None

def get_timestamp_from_key(key):
    # This function is fine as is
    import re
    match = re.search(r'(\d{8}_\d{6})', key)
    if match:
        return datetime.strptime(match.group(1), '%Y%m%d_%H%M%S')
    return None

def get_latest_level2_keys(site_id, n_frames=7, max_keys=50, max_days_back=3):
    """
    Try to find the latest radar files for a site, searching back up to max_days_back days if needed.
    """
    s3 = boto3.client('s3', region_name='us-east-1', config=Config(signature_version=UNSIGNED))
    now = datetime.utcnow()
    for day_offset in range(max_days_back + 1):
        search_date = now - timedelta(days=day_offset)
        prefix = f"{search_date:%Y/%m/%d}/{site_id}/"
        print(f"Checking S3 prefix: {prefix}")
        paginator = s3.get_paginator('list_objects_v2')
        file_timestamps = []
        for page in paginator.paginate(Bucket='noaa-nexrad-level2', Prefix=prefix, MaxKeys=max_keys):
            for obj in page.get('Contents', []):
                key = obj['Key']
                # Only exclude _MDM files, allow all other files
                if site_id in key and not key.endswith('_MDM'):
                    ts = get_timestamp_from_key(key)
                    if ts:
                        file_timestamps.append((ts, key))
            if len(file_timestamps) >= max_keys:
                break
        file_timestamps.sort()
        selected_s3_keys = [key for ts, key in file_timestamps[-n_frames:]]
        if selected_s3_keys:
            print(f"Selected {len(selected_s3_keys)} files for {site_id} on {search_date:%Y-%m-%d}")
            return selected_s3_keys
        else:
            print(f"No files found for {site_id} on {search_date:%Y-%m-%d}, trying previous day...")
    print(f"No radar files found for {site_id} in the last {max_days_back+1} days.")
    return []

def download_level2_file(key, out_dir):
    # This function is fine as is
    s3 = boto3.client('s3', region_name='us-east-1', config=Config(signature_version=UNSIGNED))
    out_path = os.path.join(out_dir, os.path.basename(key))
    # print(f"Downloading {key} to {out_path}...") # Can be noisy with parallel
    s3.download_file('noaa-nexrad-level2', key, out_path)
    return out_path

def grid_to_geojson(grid, field='reflectivity'):
    # This function is fine as is, but consider potential optimizations for feature creation
    data = grid.fields[field]['data']
    lats = grid.point_latitude['data']
    lons = grid.point_longitude['data']
    features = []

    # Optimization: Filter out NaN values early if a large portion of the grid is empty
    # This will create fewer features, smaller GeoJSON, and faster rendering
    valid_indices = ~np.isnan(data[0, :, :]) # Get indices where data is NOT NaN
    valid_lons = lons[0, valid_indices]
    valid_lats = lats[0, valid_indices]
    valid_values = data[0, valid_indices]

    for i in range(len(valid_values)):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(valid_lons[i]), float(valid_lats[i])]},
            "properties": {"value": float(valid_values[i])}
        })
    return {"type": "FeatureCollection", "features": features}

# New helper function to encapsulate single file processing for parallel execution
def process_single_radar_key(key, output_dir, fields, grid_shape, grid_limits, origin, plot_first_grid_flag):
    import matplotlib.pyplot as plt # Import locally for thread safety
    import pyart # Import locally for thread safety

    # Use a global counter or atomic flag for plotting just the first grid if parallel
    # For simplicity, if plot_first_grid_flag is True, each thread *might* attempt to plot
    # A more robust solution for single plot in parallel would involve a Lock or Manager Value
    
    local_s3_client = boto3.client('s3', region_name='us-east-1', config=Config(signature_version=UNSIGNED)) # Each thread gets its own S3 client

    processed_field_filenames = {}
    file_path = None  # Ensure file_path is always defined
    try:
        file_path = os.path.join(output_dir, os.path.basename(key))
        print(f"Downloading {key} to {file_path}...")
        local_s3_client.download_file('noaa-nexrad-level2', key, file_path)
        print(f"Download complete for {key}.")

        radar = pyart.io.read(file_path)
        # --- Robust scan time extraction for filename ---
        scan_time_py = None
        try:
            # Try Py-ART utility first
            scan_time_py = to_py_datetime(pyart.util.datetime_from_radar(radar))
        except Exception:
            scan_time_py = None
        if not scan_time_py:
            # Fallback: use radar.time['units'] and radar.time['data'][0]
            try:
                import re
                units = radar.time['units']  # e.g. 'seconds since 2025-06-25T07:36:05Z'
                match = re.search(r"since ([0-9T:\-]+)", units)
                if match:
                    base = match.group(1)
                    base_dt = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S")
                    offset = float(radar.time['data'][0])
                    scan_time_py = base_dt + timedelta(seconds=offset)
            except Exception:
                scan_time_py = None
        scan_time_str = scan_time_py.strftime("%Y%m%d_%H%M%S") if scan_time_py else "UNKNOWN_TIME"

        for field in fields:
            if field not in radar.fields:
                print(f"Field '{field}' not found in {key}. Skipping.")
                continue

            grid = pyart.map.grid_from_radars(
                radar,
                grid_shape=grid_shape,
                grid_limits=grid_limits,
                fields=[field],
                origin=origin,
            )
            
            # Plotting logic for a single grid (consider moving this out if running purely headless/scheduled)
            # If `plot_first_grid_flag` is True, this might try to plot for each processed file
            # In a parallel context, `plt.show()` can cause issues. Use `plt.savefig` instead or plot outside the loop.
            # For speed, it's best to remove or only save plots, not show them interactively.
            # if plot_first_grid_flag: # Removed complex first_grid_plotted logic for simplicity in parallel
            #    plt.figure(figsize=(8, 6))
            #    display = pyart.graph.GridMapDisplay(grid)
            #    display.plot_grid(field, level=0, vmin=-20, vmax=70, cmap='pyart_NWSRef')
            #    plt.title(f"{field} for {os.path.basename(key)} at {scan_time_py}")
            #    # plt.show() # Don't use in parallel unless you know what you're doing
            #    plt.savefig(os.path.join(output_dir, f'plot_{os.path.basename(key)}_{field}.png'))
            #    plt.close() # Close figure to free memory

            geojson_data = grid_to_geojson(grid, field)
            output_filename = os.path.join(
                output_dir,
                f'radar_data_{scan_time_str}_{field}.geojson'
            )
            with open(output_filename, 'w') as f:
                json.dump(geojson_data, f)
            processed_field_filenames[field] = output_filename
            print(f"Generated {output_filename} for {field}. Features: {len(geojson_data['features'])}")

    except Exception as e:
        print(f"Error processing {key}: {e}")
    finally:
        # Clean up downloaded raw file
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass
    return processed_field_filenames # Return dictionary of generated filenames for this key

def process_and_export_geojson(site_id, output_dir, fields=['reflectivity'], n_frames=7, plot_first_grid=False):
    # Plotting should ideally be handled outside the main loop or saved to file
    # for performance. Interactive plots will pause execution.
    
    os.makedirs(output_dir, exist_ok=True)
    
    # Grid parameters are moved here to be passed to the parallel function
    GRID_SHAPE = (1, 300, 300)  # Slightly larger grid for better visual density
    GRID_LIMITS = ((0, 10000), (-230000, 230000), (-230000, 230000))  # Typical NEXRAD range
    GRID_ORIGIN = (35.5, -97.5) # KTLX coordinates

    keys = get_latest_level2_keys(site_id, n_frames)

    # Initialize a dict of lists for generated filenames, one list per field
    all_generated_filenames = {field: [] for field in fields}

    import time
    t0 = time.time()

    # Use ThreadPoolExecutor for parallel processing
    # The number of workers can be tuned: too many can lead to diminishing returns or issues
    # A good starting point is number of CPU cores, or slightly more for I/O bound tasks
    cpu_count = os.cpu_count() or 1
    MAX_WORKERS = min(n_frames, cpu_count * 2) # Adjust based on your system, don't exceed n_frames

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Submit tasks to the executor
        future_to_key = {
            executor.submit(
                process_single_radar_key,
                key, output_dir, fields, GRID_SHAPE, GRID_LIMITS, GRID_ORIGIN, plot_first_grid
            ): key for key in keys
        }

        # Collect results as they complete
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                result_filenames_dict = future.result()
                if result_filenames_dict:
                    for field, fname in result_filenames_dict.items():
                        all_generated_filenames[field].append(fname)
            except Exception as exc:
                print(f'{key} generated an exception: {exc}')

    t1 = time.time()
    print(f"GeoJSON export for {site_id} ({n_frames} frames) took {t1-t0:.2f} seconds.")

    # Final sorting for chronological order, important for animation in JS
    for field in fields:
        all_generated_filenames[field].sort()
        print(f"Generated GeoJSON files for {field}:")
        for fname in all_generated_filenames[field]:
            print(f"    '{fname}',")

# Removed export_latest_level2_to_geojson as process_and_export_geojson handles multiple fields and better reporting

def process_all_sites(site_ids, output_dir, fields=['reflectivity'], n_frames=7, plot_first_grid=False):
    start_time = time.time()
    for site_id in site_ids:
        print(f"\nProcessing site: {site_id}")
        process_and_export_geojson(site_id, output_dir, fields=fields, n_frames=n_frames, plot_first_grid=plot_first_grid)
    end_time = time.time()
    print(f"\nTotal processing time for all sites: {end_time - start_time:.2f} seconds")

# --- AWIPS DataAccessLayer Data Type Retrieval Examples ---
from awips.dataaccess import DataAccessLayer

def fetch_satellite_example():
    DataAccessLayer.changeEDEXHost("edex-cloud.unidata.ucar.edu")
    request = DataAccessLayer.newDataRequest("satellite")
    creatingEntities = DataAccessLayer.getIdentifierValues(request, "creatingEntity")
    for entity in creatingEntities:
        print(f"Entity: {entity}")
        request = DataAccessLayer.newDataRequest("satellite")
        request.addIdentifier("creatingEntity", entity)
        availableSectors = DataAccessLayer.getAvailableLocationNames(request)
        availableSectors.sort()
        for sector in availableSectors:
            print(f"  Sector: {sector}")
            request.setLocationNames(sector)
            availableProducts = DataAccessLayer.getAvailableParameters(request)
            availableProducts.sort()
            for product in availableProducts:
                print(f"    Product: {product}")
                # Example: fetch data for this product
                # request.setParameters(product)
                # times = DataAccessLayer.getAvailableTimes(request)
                # if times:
                #     data = DataAccessLayer.getGridData(request, [times[-1]])
                #     print(f"      Data shape: {data[0].getRawData().shape}")

def fetch_binlightning_example():
    request = DataAccessLayer.newDataRequest("binlightning")
    request.addIdentifier("source", "GLMgr")
    request.setParameters("intensity")
    times = DataAccessLayer.getAvailableTimes(request)
    response = DataAccessLayer.getGeometryData(request, times[-10:-1])
    for ob in response:
        geom = ob.getGeometry()
        print(f"Lightning Point: {geom}")

def fetch_grid_example():
    request = DataAccessLayer.newDataRequest()
    request.setDatatype("grid")
    request.setLocationNames("RAP13")
    request.setParameters("T")
    request.setLevels("2.0FHAG")
    cycles = DataAccessLayer.getAvailableTimes(request, True)
    times = DataAccessLayer.getAvailableTimes(request)
    fcstRun = DataAccessLayer.getForecastRun(cycles[-1], times)
    response = DataAccessLayer.getGridData(request, [fcstRun[-1]])
    for grid in response:
        data = grid.getRawData()
        lons, lats = grid.getLatLonCoords()
        print(f"Grid shape: {data.shape}, Lon shape: {lons.shape}, Lat shape: {lats.shape}")

def fetch_warning_example():
    request = DataAccessLayer.newDataRequest()
    request.setDatatype("warning")
    request.setParameters('phensig')
    times = DataAccessLayer.getAvailableTimes(request)
    response = DataAccessLayer.getGeometryData(request, times[-50:-1])
    for ob in response:
        poly = ob.getGeometry()
        site = ob.getLocationName()
        pd   = ob.getDataTime().getValidPeriod()
        ref  = ob.getDataTime().getRefTime()
        print(f"Warning: {site}, Polygon: {poly}")

def fetch_radar_example():
    request = DataAccessLayer.newDataRequest("radar")
    request.setLocationNames("kmhx")
    request.setParameters("Digital Hybrid Scan Refl")
    availableLevels = DataAccessLayer.getAvailableLevels(request)
    times = DataAccessLayer.getAvailableTimes(request)
    response = DataAccessLayer.getGridData(request, [times[-1]])
    for image in response:
        data = image.getRawData()
        lons, lats = image.getLatLonCoords()
        print(f"Radar data shape: {data.shape}, Lon shape: {lons.shape}, Lat shape: {lats.shape}")

# Example usage:
if __name__ == "__main__":
    # Ensure this directory is where your web server serves files from
    output_directory_for_geojson = 'radar-geojson' 
    
    # Example radar IDs you want to process
    # You can add more radars here: ['KTLX', 'KABR', 'KFFC', 'KRAX', ...]
    all_sites_to_process = ['KTLX'] 
    
    # The number of frames you want for each radar/field
    num_frames_per_radar = 7 
    
    # The fields you want to extract (e.g., just reflectivity for simplicity)
    fields_to_process = ['reflectivity'] 
    
    # Set to True if you want to see a matplotlib plot of the first grid processed
    # In parallel processing, this might be tricky, consider removing or saving to file
    should_plot_first_grid = False 

    process_all_sites(
        site_ids=all_sites_to_process,
        output_dir=output_directory_for_geojson,
        fields=fields_to_process,
        n_frames=num_frames_per_radar,
        plot_first_grid=should_plot_first_grid
    )