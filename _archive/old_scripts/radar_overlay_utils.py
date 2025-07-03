import pyart
import numpy as np
import matplotlib.pyplot as plt

def save_reflectivity_image(radar, filename='reflectivity.png'):
    """Save a Py-ART reflectivity PPI as a PNG image with no axes or borders."""
    display = pyart.graph.RadarMapDisplay(radar)
    fig = plt.figure(figsize=(6, 6), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])  # Fill the whole image
    display.plot_ppi_map(
        'reflectivity', ax=ax, resolution='110m',
        vmin=-32, vmax=80, cmap='NWSRef',
        colorbar_flag=False, lat_lines=None, lon_lines=None,
        min_lon=None, max_lon=None, min_lat=None, max_lat=None,
        title_flag=False
    )
    ax.set_axis_off()
    plt.axis('off')
    plt.savefig(filename, transparent=True, bbox_inches='tight', pad_inches=0)
    plt.close(fig)

def get_radar_bounds(radar):
    """Return [[min_lat, min_lon], [max_lat, max_lon]] bounding box for radar coverage."""
    lat = radar.latitude['data'][0]
    lon = radar.longitude['data'][0]
    max_range = radar.instrument_parameters['unambiguous_range']['data'][0] / 1000  # in km

    # Approximate bounding box (improve if you want)
    min_lat = lat - (max_range / 111)
    max_lat = lat + (max_range / 111)
    min_lon = lon - (max_range / (111 * np.cos(np.radians(lat))))
    max_lon = lon + (max_range / (111 * np.cos(np.radians(lat))))

    return [[min_lat, min_lon], [max_lat, max_lon]]
