"""Public Python API for `eldia-worlds` (`endstone-eldia-worlds`).

Other Endstone plugins (such as `eldia-helpcommands`) can import `WorldsAPI` or
`get_worlds_api()` directly from `eldia_worlds` without writing custom bridge code:

```python
from eldia_worlds import get_worlds_api

api = get_worlds_api()
world_name = api.detect_world_name(dimension_name, x, z)
api.teleport_player(player, world_name="lobby", dimension="overworld", x=0.5, y=64.0, z=0.5)
```
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from .storage import VirtualWorld, WorldRegistry, normalize_world_name

if TYPE_CHECKING:
    from collections.abc import Callable


class WorldsAPI:
    """First-class API facade for querying, syncing, and teleporting across `eldia-worlds`."""

    _instance: ClassVar[WorldsAPI | None] = None

    def __init__(
        self,
        plugin: Any = None,
        *,
        registry: WorldRegistry | None = None,
        server_getter: Callable[[], Any] | None = None,
    ) -> None:
        self._plugin = plugin
        self._registry = registry
        self._server_getter = server_getter

    @classmethod
    def get_instance(cls) -> WorldsAPI:
        """Return the active `WorldsAPI` singleton (or a standalone fallback instance)."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def set_instance(cls, api: WorldsAPI | None) -> None:
        """Bind or clear the active `WorldsAPI` singleton (called by `WorldsPlugin`)."""
        cls._instance = api

    @property
    def plugin(self) -> Any:
        if self._plugin is not None:
            return self._plugin
        srv = self.server
        if srv is None:
            return None
        pm = getattr(srv, "plugin_manager", None)
        if pm is None:
            return None
        for name in ("eldia_worlds", "eldia-worlds"):
            try:
                p = pm.get_plugin(name)
                if p is not None:
                    return p
            except Exception as exc:  # noqa: BLE001
                _ = exc
        return None

    @property
    def server(self) -> Any:
        if self._server_getter is not None:
            srv = self._server_getter()
            if srv is not None:
                return srv
        if self._plugin is not None:
            return getattr(self._plugin, "server", None)
        return None

    @property
    def registry(self) -> WorldRegistry | None:
        if self._registry is not None:
            return self._registry
        p = self.plugin
        if p is not None:
            return getattr(p, "registry", None)
        return None

    # ── World Lookup & Coordinate Detection ───────────────────────────

    def get_world(self, name: str) -> VirtualWorld | None:
        """Look up a `VirtualWorld` by name (case-insensitive)."""
        reg = self.registry
        if reg is None:
            return None
        return reg.get(name)

    def list_worlds(self) -> list[VirtualWorld]:
        """Return all registered virtual and custom-dimension worlds."""
        reg = self.registry
        if reg is None:
            return []
        return reg.list_worlds()

    def detect_world_name(self, dimension_name: str, x: float, z: float) -> str:
        """Resolve the `eldia-worlds` world name at `(dimension_name, x, z)`."""
        reg = self.registry
        if reg is not None:
            dim_key = normalize_world_name(dimension_name)
            vw = reg.find_world_at(dim_key, x, z)
            if vw is not None:
                return vw.name
            vw_by_dim = reg.get(dim_key)
            if vw_by_dim is not None:
                return vw_by_dim.name
        return dimension_name or "Overworld"

    def set_world_spawn(
        self,
        world_name: str,
        x: float,
        y: float,
        z: float,
        yaw: float = 0.0,
        pitch: float = 0.0,
        *,
        dimension: str | None = None,
    ) -> VirtualWorld | None:
        """Update and persist the spawn point of a world in `WorldRegistry`."""
        reg = self.registry
        if reg is None:
            return None
        return reg.set_spawn(
            world_name,
            x=x,
            y=y,
            z=z,
            yaw=yaw,
            pitch=pitch,
            dimension=dimension,
        )

    # ── Dimension Resolution & Cross-World Teleportation ──────────────

    def resolve_dimension(self, dimension_or_world: str) -> Any:
        """Resolve an Endstone `Dimension` by dimension key or `eldia-worlds` world name."""
        target_key = dimension_or_world
        reg = self.registry
        if reg is not None:
            vw = reg.get(dimension_or_world)
            if vw is not None and vw.dimension:
                target_key = vw.dimension

        p = self.plugin
        if p is not None:
            resolver = getattr(p, "_resolve_dimension", None)
            if callable(resolver):
                try:
                    dim = resolver(target_key)
                    if dim is not None:
                        return dim
                except Exception as exc:  # noqa: BLE001
                    _ = exc

        srv = self.server
        level = getattr(srv, "level", None) if srv is not None else None
        if level is None:
            return None

        get_dim = getattr(level, "get_dimension", None)
        if callable(get_dim):
            for candidate in (target_key, target_key.lower(), target_key.upper()):
                try:
                    dim = get_dim(candidate)
                    if dim is not None:
                        return dim
                except Exception as exc:  # noqa: BLE001
                    _ = exc

        dims = getattr(level, "dimensions", None)
        if dims is not None:
            low = target_key.lower()
            for d in dims:
                d_name = str(getattr(d, "name", "")).lower()
                if d_name == low or d_name.endswith(f":{low}"):
                    return d
        return None

    def teleport_player(
        self,
        player: Any,
        *,
        world_name: str,
        dimension: str,
        x: float,
        y: float,
        z: float,
        yaw: float = 0.0,
        pitch: float = 0.0,
    ) -> bool:
        """Teleport an Endstone `Player` (or testable actor) across worlds/dimensions."""
        if hasattr(player, "teleport_to_coords") and callable(player.teleport_to_coords):
            return bool(
                player.teleport_to_coords(
                    dimension=dimension,
                    world_name=world_name,
                    x=x,
                    y=y,
                    z=z,
                    yaw=yaw,
                    pitch=pitch,
                ),
            )

        dim = (
            self.resolve_dimension(world_name)
            or self.resolve_dimension(dimension)
            or getattr(player, "dimension", None)
        )
        try:
            from endstone.level import Location

            # Endstone Location signature: Location(dimension, x, y, z, pitch, yaw)
            try:
                loc = Location(
                    dim,
                    float(x),
                    float(y),
                    float(z),
                    float(pitch),
                    float(yaw),
                )
            except TypeError:
                loc = Location(
                    float(x),
                    float(y),
                    float(z),
                    float(yaw),
                    float(pitch),
                    dim,
                )
            res = player.teleport(loc)
            return True if res is None else bool(res)
        except Exception as exc:  # noqa: BLE001
            _ = exc
            return False

    def is_block_matching_near(
        self,
        dimension: str,
        world_name: str,
        x: float,
        y: float,
        z: float,
        predicate: Callable[[str], bool],
    ) -> bool:
        """Check whether any block within a 3x3x3 neighborhood satisfies `predicate(block_type)`."""
        dim = self.resolve_dimension(dimension) or self.resolve_dimension(world_name)
        if dim is None:
            return self.server is None
        get_block_at = getattr(dim, "get_block_at", None)
        if not callable(get_block_at):
            return True

        bx = round(x - 0.5) if x != int(x) else int(x)
        by = int(y)
        bz = round(z - 0.5) if z != int(z) else int(z)
        for dy in (0, -1, 1):
            for dx in (0, -1, 1):
                for dz in (0, -1, 1):
                    try:
                        block = get_block_at(bx + dx, by + dy, bz + dz)
                        block_type = str(getattr(block, "type", ""))
                        if predicate(block_type):
                            return True
                    except Exception as exc:  # noqa: BLE001
                        _ = exc
        return False


def get_worlds_api(
    *,
    server_getter: Callable[[], Any] | None = None,
    registry: WorldRegistry | None = None,
) -> WorldsAPI:
    """Return the active `WorldsAPI` instance, binding `server_getter` or `registry` if needed."""
    api = WorldsAPI.get_instance()
    if server_getter is not None and api._server_getter is None:
        api._server_getter = server_getter
    if registry is not None and api._registry is None:
        api._registry = registry
    return api
