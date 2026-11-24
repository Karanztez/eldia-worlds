"""Persistent virtual world registry and coordinate isolation helpers."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

WorldType = Literal["void", "flat", "normal", "imported"]
DimensionKey = Literal["overworld", "nether", "the_end"]

VALID_WORLD_TYPES: tuple[str, ...] = ("void", "flat", "normal", "imported")
VALID_DIMENSIONS: tuple[str, ...] = ("overworld", "nether", "the_end")
VALID_GAMEMODES: tuple[str, ...] = ("survival", "creative", "adventure", "spectator")

DEFAULT_SPACING = 100_000
DEFAULT_BORDER_RADIUS = 2_000
_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")


def normalize_world_name(name: str) -> str:
    """Return a canonical lowercase world identifier."""
    return name.strip().lower()


def is_valid_world_name(name: str) -> bool:
    """Validate that a world name contains only safe identifier characters."""
    return bool(_NAME_PATTERN.match(name))


@dataclass
class VirtualWorld:
    """Represents a named virtual world mapped to a base dimension and coordinate zone."""

    name: str
    dimension: str = "overworld"
    world_type: str = "void"
    center_x: int = 0
    center_z: int = 0
    spawn_x: float = 0.5
    spawn_y: float = 64.0
    spawn_z: float = 0.5
    spawn_yaw: float = 0.0
    spawn_pitch: float = 0.0
    border_radius: int = DEFAULT_BORDER_RADIUS
    gamemode: str | None = None
    generated: bool = False
    builtin: bool = False
    custom_dimension: bool = False
    import_source: str | None = None
    import_version: int = 0

    def contains(self, dimension_name: str, x: float, z: float) -> bool:
        """Check whether (dimension_name, x, z) falls inside this world's border."""
        if dimension_name != self.dimension or self.border_radius <= 0:
            return False
        dx = abs(x - self.center_x)
        dz = abs(z - self.center_z)
        return dx <= self.border_radius and dz <= self.border_radius

    def chunk_overlaps(self, dimension_name: str, chunk_x: int, chunk_z: int) -> bool:
        """Check whether a 16x16 chunk overlaps this world's generation zone."""
        if dimension_name != self.dimension or self.builtin:
            return False
        radius = self.border_radius if self.border_radius > 0 else 512
        min_x = chunk_x * 16
        max_x = min_x + 15
        min_z = chunk_z * 16
        max_z = min_z + 15
        return (
            max_x >= self.center_x - radius
            and min_x <= self.center_x + radius
            and max_z >= self.center_z - radius
            and min_z <= self.center_z + radius
        )

    def clamp_coordinates(self, x: float, z: float, margin: float = 1.5) -> tuple[float, float]:
        """Clamp (x, z) back inside the world border with a small inward margin."""
        if self.border_radius <= 0:
            return x, z
        limit = max(1.0, float(self.border_radius) - margin)
        clamped_x = min(max(x, self.center_x - limit), self.center_x + limit)
        clamped_z = min(max(z, self.center_z - limit), self.center_z + limit)
        return clamped_x, clamped_z


class WorldRegistry:
    """Manages loading, saving, and allocating VirtualWorld entries."""

    def __init__(self, storage_path: Path, spacing: int = DEFAULT_SPACING) -> None:
        self.storage_path = storage_path
        self.spacing = spacing
        self.worlds: dict[str, VirtualWorld] = {}

    def load(self) -> None:
        """Load worlds from JSON storage, ensuring built-in defaults exist."""
        self.worlds.clear()
        if self.storage_path.exists():
            raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
            for item in raw.get("worlds", []):
                world = VirtualWorld(**item)
                self.worlds[normalize_world_name(world.name)] = world
        self._ensure_builtins()
        self.save()

    def save(self) -> None:
        """Persist all worlds to JSON storage."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"worlds": [asdict(w) for w in self.worlds.values()]}
        self.storage_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _ensure_builtins(self) -> None:
        defaults = [
            VirtualWorld(
                name="overworld",
                dimension="overworld",
                world_type="normal",
                center_x=0,
                center_z=0,
                spawn_x=0.5,
                spawn_y=70.0,
                spawn_z=0.5,
                border_radius=0,
                generated=True,
                builtin=True,
            ),
            VirtualWorld(
                name="nether",
                dimension="nether",
                world_type="normal",
                center_x=0,
                center_z=0,
                spawn_x=0.5,
                spawn_y=70.0,
                spawn_z=0.5,
                border_radius=0,
                generated=True,
                builtin=True,
            ),
            VirtualWorld(
                name="the_end",
                dimension="the_end",
                world_type="normal",
                center_x=0,
                center_z=0,
                spawn_x=0.5,
                spawn_y=64.0,
                spawn_z=0.5,
                border_radius=0,
                generated=True,
                builtin=True,
            ),
        ]
        for default in defaults:
            if default.name not in self.worlds:
                self.worlds[default.name] = default

    def get(self, name: str) -> VirtualWorld | None:
        """Get a world by name (case-insensitive)."""
        return self.worlds.get(normalize_world_name(name))

    def list_worlds(self) -> list[VirtualWorld]:
        """Return all worlds, built-ins first then custom worlds sorted by name."""
        builtins = [w for w in self.worlds.values() if w.builtin]
        custom = sorted((w for w in self.worlds.values() if not w.builtin), key=lambda w: w.name)
        return builtins + custom

    def _next_center(self, dimension: str) -> tuple[int, int]:
        """Allocate the next non-overlapping (center_x, center_z) slot in a dimension."""
        used_slots = {
            (w.center_x, w.center_z)
            for w in self.worlds.values()
            if w.dimension == dimension
        }
        ring = 1
        while True:
            candidates = [
                (ring * self.spacing, 0),
                (-ring * self.spacing, 0),
                (0, ring * self.spacing),
                (0, -ring * self.spacing),
                (ring * self.spacing, ring * self.spacing),
                (-ring * self.spacing, -ring * self.spacing),
            ]
            for slot in candidates:
                if slot not in used_slots:
                    return slot
            ring += 1

    def create(
        self,
        name: str,
        world_type: str = "void",
        dimension: str = "overworld",
        border_radius: int = DEFAULT_BORDER_RADIUS,
        gamemode: str | None = None,
        use_custom_dimension: bool = False,
        import_source: str | None = None,
    ) -> VirtualWorld:
        """Create and persist a new virtual or native custom-dimension world."""
        key = normalize_world_name(name)
        if not is_valid_world_name(key):
            raise ValueError("World name must be 1-32 characters of letters, numbers, '_' or '-'.")
        if key in self.worlds:
            raise ValueError(f"World '{key}' already exists.")
        if world_type not in VALID_WORLD_TYPES:
            raise ValueError(f"Invalid world type '{world_type}'. Expected: {', '.join(VALID_WORLD_TYPES)}.")
        if not use_custom_dimension and dimension not in VALID_DIMENSIONS:
            raise ValueError(f"Invalid dimension '{dimension}'. Expected: {', '.join(VALID_DIMENSIONS)}.")
        if border_radius < 0:
            raise ValueError("Border radius must be 0 (unlimited) or a positive integer.")
        if gamemode is not None and gamemode not in VALID_GAMEMODES:
            raise ValueError(f"Invalid gamemode '{gamemode}'. Expected: {', '.join(VALID_GAMEMODES)}.")

        if use_custom_dimension:
            target_dim = key
            center_x, center_z = 0, 0
            spawn_y = 64.0
        else:
            target_dim = dimension
            center_x, center_z = self._next_center(dimension)
            if world_type == "void":
                spawn_y = 200.0
            elif world_type == "flat":
                spawn_y = 64.0
            else:
                spawn_y = 100.0

        world = VirtualWorld(
            name=key,
            dimension=target_dim,
            world_type=world_type,
            center_x=center_x,
            center_z=center_z,
            spawn_x=float(center_x) + 0.5,
            spawn_y=spawn_y,
            spawn_z=float(center_z) + 0.5,
            border_radius=border_radius,
            gamemode=gamemode,
            generated=(world_type in ("normal", "imported") and not use_custom_dimension),
            builtin=False,
            custom_dimension=use_custom_dimension,
            import_source=import_source,
        )
        self.worlds[key] = world
        self.save()
        return world

    def delete(self, name: str) -> VirtualWorld:
        """Delete a custom world from the registry."""
        key = normalize_world_name(name)
        world = self.worlds.get(key)
        if world is None:
            raise ValueError(f"World '{key}' does not exist.")
        if world.builtin:
            raise ValueError(f"Built-in dimension '{key}' cannot be deleted.")
        del self.worlds[key]
        self.save()
        return world

    def find_world_at(self, dimension_name: str, x: float, z: float) -> VirtualWorld | None:
        """Find the custom virtual world containing the given coordinates, if any."""
        for world in self.worlds.values():
            if not world.builtin and world.contains(dimension_name, x, z):
                return world
        return None

    def set_spawn(
        self,
        name: str,
        x: float,
        y: float,
        z: float,
        yaw: float = 0.0,
        pitch: float = 0.0,
        *,
        dimension: str | None = None,
    ) -> VirtualWorld | None:
        """Update and persist the spawn location for an existing world."""
        world = self.get(name)
        if world is None:
            return None
        if dimension:
            world.dimension = normalize_world_name(dimension)
        world.spawn_x = float(x)
        world.spawn_y = float(y)
        world.spawn_z = float(z)
        world.spawn_yaw = float(yaw)
        world.spawn_pitch = float(pitch)
        self.save()
        return world

    def find_world_for_chunk(
        self, dimension_name: str, chunk_x: int, chunk_z: int
    ) -> VirtualWorld | None:
        """Find a custom void/flat/imported world whose zone overlaps the given chunk."""
        for world in self.worlds.values():
            if (
                not world.builtin
                and not world.custom_dimension
                and world.world_type in ("void", "flat", "imported")
                and world.chunk_overlaps(dimension_name, chunk_x, chunk_z)
            ):
                return world
        return None


class GeneratedChunkStore:
    """Tracks which chunks have already had void/flat generation applied."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.chunks: dict[str, set[str]] = {}
        self._dirty = False

    def load(self) -> None:
        self.chunks.clear()
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for world_name, keys in raw.items():
                self.chunks[world_name] = set(keys)
        self._dirty = False

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {name: sorted(keys) for name, keys in self.chunks.items()}
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        self._dirty = False

    @staticmethod
    def _key(chunk_x: int, chunk_z: int) -> str:
        return f"{chunk_x},{chunk_z}"

    def is_generated(self, world_name: str, chunk_x: int, chunk_z: int) -> bool:
        return self._key(chunk_x, chunk_z) in self.chunks.get(world_name, set())

    def mark_generated(self, world_name: str, chunk_x: int, chunk_z: int) -> None:
        bucket = self.chunks.setdefault(world_name, set())
        key = self._key(chunk_x, chunk_z)
        if key not in bucket:
            bucket.add(key)
            self._dirty = True

    def remove_world(self, world_name: str) -> None:
        if world_name in self.chunks:
            del self.chunks[world_name]
            self._dirty = True
