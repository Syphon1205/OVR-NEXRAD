import pyart
import os
import datetime

class NexradLevel3Parser:
    """
    Python utility to parse NEXRAD Level 3 files and output a structure similar to netbymatt/nexrad-level-3-data JS parser.
    """
    def __init__(self, filepath):
        self.filepath = filepath
        self.radar = None
        self.metadata = {}
        self.data = None
        self.parse()

    def parse(self):
        try:
            self.radar = pyart.io.read_level3(self.filepath)
        except Exception as e:
            raise RuntimeError(f"Could not parse Level 3 file: {e}")
        # Extract metadata
        self.metadata = {
            'productType': self._get_product_type(),
            'site': self._get_site(),
            'datetime': self._get_datetime(),
            'fields': list(self.radar.fields.keys()),
            'shape': self.radar.fields[list(self.radar.fields.keys())[0]]['data'].shape if self.radar.fields else None,
        }
        # Extract data (as numpy array)
        if self.radar.fields:
            field = list(self.radar.fields.keys())[0]
            self.data = self.radar.fields[field]['data']
        else:
            self.data = None

    def _get_product_type(self):
        # Try to extract from filename or radar object
        fname = os.path.basename(self.filepath)
        for code in ['N0Q', 'N0R', 'N0U', 'N0V', 'N0S', 'N0X', 'N0Z']:
            if code in fname:
                return code
        # Fallback: try radar object
        return getattr(self.radar, 'product', 'Unknown')

    def _get_site(self):
        # Try to extract from filename or radar object
        fname = os.path.basename(self.filepath)
        parts = fname.split('.')
        if len(parts) > 1:
            return parts[0][-3:]
        return getattr(self.radar, 'station', 'Unknown')

    def _get_datetime(self):
        # Try to extract from radar object
        try:
            dt = self.radar.time['units']
            # Example: 'seconds since 2024-06-03T21:00:00Z'
            if 'since' in dt:
                dtstr = dt.split('since')[1].strip().replace('Z','')
                return datetime.datetime.fromisoformat(dtstr)
        except Exception:
            pass
        return None

    def to_dict(self):
        return {
            'metadata': self.metadata,
            'data': self.data,
        }

# Example usage:
# parser = NexradLevel3Parser('path/to/file.N0Q')
# result = parser.to_dict()
# print(result['metadata'])
# print(result['data'])
