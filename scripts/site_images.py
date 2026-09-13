"""Procedural overhead site imagery for landing candidates.

These are project-generated illustrations, not photographs — no scraped or
proprietary imagery is used anywhere in SafeDrop. They are drawn to be
unambiguous from above: vehicles as oriented rectangles, people as head-plus-
shoulders figures, livestock as elongated bodies with visible legs.

Each render returns the ground-truth counts alongside the file, so the vision
component can be scored rather than trusted.
"""

from __future__ import annotations

import random
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Ellipse, Polygon, Rectangle

FIGSIZE = (6.4, 6.4)
DPI = 100

ASPHALT = "#4a4d52"
CONCRETE = "#8d8b85"
GRASS = "#5f8a43"
DRY_FIELD = "#a89a63"


def _canvas(color: str):
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.add_patch(Rectangle((0, 0), 100, 100, color=color, zorder=0))
    return fig, ax


def _save(fig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
    fig.savefig(path, dpi=DPI, pad_inches=0)
    plt.close(fig)


def _car(ax, x: float, y: float, angle: float, color: str, length: float = 9.0, width: float = 4.4):
    """A vehicle seen from above: body, lighter roof panel, dark windscreen band."""
    ax.add_patch(
        Rectangle(
            (x - length / 2, y - width / 2), length, width,
            angle=angle, rotation_point="center",
            facecolor=color, edgecolor="#1a1a1a", linewidth=1.1, zorder=4,
        )
    )
    ax.add_patch(
        Rectangle(
            (x - length * 0.22, y - width * 0.34), length * 0.44, width * 0.68,
            angle=angle, rotation_point=(x, y),
            facecolor="#20242b", edgecolor="none", alpha=0.75, zorder=5,
        )
    )


def _person(ax, x: float, y: float, shirt: str = "#e8443a", angle: float = 0.0):
    """A person seen from above: cast shadow, torso, outstretched arms, head."""
    ax.add_patch(Ellipse((x + 1.6, y - 1.6), 8.0, 5.4, angle=angle, facecolor="#1c2a14", alpha=0.35, zorder=5))
    for side in (-1, 1):
        ax.add_patch(
            Ellipse((x, y + side * 2.6), 5.4, 1.8, angle=angle, facecolor=shirt,
                    edgecolor="#1a1a1a", linewidth=0.6, zorder=6)
        )
    ax.add_patch(Ellipse((x, y), 6.4, 4.4, angle=angle, facecolor=shirt,
                         edgecolor="#141414", linewidth=0.9, zorder=7))
    ax.add_patch(Circle((x, y), 2.1, facecolor="#c68642", edgecolor="#141414", linewidth=0.9, zorder=8))
    ax.add_patch(Circle((x, y), 1.1, facecolor="#3b2a1c", edgecolor="none", zorder=9))


def _cow(ax, x: float, y: float, angle: float = 0.0):
    """Livestock seen from above: elongated body, head, and four legs."""
    ax.add_patch(Ellipse((x + 1.5, y - 1.5), 13.0, 7.0, angle=angle, facecolor="#4a4127", alpha=0.35, zorder=4))
    for dx, dy in ((-3.4, -2.9), (-3.4, 2.9), (3.0, -2.9), (3.0, 2.9)):
        ax.add_patch(Ellipse((x + dx, y + dy), 2.0, 1.4, angle=angle, facecolor="#241d18", edgecolor="none", zorder=5))
    ax.add_patch(
        Ellipse((x, y), 12.0, 5.8, angle=angle, facecolor="#3b3129", edgecolor="#17130f", linewidth=1.0, zorder=6)
    )
    ax.add_patch(Ellipse((x, y), 6.0, 4.4, angle=angle, facecolor="#efe6d8", edgecolor="none", alpha=0.85, zorder=7))
    ax.add_patch(Ellipse((x + 6.6, y), 4.0, 3.2, angle=angle, facecolor="#2a231d", edgecolor="#17130f", linewidth=0.8, zorder=8))
    ax.plot([x - 6.0, x - 9.5], [y, y + 1.6], color="#241d18", linewidth=1.4, zorder=8)


def _spread(n: int, seed: int, lo: float, hi: float, min_gap: float) -> list[tuple[float, float]]:
    """Rejection-sample n points that stay visually separated."""
    rng = random.Random(seed)
    points: list[tuple[float, float]] = []
    for _ in range(n):
        for _attempt in range(200):
            p = (rng.uniform(lo, hi), rng.uniform(lo, hi))
            if all((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 >= min_gap**2 for q in points):
                points.append(p)
                break
        else:
            points.append((rng.uniform(lo, hi), rng.uniform(lo, hi)))
    return points


def _dog(ax, x: float, y: float, angle: float = 0.0, coat: str = "#6b4f2a"):
    """A dog seen from above: body, head, tail. Smaller than livestock."""
    ax.add_patch(Ellipse((x + 0.8, y - 0.8), 7.0, 4.0, angle=angle, facecolor="#1c2a14", alpha=0.3, zorder=4))
    ax.add_patch(Ellipse((x, y), 6.0, 3.0, angle=angle, facecolor=coat,
                         edgecolor="#2b1d0e", linewidth=0.8, zorder=6))
    ax.add_patch(Ellipse((x + 3.4, y), 2.6, 2.4, angle=angle, facecolor=coat,
                         edgecolor="#2b1d0e", linewidth=0.7, zorder=7))
    for dx, dy in ((-1.6, -1.5), (-1.6, 1.5), (1.4, -1.5), (1.4, 1.5)):
        ax.add_patch(Ellipse((x + dx, y + dy), 1.5, 1.0, angle=angle, facecolor="#4a361c",
                             edgecolor="none", zorder=5))
    ax.plot([x - 3.0, x - 5.2], [y, y + 1.4], color="#4a361c", linewidth=1.6, zorder=6)


def _speckle(ax, color: str, n: int, seed: int, size: float = 1.4, alpha: float = 0.25):
    rng = random.Random(seed)
    for _ in range(n):
        ax.add_patch(
            Circle(
                (rng.uniform(0, 100), rng.uniform(0, 100)),
                rng.uniform(0.4, size), facecolor=color, alpha=alpha, zorder=1,
            )
        )


# --------------------------------------------------------------------------
# Scenes
# --------------------------------------------------------------------------


def parking_lot(path: Path, vehicles: int, seed: int = 3) -> dict:
    fig, ax = _canvas(ASPHALT)
    _speckle(ax, "#5e6167", 90, seed)
    for x in range(12, 96, 12):  # bay stripes
        for y0 in (10, 56):
            ax.add_patch(Rectangle((x, y0), 0.9, 32, color="#e6e6e0", alpha=0.85, zorder=2))
    ax.add_patch(Rectangle((6, 47), 88, 6, color="#3f4247", zorder=2))  # drive aisle

    rng = random.Random(seed)
    palette = ["#c9d3dc", "#8f2f2a", "#28405e", "#d8d2c4", "#2f2f33", "#9aa3ad"]
    slots = [(x + 6, y) for x in range(12, 96, 12) for y in (22, 40, 68, 86)]
    rng.shuffle(slots)
    for i in range(min(vehicles, len(slots))):
        x, y = slots[i]
        _car(ax, x, y, 90, palette[i % len(palette)])
    _save(fig, path)
    return {"people_count": 0, "vehicle_count": vehicles, "animal_count": 0,
            "large_obstacle_count": 0, "construction_equipment_present": False,
            "standing_water_visible": False,
            "visible_clear_area_percent": max(5.0, 96.0 - vehicles * 7.5)}


def sports_field(path: Path, people: int, seed: int = 11, goals: bool = True) -> dict:
    fig, ax = _canvas(GRASS)
    for i in range(0, 100, 7):  # mown stripes
        if (i // 7) % 2 == 0:
            ax.add_patch(Rectangle((0, i), 100, 7, color="#547a3b", alpha=0.55, zorder=1))
    ax.add_patch(Rectangle((10, 12), 80, 76, fill=False, edgecolor="#f2f2ec", linewidth=2.2, zorder=3))
    ax.plot([10, 90], [50, 50], color="#f2f2ec", linewidth=2.2, zorder=3)
    ax.add_patch(Circle((50, 50), 11, fill=False, edgecolor="#f2f2ec", linewidth=2.2, zorder=3))
    if goals:
        for y in (12, 88):
            ax.add_patch(Rectangle((38, y - 1.2), 24, 2.4, facecolor="#e8e8e2", edgecolor="#9a9a94", zorder=3))

    rng = random.Random(seed)
    shirts = ["#e8443a", "#2f6fd0", "#f2c14e", "#ffffff"]
    for i, (x, y) in enumerate(_spread(people, seed, lo=20, hi=80, min_gap=16.0)):
        _person(ax, x, y, shirts[i % len(shirts)], angle=rng.uniform(-60, 60))
    if people:
        ax.add_patch(Circle((rng.uniform(35, 65), rng.uniform(35, 65)), 1.5,
                            facecolor="#fafafa", edgecolor="#1a1a1a", linewidth=0.8, zorder=8))
    _save(fig, path)
    return {"people_count": people, "vehicle_count": 0, "animal_count": 0,
            "large_obstacle_count": 2 if goals else 0,
            "construction_equipment_present": False, "standing_water_visible": False,
            "visible_clear_area_percent": max(10.0, 94.0 - people * 5.0)}


def dog_park(path: Path, people: int, dogs: int, seed: int = 37) -> dict:
    """A fenced off-leash area: mown grass, a perimeter fence, people and dogs.

    Included because it is the case a map cannot warn you about — the polygon
    says "park", the geometry says "large and flat", and the ground says
    "twelve loose animals".
    """
    fig, ax = _canvas("#5f8a43")
    for i in range(0, 100, 9):
        if (i // 9) % 2 == 0:
            ax.add_patch(Rectangle((0, i), 100, 9, color="#557c3c", alpha=0.5, zorder=1))
    for x, y, w, h in ((2, 2, 96, 1.4), (2, 96.6, 96, 1.4), (2, 2, 1.4, 96), (96.6, 2, 1.4, 96)):
        ax.add_patch(Rectangle((x, y), w, h, color="#4a4034", zorder=3))  # perimeter fence
    ax.add_patch(Circle((22, 74), 6.5, facecolor="#2f5426", edgecolor="#22401d", linewidth=0.8, zorder=3))
    ax.add_patch(Circle((78, 26), 5.5, facecolor="#2f5426", edgecolor="#22401d", linewidth=0.8, zorder=3))

    rng = random.Random(seed)
    shirts = ["#e8443a", "#2f6fd0", "#f2c14e", "#ffffff", "#8e44ad"]
    for i, (x, y) in enumerate(_spread(people, seed, lo=14, hi=86, min_gap=17.0)):
        _person(ax, x, y, shirts[i % len(shirts)], angle=rng.uniform(-60, 60))
    coats = ["#6b4f2a", "#2e2a26", "#c8a06a", "#8a8378"]
    for i, (x, y) in enumerate(_spread(dogs, seed + 5, lo=12, hi=88, min_gap=13.0)):
        _dog(ax, x, y, rng.uniform(-50, 50), coats[i % len(coats)])
    _save(fig, path)
    return {"people_count": people, "vehicle_count": 0, "animal_count": dogs,
            "large_obstacle_count": 2, "construction_equipment_present": False,
            "standing_water_visible": False,
            "visible_clear_area_percent": max(12.0, 82.0 - people * 3.0 - dogs * 2.5)}


def open_field(path: Path, seed: int = 5) -> dict:
    fig, ax = _canvas("#6d8f4c")
    for i in range(0, 100, 9):
        ax.add_patch(Rectangle((0, i), 100, 9, color="#628345", alpha=0.5, zorder=1))
    _speckle(ax, "#7fa05c", 140, seed, size=2.2, alpha=0.35)
    ax.add_patch(Rectangle((0, 0), 100, 3.5, color="#8a7f60", zorder=2))  # boundary track
    _save(fig, path)
    return {"people_count": 0, "vehicle_count": 0, "animal_count": 0, "large_obstacle_count": 0,
            "construction_equipment_present": False, "standing_water_visible": False,
            "visible_clear_area_percent": 95.0}


def pasture_with_livestock(path: Path, animals: int, seed: int = 17) -> dict:
    fig, ax = _canvas(DRY_FIELD)
    _speckle(ax, "#8f8350", 160, seed, size=2.4, alpha=0.4)
    for x in (4, 96):  # fence lines
        ax.plot([x, x], [0, 100], color="#5c4b34", linewidth=2.0, zorder=2)
    for x, y in _spread(animals, seed, lo=18, hi=82, min_gap=22.0):
        _cow(ax, x, y, random.Random(int(x * 100)).uniform(-40, 40))
    _save(fig, path)
    return {"people_count": 0, "vehicle_count": 0, "animal_count": animals,
            "large_obstacle_count": 0, "construction_equipment_present": False,
            "standing_water_visible": False,
            "visible_clear_area_percent": max(20.0, 92.0 - animals * 6.0)}


def industrial_yard(path: Path, seed: int = 23, clear: bool = True) -> dict:
    fig, ax = _canvas(CONCRETE)
    _speckle(ax, "#7d7b76", 70, seed)
    for y in (18, 82):  # expansion joints
        ax.plot([0, 100], [y, y], color="#6f6d68", linewidth=1.6, zorder=2)
    ax.add_patch(Rectangle((0, 0), 100, 5, color="#5f5d59", zorder=2))
    if clear:
        _save(fig, path)
        return {"people_count": 0, "vehicle_count": 0, "animal_count": 0, "large_obstacle_count": 0,
                "construction_equipment_present": False, "standing_water_visible": False,
                "visible_clear_area_percent": 93.0}
    ax.add_patch(Rectangle((60, 60), 22, 14, facecolor="#c9a227", edgecolor="#1a1a1a", linewidth=1.2, zorder=5))
    ax.add_patch(Polygon([[60, 74], [52, 84], [56, 86], [64, 76]], facecolor="#c9a227",
                         edgecolor="#1a1a1a", linewidth=1.2, zorder=5))  # excavator arm
    ax.add_patch(Rectangle((18, 20), 20, 12, facecolor="#8a5a2b", edgecolor="#1a1a1a", linewidth=1.0, zorder=5))
    _save(fig, path)
    return {"people_count": 0, "vehicle_count": 1, "animal_count": 0, "large_obstacle_count": 2,
            "construction_equipment_present": True, "standing_water_visible": False,
            "visible_clear_area_percent": 61.0}


def urban_park(path: Path, people: int, seed: int = 31) -> dict:
    fig, ax = _canvas("#5c8641")
    for i in range(0, 100, 11):
        ax.add_patch(Rectangle((0, i), 100, 11, color="#527a3a", alpha=0.45, zorder=1))
    ax.add_patch(Polygon([[0, 44], [30, 50], [70, 42], [100, 48], [100, 54], [70, 48], [30, 56], [0, 50]],
                         facecolor="#b9a98a", edgecolor="none", zorder=2))  # footpath
    rng = random.Random(seed)
    for _ in range(5):  # tree canopies
        x, y = rng.uniform(6, 94), rng.uniform(6, 94)
        ax.add_patch(Circle((x, y), rng.uniform(4.5, 7.0), facecolor="#2f5426", edgecolor="#22401d",
                            linewidth=0.8, alpha=0.95, zorder=3))
    shirts = ["#e8443a", "#2f6fd0", "#f2c14e", "#ffffff", "#8e44ad"]
    for i, (x, y) in enumerate(_spread(people, seed, lo=10, hi=90, min_gap=15.0)):
        _person(ax, x, y, shirts[i % len(shirts)], angle=rng.uniform(-60, 60))
    _save(fig, path)
    return {"people_count": people, "vehicle_count": 0, "animal_count": 0, "large_obstacle_count": 5,
            "construction_equipment_present": False, "standing_water_visible": False,
            "visible_clear_area_percent": max(15.0, 78.0 - people * 4.0)}
