"""Endstone plugin entry point for virtual multi-world and dimension management."""

from __future__ import annotations

import json
from collections import deque
from typing import Any, ClassVar

from endstone import ColorFormat, GameMode, Player
from endstone.command import Command, CommandSender
from endstone.event import ChunkLoadEvent, PlayerMoveEvent, event_handler
from endstone.form import ActionForm, Dropdown, ModalForm, TextInput
from endstone.level import Dimension, Location
from endstone.plugin import Plugin

from .generator import apply_chunk_template, build_spawn_area, resolve_safe_spawn_y
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
        self.chunk_store = GeneratedChunkStore(self.data_folder / "generated_chunks.json")
        self.chunk_store.load()
        self._chunk_queue: deque[tuple[str, int, int]] = deque()
        self._queued_keys: set[tuple[str, int, int]] = set()
        self._tick_counter = 0
        self.register_events(self)
        self.server.scheduler.run_task(self, self._process_chunk_queue, delay=1, period=1)
        self.logger.info(f"Loaded {len(self.registry.worlds)} world(s) from {self.registry.storage_path}")

    def on_disable(self) -> None:
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

    def _teleport_player(self, player: Player, world: VirtualWorld) -> bool:
        loc = self._world_location(world)
        if loc is None:
            player.send_message(f"{ColorFormat.RED}Base dimension '{world.dimension}' is not available.")
            return False

        if not player.teleport(loc):
            player.send_message(f"{ColorFormat.RED}Teleport to '{world.name}' was cancelled.")
            return False

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
                world = self.registry.create(
                    name=name,
                    world_type=world_type,
                    dimension=dimension,
                    border_radius=radius,
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

    def _process_chunk_queue(self) -> None:
        processed = 0
        while self._chunk_queue and processed < 4:
            world_name, chunk_x, chunk_z = self._chunk_queue.popleft()
            self._queued_keys.discard((world_name, chunk_x, chunk_z))
            world = self.registry.get(world_name)
            if world is None or self.chunk_store.is_generated(world_name, chunk_x, chunk_z):
                continue
            dim = self._resolve_dimension(world.dimension)
            if dim is None:
                continue
            apply_chunk_template(self.server, dim, world, chunk_x, chunk_z)
            self.chunk_store.mark_generated(world_name, chunk_x, chunk_z)
            processed += 1

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
        dim_name = from_loc.dimension.name.lower()
        # Check if the player is currently inside a bordered custom world.
        current_world = self.registry.find_world_at(dim_name, from_loc.x, from_loc.z)
        if current_world is None or current_world.border_radius <= 0:
            return
        if not current_world.contains(to_loc.dimension.name.lower(), to_loc.x, to_loc.z):
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
            world = self.registry.create(
                name=name,
                world_type=world_type,
                dimension=dimension,
                border_radius=radius,
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
