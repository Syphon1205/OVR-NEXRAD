import numpy as np

def extract_storm_attributes(radar):
    """
    Takes a Py-ART radar object and returns storm attributes.
    Returns:
        dict: {
            "max_dbz": float,
            "hail_detected": bool,
            "max_velocity": float or None,
            "avg_vil": float
        }
    """
    attributes = {
        "max_dbz": None,
        "hail_detected": False,
        "max_velocity": None,
        "avg_vil": None,
    }

    # Max Reflectivity
    if 'reflectivity' in radar.fields:
        refl = radar.fields['reflectivity']['data']
        if hasattr(refl, 'filled'):
            refl = refl.filled(np.nan)
        max_dbz = np.nanmax(refl)
        attributes['max_dbz'] = max_dbz

        # Hail detection: simple >60 dBZ logic
        attributes['hail_detected'] = np.any(refl > 60)

        # VIL estimate
        vil_estimates = []
        for sweep in range(radar.nsweeps):
            try:
                refl_sweep = radar.get_field(sweep, 'reflectivity')
                if hasattr(refl_sweep, 'filled'):
                    refl_sweep = refl_sweep.filled(np.nan)
                z_linear = 10 ** (refl_sweep / 10)
                spacing_km = radar.range['meters_between_gates'] / 1000.0
                vil_col = np.nansum(z_linear) * spacing_km
                vil_estimates.append(vil_col)
            except Exception:
                continue
        if vil_estimates:
            attributes['avg_vil'] = np.nanmean(vil_estimates)

    # Velocity estimation
    if 'velocity' in radar.fields:
        vel = radar.fields['velocity']['data']
        if hasattr(vel, 'filled'):
            vel = vel.filled(np.nan)
        attributes['max_velocity'] = np.nanmax(np.abs(vel))

    return attributes
