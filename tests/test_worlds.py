"""Unit tests for eldia_worlds storage and coordinate isolation."""

from __future__ import annotations

from pathlib import Path

import pytest

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
