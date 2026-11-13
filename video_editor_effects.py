"""Video editor parameter validation and Pillow-rendered cover effects."""
import colorsys
from pathlib import Path
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageDraw

DEFAULTS = {
    "cover": {"zoom": 1, "x": 50, "y": 50, "fit": "cover", "brightness": 100,
              "contrast": 100, "saturation": 100, "sharpness": 100, "blur": 0,
              "warmth": 0, "hue": 0, "vignette": 0},
    "particles": {"x": 50, "y": 50, "size": 100, "opacity": 85,
                  "speed": 100, "brightness": 100, "blur": 0, "color": "#ffffff"},
    "ring": {"x": 50, "y": 50, "size": 28, "opacity": 90,
             "color": "#ffffff", "sensitivity": 300, "glow": 80, "glow_blur": 14},
    "export": {"quality": "high"},
}
LIMITS = {
    "zoom": (0.5, 3), "x": (0, 100), "y": (0, 100),
    "brightness": (0, 200), "contrast": (0, 200), "saturation": (0, 200),
    "sharpness": (0, 300), "blur": (0, 15), "warmth": (-100, 100),
    "hue": (-180, 180), "vignette": (0, 100), "size": (10, 180),
    "opacity": (0, 100), "speed": (25, 300),
    "sensitivity": (100, 500), "glow": (0, 100), "glow_blur": (0, 35)
}

def normalize_editor(data):
    if not isinstance(data, dict):
        raise ValueError("Video editor settings must be an object.")
    result = {}
    for section, fields in DEFAULTS.items():
        incoming = data.get(section) or {}
        if not isinstance(incoming, dict):
            raise ValueError(section + " settings must be an object.")
        result[section] = {}
        for name, default in fields.items():
            raw = incoming.get(name, default)
            if name == "quality":
                if raw not in ("balanced", "high", "ultra"):
                    raise ValueError("Export quality must be balanced, high or ultra.")
                value = raw
            elif name == "fit":
                if raw not in ("cover", "contain"):
                    raise ValueError("Cover fit must be cover or contain.")
                value = raw
            elif name == "color":
                value = str(raw)
                if len(value) != 7 or value[0] != "#" or any(ch not in "0123456789abcdefABCDEF" for ch in value[1:]):
                    raise ValueError("Use a six-digit particle or ring color.")
            else:
                try:
                    value = float(raw)
                except (TypeError, ValueError) as exc:
                    raise ValueError("Invalid " + section + " " + name) from exc
                low, high = LIMITS[name]
                if section == "ring" and name == "size":
                    low, high = 8, 75
                if section == "particles" and name == "size":
                    low, high = 20, 180
                if not low <= value <= high:
                    raise ValueError(section + " " + name + " must be between " + str(low) + " and " + str(high))
            result[section][name] = value
    return result


def cover_frame(source, output, opts, size=(1920, 1080)):
    """Apply persistent visual edits and write a fixed-sized frame for FFmpeg."""
    width, height = size
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
    if opts["fit"] == "cover":
        factor = max(width / image.width, height / image.height) * opts["zoom"]
    else:
        factor = min(width / image.width, height / image.height) * opts["zoom"]
    target = (max(1, round(image.width * factor)), max(1, round(image.height * factor)))
    image = image.resize(target, Image.Resampling.LANCZOS)
    if opts["hue"]:
        hsv = image.convert("HSV")
        h, s, v = hsv.split()
        h = h.point(lambda val: (val + int(opts["hue"] / 360 * 255)) % 256)
        image = Image.merge("HSV", (h, s, v)).convert("RGB")
    if opts["warmth"]:
        r, g, b = image.split()
        shift = opts["warmth"] / 100
        r = r.point(lambda p: max(0, min(255, int(p * (1 + 0.3 * shift)))))
        b = b.point(lambda p: max(0, min(255, int(p * (1 - 0.3 * shift)))))
        image = Image.merge("RGB", (r, g, b))
    image = ImageEnhance.Brightness(image).enhance(opts["brightness"] / 100)
    image = ImageEnhance.Contrast(image).enhance(opts["contrast"] / 100)
    image = ImageEnhance.Color(image).enhance(opts["saturation"] / 100)
    image = ImageEnhance.Sharpness(image).enhance(opts["sharpness"] / 100)
    if opts["blur"]:
        image = image.filter(ImageFilter.GaussianBlur(opts["blur"]))
    result = Image.new("RGB", (width, height), (0, 0, 0))
    # At 50% the selected image is centered; 0-100% pans through free space.
    x = round((width - image.width) * opts["x"] / 100)
    y = round((height - image.height) * opts["y"] / 100)
    result.paste(image, (x, y))
    if opts["vignette"]:
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        mask = Image.new("L", (width, height), 0)
        draw = ImageDraw.Draw(mask)
        border = int(min(width, height) * (0.35 + opts["vignette"] / 500))
        for n in range(24):
            alpha = round(230 * opts["vignette"] / 100 * (n / 24) ** 1.5)
            margin = int(border * (24 - n) / 24)
            draw.rectangle((margin, margin, width - margin - 1, height - margin - 1), outline=alpha, width=max(1, border // 24))
        mask = mask.filter(ImageFilter.GaussianBlur(45))
        overlay.putalpha(mask)
        result = Image.alpha_composite(result.convert("RGBA"), overlay).convert("RGB")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    result.save(output, format="PNG")
    return output
