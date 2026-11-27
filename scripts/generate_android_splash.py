"""Encode the existing wordmark as an Android vector without redrawing it.

Pillow is an optional development dependency. Unioned pixel contours preserve
the source colors and proportions without seams between adjacent raster rows.
The centered mark fits Android 12's icon safe circle.
Run from the repository root: python scripts/generate_android_splash.py
"""
from collections import defaultdict
from pathlib import Path
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]


def contours(pixels):
    """Trace exposed pixel edges, canceling shared edges within each color.

    Boundaries keep their filled area on the right. At diagonal joins, taking
    the right turn first keeps touching components separate. evenOdd filling
    then preserves the holes in the existing letters without font tracing.
    """
    edges = defaultdict(set)
    for x, y in sorted(pixels):
        if (x, y - 1) not in pixels:
            edges[(x, y)].add((x + 1, y))
        if (x + 1, y) not in pixels:
            edges[(x + 1, y)].add((x + 1, y + 1))
        if (x, y + 1) not in pixels:
            edges[(x + 1, y + 1)].add((x, y + 1))
        if (x - 1, y) not in pixels:
            edges[(x, y + 1)].add((x, y))

    directions = {(1, 0): 0, (0, 1): 1, (-1, 0): 2, (0, -1): 3}
    turn_order = {1: 0, 0: 1, 3: 2, 2: 3}
    while edges:
        start = current = min(edges)
        points = []
        incoming = None
        while True:
            points.append(current)
            candidates = edges[current]
            if incoming is None:
                following = min(candidates)
            else:
                following = min(candidates, key=lambda point: turn_order[
                    (directions[(point[0] - current[0], point[1] - current[1])] - incoming) % 4
                ])
            candidates.remove(following)
            if not candidates:
                del edges[current]
            incoming = directions[(following[0] - current[0], following[1] - current[1])]
            current = following
            if current == start:
                break
        # Retain only corners, rather than a vertex for every boundary pixel.
        yield [point for index, point in enumerate(points)
               if (point[0] - points[index - 1][0], point[1] - points[index - 1][1])
               != (points[(index + 1) % len(points)][0] - point[0],
                   points[(index + 1) % len(points)][1] - point[1])]


def contour_path(points, left, top):
    x, y = points[0]
    commands = [f"M{left+x:g},{top+y:g}"]
    for next_x, next_y in points[1:]:
        # Compact relative segments also stay below Android's resource-string limit.
        commands.append(f"h{next_x-x:g}" if next_y == y else f"v{next_y-y:g}")
        x, y = next_x, next_y
    commands.append("z")
    return "".join(commands)


def main():
    source = Image.open(ROOT / "static/email/ennoic-logo.png").convert("RGB")
    bbox = ImageOps.grayscale(source).point(lambda value: 255 if value < 245 else 0).getbbox()
    if bbox is None:
        raise ValueError("The existing wordmark has no visible ink")
    source = source.crop(bbox)
    # At 288 dp, 320/512 of the viewport is 180 dp: inside the 192 dp safe circle.
    # Use source pixel coordinates unchanged so typography is never approximated.
    viewport = max(512, source.width * 1.6)
    left, top = (viewport - source.width) / 2, (viewport - source.height) / 2
    pixels_by_color = defaultdict(set)
    for y in range(source.height):
        for x in range(source.width):
            color = source.getpixel((x, y))
            if color != (255, 255, 255):
                pixels_by_color[color].add((x, y))
    lines = [
        '<!-- Generated from static/email/ennoic-logo.png; do not redraw the brand. -->',
        '<vector xmlns:android="http://schemas.android.com/apk/res/android"',
        f'    android:width="288dp" android:height="288dp" android:viewportWidth="{viewport:g}" android:viewportHeight="{viewport:g}">',
    ]
    # The source palette is ordered from black to near-white. Paint cumulative
    # lighter silhouettes first, then darker pixels over them. Exact source
    # colors remain unchanged; overlapping boundaries avoid white antialias
    # cracks between neighboring gray contours when Android scales the vector.
    color_paths = []
    covered_pixels = set()
    for color, pixels in sorted(pixels_by_color.items()):
        covered_pixels.update(pixels)
        fill = "#" + "".join(f"{channel:02x}" for channel in color)
        path = "".join(contour_path(points, left, top) for points in contours(covered_pixels))
        if len(path) > 30000:
            raise ValueError("Splash contour exceeds Android's resource-string limit")
        color_paths.append((fill, path))
    for fill, path in reversed(color_paths):
        lines.append(f'    <path android:fillColor="{fill}" android:fillType="evenOdd" android:pathData="{path}"/>')
    lines.append('</vector>')
    destination = ROOT / "android/resources/drawable/ennoic_splash_icon.xml"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(destination)


if __name__ == "__main__":
    main()
