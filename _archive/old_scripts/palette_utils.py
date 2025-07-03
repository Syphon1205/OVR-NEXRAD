import numpy as np
import matplotlib.colors as mcolors
import re

def parse_pal_file(filepath):
    """
    Parse a .pal file and return a list of (value, (r, g, b)) tuples.
    Supports WxTools, NCEI, Viper HD, RadarOmega, and similar formats.
    """
    color_points = []
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith(';'):
                continue
            # Remove inline comments
            line = line.split(';')[0].strip()
            # Match color: or color4: or COLOR:
            m = re.match(r'^(color4?|COLOR):?\s*(.*)', line, re.IGNORECASE)
            if m:
                parts = m.group(2).split()
                if len(parts) >= 4:
                    try:
                        val = float(parts[0])
                        r = int(parts[1])
                        g = int(parts[2])
                        b = int(parts[3])
                        color_points.append((val, (r, g, b)))
                    except Exception:
                        continue
                continue
            # WxTools: SolidColor: 10 216 226 243
            m = re.match(r'SolidColor:\s*([\d.]+)\s+(\d+)\s+(\d+)\s+(\d+)', line)
            if m:
                val = float(m.group(1))
                rgb = tuple(int(m.group(i)) for i in range(2, 5))
                color_points.append((val, rgb))
                continue
            # NCEI: Color4: -25 0 0 0 255
            m = re.match(r'Color4:\s*([\-\d.]+)\s+(\d+)\s+(\d+)\s+(\d+)\s+\d+', line)
            if m:
                val = float(m.group(1))
                rgb = tuple(int(m.group(i)) for i in range(2, 5))
                color_points.append((val, rgb))
                continue
            # Viper HD: COLOR: 0       1 243 247
            m = re.match(r'COLOR:\s*([\d.]+)\s+(\d+)\s+(\d+)\s+(\d+)', line, re.IGNORECASE)
            if m:
                val = float(m.group(1))
                rgb = tuple(int(m.group(i)) for i in range(2, 5))
                color_points.append((val, rgb))
                continue
            # Fallback: whitespace split, at least 4 values
            parts = line.split()
            if len(parts) >= 4:
                try:
                    val = float(parts[0])
                    r = int(parts[1])
                    g = int(parts[2])
                    b = int(parts[3])
                    color_points.append((val, (r, g, b)))
                except Exception:
                    continue
    return color_points

def make_colormap_from_points(points, name='custom', vmin=None, vmax=None):
    """
    Create a matplotlib colormap from a list of (value, (r, g, b)) tuples.
    """
    if not points:
        raise ValueError('No color points found')
    points = sorted(points, key=lambda x: x[0])
    vals, rgbs = zip(*points)
    vals = np.array(vals)
    if vmin is None:
        vmin = vals[0]
    if vmax is None:
        vmax = vals[-1]
    norm_vals = (vals - vmin) / (vmax - vmin)
    colors = [(nv, np.array(rgb)/255.0) for nv, rgb in zip(norm_vals, rgbs)]
    cdict = {'red': [], 'green': [], 'blue': []}
    for nv, rgb in colors:
        cdict['red'].append((nv, rgb[0], rgb[0]))
        cdict['green'].append((nv, rgb[1], rgb[1]))
        cdict['blue'].append((nv, rgb[2], rgb[2]))
    return mcolors.LinearSegmentedColormap(name, cdict)

def get_classic_reflectivity_points():
    """
    Returns the classic NEXRAD/GR2Analyst reflectivity color table as (value, (r,g,b)) tuples.
    Values and colors are based on the standard 16-level NEXRAD palette.
    """
    return [
        (-30, (4, 233, 231)),   # light cyan
        (-25, (1, 159, 244)),   # blue
        (-20, (3, 0, 244)),     # deep blue
        (-15, (2, 253, 2)),     # green
        (-10, (1, 197, 1)),     # dark green
        (-5,  (0, 142, 0)),     # darker green
        (0,   (253, 248, 2)),   # yellow
        (5,   (229, 188, 0)),   # gold
        (10,  (253, 149, 0)),   # orange
        (15,  (253, 0, 0)),     # red
        (20,  (212, 0, 0)),     # dark red
        (25,  (188, 0, 0)),     # darker red
        (30,  (248, 0, 253)),   # magenta
        (35,  (152, 84, 198)),  # purple
        (40,  (253, 253, 253)), # white
        (45,  (152, 84, 198)),  # purple (repeat for high values)
        (50,  (248, 0, 253)),   # magenta (repeat)
        (55,  (188, 0, 0)),     # dark red (repeat)
        (60,  (212, 0, 0)),     # dark red (repeat)
        (65,  (253, 0, 0)),     # red (repeat)
    ]
