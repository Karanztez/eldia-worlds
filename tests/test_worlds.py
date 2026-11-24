"""Unit tests for eldia_worlds storage and coordinate isolation."""

from __future__ import annotations

from pathlib import Path

import pytest

from eldia_worlds import WorldsAPI, get_worlds_api
from eldia_worlds.importer import decode_subchunk_blocks
from eldia_worlds.storage import DEFAULT_SPACING, GeneratedChunkStore, WorldRegistry


def test_registry_initializes_builtins_and_persists(tmp_path: Path) -> None:
    storage_file = tmp_path / "worlds.json"
    registry = WorldRegistry(storage_file)
    registry.load()

    assert storage_file.exists()
    assert registry.get("overworld") is not None
    assert registry.get("nether") is not None
    assert registry.get("the_end") is not None
    assert registry.get("OVERWORLD") is not None


def test_create_allocates_isolated_coordinates_and_reloads(tmp_path: Path) -> None:
    storage_file = tmp_path / "worlds.json"
    registry = WorldRegistry(storage_file)
    registry.load()

    sky = registry.create("Sky_Arena", world_type="void", dimension="overworld", border_radius=500)
    flat = registry.create("Flat_Build", world_type="flat", dimension="overworld", border_radius=1000)

    assert sky.name == "sky_arena"
    assert (sky.center_x, sky.center_z) == (DEFAULT_SPACING, 0)
    assert (flat.center_x, flat.center_z) != (sky.center_x, sky.center_z)
    assert abs(flat.center_x - sky.center_x) >= DEFAULT_SPACING or abs(flat.center_z - sky.center_z) >= DEFAULT_SPACING

    reloaded = WorldRegistry(storage_file)
    reloaded.load()
    assert reloaded.get("sky_arena") is not None
    assert reloaded.get("flat_build") is not None


def test_border_contains_and_clamp(tmp_path: Path) -> None:
    registry = WorldRegistry(tmp_path / "worlds.json")
    registry.load()
    arena = registry.create("arena", world_type="void", dimension="overworld", border_radius=100)

    assert arena.contains("overworld", arena.center_x + 50, arena.center_z - 50)
    assert not arena.contains("overworld", arena.center_x + 150, arena.center_z)
    assert not arena.contains("nether", arena.center_x, arena.center_z)

    cx, cz = arena.clamp_coordinates(arena.center_x + 250, arena.center_z - 250)
    assert arena.contains("overworld", cx, cz)


def test_delete_custom_and_reject_builtin(tmp_path: Path) -> None:
    registry = WorldRegistry(tmp_path / "worlds.json")
    registry.load()
    registry.create("temp_world")

    with pytest.raises(ValueError, match="Built-in"):
        registry.delete("overworld")

    removed = registry.delete("temp_world")
    assert removed.name == "temp_world"
    assert registry.get("temp_world") is None


def test_chunk_overlap_and_generated_chunk_store(tmp_path: Path) -> None:
    registry = WorldRegistry(tmp_path / "worlds.json")
    registry.load()
    void_world = registry.create("void_zone", world_type="void", dimension="overworld", border_radius=64)

    cx = void_world.center_x // 16
    cz = void_world.center_z // 16
    assert void_world.chunk_overlaps("overworld", cx, cz)
    assert not void_world.chunk_overlaps("overworld", 0, 0)
    assert registry.find_world_for_chunk("overworld", cx, cz) is not None

    store = GeneratedChunkStore(tmp_path / "chunks.json")
    store.load()
    assert not store.is_generated("void_zone", cx, cz)
    store.mark_generated("void_zone", cx, cz)
    store.save()

    reloaded = GeneratedChunkStore(tmp_path / "chunks.json")
    reloaded.load()
    assert reloaded.is_generated("void_zone", cx, cz)
    reloaded.remove_world("void_zone")
    assert not reloaded.is_generated("void_zone", cx, cz)


def test_create_native_custom_dimension_uses_zero_origin(tmp_path: Path) -> None:
    registry = WorldRegistry(tmp_path / "worlds.json")
    registry.load()
    custom = registry.create("sky_dim", world_type="void", use_custom_dimension=True)

    assert custom.custom_dimension is True
    assert custom.dimension == "sky_dim"
    assert (custom.center_x, custom.center_z) == (0, 0)
    assert (custom.spawn_x, custom.spawn_y, custom.spawn_z) == (0.5, 64.0, 0.5)
    assert registry.find_world_for_chunk("sky_dim", 0, 0) is None


def test_worlds_api_package_interface(tmp_path: Path) -> None:
    registry = WorldRegistry(tmp_path / "worlds.json")
    registry.load()
    lobby = registry.create("lobby_hub", world_type="flat", dimension="overworld", border_radius=300)

    api = WorldsAPI(registry=registry)
    WorldsAPI.set_instance(api)
    try:
        resolved = get_worlds_api()
        assert resolved is api
        assert resolved.get_world("lobby_hub") is lobby
        assert resolved.detect_world_name("overworld", lobby.center_x + 10, lobby.center_z - 10) == "lobby_hub"

        updated = resolved.set_world_spawn("lobby_hub", 15.0, 80.0, -25.0, yaw=90.0, pitch=5.0)
        assert updated is not None
        assert (updated.spawn_x, updated.spawn_y, updated.spawn_z) == (15.0, 80.0, -25.0)
        assert updated.spawn_yaw == 90.0
    finally:
        WorldsAPI.set_instance(None)


def test_imported_world_registration_and_subchunk_decoder(tmp_path: Path) -> None:
    registry = WorldRegistry(tmp_path / "worlds.json")
    registry.load()
    imported = registry.create(
        "thailand",
        world_type="imported",
        dimension="overworld",
        border_radius=2000,
        import_source="thailand",
    )
    assert imported.world_type == "imported"
    assert imported.import_source == "thailand"
    assert imported.dimension == "overworld"
    assert imported.generated is True
    assert registry.find_world_for_chunk("overworld", imported.center_x // 16, imported.center_z // 16) is imported

    # Build a synthetic SubChunk v9 single-block palette (bits_per_block = 0 -> flags = 0)
    # Root TAG_Compound("") { TAG_String("name") = "minecraft:stone", TAG_End }
    nbt_entry = (
        b"\x0a\x00\x00"
        + b"\x08\x04\x00name"
        + b"\x0f\x00minecraft:stone"
        + b"\x00"
    )
    # version=9, layers=1, sy=4, flags=0 (0 bits/block)
    raw_sub = bytes([9, 1, 4, 0]) + nbt_entry
    blocks = decode_subchunk_blocks(raw_sub, 4)
    assert len(blocks) == 4096
    assert blocks[0][1] == 64
    assert blocks[0][3] == "minecraft:stone"
