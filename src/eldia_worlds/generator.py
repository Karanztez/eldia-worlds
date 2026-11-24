"""Spawn platform and terrain helpers for virtual worlds."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endstone import Server
    from endstone.level import Dimension

    from .importer import BedrockWorldImporter

from .storage import VirtualWorld

_DIMENSION_HEIGHT_RANGES: dict[str, tuple[int, int]] = {
    "overworld": (-64, 319),
    "nether": (0, 127),
    "the_end": (0, 255),
}


def _fill(server: Server, dimension_name: str, x1: int, y1: int, z1: int, x2: int, y2: int, z2: int, block: str) -> None:
    """Execute a native BDS /fill command in the target dimension."""
    cmd = f"execute in {dimension_name} run fill {x1} {y1} {z1} {x2} {y2} {z2} {block}"
    server.dispatch_command(server.command_sender, cmd)


def apply_chunk_template(
    server: Server,
    dimension: Dimension,
    world: VirtualWorld,
    chunk_x: int,
    chunk_z: int,
) -> None:
    """Clear or flatten an entire 16x16 chunk according to the world's type."""
    x1 = chunk_x * 16
    z1 = chunk_z * 16
    x2 = x1 + 15
    z2 = z1 + 15
    min_y, max_y = _DIMENSION_HEIGHT_RANGES.get(world.dimension, (-64, 319))

    if world.world_type == "void":
        # Clear in 128-tall slices (16 * 16 * 128 = 32,768 blocks, the /fill max).
        y = min_y
        while y <= max_y:
            top = min(y + 127, max_y)
            _fill(server, world.dimension, x1, y, z1, x2, top, z2, "air")
            y = top + 1
    elif world.world_type == "flat":
        if min_y < 60:
            _fill(server, world.dimension, x1, min_y, z1, x2, 59, z2, "air")
        _fill(server, world.dimension, x1, 60, z1, x2, 60, z2, "bedrock")
        _fill(server, world.dimension, x1, 61, z1, x2, 62, z2, "dirt")
        _fill(server, world.dimension, x1, 63, z1, x2, 63, z2, "grass_block")
        y = 64
        while y <= max_y:
            top = min(y + 127, max_y)
            _fill(server, world.dimension, x1, y, z1, x2, top, z2, "air")
            y = top + 1

    spawn_cx = int(world.spawn_x // 16)
    spawn_cz = int(world.spawn_z // 16)
    if chunk_x == spawn_cx and chunk_z == spawn_cz:
        build_spawn_area(dimension, world)


def _set_block(dimension: Dimension, x: int, y: int, z: int, block_type: str) -> None:
    """Safely set a block type at integer coordinates."""
    block = dimension.get_block_at(x, y, z)
    block.set_type(block_type, apply_physics=False)


def build_spawn_area(dimension: Dimension, world: VirtualWorld) -> None:
    """Build the initial spawn platform for void or flat virtual worlds."""
    cx = int(world.spawn_x // 1)
    cy = int(world.spawn_y // 1)
    cz = int(world.spawn_z // 1)

    if world.world_type == "void":
        # Clear a 9x5x9 air volume around spawn and place a 7x7 stone platform below.
        for dx in range(-4, 5):
            for dz in range(-4, 5):
                for dy in range(5):
                    _set_block(dimension, cx + dx, cy + dy, cz + dz, "minecraft:air")
        for dx in range(-3, 4):
            for dz in range(-3, 4):
                is_edge = abs(dx) == 3 or abs(dz) == 3
                if dx == 0 and dz == 0:
                    mat = "minecraft:glowstone"
                elif is_edge:
                    mat = "minecraft:stone_bricks"
                else:
                    mat = "minecraft:smooth_stone"
                _set_block(dimension, cx + dx, cy - 1, cz + dz, mat)
    elif world.world_type == "flat":
        # Clear air above and create a 17x17 flat grass platform.
        for dx in range(-8, 9):
            for dz in range(-8, 9):
                for dy in range(5):
                    _set_block(dimension, cx + dx, cy + dy, cz + dz, "minecraft:air")
                _set_block(dimension, cx + dx, cy - 1, cz + dz, "minecraft:grass_block")
                _set_block(dimension, cx + dx, cy - 2, cz + dz, "minecraft:dirt")
                _set_block(dimension, cx + dx, cy - 3, cz + dz, "minecraft:dirt")
                _set_block(dimension, cx + dx, cy - 4, cz + dz, "minecraft:bedrock")


def resolve_safe_spawn_y(dimension: Dimension, world: VirtualWorld) -> float:
    """Find a safe Y coordinate for normal worlds once chunks around spawn are loaded."""
    if world.world_type != "normal" or world.builtin:
        return world.spawn_y
    cx = int(world.spawn_x // 1)
    cz = int(world.spawn_z // 1)
    top_y = dimension.get_highest_block_y_at(cx, cz)
    if top_y > -64:
        return float(top_y + 1)
    return world.spawn_y


def apply_imported_chunk(
    server: Server,
    dimension: Dimension,
    world: VirtualWorld,
    chunk_x: int,
    chunk_z: int,
    importer: BedrockWorldImporter,
) -> None:
    """Clear a chunk in `overworld` and paste blocks from an imported Bedrock world."""
    x1 = chunk_x * 16
    z1 = chunk_z * 16
    x2 = x1 + 15
    z2 = z1 + 15
    min_y, max_y = _DIMENSION_HEIGHT_RANGES.get(world.dimension, (-64, 319))

    src_cx = chunk_x - (world.center_x // 16)
    src_cz = chunk_z - (world.center_z // 16)
    blocks = importer.read_chunk_blocks(src_cx, src_cz)
    if not blocks:
        return

    y = min_y
    while y <= max_y:
        top = min(y + 127, max_y)
        _fill(server, world.dimension, x1, y, z1, x2, top, z2, "air")
        y = top + 1

    data_cache: dict[tuple[str, tuple[tuple[str, object], ...]], object | None] = {}
    create_bd = getattr(server, "create_block_data", None)

    for lx, by, lz, bname, states in blocks:
        if by < min_y or by > max_y:
            continue
        blk = dimension.get_block_at(x1 + lx, by, z1 + lz)
        if states and callable(create_bd):
            cache_key = (bname, tuple(sorted(states.items())))
            if cache_key not in data_cache:
                try:
                    data_cache[cache_key] = create_bd(bname, states)
                except Exception as exc:  # noqa: BLE001
                    _ = exc
                    data_cache[cache_key] = None
            bd = data_cache[cache_key]
            if bd is not None:
                try:
                    blk.set_data(bd, apply_physics=False)
                    continue
                except Exception as exc:  # noqa: BLE001
                    _ = exc
        try:
            blk.set_type(bname, apply_physics=False)
        except Exception as exc:  # noqa: BLE001
            _ = exc
