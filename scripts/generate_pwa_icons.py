"""Pad/resize Ennoic's existing wordmark without redrawing it (requires Pillow).

Run: python scripts/generate_pwa_icons.py
The original black-on-white wordmark is retained on a white square. Maskable
variants keep the whole source image within the central 80%-diameter safe circle.
"""

from pathlib import Path

from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parents[1]


def main():
    source = Image.open(ROOT / "static/email/ennoic-logo.png").convert("RGB")
    # Remove the source's empty white margins, retaining the original ink/shadow.
    ink = ImageOps.grayscale(source).point(lambda value: 255 if value < 245 else 0).getbbox()
    if ink is None:
        raise ValueError("The existing Ennoic wordmark image contains no visible mark.")
    left, top, right, bottom = ink
    margin = 6
    source = source.crop((max(0, left - margin), max(0, top - margin),
                          min(source.width, right + margin), min(source.height, bottom + margin)))
    output = ROOT / "static/pwa/icons"
    output.mkdir(parents=True, exist_ok=True)
    for filename, size, fraction in (
        ("icon-192.png", 192, 0.88),
        ("icon-512.png", 512, 0.88),
        ("maskable-192.png", 192, 0.70),
        ("maskable-512.png", 512, 0.70),
        ("apple-touch-icon.png", 180, 0.88),
    ):
        image = Image.new("RGB", (size, size), "white")
        width = round(size * fraction)
        wordmark = source.resize((width, round(width * source.height / source.width)), Image.Resampling.LANCZOS)
        image.paste(wordmark, ((size - wordmark.width) // 2, (size - wordmark.height) // 2))
        image.save(output / filename, optimize=True)


if __name__ == "__main__":
    main()
