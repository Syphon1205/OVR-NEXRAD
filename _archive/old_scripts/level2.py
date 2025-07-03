# Minimal NEXRAD Level 2 file parser (MIT License)
# Adapted from pyIEM's level2.py (https://github.com/akrherz/pyIEM)
# This is a minimal, pure-Python parser for NEXRAD Level 2 files, supporting reflectivity, velocity, and spectrum width.
# Only basic sweeps and ray data are supported. For advanced features, use a full-featured library.

import struct
import gzip
import io

class Level2File:
    def __init__(self, filename):
        self.filename = filename
        self.sweeps = []  # List of dicts: {'type': int, 'first_ray': int, 'last_ray': int}
        self.rays = []    # List of dicts: {'data': [arrays], ...}
        self._parse_file()

    def _parse_file(self):
        # Open file (support .gz)
        if self.filename.endswith('.gz'):
            f = gzip.open(self.filename, 'rb')
        else:
            f = open(self.filename, 'rb')
        with f:
            # Skip message header (24 bytes)
            f.seek(24)
            # Read record by record
            ray_idx = 0
            sweep_type = None
            sweep_start = 0
            while True:
                hdr = f.read(2432)
                if not hdr or len(hdr) < 2432:
                    break
                # Message type is at byte 0 (2 bytes)
                msg_type = struct.unpack('>h', hdr[0:2])[0]
                if msg_type != 31:
                    continue  # Only process type 31 (digital radar data)
                # Extract basic info
                elev_num = hdr[48]
                n_fields = hdr[50]
                # Field types: 1=REF, 2=VEL, 3=SW, 4=ZDR, 5=PHI, 6=RHO
                field_types = [hdr[51+i] for i in range(n_fields)]
                # For simplicity, only process first field (usually reflectivity)
                # Data block starts at byte 120
                data = []
                for i in range(n_fields):
                    # Each field: 460 bytes (920 halfwords)
                    offset = 120 + i*460
                    field = struct.unpack('>460B', hdr[offset:offset+460])
                    # Convert to float, missing=0
                    arr = [(v if v < 255 else 0) for v in field]
                    data.append(arr)
                # Save ray
                self.rays.append({'data': data, 'elev_num': elev_num, 'field_types': field_types})
                # Detect sweep boundaries (simple: new elev_num)
                if sweep_type is None:
                    sweep_type = field_types[0] if field_types else 1
                    sweep_start = ray_idx
                elif elev_num != self.rays[ray_idx-1]['elev_num']:
                    # End previous sweep
                    self.sweeps.append({'type': sweep_type, 'first_ray': sweep_start, 'last_ray': ray_idx-1})
                    sweep_type = field_types[0] if field_types else 1
                    sweep_start = ray_idx
                ray_idx += 1
            # End last sweep
            if sweep_type is not None and ray_idx > 0:
                self.sweeps.append({'type': sweep_type, 'first_ray': sweep_start, 'last_ray': ray_idx-1})

    def get_ray(self, idx):
        return self.rays[idx]
