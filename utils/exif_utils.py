from PIL.ExifTags import TAGS, GPSTAGS
from pathlib import Path
from typing import Any, Dict, Optional
from PIL import Image
import requests
import time
import re
from functools import lru_cache
from PIL.TiffImagePlugin import IFDRational


def convert_to_degrees(value) -> Optional[float]:
    """Convert GPS coordinates from DMS format to decimal degrees."""
    try:
        d, m, s = value
        return float(d) + (float(m) / 60.0) + (float(s) / 3600.0)
    except Exception:
        return None


def get_gps_info(exif_data: dict) -> Dict[str, Any]:
    """Extract and normalize GPS information from raw EXIF dict."""
    gps_info = {}
    gps_raw = exif_data.get(34853)  # GPSInfo tag ID
    if not gps_raw:
        return gps_info

    # Decode GPS keys
    gps_decoded = {}
    for key, val in gps_raw.items():
        decoded = GPSTAGS.get(key, key)
        gps_decoded[decoded] = val

    # Parse latitude
    if "GPSLatitude" in gps_decoded and "GPSLatitudeRef" in gps_decoded:
        lat = convert_to_degrees(gps_decoded["GPSLatitude"])
        if lat is not None:
            if gps_decoded["GPSLatitudeRef"] in ["S", "s"]:
                lat = -lat
            gps_info["latitude"] = lat

    # Parse longitude
    if "GPSLongitude" in gps_decoded and "GPSLongitudeRef" in gps_decoded:
        lon = convert_to_degrees(gps_decoded["GPSLongitude"])
        if lon is not None:
            if gps_decoded["GPSLongitudeRef"] in ["W", "w"]:
                lon = -lon
            gps_info["longitude"] = lon

    # Parse altitude
    if "GPSAltitude" in gps_decoded:
        try:
            gps_info["altitude"] = float(gps_decoded["GPSAltitude"])
        except Exception:
            pass

    # Parse date & time
    if "GPSDateStamp" in gps_decoded and "GPSTimeStamp" in gps_decoded:
        try:
            date_str = gps_decoded["GPSDateStamp"].replace(":", "-")
            h, m, s = gps_decoded["GPSTimeStamp"]
            gps_info["gps_timestamp"] = (
                f"{date_str} {int(h):02d}:{int(m):02d}:{int(s):02d}"
            )
        except Exception:
            pass

    return gps_info


@lru_cache(maxsize=500)
def reverse_geocode(latitude: float, longitude: float) -> Optional[Dict[str, str]]:
    """Reverse geocode GPS coordinates using Nominatim API."""
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None

    try:
        time.sleep(1.0)  # respect Nominatim rate limit
        url = "https://nominatim.openstreetmap.org/reverse"
        params = {
            "format": "json",
            "lat": latitude,
            "lon": longitude,
            "addressdetails": 1,
        }
        headers = {"User-Agent": "ImageIndexer/1.0 (Research)"}
        response = requests.get(url, params=params, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        if "address" in data:
            addr = data["address"]

            # Try direct postcode, defaults to None if not found
            postcode = addr.get("postcode")

            # Fallback: extract 5-6 digit number from display_name
            if not postcode and "display_name" in data:
                match = re.search(r"\b\d{5,6}\b", data["display_name"])
                if match:
                    postcode = match.group(0)
            return {
                "location_display_name": data.get("display_name"),
                "location_country": addr.get("country"),
                "location_state": addr.get("state") or addr.get("province"),
                "location_district": addr.get("state_district"),
                "location_city": addr.get("city")
                or addr.get("town")
                or addr.get("village")
                or addr.get("hamlet")
                or addr.get("municipality")
                or addr.get("subdistrict")
                or addr.get("county"),
                "location_postcode": postcode,
                "location_road": addr.get("road")
                or addr.get("path")
                or addr.get("footway")
                or data.get("name"),
                "location_suburb": addr.get("suburb")
                or addr.get("neighbourhood")
                or addr.get("quarter")
                or addr.get("subdistrict"),
            }
    except Exception as e:
        print(f"Warning: reverse geocode failed - {e}")

    return None


def get_exif_data(image_path: Path) -> Dict[str, Any]:
    """
    Extract and parse EXIF metadata from an image.
    Returns a dictionary with specific normalized keys + parsed GPS info.
    """
    try:
        image = Image.open(image_path)
        # Convert palette images with transparency to RGBA to avoid PIL warnings
        if image.mode == "P" and "transparency" in image.info:
            image = image.convert("RGBA")
        img_width, img_height = image.size
        exif_data = None
        if hasattr(image, "_getexif"):
            try:
                exif_data = image._getexif()
            except Exception:
                exif_data = None

        exif: Dict[str, Any] = {}
        gps_info: Dict[str, Any] = {}
        location = None

        if exif_data:
            for tag_id, value in exif_data.items():
                tag = TAGS.get(tag_id, tag_id)

                # Decode bytes if necessary
                if isinstance(value, bytes):
                    try:
                        value = value.decode("utf-8", "ignore")
                    except Exception:
                        pass
                # Convert IFDRational to float
                elif isinstance(value, IFDRational):
                    try:
                        value = float(value)
                    except Exception:
                        value = None
                exif[tag] = value

            # Parse GPS info, but don't fail if it's not there
            gps_info = get_gps_info(exif_data)

            # Optional: reverse geocode ONLY if we have coordinates
            if "latitude" in gps_info and "longitude" in gps_info:
                location = reverse_geocode(gps_info["latitude"], gps_info["longitude"])

        # Determine dimensions: prefer valid EXIF dimensions if present, otherwise fallback to native image size
        raw_width = exif.get("ExifImageWidth") or exif.get("ImageWidth")
        raw_height = exif.get("ExifImageHeight") or exif.get("ImageLength")

        final_width = (
            int(raw_width)
            if (raw_width and isinstance(raw_width, (int, float)) and raw_width > 0)
            else img_width
        )
        final_height = (
            int(raw_height)
            if (raw_height and isinstance(raw_height, (int, float)) and raw_height > 0)
            else img_height
        )

        # Construct the specific meta dictionary
        meta = {
            "make": exif.get("Make"),
            "model": exif.get("Model"),
            "software": exif.get("Software"),
            "width": final_width,
            "height": final_height,
            "orientation": exif.get("Orientation"),
            "datetime_original": exif.get("DateTimeOriginal"),
            "datetime_digitized": exif.get("DateTimeDigitized"),
            "exposure_time": exif.get("ExposureTime"),
            "f_number": exif.get("FNumber"),
            "iso": exif.get("ISOSpeedRatings"),
            "focal_length": exif.get("FocalLength"),
            "flash": exif.get("Flash"),
            "latitude": gps_info.get("latitude"),
            "longitude": gps_info.get("longitude"),
            "altitude": gps_info.get("altitude"),
            "gps_timestamp": gps_info.get("gps_timestamp"),
            # Location info
            "location_display_name": (
                location.get("location_display_name") if location else None
            ),
            "location_country": (
                location.get("location_country") if location else None
            ),
            "location_state": location.get("location_state") if location else None,
            "location_city": location.get("location_city") if location else None,
            "location_postcode": (
                location.get("location_postcode") if location else None
            ),
            "location_district": (
                location.get("location_district") if location else None
            ),
        }

        return meta

    except Exception as e:
        return {"error": str(e)}


def get_camera_metadata(exif: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract camera/shooting metadata from an already-parsed EXIF dict
    (i.e. the dict returned by get_exif_data).
    """
    return {
        "make": exif.get("make"),
        "model": exif.get("model"),
        "software": exif.get("software"),
        "width": exif.get("width"),
        "height": exif.get("height"),
        "orientation": exif.get("orientation"),
        "datetime_original": exif.get("datetime_original"),
        "datetime_digitized": exif.get("datetime_digitized"),
        "exposure_time": exif.get("exposure_time"),
        "f_number": exif.get("f_number"),
        "iso": exif.get("iso"),
        "focal_length": exif.get("focal_length"),
        "flash": exif.get("flash"),
    }


if __name__ == "__main__":
    # Example usage
    img_path = Path(r"D:\Coding\MediaMCP\media\20231224173151_IMG_7683.JPG")
    metadata = get_exif_data(img_path)
    for k, v in metadata.items():
        print(f"{k}: {v}")
