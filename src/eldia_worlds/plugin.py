"""Endstone plugin entry point for virtual multi-world and dimension management."""

import json
from collections import deque
from typing import Any, ClassVar

from endstone import ColorFormat, GameMode, Player
from endstone.command import Command, CommandSender
from endstone.event import ChunkLoadEvent, PlayerMoveEvent, event_handler
from endstone.form import ActionForm, Dropdown, ModalForm, TextInput
from endstone.level import Dimension, Location
from endstone.plugin import Plugin

from .api import WorldsAPI
from .generator import (
    apply_chunk_template,
    apply_imported_chunk,
    build_spawn_area,
    resolve_safe_spawn_y,
)
from .importer import BedrockWorldImporter, read_level_dat_spawn, resolve_import_world_dir
from .storage import (
    DEFAULT_BORDER_RADIUS,
    VALID_DIMENSIONS,
    VALID_GAMEMODES,
    VALID_WORLD_TYPES,
    GeneratedChunkStore,
    VirtualWorld,
    WorldRegistry,
    normalize_world_name,
)

_DIMENSION_TYPE_MAP = {
    "overworld": Dimension.OVERWORLD,
    "nether": Dimension.NETHER,
    "the_end": Dimension.THE_END,
}

_GAMEMODE_MAP = {
    "survival": GameMode.SURVIVAL,
    "creative": GameMode.CREATIVE,
    "adventure": GameMode.ADVENTURE,
    "spectator": GameMode.SPECTATOR,
}


class WorldsPlugin(Plugin):
    """Virtual multi-world and dimension manager plugin for Endstone."""

    prefix = "EldiaWorlds"
    api_version = "0.11"
    description = "Create and manage isolated virtual worlds and dimensions"
    authors: ClassVar[list[str]] = ["Eldia"]

    commands: ClassVar[dict[str, Any]] = {
        "world": {
            "description": "Manage and teleport between virtual worlds and dimensions",
            "usages": [
                "/world",
                "/world gui",
                "/world list",
                "/world info <name: str>",
                "/world tp <name: str> [player: player]",
                "/world create <name: str> [world_type: str] [dimension: str] [radius: int]",
                "/world import",
                "/world reimport <name: str>",
                "/world setspawn [name: str]",
                "/world setborder <name: str> <radius: int>",
                "/world setgamemode <name: str> <mode: str>",
                "/world delete <name: str>",
            ],
            "aliases": ["dim", "worlds"],
            "permissions": ["eldia_worlds.command.world"],
        }
    }

    permissions: ClassVar[dict[str, Any]] = {
        "eldia_worlds.command.world": {
            "description": "Allows viewing world list and opening the world menu",
            "default": "true",
        },
        "eldia_worlds.command.tp": {
            "description": "Allows teleporting to virtual worlds",
            "default": "op",
        },
        "eldia_worlds.command.admin": {
            "description": "Allows creating, editing, and deleting virtual worlds",
            "default": "op",
        },
    }

    def on_enable(self) -> None:
        self.registry = WorldRegistry(self.data_folder / "worlds.json")
        self.registry.load()
        self.api = WorldsAPI(self, registry=self.registry, server_getter=lambda: self.server)
        WorldsAPI.set_instance(self.api)
        self.chunk_store = GeneratedChunkStore(self.data_folder / "generated_chunks.json")
        self.chunk_store.load()
        self.import_dir = self.data_folder / "import"
        self.import_dir.mkdir(parents=True, exist_ok=True)
        self._importers: dict[str, BedrockWorldImporter] = {}
        self._chunk_queue: deque[tuple[str, int, int]] = deque()
        self._queued_keys: set[tuple[str, int, int]] = set()
        self._chunk_retries: dict[tuple[str, int, int], int] = {}
        self._warned_missing_importers: set[str] = set()
        self._logged_imported_chunks = 0
        self._tick_counter = 0
        imported_names = self._scan_import_folder()
        if imported_names:
            self.logger.info(f"Auto-imported map(s) into overworld: {', '.join(imported_names)}")
        # Reset stale chunk_store marks from pre-0.2.6 imported worlds once
        healed = False
        for w in self.registry.list_worlds():
            if w.world_type == "imported" and w.import_version < 3:
                self.chunk_store.remove_world(w.name)
                w.import_version = 3
                healed = True
        if healed:
            self.chunk_store.save()
            self.registry.save()
        self.register_events(self)
        self.server.scheduler.run_task(self, self._process_chunk_queue, delay=1, period=1)
        self.server.scheduler.run_task(self, self._sync_native_dimensions, delay=1)
        self.logger.info(f"Loaded {len(self.registry.worlds)} world(s) from {self.registry.storage_path}")

    def _get_importer(self, world: VirtualWorld) -> BedrockWorldImporter | None:
        source_name = world.import_source or world.name
        if source_name in self._importers:
            return self._importers[source_name]
        folder = self.import_dir / source_name
        if not folder.is_dir():
            return None
        resolved = resolve_import_world_dir(folder)
        if resolved is None:
            return None
        imp = BedrockWorldImporter(resolved)
        self._importers[source_name] = imp
        return imp

    def _scan_import_folder(self) -> list[str]:
        """Scan `plugins/eldia_worlds/import/` and register any new Bedrock maps into `overworld`."""
        if not self.import_dir.is_dir():
            return []
        added: list[str] = []
        for entry in sorted(self.import_dir.iterdir()):
            if not entry.is_dir():
                continue
            world_dir = resolve_import_world_dir(entry)
            if world_dir is None:
                continue
            key = normalize_world_name(entry.name)
            if not key:
                continue
            existing = self.registry.get(key)
            if existing is not None:
                if existing.world_type == "imported" and existing.custom_dimension:
                    cx, cz = self.registry._next_center("overworld")
                    sx, sy, sz = read_level_dat_spawn(world_dir)
                    existing.dimension = "overworld"
                    existing.custom_dimension = False
                    existing.center_x = cx
                    existing.center_z = cz
                    existing.spawn_x = float(cx) + sx
                    existing.spawn_y = sy
                    existing.spawn_z = float(cz) + sz
                    existing.generated = True
                    self.registry.save()
                    self.logger.info(
                        f"Restored imported world '{existing.name}' to overworld at ({cx}, {cz})"
                    )
                continue
            try:
                sx, sy, sz = read_level_dat_spawn(world_dir)
                importer = BedrockWorldImporter(world_dir)
                bounds = importer.get_chunk_bounds()
                radius = DEFAULT_BORDER_RADIUS
                if bounds is not None:
                    min_cx, max_cx, min_cz, max_cz = bounds
                    max_block = max(
                        abs(min_cx * 16),
                        abs(max_cx * 16 + 15),
                        abs(min_cz * 16),
                        abs(max_cz * 16 + 15),
                    )
                    radius = min(45_000, max(DEFAULT_BORDER_RADIUS, max_block + 64))
                vw = self.registry.create(
                    name=key,
                    world_type="imported",
                    dimension="overworld",
                    border_radius=radius,
                    use_custom_dimension=False,
                    import_source=entry.name,
                )
                vw.spawn_x = float(vw.center_x) + sx
                vw.spawn_y = sy
                vw.spawn_z = float(vw.center_z) + sz
                self.registry.save()
                self._importers[entry.name] = importer
                added.append(vw.name)
            except (ValueError, OSError) as exc:
                self.logger.warning(f"Failed to import '{entry.name}': {exc}")
        return added

    def _has_native_plugin(self) -> bool:
        return self.server.plugin_manager.get_plugin("eldia_worlds_native") is not None

    def _register_native_dimension(self, dim_name: str, world_type: str) -> bool:
        if not self._has_native_plugin():
            return False
        self.server.dispatch_command(
            self.server.command_sender,
            f"nativeworld create {dim_name} {world_type}",
        )
        return self._resolve_dimension(dim_name) is not None

    def _sync_native_dimensions(self) -> None:
        if not self._has_native_plugin():
            return
        changed = False
        for world in self.registry.list_worlds():
            if world.builtin or world.world_type == "imported" or world.import_source:
                continue
            if not world.custom_dimension and (abs(world.center_x) >= 100_000 or abs(world.center_z) >= 100_000):
                # Upgrade legacy far-coordinate worlds to true native CustomDimensions
                if self._register_native_dimension(world.name, world.world_type):
                    world.dimension = world.name
                    world.center_x = 0
                    world.center_z = 0
                    world.spawn_x = 0.5
                    world.spawn_y = 64.0
                    world.spawn_z = 0.5
                    world.custom_dimension = True
                    world.generated = False
                    changed = True
                    self.logger.info(f"Upgraded world '{world.name}' to native CustomDimension at (0.5, 64.0, 0.5)")
            elif world.custom_dimension:
                self._register_native_dimension(world.dimension, world.world_type)
        if changed:
            self.registry.save()

    def on_disable(self) -> None:
        WorldsAPI.set_instance(None)
        self.chunk_store.save()

    def _resolve_dimension(self, dimension_key: str) -> Dimension | None:
        level = self.server.level
        try:
            dim = level.get_dimension(dimension_key)
            if dim is not None:
                return dim
        except (KeyError, ValueError, RuntimeError):
            dim = None
        target_type = _DIMENSION_TYPE_MAP.get(dimension_key.lower())
        for dim in level.dimensions:
            if target_type is not None and dim.type == target_type:
                return dim
            if dim.name.lower() == dimension_key.lower():
                return dim
        return None

    def _world_location(self, world: VirtualWorld) -> Location | None:
        dim = self._resolve_dimension(world.dimension)
        if dim is None:
            return None
        return Location(
            dim,
            world.spawn_x,
            world.spawn_y,
            world.spawn_z,
            world.spawn_pitch,
            world.spawn_yaw,
        )

    def _enqueue_imported_spawn_chunks(self, world: VirtualWorld) -> None:
        """Enqueue the 5x5 chunk area around an imported world's spawn once loaded."""
        if world.world_type != "imported":
            return
        scx = int(world.spawn_x // 16)
        scz = int(world.spawn_z // 16)
        for dx in range(-2, 3):
            for dz in range(-2, 3):
                self._enqueue_chunk(world, scx + dx, scz + dz)

    def _teleport_player(self, player: Player, world: VirtualWorld) -> bool:
        loc = self._world_location(world)
        if loc is None:
            player.send_message(f"{ColorFormat.RED}Base dimension '{world.dimension}' is not available.")
            return False

        if not player.teleport(loc):
            player.send_message(f"{ColorFormat.RED}Teleport to '{world.name}' was cancelled.")
            return False

        if world.world_type == "imported":
            self.server.scheduler.run_task(
                self,
                lambda w=world: self._enqueue_imported_spawn_chunks(w),
                delay=5,
            )

        if world.gamemode and world.gamemode in _GAMEMODE_MAP:
            player.game_mode = _GAMEMODE_MAP[world.gamemode]

        if not world.generated:
            def _finalize_spawn() -> None:
                dim = self._resolve_dimension(world.dimension)
                if dim is None or not player.is_valid:
                    return
                try:
                    if world.world_type in ("void", "flat"):
                        build_spawn_area(dim, world)
                    else:
                        world.spawn_y = resolve_safe_spawn_y(dim, world)
                    world.generated = True
                    self.registry.save()
                    safe_loc = self._world_location(world)
                    if safe_loc is not None:
                        player.teleport(safe_loc)
                except (OSError, RuntimeError, ValueError) as exc:
                    self.logger.warning(f"Failed to build spawn for '{world.name}': {exc}")

            self.server.scheduler.run_task(self, _finalize_spawn, delay=10)

        player.send_message(
            f"{ColorFormat.GREEN}Teleported to world '{world.name}' "
            f"({world.dimension}, {world.world_type})."
        )
        return True

    def _open_main_gui(self, player: Player) -> None:
        form = ActionForm(
            title="Eldia Worlds",
            content="Select a world to teleport, or create a new virtual dimension.",
        )
        worlds = self.registry.list_worlds()
        if player.has_permission("eldia_worlds.command.tp"):
            for world in worlds:
                tag = "Built-in" if world.builtin else f"{world.world_type} | {world.dimension}"
                form.add_button(
                    f"{world.name}\n[{tag}]",
                    on_click=lambda p, w=world: self._teleport_player(p, w),
                )
        if player.has_permission("eldia_worlds.command.admin"):
            form.add_button(
                "Create New World",
                on_click=self._open_create_gui,
            )
        player.send_form(form)

    def _open_create_gui(self, player: Player) -> None:
        if not player.has_permission("eldia_worlds.command.admin"):
            player.send_message(f"{ColorFormat.RED}You do not have permission to create worlds.")
            return

        def _on_submit(p: Player, raw: str) -> None:
            try:
                values = json.loads(raw)
                name = str(values[0]).strip()
                world_type = VALID_WORLD_TYPES[int(values[1])]
                dimension = VALID_DIMENSIONS[int(values[2])]
                radius_text = str(values[3]).strip()
                radius = int(radius_text) if radius_text else DEFAULT_BORDER_RADIUS
                key = normalize_world_name(name)
                use_custom = self._register_native_dimension(key, world_type)
                world = self.registry.create(
                    name=name,
                    world_type=world_type,
                    dimension=dimension,
                    border_radius=radius,
                    use_custom_dimension=use_custom,
                )
                p.send_message(
                    f"{ColorFormat.GREEN}Created world '{world.name}' "
                    f"({world.world_type} in {world.dimension}). Teleporting..."
                )
                self._teleport_player(p, world)
            except (ValueError, IndexError, TypeError, json.JSONDecodeError) as exc:
                p.send_message(f"{ColorFormat.RED}Could not create world: {exc}")

        form = ModalForm(
            title="Create Virtual World",
            controls=[
                TextInput(label="World Name", placeholder="e.g. skyblock_1"),
                Dropdown(label="World Type", options=list(VALID_WORLD_TYPES), default_index=0),
                Dropdown(label="Base Dimension", options=list(VALID_DIMENSIONS), default_index=0),
                TextInput(
                    label="Border Radius (0 = unlimited)",
                    placeholder=str(DEFAULT_BORDER_RADIUS),
                    default_value=str(DEFAULT_BORDER_RADIUS),
                ),
            ],
            submit_button="Create & Teleport",
            on_submit=_on_submit,
        )
        player.send_form(form)

    def _enqueue_chunk(self, world: VirtualWorld, chunk_x: int, chunk_z: int) -> None:
        key = (world.name, chunk_x, chunk_z)
        if key in self._queued_keys or self.chunk_store.is_generated(world.name, chunk_x, chunk_z):
            return
        self._queued_keys.add(key)
        self._chunk_queue.append(key)

    def _defer_chunk(self, key: tuple[str, int, int], reason: str) -> bool:
        """Re-queue an unloaded or not-yet-writable chunk up to 40 attempts before giving up."""
        retries = self._chunk_retries.get(key, 0) + 1
        if retries <= 40:
            self._chunk_retries[key] = retries
            return True
        self._chunk_retries.pop(key, None)
        self.logger.warning(f"Dropped chunk {key} after 40 retries ({reason}).")
        return False

    def _process_chunk_queue(self) -> None:
        processed = 0
        loaded_by_dim: dict[str, set[tuple[int, int]]] = {}
        deferred: list[tuple[str, int, int]] = []
        while self._chunk_queue and processed < 4:
            key = self._chunk_queue.popleft()
            world_name, chunk_x, chunk_z = key
            self._queued_keys.discard(key)
            world = self.registry.get(world_name)
            if world is None or self.chunk_store.is_generated(world_name, chunk_x, chunk_z):
                self._chunk_retries.pop(key, None)
                continue
            dim = self._resolve_dimension(world.dimension)
            if dim is None:
                continue
            dim_key = world.dimension.lower()
            if dim_key not in loaded_by_dim:
                loaded_by_dim[dim_key] = {(c.x, c.z) for c in dim.loaded_chunks}
            if (chunk_x, chunk_z) not in loaded_by_dim[dim_key]:
                if self._defer_chunk(key, "chunk not in loaded_chunks"):
                    deferred.append(key)
                continue
            if world.world_type == "imported":
                importer = self._get_importer(world)
                if importer is None:
                    self._chunk_retries.pop(key, None)
                    if world.name not in self._warned_missing_importers:
                        self._warned_missing_importers.add(world.name)
                        self.logger.warning(
                            f"Importer not found for '{world.name}' "
                            f"(import_source={world.import_source}, dir={self.import_dir})"
                        )
                    continue
                ok = apply_imported_chunk(
                    self.server,
                    dim,
                    world,
                    chunk_x,
                    chunk_z,
                    importer,
                    logger=self.logger,
                )
                if not ok:
                    if self._defer_chunk(key, "chunk not writable"):
                        deferred.append(key)
                    continue
                self._chunk_retries.pop(key, None)
                self.chunk_store.mark_generated(world_name, chunk_x, chunk_z)
                self._logged_imported_chunks += 1
                if self._logged_imported_chunks <= 5 or self._logged_imported_chunks % 25 == 0:
                    self.logger.info(
                        f"Applied imported chunk ({chunk_x}, {chunk_z}) for '{world.name}' "
                        f"(total applied this session: {self._logged_imported_chunks})"
                    )
                # Imported chunks can have 10k+ blocks; limit to 1 per tick for smooth TPS
                processed = 4
                continue
            apply_chunk_template(self.server, dim, world, chunk_x, chunk_z)
            self._chunk_retries.pop(key, None)
            self.chunk_store.mark_generated(world_name, chunk_x, chunk_z)
            processed += 1

        for key in deferred:
            if key not in self._queued_keys:
                self._queued_keys.add(key)
                self._chunk_queue.append(key)

        self._tick_counter += 1
        if self._tick_counter % 100 == 0:
            self.chunk_store.save()

    @event_handler
    def on_chunk_load(self, event: ChunkLoadEvent) -> None:
        chunk = event.chunk
        dim_name = chunk.dimension.name.lower()
        world = self.registry.find_world_for_chunk(dim_name, chunk.x, chunk.z)
        if world is not None:
            self._enqueue_chunk(world, chunk.x, chunk.z)

    @event_handler
    def on_player_move(self, event: PlayerMoveEvent) -> None:
        to_loc = event.to_location
        from_loc = event.from_location
        if to_loc is None or from_loc is None:
            return
        from_dim = from_loc.dimension.name.lower()
        to_dim = to_loc.dimension.name.lower()
        # Never clamp cross-dimension teleports or long-distance teleports (/warp, /world tp).
        if from_dim != to_dim:
            return
        if abs(to_loc.x - from_loc.x) > 32.0 or abs(to_loc.z - from_loc.z) > 32.0:
            return
        # Check if the player is currently inside a bordered custom world.
        current_world = self.registry.find_world_at(from_dim, from_loc.x, from_loc.z)
        if current_world is None or current_world.border_radius <= 0:
            return
        if not current_world.contains(to_dim, to_loc.x, to_loc.z):
            clamped_x, clamped_z = current_world.clamp_coordinates(to_loc.x, to_loc.z)
            safe_loc = Location(
                from_loc.dimension,
                clamped_x,
                from_loc.y,
                clamped_z,
                from_loc.pitch,
                from_loc.yaw,
            )
            event.to_location = safe_loc
            event.player.send_tip(
                f"{ColorFormat.RED}Reached border of world '{current_world.name}'"
            )

    def _require_perm(self, sender: CommandSender, perm: str) -> bool:
        if sender.has_permission(perm):
            return True
        sender.send_message(f"{ColorFormat.RED}You do not have permission ({perm}).")
        return False

    def _handle_list(self, sender: CommandSender) -> bool:
        sender.send_message(f"{ColorFormat.GOLD}=== Registered Worlds ===")
        for w in self.registry.list_worlds():
            border = "unlimited" if w.border_radius <= 0 else f"+/-{w.border_radius}"
            gm = w.gamemode or "server-default"
            sender.send_message(
                f"{ColorFormat.AQUA}{w.name}{ColorFormat.WHITE} - "
                f"dim={w.dimension}, type={w.world_type}, border={border}, gm={gm}"
            )
        return True

    def _handle_info(self, sender: CommandSender, args: list[str]) -> bool:
        if len(args) < 2:
            sender.send_message(f"{ColorFormat.RED}Usage: /world info <name>")
            return True
        world = self.registry.get(args[1])
        if world is None:
            sender.send_message(f"{ColorFormat.RED}World '{args[1]}' not found.")
            return True
        sender.send_message(
            f"{ColorFormat.GOLD}World '{world.name}':\n"
            f"{ColorFormat.WHITE}  Dimension: {world.dimension}\n"
            f"  Type: {world.world_type}\n"
            f"  Center: ({world.center_x}, {world.center_z})\n"
            f"  Spawn: ({world.spawn_x:.1f}, {world.spawn_y:.1f}, {world.spawn_z:.1f})\n"
            f"  Border Radius: {world.border_radius}\n"
            f"  GameMode: {world.gamemode or 'server-default'}"
        )
        return True

    def _handle_tp(self, sender: CommandSender, args: list[str]) -> bool:
        if not self._require_perm(sender, "eldia_worlds.command.tp"):
            return True
        if len(args) < 2:
            sender.send_message(f"{ColorFormat.RED}Usage: /world tp <name> [player]")
            return True
        world = self.registry.get(args[1])
        if world is None:
            sender.send_message(f"{ColorFormat.RED}World '{args[1]}' not found.")
            return True

        target: Player | None
        if len(args) >= 3:
            target = self.server.get_player(args[2])
            if target is None:
                sender.send_message(f"{ColorFormat.RED}Player '{args[2]}' is not online.")
                return True
        elif isinstance(sender, Player):
            target = sender
        else:
            sender.send_message(f"{ColorFormat.RED}Specify a player when running from console.")
            return True

        self._teleport_player(target, world)
        if target is not sender:
            sender.send_message(f"{ColorFormat.GREEN}Teleported {target.name} to '{world.name}'.")
        return True

    def _handle_create(self, sender: CommandSender, args: list[str]) -> bool:
        if not self._require_perm(sender, "eldia_worlds.command.admin"):
            return True
        if len(args) < 2:
            sender.send_message(
                f"{ColorFormat.RED}Usage: /world create <name> [void|flat|normal] "
                f"[overworld|nether|the_end] [radius]"
            )
            return True

        name = args[1]
        world_type = args[2].lower() if len(args) >= 3 else "void"
        dimension = args[3].lower() if len(args) >= 4 else "overworld"
        try:
            radius = int(args[4]) if len(args) >= 5 else DEFAULT_BORDER_RADIUS
        except ValueError:
            sender.send_message(f"{ColorFormat.RED}Border radius must be an integer.")
            return True

        try:
            key = normalize_world_name(name)
            use_custom = self._register_native_dimension(key, world_type)
            world = self.registry.create(
                name=name,
                world_type=world_type,
                dimension=dimension,
                border_radius=radius,
                use_custom_dimension=use_custom,
            )
        except ValueError as exc:
            sender.send_message(f"{ColorFormat.RED}{exc}")
            return True

        sender.send_message(
            f"{ColorFormat.GREEN}Created world '{world.name}' "
            f"(type={world.world_type}, dim={world.dimension}, "
            f"center=({world.center_x}, {world.center_z}))."
        )
        if isinstance(sender, Player):
            self._teleport_player(sender, world)
        return True

    def _handle_setspawn(self, sender: CommandSender, args: list[str]) -> bool:
        if not self._require_perm(sender, "eldia_worlds.command.admin"):
            return True
        if not isinstance(sender, Player):
            sender.send_message(f"{ColorFormat.RED}Only players can run /world setspawn.")
            return True
        loc = sender.location
        dim_name = loc.dimension.name.lower()
        if len(args) >= 2:
            world = self.registry.get(args[1])
        else:
            world = self.registry.find_world_at(dim_name, loc.x, loc.z)
            if world is None:
                world = self.registry.get(dim_name)
        if world is None:
            sender.send_message(f"{ColorFormat.RED}Target world not found.")
            return True

        world.dimension = dim_name
        world.spawn_x = loc.x
        world.spawn_y = loc.y
        world.spawn_z = loc.z
        world.spawn_yaw = loc.yaw
        world.spawn_pitch = loc.pitch
        self.registry.save()
        sender.send_message(
            f"{ColorFormat.GREEN}Updated spawn for '{world.name}' to "
            f"({loc.x:.1f}, {loc.y:.1f}, {loc.z:.1f}) in {dim_name}."
        )
        return True

    def _handle_setborder(self, sender: CommandSender, args: list[str]) -> bool:
        if not self._require_perm(sender, "eldia_worlds.command.admin"):
            return True
        if len(args) < 3:
            sender.send_message(f"{ColorFormat.RED}Usage: /world setborder <name> <radius>")
            return True
        world = self.registry.get(args[1])
        if world is None:
            sender.send_message(f"{ColorFormat.RED}World '{args[1]}' not found.")
            return True
        try:
            radius = int(args[2])
        except ValueError:
            sender.send_message(f"{ColorFormat.RED}Radius must be an integer.")
            return True
        if radius < 0:
            sender.send_message(f"{ColorFormat.RED}Radius must be >= 0.")
            return True
        world.border_radius = radius
        self.registry.save()
        sender.send_message(
            f"{ColorFormat.GREEN}Set border radius for '{world.name}' to {radius}."
        )
        return True

    def _handle_setgamemode(self, sender: CommandSender, args: list[str]) -> bool:
        if not self._require_perm(sender, "eldia_worlds.command.admin"):
            return True
        if len(args) < 3:
            sender.send_message(
                f"{ColorFormat.RED}Usage: /world setgamemode <name> <{'|'.join(VALID_GAMEMODES)}|none>"
            )
            return True
        world = self.registry.get(args[1])
        if world is None:
            sender.send_message(f"{ColorFormat.RED}World '{args[1]}' not found.")
            return True
        mode = args[2].lower()
        if mode in ("none", "default", "clear"):
            world.gamemode = None
        elif mode in VALID_GAMEMODES:
            world.gamemode = mode
        else:
            sender.send_message(
                f"{ColorFormat.RED}Invalid gamemode. Expected: {', '.join(VALID_GAMEMODES)} or none."
            )
            return True
        self.registry.save()
        sender.send_message(
            f"{ColorFormat.GREEN}Set gamemode for '{world.name}' to {world.gamemode or 'server-default'}."
        )
        return True

    def _handle_delete(self, sender: CommandSender, args: list[str]) -> bool:
        if not self._require_perm(sender, "eldia_worlds.command.admin"):
            return True
        if len(args) < 2:
            sender.send_message(f"{ColorFormat.RED}Usage: /world delete <name>")
            return True
        try:
            removed = self.registry.delete(args[1])
        except ValueError as exc:
            sender.send_message(f"{ColorFormat.RED}{exc}")
            return True
        self.chunk_store.remove_world(removed.name)
        self.chunk_store.save()
        sender.send_message(f"{ColorFormat.GREEN}Deleted world '{removed.name}' from registry.")
        return True

    def on_command(self, sender: CommandSender, command: Command, args: list[str]) -> bool:
        if command.name != "world":
            return False

        if not args:
            if isinstance(sender, Player):
                self._open_main_gui(sender)
                return True
            return self._handle_list(sender)

        sub = normalize_world_name(args[0])
        if sub == "gui":
            if isinstance(sender, Player):
                self._open_main_gui(sender)
            else:
                sender.send_message(f"{ColorFormat.RED}GUI is only available to in-game players.")
            return True
        if sub == "list":
            return self._handle_list(sender)
        if sub == "info":
            return self._handle_info(sender, args)
        if sub in ("tp", "go", "warp"):
            return self._handle_tp(sender, args)
        if sub == "create":
            return self._handle_create(sender, args)
        if sub == "import":
            if not self._require_perm(sender, "eldia_worlds.command.admin"):
                return True
            added = self._scan_import_folder()
            if added:
                sender.send_message(
                    f"{ColorFormat.GREEN}Imported map(s) into overworld: {', '.join(added)}. "
                    f"Use /world tp <name> to teleport."
                )
            else:
                sender.send_message(
                    f"{ColorFormat.YELLOW}No new Bedrock maps found in {self.import_dir}."
                )
            return True
        if sub == "reimport":
            if not self._require_perm(sender, "eldia_worlds.command.admin"):
                return True
            if len(args) < 2:
                sender.send_message(f"{ColorFormat.RED}Usage: /world reimport <name>")
                return True
            target_world = self.registry.get(args[1])
            if target_world is None or target_world.world_type != "imported":
                sender.send_message(f"{ColorFormat.RED}Imported world '{args[1]}' not found.")
                return True
            self.chunk_store.remove_world(target_world.name)
            self.chunk_store.save()
            self._enqueue_imported_spawn_chunks(target_world)
            sender.send_message(
                f"{ColorFormat.GREEN}Cleared chunk cache for '{target_world.name}'. Rebuilding chunks..."
            )
            return True
        if sub == "setspawn":
            return self._handle_setspawn(sender, args)
        if sub == "setborder":
            return self._handle_setborder(sender, args)
        if sub in ("setgamemode", "gm"):
            return self._handle_setgamemode(sender, args)
        if sub in ("delete", "del", "remove"):
            return self._handle_delete(sender, args)

        sender.send_message(
            f"{ColorFormat.RED}Unknown subcommand '{args[0]}'. "
            "Use /world list, /world create, /world tp, /world info, /world setspawn, or /world delete."
        )
        return True
