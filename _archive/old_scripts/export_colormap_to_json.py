import matplotlib
import json

# Export a Py-ART colormap (e.g., HomeyerRainbow) as a JS-friendly array
cmap = matplotlib.cm.get_cmap('pyart_HomeyerRainbow')
colors = [matplotlib.colors.rgb2hex(cmap(i/255.0)) for i in range(256)]
with open('colormap_homeyer.json', 'w') as f:
    json.dump(colors, f)
print('Exported colormap_homeyer.json')
