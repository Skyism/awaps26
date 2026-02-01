import os
import random
from collections import deque
from dataclasses import dataclass
from typing import List, Optional, Tuple

from game_constants import FoodType, ShopCosts, Team, TileType
from item import Food, Pan, Plate
from robot_controller import RobotController

# python src/game.py --red bots/goon.py --blue bots/goon.py --map maps/map1.txt --render

# python src/game.py --red bots/duo_noodle_bot.py --blue bots/goon.py --map maps/map1.txt --replay replay_path.json


LOGGING_ENABLED = os.environ.get("ENABLE_LOGGING", "0") == "1"


class SafeFile:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def write(self, msg):
        pass


def safe_open(file, mode):
    if LOGGING_ENABLED:
        return open(file, mode)
    return SafeFile()


def logger(msg):
    if LOGGING_ENABLED:
        with safe_open("tmp/goon.txt", "a") as f:
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


class BotPlayer:
    def __init__(self, map_copy):
        self.map = map_copy
        self.cooker_pos = []
        self.shop_pos = []
        self.submit_pos = []
        self.trash_pos = []
        self.all_counters = []
        self.initialized = False
        self.bot_id = None
        self.cooking = None
        self.chopping = None
        self.bot_cooking = {}
        self.bot_chopping = {}
        self.throw_out = None
        self.ready_for_sabotage = [False, False, False, False]

        self.bot_claimed_locations = {}
        self.bot_selected_locations = {}

        # shared stattes
        self.current_order = ActiveOrder()
        self.path_cache = {}
        self.turn_map = None
        self.bot_orders = {}
        self.order_claims = {}

        if LOGGING_ENABLED:
            open("tmp/goon.txt", "w").close()

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
        # if LOGGING_ENABLED:
        #     with safe_open('/tmp/counter_debug.txt', 'w') as f:
        #         f.write(f"All counters found: {self.all_counters}\n")
        #         f.write(f"Chopping counter: {self.chopping_counter}\n")
        #         f.write(f"Staging counter: {self.staging_counter}\n")
        #         f.write(f"Assembly counter: {self.assembly_counter}\n")

        logger(f"self.cooker_pos: {self.cooker_pos}")
        logger(f"self.shop_pos: {self.shop_pos}")
        logger(f"self.submit_pos: {self.submit_pos}")
        logger(f"self.trash_pos: {self.trash_pos}")
        logger(f"self.all_counters: {self.all_counters}")

    # ==================== PATHFINDING HELPERS ====================

    def get_bfs_path_steps(
        self, controller: RobotController, start: Tuple[int, int], target_predicate
    ) -> Optional[List[Tuple[int, int]]]:
        """BFS pathfinding - returns full step list [(dx, dy), ...] or None"""
        queue = deque([(start, [])])
        visited = set([start])
        map_for_turn = self.turn_map
        if map_for_turn is None:
            map_for_turn = controller.get_map(controller.get_team())
        w, h = map_for_turn.width, map_for_turn.height

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
                return path

            # Explore neighbors (Chebyshev distance)
            for dx in [-1, 0, 1]:
                for dy in [-1, 0, 1]:
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = curr_x + dx, curr_y + dy

                    if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in visited:
                        # Check if tile is walkable and not occupied by other bots
                        if (
                            map_for_turn.is_tile_walkable(nx, ny)
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

        def try_step(step: Tuple[int, int]):
            if not step or (step[0] == 0 and step[1] == 0):
                return False, None
            if controller.move(bot_id, step[0], step[1]):
                new_from = (bx + step[0], by + step[1])
                return True, new_from
            return False, None

        # Find path and move
        def is_adjacent(x, y, tile):
            return max(abs(x - target_x), abs(y - target_y)) <= 1

        cache_key = ((bx, by), (target_x, target_y))
        cached_steps = self.path_cache.get(cache_key)
        if cached_steps:
            step = cached_steps[0]
            moved, new_from = try_step(step)
            if moved:
                if len(cached_steps) > 1:
                    self.path_cache[(new_from, (target_x, target_y))] = cached_steps[1:]
                return False
            self.path_cache.pop(cache_key, None)

        steps = self.get_bfs_path_steps(controller, (bx, by), is_adjacent)
        if steps:
            self.path_cache[cache_key] = steps
            step = steps[0]
            moved, new_from = try_step(step)
            if moved and len(steps) > 1:
                self.path_cache[(new_from, (target_x, target_y))] = steps[1:]

        return False

    def init_claim_locations(self, bot_id: int, controller: RobotController):
        # need to claim 2 counters, 1 stove
        # Need to select (not exclusive) one shop and one submit

        # Get bot's current position
        bot_state = controller.get_bot_state(bot_id)
        if not bot_state:
            return

        bot_x, bot_y = bot_state["x"], bot_state["y"]

        # Select 2 non-claimed accessible counters
        self.bot_claimed_locations[bot_id] = {
            "counters": [],
            "stove": None,
        }

        vals = self.bot_claimed_locations.values()

        all_claimed_counters = [loc.get("counters") for loc in vals]

        for counter in self.all_counters:
            if len(self.bot_claimed_locations[bot_id]["counters"]) >= 2:
                break
            is_claimed = False
            for claimed in all_claimed_counters:
                if counter in claimed:
                    is_claimed = True
                    break
            if not is_claimed:
                # Check if counter is accessible via BFS
                cx, cy = counter

                def is_target_counter(x, y, tile):
                    return x == cx and y == cy

                path = self.get_bfs_path_steps(
                    controller, (bot_x, bot_y), is_target_counter
                )
                if path is not None:  # If a path exists, the counter is accessible
                    self.bot_claimed_locations[bot_id]["counters"].append(counter)

        # Select 1 non-claimed accessible stove using BFS
        all_claimed_stoves = [
            loc.get("stove") for loc in vals if loc.get("stove") is not None
        ]

        for stove in self.cooker_pos:
            if self.bot_claimed_locations[bot_id]["stove"] is not None:
                break
            if stove not in all_claimed_stoves:
                # Check if stove is accessible via BFS
                sx, sy = stove

                def is_target_stove(x, y, tile):
                    return x == sx and y == sy

                path = self.get_bfs_path_steps(
                    controller, (bot_x, bot_y), is_target_stove
                )
                if path is not None:  # If a path exists, the stove is accessible
                    self.bot_claimed_locations[bot_id]["stove"] = stove
                    break

        # Select accessible shop using BFS (find adjacent position)
        selected_shop = None
        for shop in self.shop_pos:
            shop_x, shop_y = shop

            def is_adjacent_to_shop(x, y, tile):
                return max(abs(x - shop_x), abs(y - shop_y)) <= 1

            path = self.get_bfs_path_steps(
                controller, (bot_x, bot_y), is_adjacent_to_shop
            )
            if path is not None:  # If a path exists, the shop is accessible
                selected_shop = shop
                break

        # Select accessible submit using BFS (find adjacent position)
        selected_submit = None
        for submit in self.submit_pos:
            submit_x, submit_y = submit

            def is_adjacent_to_submit(x, y, tile):
                return max(abs(x - submit_x), abs(y - submit_y)) <= 1

            path = self.get_bfs_path_steps(
                controller, (bot_x, bot_y), is_adjacent_to_submit
            )
            if path is not None:  # If a path exists, the submit is accessible
                selected_submit = submit
                break

        self.bot_selected_locations[bot_id] = {
            "shop": selected_shop,
            "submit": selected_submit,
        }

    def should_get_order(self, controller: RobotController) -> bool:
        """Decide whether to get a new order based on current state"""
        if self.current_order.order is None:
            return True
        # Additional logic can be added here (e.g., timeouts, order complexity)
        return False

    def _get_bot_order(self, bot_id: int) -> ActiveOrder:
        order = self.bot_orders.get(bot_id)
        if order is None:
            order = ActiveOrder()
            self.bot_orders[bot_id] = order
        return order

    def _release_order_claim(self, order_id: int, bot_id: int):
        if self.order_claims.get(order_id) == bot_id:
            del self.order_claims[order_id]

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
        bot_state = controller.get_bot_state(bot_id)
        bot_x, bot_y = bot_state["x"], bot_state["y"]
        res = []
        for x, y in self.all_counters:
            tile = controller.get_tile(controller.get_team(), x, y)
            if tile.item is None:
                res.append((x, y))
        if not res:
            # Fallback to first counter if none are empty
            return self.all_counters[0] if self.all_counters else None
        return self.find_closest(controller, bot_x, bot_y, res)

    def find_empty_cooker(self, controller: RobotController, bot_id: int):
        bot_state = controller.get_bot_state(bot_id)
        bot_x, bot_y = bot_state["x"], bot_state["y"]
        res = []
        for x, y in self.cooker_pos:
            tile = controller.get_tile(controller.get_team(), x, y)
            if tile.item is None:
                res.append((x, y))
        if not res:
            # Fallback to first stove if none are empty
            return self.cooker_pos[0] if self.cooker_pos else None
        return self.find_closest(controller, bot_x, bot_y, res)

    def get_order(
        self,
        controller: RobotController,
        bot_id: int,
        active_orders=None,
        plate_x: Optional[int] = None,
        plate_y: Optional[int] = None,
    ):
        if self.should_get_order(controller):
            if active_orders is None:
                orders = controller.get_orders(controller.get_team())
                active_orders = [o for o in orders if o["is_active"]].sort(
                    key=lambda o: o["expires_turn"], reverse=True
                )
                logger(f"orders: {orders}")
            for order in active_orders:
                order_id = order["order_id"]
                claimed_by = self.order_claims.get(order_id)
                # if is_good_order(order, controller, bot_id):
                if claimed_by is None or claimed_by == bot_id:
                    self.order_claims[order_id] = bot_id
                    self.current_order.order = order
                    self.current_order.required = self.current_order.order["required"]
                    plate_tracker = PlateTracker(self.current_order.required)
                    if plate_x is not None and plate_y is not None:
                        plate_tracker.plate_pos = (plate_x, plate_y)
                    self.current_order.plate_tracker = plate_tracker
                    self.current_order.stove_pos = None
                    logger(f"bot {bot_id} got order {self.current_order.order}")
                    return True
        return False

    # def is_good_order(order, controller: RobotController, bot_id):
    #     # check if its worth it
    #     time_needed = 0
    #     curloc = controller.get_bot_state(bot_id)
    #     shop_loc
    #     stove_loc
    #     counter1_loc
    #     counter2_loc
    #     submit_loc
    #     ing_costs = 0
    #     time_needed = (
    #         time_needed
    #         + abs(curloc["x"] - shop_loc["x"])
    #         + abs(curloc["y"] - shop_loc["y"])
    #         + abs(counter1_loc["x"] - shop_loc["x"])
    #         + abs(counter1_loc["y"] - shop_loc["y"])
    #         + abs(counter1_loc["x"] - submit_loc["x"])
    #         + abs(counter1_loc["y"] - submit_loc["y"])
    #     )
    #     if "EGG" in order["required"]:
    #         ing_costs = ing_costs + 20
    #         time_needed = (
    #             time_needed
    #             + abs(shop_loc["x"] - stove_loc["x"])
    #             + abs(shop_loc["y"] - stove_loc["y"])
    #             + abs(stove_loc["x"] - counter1_loc["x"])
    #             + abs(stove_loc["y"] - counter1_loc["y"])
    #             + abs(counter1_loc["x"] - shop_loc["x"])
    #             + abs(counter1_loc["y"] - shop_loc["y"])
    #             + 20
    #         )
    #     if "ONION" in order["required"]:
    #         ing_costs = ing_costs + 30
    #         time_needed = (
    #             time_needed
    #             + abs(shop_loc["x"] - counter2_loc["x"])
    #             + abs(shop_loc["y"] - counter2_loc["y"])
    #             + abs(counter2_loc["x"] - counter1_loc["x"])
    #             + abs(counter2_loc["y"] - counter1_loc["y"])
    #             + abs(counter1_loc["x"] - shop_loc["x"])
    #             + abs(counter1_loc["y"] - shop_loc["y"])
    #         )
    #     if "MEAT" in order["required"]:
    #         ing_costs = ing_costs + 80
    #         time_needed = (
    #             time_needed
    #             + abs(shop_loc["x"] - counter2_loc["x"])
    #             + abs(shop_loc["y"] - counter2_loc["y"])
    #             + abs(counter2_loc["x"] - stove_loc["x"])
    #             + abs(counter2_loc["y"] - stove_loc["y"])
    #             + abs(stove_loc["x"] - counter1_loc["x"])
    #             + abs(stove_loc["y"] - counter1_loc["y"])
    #             + abs(counter1_loc["x"] - shop_loc["x"])
    #             + abs(counter1_loc["y"] - shop_loc["y"])
    #             + 20
    #         )
    #     if "NOODLES" in order["required"]:
    #         ing_costs = ing_costs + 40
    #         time_needed = (
    #             time_needed
    #             + abs(shop_loc["x"] - counter1_loc["x"])
    #             + abs(shop_loc["y"] - counter1_loc["y"])
    #             + abs(counter1_loc["x"] - shop_loc["x"])
    #             + abs(counter1_loc["y"] - shop_loc["y"])
    #         )
    #     if "SAUCE" in order["required"]:
    #         ing_costs = ing_costs + 10
    #         time_needed = (
    #             time_needed
    #             + abs(shop_loc["x"] - counter1_loc["x"])
    #             + abs(shop_loc["y"] - counter1_loc["y"])
    #             + abs(counter1_loc["x"] - shop_loc["x"])
    #             + abs(counter1_loc["y"] - shop_loc["y"])
    #         )

    #     if ing_costs >= order["reward"] + order["penalty"]:
    #         return False
    #     if order["expires_turn"] - controller.get_turn() > time_needed:
    #         return False
    #     return True

    def _cleanup_expired_order_ingredients(
        self, controller: RobotController, bot_id: int
    ):
        """Trash any ingredients associated with an expired order. Returns True when cleanup is complete, False otherwise."""
        bot_state = controller.get_bot_state(bot_id)
        if not bot_state:
            return True

        # Trash any held item (food, plate with partial order, etc.)
        holding = bot_state.get("holding")
        if holding:
            logger(f"Bot {bot_id}: Trashing held item due to expired order: {holding}")
            # Move to trash and discard
            if (
                holding.get("type") != "Plate"
                or holding.get("dirty")
                or len(holding.get("food")) > 0
            ):
                if self.trash_pos:
                    tx, ty = self.trash_pos[0]
                    if self.move_towards(controller, bot_id, tx, ty):
                        controller.trash(bot_id, tx, ty)
                    return False  # Action consumed
                return False  # Still moving
            else:
                self.get_plate(controller, bot_id)
                return False

        # Clear cooking state and optionally retrieve/trash food from cooker
        if self.bot_cooking.get(bot_id):
            (item, x, y) = self.bot_cooking[bot_id]
            logger(
                f"Bot {bot_id}: Clearing cooking state at ({x}, {y}) due to expired order"
            )
            # Try to take food from pan and trash it
            stove_tile = controller.get_tile(controller.get_team(), x, y)
            if stove_tile and stove_tile.item:
                stove_item = controller.item_to_public_dict(stove_tile.item)
                if stove_item and stove_item.get("food"):
                    # Move adjacent and take from pan
                    if self.move_towards(controller, bot_id, x, y):
                        if controller.take_from_pan(bot_id, x, y):
                            # Now trash it next turn
                            return False  # Action consumed
                    return False  # Still moving
            # No item on stove, just clear the state
            self.bot_cooking[bot_id] = None
            return False  # State cleared, continue next turn

        # Clear chopping state and optionally retrieve/trash food from counter
        if self.bot_chopping.get(bot_id):
            (item, x, y) = self.bot_chopping[bot_id]
            logger(
                f"Bot {bot_id}: Clearing chopping state at ({x}, {y}) due to expired order"
            )
            # Try to pick up food from counter and trash it
            counter_tile = controller.get_tile(controller.get_team(), x, y)
            if counter_tile and counter_tile.item:
                # Move adjacent and pick up
                if self.move_towards(controller, bot_id, x, y):
                    if controller.pickup(bot_id, x, y):
                        # Now trash it next turn
                        return False  # Action consumed
                return False  # Still moving
            # No item on counter, just clear the state
            self.bot_chopping[bot_id] = None
            return False  # State cleared, continue next turn

        # Clear plate on counter if exists

        if (
            self.current_order.plate_tracker
            and self.current_order.plate_tracker.plate_pos
        ):
            px, py = self.current_order.plate_tracker.plate_pos
            plate_tile = controller.get_tile(controller.get_team(), px, py)
            if plate_tile and plate_tile.item:
                plate = controller.item_to_public_dict(plate_tile.item)
                logger(
                    f"Bot {bot_id}: Clearing plate at ({px}, {py}) due to expired order"
                )
                if plate and not plate.get("dirty") and len(plate.get("food", [])) == 0:
                    # Clean empty plate, we can reuse it
                    return True
            if plate_tile and plate_tile.item:
                # Move adjacent and pick up the plate
                if self.move_towards(controller, bot_id, px, py):
                    if controller.pickup(bot_id, px, py):
                        # Trash the plate next turn
                        return False  # Action consumed
                return False  # Still moving
            # No plate on counter, clear the tracker
            self.current_order.plate_tracker.plate_pos = None
            return False  # State cleared, continue next turn

        # Everything is cleaned up
        return True

    def should_get_plate(self, controller: RobotController):
        """Decide whether to get a plate based on current order state"""
        if (
            self.current_order.order
            and self.current_order.plate_tracker.plate_pos is None
        ):
            return True
        # Additional logic can be added here (e.g., plate type requirements)
        return False

    def get_plate(self, controller: RobotController, bot_id: int):
        """Buy a plate and place it on a counter"""
        bot_state = controller.get_bot_state(bot_id)

        with safe_open("tmp/plate.txt", "a") as f:
            f.write(f"=== get_plate called ===\n")
            f.write(f"bot_state: {bot_state}\n")
            if bot_state:
                f.write(f"holding key exists: {'holding' in bot_state}\n")
                f.write(f"holding value: {bot_state.get('holding')}\n")

        # If already holding plate, place it on counter
        if bot_state and bot_state.get("holding") is not None:
            held_item = bot_state["holding"]
            with safe_open("tmp/plate.txt", "a") as f:
                f.write(f"held_item: {held_item}\n")

            if held_item and held_item["type"] == "Plate":
                with safe_open("tmp/plate.txt", "a") as f:
                    f.write(f"Bot is holding a plate, trying to place it\n")
                # Find a counter to place the plate
                counter = self.find_empty_counter(controller, bot_id)
                if counter:
                    cx, cy = counter
                    if self.move_towards(controller, bot_id, cx, cy):
                        if controller.place(bot_id, cx, cy):
                            self.current_order.plate_tracker.plate_pos = (cx, cy)
                            with safe_open("tmp/plate.txt", "a") as f:
                                f.write(f"Placed plate at ({cx}, {cy})\n")
                            return True
                    # Still moving towards counter, don't try to buy
                    with safe_open("tmp/plate.txt", "a") as f:
                        f.write(f"Still moving towards counter at ({cx}, {cy})\n")
                    return False
            # TODO: Handle case where bot is holding something else (trash it?)

        # Otherwise, go buy a plate (only if not holding anything)
        with safe_open("tmp/plate.txt", "a") as f:
            f.write(f"Attempting to buy plate\n")

        shop = self.bot_selected_locations[bot_id]["shop"]
        if shop is None:
            logger(f"Bot {bot_id}: No accessible shop found, cannot buy plate")
            return False
        sx, sy = shop
        if self.move_towards(controller, bot_id, sx, sy):
            if (
                controller.get_team_money(controller.get_team())
                >= ShopCosts.PLATE.buy_cost
            ):
                if controller.buy(bot_id, ShopCosts.PLATE, sx, sy):
                    with safe_open("tmp/plate.txt", "a") as f:
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

        with safe_open("tmp/plate.txt", "a") as f:
            f.write(f"=== get_pan called ===\n")
            f.write(f"bot_state: {bot_state}\n")
            if bot_state:
                f.write(f"holding key exists: {'holding' in bot_state}\n")
                f.write(f"holding value: {bot_state.get('holding')}\n")

        # If already holding pan, place it on nearest empty cooker
        if bot_state and bot_state.get("holding") is not None:
            held_item = bot_state["holding"]
            with safe_open("tmp/plate.txt", "a") as f:
                f.write(f"held_item: {held_item}\n")

            if held_item and held_item["type"] == "Pan":
                with safe_open("tmp/plate.txt", "a") as f:
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

                    with safe_open("tmp/plate.txt", "a") as f:
                        f.write(f"Nearest empty cooker at ({cx}, {cy})\n")

                    if self.move_towards(controller, bot_id, cx, cy):
                        if controller.place(bot_id, cx, cy):
                            with safe_open("tmp/plate.txt", "a") as f:
                                f.write(f"Placed pan at ({cx}, {cy})\n")
                            return True
                    # Still moving towards cooker, don't try to buy
                    with safe_open("tmp/plate.txt", "a") as f:
                        f.write(f"Still moving towards cooker at ({cx}, {cy})\n")
                    return False
                else:
                    with safe_open("tmp/plate.txt", "a") as f:
                        f.write(f"No empty cookers available\n")
                    return False
            # TODO: Handle case where bot is holding something else (trash it?)

        # Otherwise, go buy a pan (only if not holding anything)
        if not bot_state or bot_state.get("holding") is not None:
            with safe_open("tmp/plate.txt", "a") as f:
                f.write(
                    f"Bot is holding something else, cannot buy pan {bot_state.get("holding")}\n"
                )
            return False
        with safe_open("tmp/plate.txt", "a") as f:
            f.write(f"Attempting to buy pan\n")
        shop = self.bot_selected_locations.get(bot_id, {}).get("shop")
        if shop is None:
            # Fallback to first shop if bot_selected_locations not set
            shop = self.shop_pos[0] if self.shop_pos else None
        if shop is None:
            logger(f"Bot {bot_id}: No accessible shop found, cannot buy pan")
            return False
        sx, sy = shop
        if self.move_towards(controller, bot_id, sx, sy):
            if (
                controller.get_team_money(controller.get_team())
                >= ShopCosts.PAN.buy_cost
            ):
                if controller.buy(bot_id, ShopCosts.PAN, sx, sy):
                    with safe_open("tmp/plate.txt", "a") as f:
                        f.write(f"Purchased pan at ({sx}, {sy})\n")
                    return True

    def prep_ings(self, controller: RobotController, held_item, bot_id: int):

        def find_next_ing():
            for i, ing in enumerate(self.current_order.plate_tracker.ing_on_plate):
                if not ing:
                    return self.current_order.required[i]
            return None

        def get_ing(ing_name: str):
            # If holding the ingredient already, return
            bot_state = controller.get_bot_state(self.bot_id)
            if bot_state and bot_state.get("holding"):
                if (
                    held_item
                    and held_item["type"] == "Food"
                    and held_item.get("name") == ing_name
                ):
                    return True
                else:
                    # Holding wrong item, need to trash it first
                    tx, ty = self.trash_pos[0]
                    if self.move_towards(controller, self.bot_id, tx, ty):
                        if controller.trash(self.bot_id, tx, ty):
                            return False
                    return False  # Don't continue to buy, wait until item is trashed
            # Only buy if not holding anything
            sx, sy = self.bot_selected_locations[self.bot_id]["shop"]
            if self.move_towards(controller, self.bot_id, sx, sy):
                # buy the ingredient
                ing_type = FoodType[ing_name.upper()]
                if controller.buy(self.bot_id, ing_type, sx, sy):
                    return True
            return False  # Still moving or buy failed

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
                cx, cy = self.find_empty_cooker(controller, bot_id)
                if self.move_towards(
                    controller,
                    self.bot_id,
                    cx,
                    cy,
                ):
                    if controller.place(self.bot_id, cx, cy):
                        self.cooking = (held_item, cx, cy)
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
                        self.bot_id,
                        cx,
                        cy,
                    ):
                        controller.place(self.bot_id, cx, cy)
                        self.chopping = (
                            held_item,
                            cx,
                            cy,
                        )
                        return True
                return False

            else:
                logger(f"No more cooking needed for {held_item}")
                return True

        # # Check if a plate exists otherwise call get_plate
        # if self.current_order.plate_tracker.plate_pos is None:
        #     self.get_plate(controller, self.bot_id)
        #     return

        if self.cooking:
            # Check if it's done cooking and pick it up, otherwise wait.
            (item, x, y) = self.cooking
            if self.move_towards(controller, self.bot_id, x, y):
                stove_tile = controller.get_tile(controller.get_team(), x, y)
                logger(f"Stove tile: {stove_tile}")
                if stove_tile and stove_tile.item:
                    stove_item = controller.item_to_public_dict(stove_tile.item)
                    logger(f"Stove item: {stove_item}")
                    food = stove_item.get("food") if stove_item else None
                    if food and food.get("cooked_stage") >= 1:
                        if controller.take_from_pan(self.bot_id, x, y):
                            self.cooking = None
            return False

        if self.chopping:
            # Check if it's done chopping and pick it up, otherwise wait.
            (item, x, y) = self.chopping
            if self.move_towards(controller, self.bot_id, x, y):
                counter_tile = controller.get_tile(controller.get_team(), x, y)
                logger(f"Counter tile: {counter_tile}")
                if counter_tile and counter_tile.item:
                    counter_item = controller.item_to_public_dict(counter_tile.item)
                    logger(f"Counter item: {counter_item}")
                    if counter_item.get("chopped"):
                        if controller.pickup(self.bot_id, x, y):
                            self.chopping = None
                    else:
                        controller.chop(self.bot_id, x, y)
                        return False
            return False

        def plate_ing():
            px, py = self.current_order.plate_tracker.plate_pos
            if self.move_towards(
                controller,
                self.bot_id,
                px,
                py,
            ):
                ing_name = find_next_ing()
                if controller.add_food_to_plate(
                    self.bot_id,
                    px,
                    py,
                ):
                    # mark ingredient as placed on plate
                    for i, req in enumerate(self.current_order.required):
                        if (
                            req == ing_name
                            and not self.current_order.plate_tracker.ing_on_plate[i]
                        ):
                            self.current_order.plate_tracker.ing_on_plate[i] = True
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
        """
        Docstring for submit_plate: check self.current_order states
        """
        # If not holding the plate, pick it up
        if held_item is None:
            px, py = self.current_order.plate_tracker.plate_pos
            if self.move_towards(controller, bot_id, px, py):
                controller.pickup(bot_id, px, py)
        else:
            # Move to submit location and submit
            submit = self.bot_selected_locations.get(bot_id, {}).get("submit")
            if submit is None:
                # Fallback to first submit if bot_selected_locations not set
                submit = self.submit_pos[0] if self.submit_pos else None
            if submit is None:
                logger(f"Bot {bot_id}: No accessible submit found, cannot submit plate")
                return
            sx, sy = submit
            if self.move_towards(controller, bot_id, sx, sy):
                if controller.submit(bot_id, sx, sy):
                    logger(f"Bot {bot_id}: Submitted order {self.current_order.order}")
                    # Reset current order
                    if self.current_order.order:
                        self._release_order_claim(
                            self.current_order.order["order_id"], bot_id
                        )
                    self.current_order = ActiveOrder()
                    self.bot_orders[bot_id] = self.current_order

    def bot_turn(self, controller: RobotController, bot_id: int):
        past_plate_x, past_plate_y = None, None
        bot_state = controller.get_bot_state(bot_id)
        bx, by = bot_state["x"], bot_state["y"]
        holding = bot_state.get("holding")
        with safe_open("tmp/holding.txt", "a") as f:
            f.write(f"Bot is at ({bx}, {by}) and holding {(holding)}\n")

        if not self.initialized:

            self._initialize_locations(controller)

            self.initialized = True

        # switch_info = controller.get_switch_info()

        # # If is switched
        # logger(f"Bot {bot_id}: Switch info: {switch_info}")

        # if switch_info and switch_info.get("my_team_switched"):
        #     # Run switched sabotage logic
        #     logger(
        #         f"Bot {bot_id}: [SWITCH CHECK] my_team_switched=True, entering sabotage mode"
        #     )
        #     self.run_sabotage(controller, bot_id)
        #     return
        # elif (
        #     switch_info
        #     and switch_info.get("window_active")
        #     and switch_info.get("turn") >= (switch_info.get("window_end_turn") - 100)
        # ):
        #     # Prepare for switch - actively clear hands by trashing/placing items
        #     logger(
        #         f"Bot {bot_id}: [SWITCH PREP] Preparing to switch. Holding: {holding}"
        #     )

        #     if holding is not None:
        #         # Actively trash or place the item to clear hands
        #         held_type = holding.get("type")

        #         # Try to trash it
        #         if self.trash_pos:
        #             tx, ty = self.trash_pos[0]
        #             logger(
        #                 f"Bot {bot_id}: [SWITCH PREP] Trashing {held_type} to clear hands"
        #             )
        #             if self.move_towards(controller, bot_id, tx, ty):
        #                 if controller.trash(bot_id, tx, ty):
        #                     logger(
        #                         f"Bot {bot_id}: [SWITCH PREP] Successfully trashed item!"
        #                     )
        #                     self.ready_for_sabotage[bot_id] = True
        #                 else:
        #                     logger(
        #                         f"Bot {bot_id}: [SWITCH PREP] Failed to trash, trying next turn"
        #                     )
        #                     self.ready_for_sabotage[bot_id] = False
        #             else:
        #                 logger(f"Bot {bot_id}: [SWITCH PREP] Moving towards trash")
        #                 self.ready_for_sabotage[bot_id] = False
        #             return
        #         else:
        #             logger(
        #                 f"Bot {bot_id}: [SWITCH PREP] No trash found, marking not ready"
        #             )
        #             self.ready_for_sabotage[bot_id] = False
        #             return
        #     else:
        #         # Hands are clear, mark ready
        #         self.ready_for_sabotage[bot_id] = True
        #         logger(f"Bot {bot_id}: [SWITCH PREP] Hands clear, ready to switch")

        #     # Check if both bots are ready
        #     if sum([1 for v in self.ready_for_sabotage if v]) >= 2:
        #         # Both bots are ready, switch now
        #         logger(f"Bot {bot_id}: [SWITCH CHECK] Both bots ready, switching maps!")
        #         controller.switch_maps()
        #         logger(f"Bot {bot_id}: [SWITCH] Successfully switched maps!")
        #         return
        #     else:
        #         logger(
        #             f"Bot {bot_id}: [SWITCH CHECK] Waiting for other bot to clear hands. Ready: {self.ready_for_sabotage}"
        #         )
        #         return

        # Initialize bot-specific claimed locations if not done yet
        if bot_id not in self.bot_selected_locations:
            self.init_claim_locations(bot_id, controller)

        self.bot_id = bot_id
        self.current_order = self._get_bot_order(bot_id)
        self.cooking = self.bot_cooking.get(bot_id)
        self.chopping = self.bot_chopping.get(bot_id)

        orders = controller.get_orders(controller.get_team())
        active_orders = [o for o in orders if o["is_active"]]
        active_order_ids = {o["order_id"] for o in active_orders}
        if (
            self.current_order.order
            and self.current_order.order.get("order_id") not in active_order_ids
        ):
            self._release_order_claim(self.current_order.order["order_id"], bot_id)
            if not self._cleanup_expired_order_ingredients(controller, bot_id):
                return
            if (
                self.current_order.plate_tracker
                and self.current_order.plate_tracker.plate_pos
            ):
                past_plate_x, past_plate_y = self.current_order.plate_tracker.plate_pos
            else:
                past_plate_x, past_plate_y = None, None
            self.current_order = ActiveOrder()
            self.bot_orders[bot_id] = self.current_order

        if not self.current_order.order:
            self.get_order(
                controller, bot_id, active_orders, past_plate_x, past_plate_y
            )
        elif self.should_get_plate(controller):
            self.get_plate(controller, bot_id)
        elif self.should_get_pan(controller):
            self.get_pan(controller, bot_id)
        elif not all(self.current_order.plate_tracker.ing_on_plate):
            self.prep_ings(controller, holding, bot_id)
        else:
            self.submit_plate(controller, holding, bot_id)

        self.bot_cooking[bot_id] = self.cooking
        self.bot_chopping[bot_id] = self.chopping

    def play_turn(self, controller: RobotController):
        orders = controller.get_orders(controller.get_team())
        active_orders = sorted(
            [o for o in orders if o["is_active"]],
            key=lambda o: o["expires_turn"],
            reverse=True,
        )
        logger(f"orders: {active_orders}")
        self.turn_map = controller.get_map(controller.get_team())
        bots = controller.get_team_bot_ids(controller.get_team())
        logger(f"[PLAY_TURN] Running turn for bots: {bots[:2]}")
        for bot_id in bots[:2]:
            logger(f"[PLAY_TURN] Calling bot_turn for bot {bot_id}")
            self.bot_turn(controller, bot_id)

    def run_sabotage(self, controller: RobotController, bot_id: int):
        """
        Sabotage logic:
        - Move pans and clean plates to counters
        - Trash expensive food and plates with food
        """
        logger(f"Bot {bot_id}: [SABOTAGE] run_sabotage called")

        bot_state = controller.get_bot_state(bot_id)
        if not bot_state:
            logger(f"Bot {bot_id}: [SABOTAGE] No bot state, returning")
            return

        bot_x, bot_y = bot_state["x"], bot_state["y"]
        holding = bot_state.get("holding")
        current_map = controller.get_map(controller.get_team())

        logger(
            f"Bot {bot_id}: [SABOTAGE MODE] Position: ({bot_x}, {bot_y}), Holding: {holding}"
        )

        # If holding something, decide what to do with it
        if holding:
            held_type = holding.get("type")

            # Pans and clean plates go to counters
            if held_type == "Pan":
                logger(f"Bot {bot_id}: [SABOTAGE] Holding pan, moving to counter")
                # Find empty counters
                empty_counters = []
                for x in range(current_map.width):
                    for y in range(current_map.height):
                        tile = current_map.tiles[x][y]
                        if tile.tile_name == "COUNTER" and tile.item is None:
                            empty_counters.append((x, y))

                if empty_counters:
                    nearest_counter = min(
                        empty_counters,
                        key=lambda pos: max(abs(bot_x - pos[0]), abs(bot_y - pos[1])),
                    )
                    cx, cy = nearest_counter
                    logger(
                        f"Bot {bot_id}: [SABOTAGE] Moving pan to counter ({cx}, {cy})"
                    )
                    if self.move_towards(controller, bot_id, cx, cy):
                        if controller.place(bot_id, cx, cy):
                            logger(f"Bot {bot_id}: [SABOTAGE] Placed pan on counter!")
                        else:
                            logger(f"Bot {bot_id}: [SABOTAGE] Failed to place pan")
                else:
                    logger(f"Bot {bot_id}: [SABOTAGE] No empty counters, keeping pan")
                return

            elif held_type == "Plate":
                # Check if plate has food
                plate_food = holding.get("food", [])
                if len(plate_food) > 0 or holding.get("dirty"):
                    # Plate has food or is dirty, trash it
                    logger(
                        f"Bot {bot_id}: [SABOTAGE] Holding dirty/loaded plate, trashing it"
                    )
                    trash_positions = []
                    for x in range(current_map.width):
                        for y in range(current_map.height):
                            if current_map.tiles[x][y].tile_name == "TRASH":
                                trash_positions.append((x, y))

                    if trash_positions:
                        nearest_trash = min(
                            trash_positions,
                            key=lambda pos: max(
                                abs(bot_x - pos[0]), abs(bot_y - pos[1])
                            ),
                        )
                        tx, ty = nearest_trash
                        if self.move_towards(controller, bot_id, tx, ty):
                            if controller.trash(bot_id, tx, ty):
                                logger(
                                    f"Bot {bot_id}: [SABOTAGE] Trashed loaded plate!"
                                )
                            else:
                                logger(
                                    f"Bot {bot_id}: [SABOTAGE] Failed to trash plate"
                                )
                else:
                    # Clean empty plate, move to counter
                    logger(
                        f"Bot {bot_id}: [SABOTAGE] Holding clean plate, moving to counter"
                    )
                    empty_counters = []
                    for x in range(current_map.width):
                        for y in range(current_map.height):
                            tile = current_map.tiles[x][y]
                            if tile.tile_name == "COUNTER" and tile.item is None:
                                empty_counters.append((x, y))

                    if empty_counters:
                        nearest_counter = min(
                            empty_counters,
                            key=lambda pos: max(
                                abs(bot_x - pos[0]), abs(bot_y - pos[1])
                            ),
                        )
                        cx, cy = nearest_counter
                        if self.move_towards(controller, bot_id, cx, cy):
                            if controller.place(bot_id, cx, cy):
                                logger(
                                    f"Bot {bot_id}: [SABOTAGE] Placed clean plate on counter!"
                                )
                return

            elif held_type == "Food":
                # Trash expensive food
                food_name = holding.get("name", "")
                logger(f"Bot {bot_id}: [SABOTAGE] Holding {food_name}, trashing it")
                trash_positions = []
                for x in range(current_map.width):
                    for y in range(current_map.height):
                        if current_map.tiles[x][y].tile_name == "TRASH":
                            trash_positions.append((x, y))

                if trash_positions:
                    nearest_trash = min(
                        trash_positions,
                        key=lambda pos: max(abs(bot_x - pos[0]), abs(bot_y - pos[1])),
                    )
                    tx, ty = nearest_trash
                    if self.move_towards(controller, bot_id, tx, ty):
                        if controller.trash(bot_id, tx, ty):
                            logger(f"Bot {bot_id}: [SABOTAGE] Trashed {food_name}!")
                        else:
                            logger(f"Bot {bot_id}: [SABOTAGE] Failed to trash food")
                return

        # Not holding anything, scan for targets
        logger(f"Bot {bot_id}: [SABOTAGE] Scanning for sabotage targets")

        # Priority 1: Pans on cookers
        pans_on_cookers = []
        # Priority 2: Expensive food on counters (MEAT, ONION, EGG, NOODLES)
        expensive_food = []
        # Priority 3: Plates on counters
        plates_on_counters = []

        for x in range(current_map.width):
            for y in range(current_map.height):
                tile = current_map.tiles[x][y]
                if tile.item is None:
                    continue

                item = controller.item_to_public_dict(tile.item)
                if not item:
                    continue

                item_type = item.get("type")

                if tile.tile_name == "COOKER" and item_type == "Pan":
                    pans_on_cookers.append((x, y))
                    logger(f"Bot {bot_id}: [SABOTAGE] Found pan at cooker ({x}, {y})")

                elif tile.tile_name == "COUNTER":
                    if item_type == "Food":
                        food_name = item.get("name", "")
                        if food_name in ["MEAT", "ONION", "EGG", "NOODLES"]:
                            expensive_food.append((x, y, food_name))
                            logger(
                                f"Bot {bot_id}: [SABOTAGE] Found {food_name} on counter ({x}, {y})"
                            )

                    elif item_type == "Plate":
                        plates_on_counters.append((x, y))
                        logger(
                            f"Bot {bot_id}: [SABOTAGE] Found plate on counter ({x}, {y})"
                        )

        logger(
            f"Bot {bot_id}: [SABOTAGE] Found {len(pans_on_cookers)} pans, {len(expensive_food)} expensive foods, {len(plates_on_counters)} plates"
        )

        # Pick target in priority order
        target = None
        target_type = None

        if pans_on_cookers:
            target = min(
                pans_on_cookers,
                key=lambda pos: max(abs(bot_x - pos[0]), abs(bot_y - pos[1])),
            )
            target_type = "pan"
        elif expensive_food:
            nearest = min(
                expensive_food,
                key=lambda item: max(abs(bot_x - item[0]), abs(bot_y - item[1])),
            )
            target = (nearest[0], nearest[1])
            target_type = f"food ({nearest[2]})"
        elif plates_on_counters:
            target = min(
                plates_on_counters,
                key=lambda pos: max(abs(bot_x - pos[0]), abs(bot_y - pos[1])),
            )
            target_type = "plate"
        else:
            logger(f"Bot {bot_id}: [SABOTAGE] No targets found, sabotage complete")
            return

        # Move to target and pick it up
        tx, ty = target
        logger(f"Bot {bot_id}: [SABOTAGE] Targeting {target_type} at ({tx}, {ty})")

        if self.move_towards(controller, bot_id, tx, ty):
            logger(f"Bot {bot_id}: [SABOTAGE] Adjacent to target, attempting pickup")
            if controller.pickup(bot_id, tx, ty):
                logger(
                    f"Bot {bot_id}: [SABOTAGE] Successfully picked up {target_type}!"
                )
            else:
                logger(f"Bot {bot_id}: [SABOTAGE] Failed to pickup {target_type}")
        else:
            logger(
                f"Bot {bot_id}: [SABOTAGE] Still moving towards {target_type} at ({tx}, {ty})"
            )
