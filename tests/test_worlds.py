"""Unit tests for eldia_worlds storage and coordinate isolation."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

from eldia_worlds import WorldsAPI, get_worlds_api
from eldia_worlds.generator import apply_imported_chunk
from eldia_worlds.importer import decode_subchunk_blocks
from eldia_worlds.plugin import TICK_BUILD_RADIUS, WorldsPlugin
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

    # Imported worlds must never be treated as native custom dimensions
    assert imported.custom_dimension is False
    assert imported.import_source is not None


def _make_dummy_plugin(tmp_path: Path, registry: WorldRegistry) -> SimpleNamespace:
    store = GeneratedChunkStore(tmp_path / "chunks.json")
    store.load()
    warnings: list[str] = []
    infos: list[str] = []
    logger = SimpleNamespace(
        warning=warnings.append,
        info=infos.append,
        debug=lambda _m: None,
    )
    plugin = SimpleNamespace(
        registry=registry,
        chunk_store=store,
        import_dir=tmp_path / "import",
        logger=logger,
        _importers={},
        _chunk_queue=deque(),
        _queued_keys=set(),
        _chunk_retries={},
        _warned_missing_importers=set(),
        _logged_imported_chunks=0,
        _tick_counter=0,
        server=SimpleNamespace(),
        _warnings=warnings,
        _infos=infos,
    )
    plugin._active_loads = {}
    plugin._defer_chunk = MethodType(WorldsPlugin._defer_chunk, plugin)
    plugin._get_importer = MethodType(WorldsPlugin._get_importer, plugin)
    plugin._enqueue_chunk = MethodType(WorldsPlugin._enqueue_chunk, plugin)
    plugin._is_in_active_load = MethodType(WorldsPlugin._is_in_active_load, plugin)
    plugin._is_within_player_tick_radius = MethodType(
        WorldsPlugin._is_within_player_tick_radius, plugin
    )
    plugin._tick_active_loads = MethodType(WorldsPlugin._tick_active_loads, plugin)
    return plugin


def test_on_player_move_skips_cross_dimension_and_teleports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "eldia_worlds.plugin.Location",
        lambda dim, x, y, z, pitch, yaw: SimpleNamespace(
            dimension=dim, x=x, y=y, z=z, pitch=pitch, yaw=yaw
        ),
    )
    registry = WorldRegistry(tmp_path / "worlds.json")
    registry.load()
    loy = registry.create("loykathong", world_type="void", use_custom_dimension=True, border_radius=2000)
    thailand = registry.create("thailand", world_type="imported", dimension="overworld", border_radius=1200)
    assert loy.dimension == "loykathong"
    assert thailand.center_x == DEFAULT_SPACING

    plugin = _make_dummy_plugin(tmp_path, registry)
    tips: list[str] = []
    player = SimpleNamespace(send_tip=tips.append)

    dim_loy = SimpleNamespace(name="loykathong")
    dim_overworld = SimpleNamespace(name="Overworld")

    # 1. Cross-dimension teleport (loykathong edge -> overworld) must NOT clamp back to loykathong
    cross_to = SimpleNamespace(dimension=dim_overworld, x=0.5, y=64.0, z=0.5, pitch=0.0, yaw=0.0)
    cross_event = SimpleNamespace(
        from_location=SimpleNamespace(
            dimension=dim_loy, x=1998.0, y=65.0, z=101.0, pitch=0.0, yaw=0.0
        ),
        to_location=cross_to,
        player=player,
    )
    WorldsPlugin.on_player_move(plugin, cross_event)
    assert cross_event.to_location is cross_to
    assert tips == []

    # 2. Same-dimension long-distance teleport (thailand -> overworld spawn) must NOT clamp
    far_to = SimpleNamespace(dimension=dim_overworld, x=0.5, y=70.0, z=0.5, pitch=0.0, yaw=0.0)
    far_event = SimpleNamespace(
        from_location=SimpleNamespace(
            dimension=dim_overworld,
            x=float(thailand.center_x),
            y=65.0,
            z=float(thailand.center_z),
            pitch=0.0,
            yaw=0.0,
        ),
        to_location=far_to,
        player=player,
    )
    WorldsPlugin.on_player_move(plugin, far_event)
    assert far_event.to_location is far_to
    assert tips == []

    # 3. Short step across border MUST clamp inside border
    edge_event = SimpleNamespace(
        from_location=SimpleNamespace(
            dimension=dim_loy, x=1999.0, y=65.0, z=100.0, pitch=0.0, yaw=0.0
        ),
        to_location=SimpleNamespace(
            dimension=dim_loy, x=2002.0, y=65.0, z=100.0, pitch=0.0, yaw=0.0
        ),
        player=player,
    )
    WorldsPlugin.on_player_move(plugin, edge_event)
    assert edge_event.to_location.x <= 1998.5
    assert len(tips) == 1


def test_process_chunk_queue_retries_unloaded_and_skips_missing_importer(tmp_path: Path) -> None:
    registry = WorldRegistry(tmp_path / "worlds.json")
    registry.load()
    thailand = registry.create(
        "thailand",
        world_type="imported",
        dimension="overworld",
        border_radius=1200,
        import_source="thailand",
    )
    plugin = _make_dummy_plugin(tmp_path, registry)
    cx = thailand.center_x // 16
    cz = thailand.center_z // 16
    dim_overworld = SimpleNamespace(name="Overworld")
    plugin.server.online_players = [
        SimpleNamespace(
            location=SimpleNamespace(dimension=dim_overworld, x=cx * 16 + 8.0, y=65.0, z=cz * 16 + 8.0)
        )
    ]
    loaded_chunks: list[SimpleNamespace] = []
    plugin._resolve_dimension = lambda _key: SimpleNamespace(loaded_chunks=loaded_chunks)

    plugin._enqueue_chunk(thailand, cx, cz)

    # Tick 1: chunk is not in loaded_chunks yet -> must be deferred (not dropped or marked generated)
    WorldsPlugin._process_chunk_queue(plugin)
    assert ("thailand", cx, cz) in plugin._queued_keys
    assert len(plugin._chunk_queue) == 1
    assert not plugin.chunk_store.is_generated("thailand", cx, cz)

    # Tick 2: chunk loads, but import folder doesn't exist -> must warn and NOT mark generated
    loaded_chunks.append(SimpleNamespace(x=cx, z=cz))
    WorldsPlugin._process_chunk_queue(plugin)
    assert not plugin.chunk_store.is_generated("thailand", cx, cz)
    assert len(plugin._warnings) == 1


def test_chunk_outside_tick_radius_not_marked_and_proximity_scanner_orders_nearest(
    tmp_path: Path,
) -> None:
    registry = WorldRegistry(tmp_path / "worlds.json")
    registry.load()
    thailand = registry.create(
        "thailand",
        world_type="imported",
        dimension="overworld",
        border_radius=1200,
        import_source="thailand",
    )
    plugin = _make_dummy_plugin(tmp_path, registry)
    pcx = thailand.center_x // 16
    pcz = thailand.center_z // 16
    dim_overworld = SimpleNamespace(name="Overworld")
    plugin.server.online_players = [
        SimpleNamespace(
            location=SimpleNamespace(
                dimension=dim_overworld, x=pcx * 16 + 4.0, y=65.0, z=pcz * 16 + 4.0
            )
        )
    ]
    far_cx = pcx + TICK_BUILD_RADIUS + 4
    far_cz = pcz + TICK_BUILD_RADIUS + 4
    plugin._resolve_dimension = lambda _key: SimpleNamespace(
        loaded_chunks=[SimpleNamespace(x=far_cx, z=far_cz)]
    )

    # Outer chunk queued out of radius must be skipped WITHOUT being marked generated
    plugin._enqueue_chunk(thailand, far_cx, far_cz)
    WorldsPlugin._process_chunk_queue(plugin)
    assert not plugin.chunk_store.is_generated("thailand", far_cx, far_cz)
    assert len(plugin._chunk_queue) == 0

    # Proximity scanner enqueues (2*R + 1)^2 chunks ordered closest to player first
    WorldsPlugin._scan_player_nearby_chunks(plugin)
    side = 2 * TICK_BUILD_RADIUS + 1
    assert len(plugin._chunk_queue) == side * side
    first_item = plugin._chunk_queue[0]
    assert first_item == ("thailand", pcx, pcz)


def test_apply_imported_chunk_returns_false_when_bottom_fill_fails(tmp_path: Path) -> None:
    registry = WorldRegistry(tmp_path / "worlds.json")
    registry.load()
    thailand = registry.create(
        "thailand",
        world_type="imported",
        dimension="overworld",
        border_radius=1200,
        import_source="thailand",
    )
    cx = thailand.center_x // 16
    cz = thailand.center_z // 16
    importer = SimpleNamespace(
        read_chunk_blocks=lambda _scx, _scz: [(0, 64, 0, "minecraft:stone", {})]
    )
    server = SimpleNamespace(
        command_sender=object(),
        dispatch_command=lambda _sender, _cmd: False,
    )
    dimension = SimpleNamespace(
        get_block_at=lambda _x, _y, _z: SimpleNamespace(
            type="minecraft:bedrock",
            set_type=lambda *_a, **_kw: None,
        ),
        get_highest_block_y_at=lambda _x, _z: 64,
    )

    ok = apply_imported_chunk(server, dimension, thailand, cx, cz, importer)
    assert ok is False


def test_world_load_creates_and_cleans_up_tickingarea(tmp_path: Path) -> None:
    registry = WorldRegistry(tmp_path / "worlds.json")
    registry.load()
    thailand = registry.create(
        "thailand",
        world_type="imported",
        dimension="overworld",
        border_radius=1200,
        import_source="thailand",
    )
    plugin = _make_dummy_plugin(tmp_path, registry)
    dispatched: list[str] = []
    plugin.server = SimpleNamespace(
        online_players=[],
        command_sender=object(),
        dispatch_command=lambda _sender, cmd: dispatched.append(cmd) or True,
    )
    plugin._importers["thailand"] = SimpleNamespace(ensure_indexed=lambda: None)
    plugin._require_perm = lambda _sender, _perm: True

    messages: list[str] = []
    sender = SimpleNamespace(send_message=messages.append, is_valid=True)

    handled = WorldsPlugin._handle_load(plugin, sender, ["load", "thailand", "1"])
    assert handled is True
    assert any("tickingarea add" in cmd for cmd in dispatched)
    assert "thailand" in plugin._active_loads
    assert len(plugin._chunk_queue) == 9

    scx = int(thailand.spawn_x // 16)
    scz = int(thailand.spawn_z // 16)
    assert plugin._is_within_player_tick_radius("overworld", scx, scz) is True

    for cx, cz in plugin._active_loads["thailand"]["chunks"]:
        plugin.chunk_store.mark_generated("thailand", cx, cz)

    WorldsPlugin._tick_active_loads(plugin)
    assert any("tickingarea remove" in cmd for cmd in dispatched)
    assert "thailand" not in plugin._active_loads
    assert any("Pre-loaded 'thailand' (9/9 chunks ready)" in msg for msg in messages)
