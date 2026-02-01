import random
from collections import deque
from dataclasses import dataclass
from typing import List, Optional, Tuple

from game_constants import FoodType, ShopCosts, Team, TileType
from item import Food, Pan, Plate
from robot_controller import RobotController

# python src/game.py --red bots/goon.py --blue bots/goon.py --map maps/map1.txt --render

# python src/game.py --red bots/duo_noodle_bot.py --blue bots/goon.py --map maps/map1.txt --replay replay_path.json


def logger(msg):
    with open("tmp/goon.txt", "a") as f:
        f.write(msg + "\n")


@dataclass
class PlateTracker:
    """Tracks which ingredients have been added to the plate"""

    def __init__(self, ing):
        self.plate_pos = None  # Position where plate is placed (counter)
        self.ing_on_plate = [False for _ in range(len(ing))]

    def add_ing(self, ing):
        self.ing_on_plate = [
            False for _ in range(len(ing))
        ]  # TODO: first instance of ing to True


@dataclass
class ActiveOrder:
    order = None
    required: Optional[List[str]] = None
    plate_tracker: Optional[PlateTracker] = None  # Renamed from 'plate'
    stove_pos: Optional[tuple] = None


@dataclass
class BotState:
    """Per-bot state tracking"""

    bot_id: int
    current_order: Optional[ActiveOrder] = None
    cooking: Optional[Tuple] = None  # (item, x, y)
    chopping: Optional[Tuple] = None  # (item, x, y)
    reserved_counter: Optional[Tuple[int, int]] = None
    reserved_cooker: Optional[Tuple[int, int]] = None


class ResourceReservations:
    """Global resource reservation system"""

    def __init__(self):
        self.counter_reservations = {}  # {(x, y): bot_id}
        self.cooker_reservations = {}  # {(x, y): bot_id}

    def reserve_counter(self, bot_id: int, pos: Tuple[int, int]) -> bool:
        if pos in self.counter_reservations:
            return self.counter_reservations[pos] == bot_id
        self.counter_reservations[pos] = bot_id
        return True

    def release_counter(self, bot_id: int, pos: Tuple[int, int]):
        if self.counter_reservations.get(pos) == bot_id:
            del self.counter_reservations[pos]

    def reserve_cooker(self, bot_id: int, pos: Tuple[int, int]) -> bool:
        if pos in self.cooker_reservations:
            return self.cooker_reservations[pos] == bot_id
        self.cooker_reservations[pos] = bot_id
        return True

    def release_cooker(self, bot_id: int, pos: Tuple[int, int]):
        if self.cooker_reservations.get(pos) == bot_id:
            del self.cooker_reservations[pos]

    def is_counter_reserved_by_other(self, bot_id: int, pos: Tuple[int, int]) -> bool:
        reserved_by = self.counter_reservations.get(pos)
        return reserved_by is not None and reserved_by != bot_id

    def is_cooker_reserved_by_other(self, bot_id: int, pos: Tuple[int, int]) -> bool:
        reserved_by = self.cooker_reservations.get(pos)
        return reserved_by is not None and reserved_by != bot_id


class BotPlayer:
    def __init__(self, map_copy):
        self.map = map_copy
        self.cooker_pos = []
        self.shop_pos = []
        self.submit_pos = []
        self.trash_pos = []
        self.all_counters = []
        self.initialized = False

        # Per-bot state tracking (replaces shared state)
        self.bot_states = {}  # Dict[bot_id, BotState]
        self.reservations = ResourceReservations()

        open("tmp/goon.txt", "w")

    def _initialize_locations(self, controller: RobotController):
        """Find and cache important tile locations once"""

        m = controller.get_map(controller.get_team())

        # Find all important tiles
        for x in range(m.width):
            for y in range(m.height):
                tile = m.tiles[x][y]
                tile_name = tile.tile_name

                if tile_name == "SHOP":
                    self.shop_pos.append((x, y))
                elif tile_name == "SUBMIT":
                    self.submit_pos.append((x, y))
                elif tile_name == "TRASH":
                    self.trash_pos.append((x, y))
                elif tile_name == "COOKER":
                    self.cooker_pos.append((x, y))
                elif tile_name == "COUNTER":
                    self.all_counters.append((x, y))

        # # Assign counters to different purposes
        # if len(self.all_counters) >= 3:
        #     self.chopping_counter = self.all_counters[0]
        #     self.staging_counter = self.all_counters[1]
        #     self.assembly_counter = self.all_counters[2]
        # elif len(self.all_counters) >= 2:
        #     self.chopping_counter = self.all_counters[0]
        #     self.staging_counter = self.all_counters[1]
        #     self.assembly_counter = self.all_counters[1]  # Reuse staging for assembly
        # elif len(self.all_counters) >= 1:
        #     # Fallback: use same counter for all (will have issues)
        #     self.chopping_counter = self.all_counters[0]
        #     self.staging_counter = self.all_counters[0]
        #     self.assembly_counter = self.all_counters[0]

        # # Debug: log counter assignments
        # with open('/tmp/counter_debug.txt', 'w') as f:
        #     f.write(f"All counters found: {self.all_counters}\n")
        #     f.write(f"Chopping counter: {self.chopping_counter}\n")
        #     f.write(f"Staging counter: {self.staging_counter}\n")
        #     f.write(f"Assembly counter: {self.assembly_counter}\n")

        logger(f"self.cooker_pos: {self.cooker_pos}")
        logger(f"self.shop_pos: {self.shop_pos}")
        logger(f"self.submit_pos: {self.submit_pos}")
        logger(f"self.trash_pos: {self.trash_pos}")
        logger(f"self.all_counters: {self.all_counters}")

    # ==================== PATHFINDING HELPERS ====================

    def get_bfs_path(
        self, controller: RobotController, start: Tuple[int, int], target_predicate
    ) -> Optional[Tuple[int, int]]:
        """BFS pathfinding - returns first step (dx, dy) or None"""
        queue = deque([(start, [])])
        visited = set([start])
        w, h = self.map.width, self.map.height

        # Get all bot positions to avoid collisions
        bot_positions = set()
        for bot_id in controller.get_team_bot_ids(controller.get_team()):
            bot = controller.get_bot_state(bot_id)
            if bot:
                bot_positions.add((bot["x"], bot["y"]))

        # Also get enemy bot positions (they block movement too)
        try:
            enemy_team = controller.get_enemy_team()
            # We can't directly get enemy bot IDs, so skip this for now
            # Enemy bots will cause move failures but won't prevent pathfinding
        except:
            pass

        while queue:
            (curr_x, curr_y), path = queue.popleft()
            tile = controller.get_tile(controller.get_team(), curr_x, curr_y)

            if target_predicate(curr_x, curr_y, tile):
                if not path:
                    return (0, 0)  # Already at target
                return path[0]

            # Explore neighbors (Chebyshev distance)
            for dx in [-1, 0, 1]:
                for dy in [-1, 0, 1]:
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = curr_x + dx, curr_y + dy

                    if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in visited:
                        # Check if tile is walkable and not occupied by other bots
                        if (
                            controller.get_map(controller.get_team()).is_tile_walkable(
                                nx, ny
                            )
                            and (nx, ny) not in bot_positions
                        ):
                            visited.add((nx, ny))
                            queue.append(((nx, ny), path + [(dx, dy)]))

        return None

    def move_towards(
        self, controller: RobotController, bot_id: int, target_x: int, target_y: int
    ) -> bool:
        """Move bot toward target. Returns True if adjacent, False otherwise"""
        bot_state = controller.get_bot_state(bot_id)
        if not bot_state:
            return False

        bx, by = bot_state["x"], bot_state["y"]

        # Check if already adjacent (Chebyshev distance <= 1)
        if max(abs(bx - target_x), abs(by - target_y)) <= 1:
            return True

        # Find path and move
        def is_adjacent(x, y, tile):
            return max(abs(x - target_x), abs(y - target_y)) <= 1

        step = self.get_bfs_path(controller, (bx, by), is_adjacent)
        if step and (step[0] != 0 or step[1] != 0):
            # Try to move - if it fails (blocked by another bot), just wait
            controller.move(bot_id, step[0], step[1])

        return False

    def should_get_order(self, controller: RobotController) -> bool:
        """Decide whether to get a new order based on current state"""
        if self.current_order.order is None:
            return True
        # Additional logic can be added here (e.g., timeouts, order complexity)
        return False

    # todo add stove logic
    def find_closest(self, controller, bot_x, bot_y, list_of_coordinates):
        best_dist = 9999
        best_pos = None
        for x, y in list_of_coordinates:
            dist = max(abs(bot_x - x), abs(bot_y - y))
            if dist < best_dist:
                best_dist = dist
                best_pos = (x, y)
        return best_pos

    def find_empty_counter(self, controller: RobotController, bot_id: int):
        """Find and reserve an empty counter for this bot."""
        bot_state_obj = self.bot_states[bot_id]
        bot_game_state = controller.get_bot_state(bot_id)
        bot_x, bot_y = bot_game_state["x"], bot_game_state["y"]

        # If already reserved, use that
        if bot_state_obj.reserved_counter:
            return bot_state_obj.reserved_counter

        # Find available counters (empty AND not reserved by others)
        res = []
        for x, y in self.all_counters:
            tile = controller.get_tile(controller.get_team(), x, y)
            if tile.item is None:
                # Check if not reserved by another bot
                if not self.reservations.is_counter_reserved_by_other(bot_id, (x, y)):
                    res.append((x, y))

        if not res:
            # Fallback to first counter if none are empty
            return self.all_counters[0] if self.all_counters else None

        # Find closest and reserve it
        best_counter = self.find_closest(controller, bot_x, bot_y, res)
        if best_counter and self.reservations.reserve_counter(bot_id, best_counter):
            bot_state_obj.reserved_counter = best_counter
            return best_counter

        return None

    def get_order(self, controller: RobotController):
        if self.should_get_order(controller):
            orders = controller.get_orders(controller.get_team())
            active_orders = [o for o in orders if o["is_active"]]
            if active_orders:
                self.current_order.order = active_orders[0]
                self.current_order.required = self.current_order.order["required"]
                plate_tracker = PlateTracker(self.current_order.required)
                self.current_order.plate_tracker = plate_tracker
                self.current_order.stove_pos = None
                return True
        return False

    def _assign_orders_to_bots(self, controller: RobotController, bots: List[int]):
        """Assign one order per bot, reassign when bot finishes."""
        orders = controller.get_orders(controller.get_team())
        active_orders = [o for o in orders if o["is_active"]]

        for bot_id in bots[:2]:  # Only 2 bots
            bot_state = self.bot_states[bot_id]

            # Check if current order is still valid
            if bot_state.current_order and bot_state.current_order.order:
                current_order_id = bot_state.current_order.order["order_id"]
                still_active = any(
                    o["order_id"] == current_order_id for o in active_orders
                )
                if not still_active:
                    # Order completed/expired, clear it
                    bot_state.current_order = None

            # Assign new order if bot is idle
            if bot_state.current_order is None and active_orders:
                for order in active_orders:
                    if not self._is_order_assigned(order["order_id"]):
                        bot_state.current_order = ActiveOrder()
                        bot_state.current_order.order = order
                        bot_state.current_order.required = order["required"]
                        bot_state.current_order.plate_tracker = PlateTracker(
                            order["required"]
                        )
                        break

    def _is_order_assigned(self, order_id: int) -> bool:
        """Check if an order is already assigned to any bot."""
        for bot_state in self.bot_states.values():
            if (
                bot_state.current_order
                and bot_state.current_order.order
                and bot_state.current_order.order["order_id"] == order_id
            ):
                return True
        return False

    def should_get_plate(self, bot_state: BotState) -> bool:
        """Decide whether to get a plate based on current order state"""
        if (
            bot_state.current_order
            and bot_state.current_order.order
            and bot_state.current_order.plate_tracker.plate_pos is None
        ):
            return True
        return False

    def get_plate(self, controller: RobotController, bot_id: int):
        """Buy a plate and place it on a counter"""
        bot_state = controller.get_bot_state(bot_id)

        with open("tmp/plate.txt", "a") as f:
            f.write(f"=== get_plate called ===\n")
            f.write(f"bot_state: {bot_state}\n")
            if bot_state:
                f.write(f"holding key exists: {'holding' in bot_state}\n")
                f.write(f"holding value: {bot_state.get('holding')}\n")

        # If already holding plate, place it on counter
        if bot_state and bot_state.get("holding") is not None:
            held_item = bot_state["holding"]
            with open("tmp/plate.txt", "a") as f:
                f.write(f"held_item: {held_item}\n")

            if held_item and held_item["type"] == "Plate":
                with open("tmp/plate.txt", "a") as f:
                    f.write(f"Bot is holding a plate, trying to place it\n")
                # Find a counter to place the plate
                counter = self.find_empty_counter(controller, bot_id)
                if counter:
                    cx, cy = counter
                    if self.move_towards(controller, bot_id, cx, cy):
                        if controller.place(bot_id, cx, cy):
                            self.bot_states[
                                bot_id
                            ].current_order.plate_tracker.plate_pos = (cx, cy)
                            with open("tmp/plate.txt", "a") as f:
                                f.write(f"Placed plate at ({cx}, {cy})\n")
                            return True
                    # Still moving towards counter, don't try to buy
                    with open("tmp/plate.txt", "a") as f:
                        f.write(f"Still moving towards counter at ({cx}, {cy})\n")
                    return False
            # TODO: Handle case where bot is holding something else (trash it?)

        # Otherwise, go buy a plate (only if not holding anything)
        with open("tmp/plate.txt", "a") as f:
            f.write(f"Attempting to buy plate\n")
        sx, sy = self.shop_pos[0]
        if self.move_towards(controller, bot_id, sx, sy):
            if (
                controller.get_team_money(controller.get_team())
                >= ShopCosts.PLATE.buy_cost
            ):
                if controller.buy(bot_id, ShopCosts.PLATE, sx, sy):
                    with open("tmp/plate.txt", "a") as f:
                        f.write(f"Purchased plate at ({sx}, {sy})\n")
                    return True

        return False

    def should_get_pan(self, controller: RobotController) -> bool:
        """ "Decide wheteher to get a pan based on current map state"""
        # Get pan unless number of pans in stoves + held pans >= number of stoves
        num_stoves = len(self.cooker_pos)
        bots = [
            controller.get_bot_state(i)
            for i in controller.get_team_bot_ids(controller.get_team())
        ]
        # held_pans = sum(1 for bot in bots if bot and controller.item_to_public_dict(bot['held_item']).type == 'Pan')
        pans_on_stoves = 0
        for stove_pos in self.cooker_pos:
            stove_tile = controller.get_tile(
                controller.get_team(), stove_pos[0], stove_pos[1]
            )
            if stove_tile and stove_tile.item:
                item = controller.item_to_public_dict(stove_tile.item)
                if item["type"] == "Pan":
                    pans_on_stoves += 1
        if (pans_on_stoves) < num_stoves:
            return True
        return False

    def get_pan(self, controller: RobotController, bot_id: int):
        """Buy a pan and place it on the nearest empty cooker"""
        bot_state = controller.get_bot_state(bot_id)

        with open("tmp/plate.txt", "a") as f:
            f.write(f"=== get_pan called ===\n")
            f.write(f"bot_state: {bot_state}\n")
            if bot_state:
                f.write(f"holding key exists: {'holding' in bot_state}\n")
                f.write(f"holding value: {bot_state.get('holding')}\n")

        # If already holding pan, place it on nearest empty cooker
        if bot_state and bot_state.get("holding") is not None:
            held_item = bot_state["holding"]
            with open("tmp/plate.txt", "a") as f:
                f.write(f"held_item: {held_item}\n")

            if held_item and held_item["type"] == "Pan":
                with open("tmp/plate.txt", "a") as f:
                    f.write(f"Bot is holding a pan, trying to place it\n")

                # Find nearest empty cooker
                empty_cookers = []
                for cooker_pos in self.cooker_pos:
                    cx, cy = cooker_pos
                    stove_tile = controller.get_tile(controller.get_team(), cx, cy)
                    if stove_tile and stove_tile.item is None:
                        empty_cookers.append((cx, cy))

                if empty_cookers:
                    # Find nearest empty cooker
                    bot_pos = (bot_state["x"], bot_state["y"])
                    nearest_cooker = min(
                        empty_cookers,
                        key=lambda pos: abs(pos[0] - bot_pos[0])
                        + abs(pos[1] - bot_pos[1]),
                    )
                    cx, cy = nearest_cooker

                    with open("tmp/plate.txt", "a") as f:
                        f.write(f"Nearest empty cooker at ({cx}, {cy})\n")

                    if self.move_towards(controller, bot_id, cx, cy):
                        if controller.place(bot_id, cx, cy):
                            with open("tmp/plate.txt", "a") as f:
                                f.write(f"Placed pan at ({cx}, {cy})\n")
                            return True
                    # Still moving towards cooker, don't try to buy
                    with open("tmp/plate.txt", "a") as f:
                        f.write(f"Still moving towards cooker at ({cx}, {cy})\n")
                    return False
                else:
                    with open("tmp/plate.txt", "a") as f:
                        f.write(f"No empty cookers available\n")
                    return False
            # TODO: Handle case where bot is holding something else (trash it?)

        # Otherwise, go buy a pan (only if not holding anything)
        with open("tmp/plate.txt", "a") as f:
            f.write(f"Attempting to buy pan\n")
        sx, sy = self.shop_pos[0]
        if self.move_towards(controller, bot_id, sx, sy):
            if (
                controller.get_team_money(controller.get_team())
                >= ShopCosts.PAN.buy_cost
            ):
                if controller.buy(bot_id, ShopCosts.PAN, sx, sy):
                    with open("tmp/plate.txt", "a") as f:
                        f.write(f"Purchased pan at ({sx}, {sy})\n")
                    return True

    def find_empty_cooker(self, controller: RobotController, bot_id: int):
        """Find and reserve an empty cooker."""
        bot_state_obj = self.bot_states[bot_id]
        bot_game_state = controller.get_bot_state(bot_id)
        bot_x, bot_y = bot_game_state["x"], bot_game_state["y"]

        # If already reserved, use that
        if bot_state_obj.reserved_cooker:
            return bot_state_obj.reserved_cooker

        # Find available cookers (empty or with empty pan, not reserved by others)
        available = []
        for x, y in self.cooker_pos:
            tile = controller.get_tile(controller.get_team(), x, y)
            # Cooker is available if it has no item, or has a pan without food
            if tile and (
                tile.item is None
                or (
                    hasattr(tile, "item")
                    and controller.item_to_public_dict(tile.item).get("type") == "Pan"
                    and controller.item_to_public_dict(tile.item).get("food") is None
                )
            ):
                if not self.reservations.is_cooker_reserved_by_other(bot_id, (x, y)):
                    available.append((x, y))

        if not available:
            return None

        # Find closest and reserve it
        best_cooker = self.find_closest(controller, bot_x, bot_y, available)
        if best_cooker and self.reservations.reserve_cooker(bot_id, best_cooker):
            bot_state_obj.reserved_cooker = best_cooker
            return best_cooker

        return None

    def release_counter_if_done(self, controller: RobotController, bot_id: int):
        """Release counter reservation when bot is done using it."""
        bot_state = self.bot_states[bot_id]
        if bot_state.reserved_counter:
            x, y = bot_state.reserved_counter
            tile = controller.get_tile(controller.get_team(), x, y)
            # Release if counter is empty (bot picked up the item)
            if tile and tile.item is None:
                self.reservations.release_counter(bot_id, bot_state.reserved_counter)
                bot_state.reserved_counter = None

    def prep_ings(self, controller: RobotController, held_item, bot_id: int):
        bot_state_obj = self.bot_states[bot_id]

        def find_next_ing():
            for i, ing in enumerate(
                bot_state_obj.current_order.plate_tracker.ing_on_plate
            ):
                if not ing:
                    return bot_state_obj.current_order.required[i]
            return None

        def get_ing(ing_name: str):
            # If holding the ingredient already, return
            bot_game_state = controller.get_bot_state(bot_id)
            if bot_game_state and bot_game_state.get("holding"):
                if (
                    held_item
                    and held_item["type"] == "Food"
                    and held_item.get("name") == ing_name
                ):
                    return True
                else:
                    tx, ty = self.trash_pos[0]
                    if self.move_towards(controller, bot_id, tx, ty):
                        controller.trash(bot_id, tx, ty)
                    return False
            sx, sy = self.shop_pos[0]
            if self.move_towards(controller, bot_id, sx, sy):
                # buy the ingredient
                ing_type = FoodType[ing_name.upper()]
                if controller.buy(bot_id, ing_type, sx, sy):
                    return True

        def cook_ing(ing_name: str):
            # Don't shadow the parameter - use the held_item from outer scope
            if not held_item:
                return False

            if held_item.get("cooked_stage") == 2:
                # burnt, trash it
                if self.move_towards(
                    controller, self.bot_id, self.trash_pos[0][0], self.trash_pos[0][1]
                ):
                    controller.trash(
                        self.bot_id, self.trash_pos[0][0], self.trash_pos[0][1]
                    )
                return False

            if ing_name in ["EGG", "MEAT"] and held_item.get("cooked_stage") != 1:
                if self.move_towards(
                    controller,
                    bot_id,
                    self.cooker_pos[0][0],
                    self.cooker_pos[0][1],
                ):
                    if controller.place(
                        bot_id, self.cooker_pos[0][0], self.cooker_pos[0][1]
                    ):
                        bot_state_obj.cooking = (
                            held_item,
                            self.cooker_pos[0][0],
                            self.cooker_pos[0][1],
                        )
                return False

            elif ing_name in ["ONIONS", "MEAT"] and not held_item.get("chopped"):
                logger(
                    f"Ingredient needs chopping {held_item}, {held_item.get('chopped')}"
                )
                counter = self.find_empty_counter(controller, bot_id)
                # needs to be chopped
                if counter:
                    cx, cy = counter
                    if self.move_towards(
                        controller,
                        bot_id,
                        cx,
                        cy,
                    ):
                        controller.place(bot_id, cx, cy)
                        bot_state_obj.chopping = (
                            held_item,
                            cx,
                            cy,
                        )
                        return False  # Action consumed, wait for next turn
                return False

            else:
                logger(f"No more cooking needed for {held_item}")
                return True

        def plate_ing():
            px, py = bot_state_obj.current_order.plate_tracker.plate_pos
            if self.move_towards(
                controller,
                bot_id,
                px,
                py,
            ):
                ing_name = find_next_ing()
                if controller.place(
                    bot_id,
                    px,
                    py,
                ):
                    # mark ingredient as placed on plate
                    for i, req in enumerate(bot_state_obj.current_order.required):
                        if (
                            req == ing_name
                            and not bot_state_obj.current_order.plate_tracker.ing_on_plate[
                                i
                            ]
                        ):
                            bot_state_obj.current_order.plate_tracker.ing_on_plate[
                                i
                            ] = True
                            break
                    return True

        if bot_state_obj.cooking:
            # Check if it's done cooking and pick it up, otherwise wait.
            (item, x, y) = bot_state_obj.cooking
            stove_tile = controller.get_tile(controller.get_team(), x, y)
            logger(f"Stove tile: {stove_tile}")
            if stove_tile and stove_tile.item:
                stove_item = controller.item_to_public_dict(stove_tile.item)
                logger(f"Stove item: {stove_item}")
                if stove_item.get("food").get("cooked_stage") >= 1:
                    if controller.take_from_pan(bot_id, x, y):
                        bot_state_obj.cooking = None
            return False

        if bot_state_obj.chopping:
            # Check if it's done chopping and pick it up, otherwise wait.
            (item, x, y) = bot_state_obj.chopping
            counter_tile = controller.get_tile(controller.get_team(), x, y)
            logger(f"Counter tile: {counter_tile}")
            if counter_tile and counter_tile.item:
                counter_item = controller.item_to_public_dict(counter_tile.item)
                logger(f"Counter item: {counter_item}")
                if counter_item.get("chopped"):
                    if controller.pickup(bot_id, x, y):
                        bot_state_obj.chopping = None
                        # Release counter reservation after picking up
                        self.release_counter_if_done(controller, bot_id)
                        return False  # Action consumed, return
                else:
                    # Not chopped yet, keep chopping
                    if not held_item:  # Only chop if not holding anything
                        controller.chop(bot_id, x, y)
            return False

        def plate_ing():
            px, py = bot_state_obj.current_order.plate_tracker.plate_pos
            if self.move_towards(
                controller,
                bot_id,
                px,
                py,
            ):
                ing_name = find_next_ing()
                if controller.add_food_to_plate(
                    bot_id,
                    px,
                    py,
                ):
                    # mark ingredient as placed on plate
                    for i, req in enumerate(bot_state_obj.current_order.required):
                        if (
                            req == ing_name
                            and not bot_state_obj.current_order.plate_tracker.ing_on_plate[
                                i
                            ]
                        ):
                            bot_state_obj.current_order.plate_tracker.ing_on_plate[
                                i
                            ] = True
                            break
                    return True

        next_ing = find_next_ing()
        if next_ing is None:
            return  # All ingredients are already on the plate
        if held_item is None:
            get_ing(next_ing)
        else:
            done = cook_ing(next_ing)
            if done:
                plate_ing()

    def submit_plate(self, controller, held_item, bot_id: int):
        """Submit the completed plate and reset bot's order."""
        bot_state_obj = self.bot_states[bot_id]

        # If not holding the plate, pick it up
        if held_item is None:
            px, py = bot_state_obj.current_order.plate_tracker.plate_pos
            if self.move_towards(controller, bot_id, px, py):
                controller.pickup(bot_id, px, py)
        else:
            # Move to submit location and submit
            sx, sy = self.submit_pos[0]
            if self.move_towards(controller, bot_id, sx, sy):
                if controller.submit(bot_id, sx, sy):
                    # Reset bot's current order (will be reassigned next turn)
                    bot_state_obj.current_order = None

    def bot_turn(self, controller: RobotController, bot_id: int):
        # Get bot-specific state
        bot_state = self.bot_states[bot_id]
        bot_game_state = controller.get_bot_state(bot_id)

        if not bot_game_state:
            return

        bx, by = bot_game_state["x"], bot_game_state["y"]
        holding = bot_game_state.get("holding")
        with open("tmp/holding.txt", "a") as f:
            f.write(f"Bot {bot_id} is at ({bx}, {by}) and holding {(holding)}\n")

        # State machine using bot's own order
        if not bot_state.current_order or not bot_state.current_order.order:
            return  # No order assigned, wait
        elif self.should_get_plate(bot_state):
            self.get_plate(controller, bot_id)
        elif self.should_get_pan(controller):
            self.get_pan(controller, bot_id)
        elif not all(bot_state.current_order.plate_tracker.ing_on_plate):
            self.prep_ings(controller, holding, bot_id)
        else:
            self.submit_plate(controller, holding, bot_id)

    def play_turn(self, controller: RobotController):
        bots = controller.get_team_bot_ids(controller.get_team())

        # One-time initialization
        if not self.initialized:
            self._initialize_locations(controller)
            self.initialized = True
            for bot_id in bots:
                self.bot_states[bot_id] = BotState(bot_id=bot_id)

        # Assign orders to bots every turn (handles reassignment)
        self._assign_orders_to_bots(controller, bots)

        # Execute each bot sequentially (limit to 2 bots)
        for bot_id in bots[:2]:
            self.bot_turn(controller, bot_id)
