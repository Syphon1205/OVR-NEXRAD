import os
import tempfile
import requests
import xarray as xr
import numpy as np
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
from PIL import Image

def fetch_hrrr_grib2(field="TMP:2 m", model_run_hour=None):
    """
    Download the latest HRRR CONUS GRIB2 file and return the local path.
    """
    # Use current UTC date/hour for latest run
    from datetime import datetime, timedelta
    now = datetime.utcnow() if model_run_hour is None else model_run_hour
    date_str = now.strftime("%Y%m%d")
    hour_str = now.strftime("%H")
    # HRRR files are named hrrr.t{hour}z.wrfsfcf00.grib2
    url = f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/hrrr/prod/hrrr.{date_str}/conus/hrrr.t{hour_str}z.wrfsfcf00.grib2"
    local_path = os.path.join(tempfile.gettempdir(), f"hrrr.t{hour_str}z.wrfsfcf00.grib2")
    if not os.path.exists(local_path):
        r = requests.get(url, stream=True)
        if r.status_code == 200:
            with open(local_path, 'wb') as f:
                for chunk in r.iter_content(1024 * 1024):
                    f.write(chunk)
        else:
            raise Exception(f"Failed to download HRRR file: {url}")
    return local_path

def render_hrrr_field_to_png(grib2_path, field="TMP:2 m", out_png=None, out_world=None):
    """
    Extract the specified field from the GRIB2 file and render as PNG with world file.
    """
    ds = xr.open_dataset(grib2_path, engine="cfgrib")
    # Find the variable for the field
    var_names = list(ds.data_vars)
    if field == "TMP:2 m":
        var = [str(v) for v in var_names if ("2 m" in str(v) or "t2m" in str(v).lower())][0]
        arr = ds[var].values - 273.15  # K to C
        cmap = plt.get_cmap("coolwarm")
        vmin, vmax = -30, 45
    elif field == "REFC:entire atmosphere":
        var = [str(v) for v in var_names if "refc" in str(v).lower()][0]
        arr = ds[var].values
        cmap = plt.get_cmap("nipy_spectral")
        vmin, vmax = 0, 75
    elif field == "UGRD:10 m":
        var = [str(v) for v in var_names if ("u10" in str(v).lower() or "ugrd" in str(v).lower())][0]
        arr = ds[var].values
        cmap = plt.get_cmap("viridis")
        vmin, vmax = -40, 40
    elif field == "VGRD:10 m":
        var = [str(v) for v in var_names if ("v10" in str(v).lower() or "vgrd" in str(v).lower())][0]
        arr = ds[var].values
        cmap = plt.get_cmap("viridis")
        vmin, vmax = -40, 40
    else:
        raise Exception(f"Unsupported field: {field}")
    # Get lat/lon
    lats = ds.latitude.values
    lons = ds.longitude.values
    # Plot with Cartopy
    fig = plt.figure(figsize=(8, 6), dpi=150)
    ax = plt.axes(projection=ccrs.PlateCarree())
    im = ax.pcolormesh(lons, lats, arr, cmap=cmap, vmin=vmin, vmax=vmax, transform=ccrs.PlateCarree())
    # Set extent if possible (use ax for Cartopy GeoAxes)
    try:
        geo_ax = ax if hasattr(ax, 'set_extent') else None
        if geo_ax:
            geo_ax.set_extent([float(np.min(lons)), float(np.max(lons)), float(np.min(lats)), float(np.max(lats))], crs=ccrs.PlateCarree())
    except Exception:
        pass
    ax.axis('off')
    plt.tight_layout(pad=0)
    if out_png is None:
        out_png = os.path.join(tempfile.gettempdir(), "hrrr_field.png")
    plt.savefig(out_png, bbox_inches='tight', pad_inches=0, transparent=True)
    plt.close(fig)
    # Write world file for georeferencing
    if out_world is not None:
        img = Image.open(out_png)
        width, height = img.size
        xres = (float(np.max(lons)) - float(np.min(lons))) / width
        yres = (float(np.max(lats)) - float(np.min(lats))) / height
        with open(out_world, 'w') as f:
            f.write(f"{xres}\n0.0\n0.0\n{-yres}\n{float(np.min(lons))}\n{float(np.max(lats))}\n")
    return out_png

def plot_sounding_with_sharppy(p, t, td, u, v, z=None, station='HRRR', time=None, out_png=None):
    """
    Plot a Skew-T/Log-P diagram using SHARPpy from profile arrays.
    p, t, td: pressure (hPa), temperature (C), dewpoint (C)
    u, v: wind components (kts)
    z: height (m, optional)
    station: station name
    time: datetime (optional)
    out_png: output PNG path
    """
    import sharppy.sharptab.profile as profile
    import sharppy.sharptab.utils as utils
    import sharppy.sharptab.params as params
    from sharppy.plot.skewt import SkewT
    # Create SHARPpy profile
    prof = profile.create_profile(pres=p, hght=z, tmpc=t, dwpc=td, u=u, v=v, strictQC=False, date=time)
    fig = plt.figure(figsize=(7,7))
    skew = SkewT(fig, rotation=45)
    skew.plot_profile(prof)
    skew.plot_winds(prof)
    skew.ax.set_title(f"Sounding: {station}")
    if out_png is None:
        out_png = os.path.join(tempfile.gettempdir(), "sounding.png")
    plt.savefig(out_png, bbox_inches='tight', pad_inches=0.1, dpi=150)
    plt.close(fig)
    return out_png

def extract_hrrr_profile(grib2_path, lat, lon):
    """
    Extract a vertical profile (sounding) from HRRR at the nearest grid point to (lat, lon).
    Returns: dict with p, t, td, u, v, z arrays (all in SHARPpy units)
    """
    ds = xr.open_dataset(grib2_path, engine="cfgrib")
    # Find nearest grid point
    lat_arr = ds.latitude.values
    lon_arr = ds.longitude.values
    dist = (lat_arr - lat)**2 + (lon_arr - lon)**2
    idx = np.unravel_index(np.argmin(dist), lat_arr.shape)
    # Extract vertical levels
    p = ds['isobaricInhPa'].values if 'isobaricInhPa' in ds else ds['isobaricInhPa0'].values
    t = ds['t'].values[:, idx[0], idx[1]] - 273.15  # K to C
    td = ds['d2m'].values[:, idx[0], idx[1]] - 273.15 if 'd2m' in ds else t - 2  # fallback
    u = ds['u'].values[:, idx[0], idx[1]] * 1.94384 if 'u' in ds else np.zeros_like(t)  # m/s to kts
    v = ds['v'].values[:, idx[0], idx[1]] * 1.94384 if 'v' in ds else np.zeros_like(t)
    z = ds['gh'].values[:, idx[0], idx[1]] if 'gh' in ds else None
    return dict(p=p, t=t, td=td, u=u, v=v, z=z)

def plot_hrrr_sounding_at_location(grib2_path, lat, lon, station='HRRR', time=None, out_png=None):
    """
    Convenience function: extract HRRR profile at (lat, lon) and plot Skew-T with SHARPpy.
    Returns the path to the PNG image.
    """
    prof = extract_hrrr_profile(grib2_path, lat, lon)
    return plot_sounding_with_sharppy(
        prof['p'], prof['t'], prof['td'], prof['u'], prof['v'], z=prof['z'], station=station, time=time, out_png=out_png
    )
