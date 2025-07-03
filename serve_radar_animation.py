import os
from radar_animation_utils import get_radar_animation_frames

def serve_radar_animation_for_site(main_window, site_id, n_frames=6, interval_ms=200):
    """
    Fetches radar animation frames for a site, serves them to the frontend via JS bridge.
    Assumes PNGs are accessible via a static file server or local file URLs.
    """
    pngs, bounds, lat, lon, radius_km = get_radar_animation_frames(site_id, n_frames)
    if not pngs:
        return
    # Convert file paths to URLs (assuming local file URLs for QWebEngineView)
    png_urls = [f'file:///{os.path.abspath(png).replace(os.sep, "/")}' for png in pngs]
    js = f"window.setRadarAnimationFrames({png_urls}, {bounds}, {lat}, {lon}, {radius_km}, {interval_ms})"
    try:
        page = main_window.radar_map.page() if hasattr(main_window, 'radar_map') and main_window.radar_map else None
        if page is not None:
            page.runJavaScript(js)
    except Exception as e:
        print(f"Error sending radar animation to JS: {e}")
