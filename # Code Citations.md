# Code Citations

## License: MIT
https://github.com/jtfedd/3d-radar/tree/8b73aafaa5f76a3631670b11482424e168802ac0/experiment/radar.py

```
def make_map(bbox, projection=ccrs.PlateCarree()):
    fig, ax = plt.subplots(figsize=(16, 16),
                           subplot_kw=dict(projection=projection))
    ax.set_extent(bbox)
    ax.coastlines(resolution='50m')
```


## License: MIT
https://github.com/jtfedd/3d-radar/tree/8b73aafaa5f76a3631670b11482424e168802ac0/examples/radar.py

```
[0])
    else:
        print("No levels found for " + prod)
        continue

    cycles = DataAccessLayer.getAvailableTimes(request, True)
    times = DataAccessLayer.getAvailableTimes(request)

    if times:
        print()
        response = DataAccessLayer.getGridData(request, [times[-1
```


## License: MIT
https://github.com/USA-RedDragon/nws-slack-bot/tree/2955f76433afcff69646b7dbd62a8d5f2dbf223c/src/map.py

```
= grid.getLatLonCoords()

        print('Time :', str(grid.getDataTime()))
        flat = np.ndarray.flatten(data)
        print('Name :', str(grid.getLocationName()))
        print('Prod :',
```


## License: MIT
https://github.com/jtfedd/3d-radar/tree/8b73aafaa5f76a3631670b11482424e168802ac0/experiment/view_data.py

```
))
        print('Range:' , np.nanmin(flat), " to ", np.nanmax(flat), " (Unit :", grid.getUnit(), ")")
        print('Size :', str(data.shape
```

