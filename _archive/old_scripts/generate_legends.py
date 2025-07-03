import matplotlib.pyplot as plt
from matplotlib import colors as mcolors
import numpy as np
import pyart
from palette_utils import parse_pal_file, make_colormap_from_points
import os

def save_colorbar(cmap, vmin, vmax, label, ticks, ticklabels, filename):
    fig, ax = plt.subplots(figsize=(6, 1))
    fig.subplots_adjust(bottom=0.5)
    norm = mcolors.Normalize(vmin, vmax)
    cb = plt.colorbar(
        plt.cm.ScalarMappable(norm=norm, cmap=cmap),
        cax=ax, orientation='horizontal',
        ticks=ticks
    )
    cb.set_label(label, fontsize=14)
    cb.ax.set_xticklabels(ticklabels)
    plt.savefig(filename, bbox_inches='tight', dpi=150)
    plt.close(fig)

if __name__ == "__main__":
    # Reflectivity (dBZ)
    cmap_ref = pyart.graph.cm.NWSRef
    save_colorbar(
        cmap_ref, -20, 80, 'Reflectivity (dBZ)',
        ticks=[-20, 0, 20, 40, 60, 80],
        ticklabels=['-20', '0', '20', '40', '60', '80'],
        filename='legend_reflectivity.png'
    )
    # Velocity (m/s)
    cmap_vel = pyart.graph.cm.NWSVel
    save_colorbar(
        cmap_vel, -32, 32, 'Velocity (m/s)',
        ticks=[-32, -16, 0, 16, 32],
        ticklabels=['-32', '-16', '0', '16', '32'],
        filename='legend_velocity.png'
    )
    # Correlation Coefficient (rhohv)
    cmap_rhohv = pyart.graph.cm.NWSRhoHV
    save_colorbar(
        cmap_rhohv, 0.5, 1.0, 'Correlation Coefficient',
        ticks=[0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
        ticklabels=['0.5', '0.6', '0.7', '0.8', '0.9', '1.0'],
        filename='legend_rhohv.png'
    )
    # Example for custom palette (uncomment and edit as needed):
    # pal_path = os.path.expanduser('~/Downloads/WxTools - HRRR Reflectivity.pal')
    # points = parse_pal_file(pal_path)
    # cmap_custom = make_colormap_from_points(points, name='WxTools')
    # save_colorbar(cmap_custom, 0, 80, 'WxTools Reflectivity', [0, 20, 40, 60, 80], ['0', '20', '40', '60', '80'], 'legend_wxtools.png')
