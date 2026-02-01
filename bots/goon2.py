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
        f.flush()


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

        self.bot_claimed_locations = {}
        self.bot_selected_locations = {}

        # shared stattes
        self.current_order = ActiveOrder()
        self.path_cache = {}
        self.turn_map = None
        self.bot_orders = {}
        self.order_claims = {}

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

    # ==================== LOCATION SELECTION HELPERS ====================

    def _chebyshev_distance(self, x1: int, y1: int, x2: int, y2: int) -> int:
        """Calculate Chebyshev distance (max of absolute differences)"""
        return max(abs(x1 - x2), abs(y1 - y2))

    def _is_accessible(
        self,
        controller: RobotController,
        bot_x: int,
        bot_y: int,
        target: Tuple[int, int],
    ) -> bool:
        """Check if target position is accessible via BFS"""
        tx, ty = target

        def is_target(x, y, tile):
            return x == tx and y == ty

        path = self.get_bfs_path_steps(controller, (bot_x, bot_y), is_target)
        return path is not None

    def _is_accessible_adjacent(
        self,
        controller: RobotController,
        bot_x: int,
        bot_y: int,
        target: Tuple[int, int],
    ) -> bool:
        """Check if position adjacent to target is accessible via BFS"""
        tx, ty = target

        def is_adjacent(x, y, tile):
            return max(abs(x - tx), abs(y - ty)) <= 1

        path = self.get_bfs_path_steps(controller, (bot_x, bot_y), is_adjacent)
        return path is not None

    def _find_all_accessible_locations(
        self, controller: RobotController, bot_id: int, bot_x: int, bot_y: int
    ):
        """Find all accessible locations of each type"""
        accessible = {"counters": [], "stoves": [], "shops": [], "submits": []}

        # Find accessible counters (need to be adjacent, not standing on them)
        for counter in self.all_counters:
            if self._is_accessible_adjacent(controller, bot_x, bot_y, counter):
                accessible["counters"].append(counter)

        # Find accessible stoves (need to be adjacent)
        for stove in self.cooker_pos:
            if self._is_accessible_adjacent(controller, bot_x, bot_y, stove):
                accessible["stoves"].append(stove)

        # Find accessible shops (need to be adjacent)
        for shop in self.shop_pos:
            if self._is_accessible_adjacent(controller, bot_x, bot_y, shop):
                accessible["shops"].append(shop)

        # Find accessible submits (need to be adjacent)
        for submit in self.submit_pos:
            if self._is_accessible_adjacent(controller, bot_x, bot_y, submit):
                accessible["submits"].append(submit)

        return accessible

    # ==================== LOCATION CLAIMING ====================

    def init_claim_locations(self, bot_id: int, controller: RobotController):
        """Select locations using cluster optimization to minimize travel distance"""
        bot_state = controller.get_bot_state(bot_id)
        if not bot_state:
            return

        bot_x, bot_y = bot_state["x"], bot_state["y"]

        # Initialize this bot's entry FIRST to avoid it being counted in claimed locations
        if bot_id not in self.bot_claimed_locations:
            self.bot_claimed_locations[bot_id] = {
                "counters": [],
                "stove": None,
                "plate_counter": None,  # Dedicated counter for plates
            }
        if bot_id not in self.bot_selected_locations:
            self.bot_selected_locations[bot_id] = {
                "shop": None,
                "submit": None,
            }

        # Find all accessible locations
        accessible = self._find_all_accessible_locations(
            controller, bot_id, bot_x, bot_y
        )

        # Get already claimed counters and stoves (excluding this bot's current claims)
        claimed_counters = []
        claimed_stoves = []
        for other_bot_id, loc in self.bot_claimed_locations.items():
            if other_bot_id != bot_id:  # Don't count this bot's own claims
                claimed_counters.extend(loc.get("counters", []))
                stove = loc.get("stove")
                if stove:
                    claimed_stoves.append(stove)

        # Filter out claimed locations
        available_counters = [
            c for c in accessible["counters"] if c not in claimed_counters
        ]
        available_stoves = [s for s in accessible["stoves"] if s not in claimed_stoves]

        # Need at least 2 counters, 1 stove, 1 shop, 1 submit
        if (
            len(available_counters) < 2
            or len(available_stoves) < 1
            or len(accessible["shops"]) < 1
            or len(accessible["submits"]) < 1
        ):
            logger(
                f"Bot {bot_id}: Insufficient locations available, using fallback selection"
            )
            self._fallback_location_selection(
                bot_id, accessible, available_counters, available_stoves
            )
            return

        # Find best cluster by trying each counter as primary
        best_score = float("inf")
        best_cluster = None

        for primary_counter in available_counters:
            px, py = primary_counter

            # Find nearest second counter
            available_for_second = [
                c for c in available_counters if c != primary_counter
            ]
            if not available_for_second:
                continue

            second_counter = min(
                available_for_second,
                key=lambda c: self._chebyshev_distance(px, py, c[0], c[1]),
            )

            # Find nearest stove
            nearest_stove = min(
                available_stoves,
                key=lambda s: self._chebyshev_distance(px, py, s[0], s[1]),
            )

            # Find nearest shop
            nearest_shop = min(
                accessible["shops"],
                key=lambda s: self._chebyshev_distance(px, py, s[0], s[1]),
            )

            # Find nearest submit
            nearest_submit = min(
                accessible["submits"],
                key=lambda s: self._chebyshev_distance(px, py, s[0], s[1]),
            )

            # Calculate cluster score (total distance from primary counter)
            score = (
                self._chebyshev_distance(px, py, second_counter[0], second_counter[1])
                + self._chebyshev_distance(px, py, nearest_stove[0], nearest_stove[1])
                + self._chebyshev_distance(px, py, nearest_shop[0], nearest_shop[1])
                + self._chebyshev_distance(px, py, nearest_submit[0], nearest_submit[1])
            )

            if score < best_score:
                best_score = score
                best_cluster = {
                    "counters": [primary_counter, second_counter],
                    "stove": nearest_stove,
                    "shop": nearest_shop,
                    "submit": nearest_submit,
                }

        if best_cluster:
            self.bot_claimed_locations[bot_id] = {
                "counters": best_cluster["counters"],
                "stove": best_cluster["stove"],
                "plate_counter": best_cluster["counters"][
                    0
                ],  # Use first counter for plates
            }

            self.bot_selected_locations[bot_id] = {
                "shop": best_cluster["shop"],
                "submit": best_cluster["submit"],
            }

        else:

            self._fallback_location_selection(
                bot_id, accessible, available_counters, available_stoves
            )

    def _fallback_location_selection(
        self,
        bot_id: int,
        accessible: dict,
        available_counters: list,
        available_stoves: list,
    ):
        """Fallback to simple first-available selection if cluster optimization fails"""
        # Select first 2 available counters
        counters = (
            available_counters[:2]
            if len(available_counters) >= 2
            else available_counters
        )

        # Select first available stove
        stove = available_stoves[0] if available_stoves else None

        # Select first available shop and submit
        shop = accessible["shops"][0] if accessible["shops"] else None
        submit = accessible["submits"][0] if accessible["submits"] else None

        self.bot_claimed_locations[bot_id] = {
            "counters": counters,
            "stove": stove,
            "plate_counter": (
                counters[0] if counters else None
            ),  # Use first counter for plates
        }

        self.bot_selected_locations[bot_id] = {
            "shop": shop,
            "submit": submit,
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
        """Find an empty counter from this bot's claimed counters"""
        bot_state = controller.get_bot_state(bot_id)
        bot_x, bot_y = bot_state["x"], bot_state["y"]

        # Use bot's claimed counters
        claimed_counters = self.bot_claimed_locations.get(bot_id, {}).get(
            "counters", []
        )
        if not claimed_counters:
            # Fallback to all counters if no claimed counters
            claimed_counters = self.all_counters

        res = []
        for x, y in claimed_counters:
            tile = controller.get_tile(controller.get_team(), x, y)
            if tile.item is None:
                res.append((x, y))

        if not res:
            # If all claimed counters are occupied, return the closest one anyway
            return self.find_closest(controller, bot_x, bot_y, claimed_counters)

        return self.find_closest(controller, bot_x, bot_y, res)

    def find_empty_cooker(self, controller: RobotController, bot_id: int):
        """Return this bot's claimed stove"""
        # Use bot's claimed stove
        claimed_stove = self.bot_claimed_locations.get(bot_id, {}).get("stove")

        if claimed_stove:
            return claimed_stove

        # Fallback to finding closest empty stove if no claimed stove
        bot_state = controller.get_bot_state(bot_id)
        bot_x, bot_y = bot_state["x"], bot_state["y"]
        res = []
        for x, y in self.cooker_pos:
            tile = controller.get_tile(controller.get_team(), x, y)
            if tile.item is None:
                res.append((x, y))
        if not res:
            return self.cooker_pos[0] if self.cooker_pos else None
        return self.find_closest(controller, bot_x, bot_y, res)

    def get_order(self, controller: RobotController, bot_id: int, active_orders=None):
        if self.should_get_order(controller):
            if active_orders is None:
                orders = controller.get_orders(controller.get_team())
                active_orders = [o for o in orders if o["is_active"]]

            # Sort orders by expiration time descending (most time remaining first)
            # This helps avoid taking orders that are about to expire
            active_orders.sort(key=lambda o: o["expires_turn"], reverse=True)

            # Pass 1: Strict time check
            for order in active_orders:
                order_id = order["order_id"]
                claimed_by = self.order_claims.get(order_id)
                logger(
                    f"Bot {bot_id}: Evaluating Order {order_id} (Pass 1: Strict, claimed_by={claimed_by})"
                )
                if (claimed_by is None or claimed_by == bot_id) and self.is_good_order(
                    order, controller, bot_id, strict_time=True
                ):
                    return self._accept_order(controller, bot_id, order)

            # Pass 2: Loose time check (fallback)
            for order in active_orders:
                order_id = order["order_id"]
                claimed_by = self.order_claims.get(order_id)
                logger(
                    f"Bot {bot_id}: Evaluating Order {order_id} (Pass 2: Loose, claimed_by={claimed_by})"
                )
                if (claimed_by is None or claimed_by == bot_id) and self.is_good_order(
                    order, controller, bot_id, strict_time=False
                ):
                    return self._accept_order(controller, bot_id, order)
        return False

    def _accept_order(self, controller, bot_id, order):
        order_id = order["order_id"]

        # Retrieve saved plate position from bot's claimed locations
        old_plate_pos = self.bot_claimed_locations.get(bot_id, {}).get(
            "saved_plate_pos"
        )

        # Verify the saved plate position still has a clean plate
        if old_plate_pos:
            px, py = old_plate_pos
            plate_tile = controller.get_tile(controller.get_team(), px, py)
            if plate_tile and plate_tile.item:
                plate = controller.item_to_public_dict(plate_tile.item)
                if not (
                    plate
                    and plate.get("type") == "Plate"
                    and not plate.get("dirty")
                    and len(plate.get("food", [])) == 0
                ):
                    # Plate is no longer valid, clear it
                    old_plate_pos = None
                    logger(
                        f"Bot {bot_id}: [PLATE INVALID] Saved plate position {(px, py)} no longer has a valid clean plate, clearing"
                    )
            else:
                # No plate at saved position
                old_plate_pos = None

        self.order_claims[order_id] = bot_id
        self.current_order.order = order
        self.current_order.required = self.current_order.order["required"]
        plate_tracker = PlateTracker(self.current_order.required)

        # Transfer plate position if we had a clean empty plate
        if old_plate_pos:
            plate_tracker.plate_pos = old_plate_pos
            logger(
                f"Bot {bot_id}: [PLATE REUSE] Transferring saved plate position {old_plate_pos} to new order"
            )
            # Clear saved position now that we've used it
            self.bot_claimed_locations[bot_id]["saved_plate_pos"] = None
        else:
            logger(
                f"Bot {bot_id}: [PLATE START] No clean plate available to transfer, starting fresh"
            )

        self.current_order.plate_tracker = plate_tracker
        self.current_order.stove_pos = None
        return True

    def _accept_order(self, controller, bot_id, order):
        order_id = order["order_id"]
        # Retrieve saved plate position from bot's claimed locations
        old_plate_pos = self.bot_claimed_locations.get(bot_id, {}).get(
            "saved_plate_pos"
        )

        # Verify the saved plate position still has a clean plate
        if old_plate_pos:
            px, py = old_plate_pos
            plate_tile = controller.get_tile(controller.get_team(), px, py)
            if plate_tile and plate_tile.item:
                plate = controller.item_to_public_dict(plate_tile.item)
                if not (
                    plate
                    and plate.get("type") == "Plate"
                    and not plate.get("dirty")
                    and len(plate.get("food", [])) == 0
                ):
                    # Plate is no longer valid, clear it
                    old_plate_pos = None
                    logger(
                        f"Bot {bot_id}: [PLATE INVALID] Saved plate position {(px, py)} no longer has a valid clean plate, clearing"
                    )
            else:
                # No plate at saved position
                old_plate_pos = None

        self.order_claims[order_id] = bot_id
        self.current_order.order = order
        self.current_order.required = self.current_order.order["required"]
        plate_tracker = PlateTracker(self.current_order.required)

        # Transfer plate position if we had a clean empty plate
        if old_plate_pos:
            plate_tracker.plate_pos = old_plate_pos
            logger(
                f"Bot {bot_id}: [PLATE REUSE] Transferring saved plate position {old_plate_pos} to new order"
            )
            # Clear saved position now that we've used it
            self.bot_claimed_locations[bot_id]["saved_plate_pos"] = None
        else:
            logger(
                f"Bot {bot_id}: [PLATE START] No clean plate available to transfer, starting fresh"
            )

        self.current_order.plate_tracker = plate_tracker
        self.current_order.stove_pos = None
        return True

    def is_good_order(
        self, order, controller: RobotController, bot_id, strict_time: bool = True
    ):
        """Check if the order is worth taking based on cost and time remaining."""
        # Get bot's claimed locations
        claimed = self.bot_claimed_locations.get(bot_id, {})
        selected = self.bot_selected_locations.get(bot_id, {})

        stove_loc = claimed.get("stove")
        counters = claimed.get("counters", [])
        shop_loc = selected.get("shop")
        submit_loc = selected.get("submit")

        if not (stove_loc and len(counters) >= 2 and shop_loc and submit_loc):
            return True  # Fallback if locations not fully claimed yet

        counter1_loc = counters[0]
        counter2_loc = counters[1]

        # Current bot location
        cur_state = controller.get_bot_state(bot_id)
        if not cur_state:
            return True
        cur_loc = (cur_state["x"], cur_state["y"])

        time_needed = 0
        ing_costs = 0

        # Initial travel to shop
        time_needed += self._chebyshev_distance(
            cur_loc[0], cur_loc[1], shop_loc[0], shop_loc[1]
        )

        # Estimate time for each ingredient
        # logger(f"Bot {bot_id}: [DEBUG] Calculating costs for {order['order_id']}")
        for ing in order["required"]:
            if ing == "EGG":
                ing_costs += 20
                # Shop -> Stove -> Counter1 -> Shop
                time_needed += self._chebyshev_distance(
                    shop_loc[0], shop_loc[1], stove_loc[0], stove_loc[1]
                )
                time_needed += self._chebyshev_distance(
                    stove_loc[0], stove_loc[1], counter1_loc[0], counter1_loc[1]
                )
                time_needed += self._chebyshev_distance(
                    counter1_loc[0], counter1_loc[1], shop_loc[0], shop_loc[1]
                )
                time_needed += 25  # (20 cooking + 5 moves/interact)
            elif ing == "ONION":
                ing_costs += 30
                # Shop -> Counter2 -> Counter1 -> Shop
                time_needed += self._chebyshev_distance(
                    shop_loc[0], shop_loc[1], counter2_loc[0], counter2_loc[1]
                )
                time_needed += self._chebyshev_distance(
                    counter2_loc[0], counter2_loc[1], counter1_loc[0], counter1_loc[1]
                )
                time_needed += self._chebyshev_distance(
                    counter1_loc[0], counter1_loc[1], shop_loc[0], shop_loc[1]
                )
                time_needed += 15  # (10 chopping + 5 moves/interact)
            elif ing == "MEAT":
                ing_costs += 80
                # Shop -> Counter2 -> Stove -> Counter1 -> Shop
                time_needed += self._chebyshev_distance(
                    shop_loc[0], shop_loc[1], counter2_loc[0], counter2_loc[1]
                )
                time_needed += self._chebyshev_distance(
                    counter2_loc[0], counter2_loc[1], stove_loc[0], stove_loc[1]
                )
                time_needed += self._chebyshev_distance(
                    stove_loc[0], stove_loc[1], counter1_loc[0], counter1_loc[1]
                )
                time_needed += self._chebyshev_distance(
                    counter1_loc[0], counter1_loc[1], shop_loc[0], shop_loc[1]
                )
                time_needed += 40  # (10 chop + 20 cook + 10 moves/interact)
            elif ing == "NOODLES":
                ing_costs += 40
                time_needed += self._chebyshev_distance(
                    shop_loc[0], shop_loc[1], counter1_loc[0], counter1_loc[1]
                )
                time_needed += self._chebyshev_distance(
                    counter1_loc[0], counter1_loc[1], shop_loc[0], shop_loc[1]
                )
                time_needed += 5
            elif ing == "SAUCE":
                ing_costs += 10
                time_needed += self._chebyshev_distance(
                    shop_loc[0], shop_loc[1], counter1_loc[0], counter1_loc[1]
                )
                time_needed += self._chebyshev_distance(
                    counter1_loc[0], counter1_loc[1], shop_loc[0], shop_loc[1]
                )
                time_needed += 5

        # Add travel to submit
        time_needed += self._chebyshev_distance(
            counter1_loc[0], counter1_loc[1], submit_loc[0], submit_loc[1]
        )
        time_needed += 5  # Buff for plate management and final move

        # Check profitability
        if ing_costs >= order["reward"] + order["penalty"]:
            logger(
                f"Bot {bot_id}: Order {order['order_id']} rejected (unprofitable: cost={ing_costs}, reward={order['reward']})"
            )
            return False

        # Check time viability
        turns_left = order["expires_turn"] - controller.get_turn()
        if strict_time and turns_left < time_needed * 0.7:
            logger(
                f"Bot {bot_id}: Order {order['order_id']} rejected (time: {turns_left} < needed {time_needed}*0.7)"
            )
            return False

        logger(
            f"Bot {bot_id}: Order {order['order_id']} accepted (time: {turns_left} >= needed {time_needed}*0.7, cost={ing_costs}, strict={strict_time})"
        )
        return True

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
            # If holding a dirty plate or plate with food, trash it
            if (
                holding.get("type") != "Plate"
                or holding.get("dirty")
                or len(holding.get("food", [])) > 0
            ):
                logger(
                    f"Bot {bot_id}: [CLEANUP] Trashing held item {holding.get('type')} because order expired"
                )
                if self.trash_pos:
                    tx, ty = self.trash_pos[0]
                    if self.move_towards(controller, bot_id, tx, ty):
                        controller.trash(bot_id, tx, ty)
                    return False  # Action consumed
                return False  # Still moving
            else:
                # Holding clean empty plate - place it on designated counter for safekeeping
                plate_counter = self.bot_claimed_locations.get(bot_id, {}).get(
                    "plate_counter"
                )
                if plate_counter:
                    cx, cy = plate_counter
                    logger(
                        f"Bot {bot_id}: [CLEANUP PRESERVE] Placing clean held plate on designated counter ({cx}, {cy}) for safekeeping"
                    )
                    if self.move_towards(controller, bot_id, cx, cy):
                        if controller.place(bot_id, cx, cy):
                            # Plate successfully placed, it will be picked up by saved_plate_pos mechanism
                            return False  # Action consumed, continue cleanup next turn
                    return False  # Still moving
                else:
                    # No designated counter, trash the plate
                    logger(
                        f"Bot {bot_id}: [CLEANUP] No designated counter, trashing clean plate"
                    )
                    if self.trash_pos:
                        tx, ty = self.trash_pos[0]
                        if self.move_towards(controller, bot_id, tx, ty):
                            controller.trash(bot_id, tx, ty)
                        return False
                    return False

        # Clear cooking state and optionally retrieve/trash food from cooker
        if self.bot_cooking.get(bot_id):
            (item, x, y) = self.bot_cooking[bot_id]

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
                if plate and not plate.get("dirty") and len(plate.get("food", [])) == 0:
                    logger(
                        f"Bot {bot_id}: [PLATE PRESERVE] Found clean empty plate at ({px}, {py}) during cleanup, preserving it for next order to save money"
                    )
                    # Don't clear plate_tracker.plate_pos, keep it for next order
                    return True
                # Plate has food or is dirty, need to trash it
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

    def should_get_plate(self, controller: RobotController, bot_id: int):
        """Decide whether to get a plate based on current order state"""
        if not self.current_order.order:
            return False

        # If already holding a plate, we definitely don't need to get one
        bot_state = controller.get_bot_state(bot_id)
        if (
            bot_state
            and bot_state.get("holding")
            and bot_state["holding"]["type"] == "Plate"
        ):
            return False

        # If we have a plate position saved, verify the plate actually exists there
        if self.current_order.plate_tracker.plate_pos is not None:
            px, py = self.current_order.plate_tracker.plate_pos
            plate_tile = controller.get_tile(controller.get_team(), px, py)
            if plate_tile and plate_tile.item:
                plate = controller.item_to_public_dict(plate_tile.item)
                if plate and plate.get("type") == "Plate" and not plate.get("dirty"):
                    # Plate exists and is not dirty, we're good
                    # Don't check if empty - we're actively adding food to it!
                    return False
            # Plate position is set but plate doesn't exist or is invalid, clear it
            logger(
                f"Bot {bot_id}: [PLATE CLEAR] Clearing stored plate position {self.current_order.plate_tracker.plate_pos} because no valid plate was found there"
            )
            self.current_order.plate_tracker.plate_pos = None

        # No valid plate position, need to get a plate
        return True

    def get_plate(self, controller: RobotController, bot_id: int):
        """Buy a plate and place it on a counter"""
        bot_state = controller.get_bot_state(bot_id)

        # Get the bot's dedicated plate counter
        plate_counter = self.bot_claimed_locations.get(bot_id, {}).get("plate_counter")
        if plate_counter:
            cx, cy = plate_counter
            # Check if there's already a clean empty plate on our designated counter
            counter_tile = controller.get_tile(controller.get_team(), cx, cy)
            if counter_tile and counter_tile.item:
                counter_item = controller.item_to_public_dict(counter_tile.item)
                if (
                    counter_item
                    and counter_item.get("type") == "Plate"
                    and not counter_item.get("dirty")
                    and len(counter_item.get("food", [])) == 0
                ):
                    # Clean empty plate already exists, just set the position
                    self.current_order.plate_tracker.plate_pos = (cx, cy)
                    logger(
                        f"Bot {bot_id}: [PLATE REUSE] Found existing clean plate at designated counter ({cx}, {cy}), using it instead of buying"
                    )
                    return True

        # If already holding plate, place it on counter
        if bot_state and bot_state.get("holding") is not None:
            held_item = bot_state["holding"]

            if held_item and held_item["type"] == "Plate":
                # Use the bot's dedicated plate counter
                if not plate_counter:
                    logger(f"Bot {bot_id}: No plate counter assigned")
                    return False

                cx, cy = plate_counter
                if self.move_towards(controller, bot_id, cx, cy):
                    if controller.place(bot_id, cx, cy):
                        self.current_order.plate_tracker.plate_pos = (cx, cy)
                        logger(
                            f"Bot {bot_id}: [PLATE PLACE] Placed held plate at dedicated counter ({cx}, {cy}) for safekeeping"
                        )
                        return True
                # Still moving towards counter
                return False
            # TODO: Handle case where bot is holding something else (trash it?)

        # Otherwise, go buy a plate (only if not holding anything)

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
                    logger(
                        f"Bot {bot_id}: [PLATE BUY] Bought new plate at ({sx}, {sy}) because no clean plate was found"
                    )
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

        # If already holding pan, place it on nearest empty cooker
        if bot_state and bot_state.get("holding") is not None:
            held_item = bot_state["holding"]

            if held_item and held_item["type"] == "Pan":

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

                    if self.move_towards(controller, bot_id, cx, cy):
                        if controller.place(bot_id, cx, cy):
                            logger(f"Bot {bot_id}: Placed pan at ({cx}, {cy})")
                            return True
                    # Still moving towards cooker, don't try to buy

                    return False
                else:

                    return False
            # TODO: Handle case where bot is holding something else (trash it?)

        # Otherwise, go buy a pan (only if not holding anything)

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
                    logger(f"Bot {bot_id}: Bought pan at ({sx}, {sy})")
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

                return True

        if self.cooking:
            # Check if it's done cooking and pick it up, otherwise wait.
            (item, x, y) = self.cooking
            if self.move_towards(controller, self.bot_id, x, y):
                stove_tile = controller.get_tile(controller.get_team(), x, y)
                if stove_tile and stove_tile.item:
                    stove_item = controller.item_to_public_dict(stove_tile.item)
                    food = stove_item.get("food")
                    if food and food.get("cooked_stage") >= 1:
                        if controller.take_from_pan(self.bot_id, x, y):
                            self.cooking = None
            return False

        if self.chopping:
            # Check if it's done chopping and pick it up, otherwise wait.
            (item, x, y) = self.chopping
            if self.move_towards(controller, self.bot_id, x, y):
                counter_tile = controller.get_tile(controller.get_team(), x, y)

                if counter_tile and counter_tile.item:
                    counter_item = controller.item_to_public_dict(counter_tile.item)
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
                    logger(
                        f"Bot {self.bot_id}: [PLATE ADD] Placing {ing_name} on plate at ({px}, {py}) to fulfill order"
                    )
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
        Submit the completed order.
        """
        # Log entry to debug submission issues
        logger(
            f"Bot {bot_id}: [SUBMIT START] submit_plate called. Held item: {held_item}"
        )

        # If not holding the plate, pick it up
        if held_item is None:
            if not self.current_order.plate_tracker.plate_pos:
                logger(
                    f"Bot {bot_id}: [SUBMIT ERROR] No plate position tracked, cannot pickup plate"
                )
                return

            px, py = self.current_order.plate_tracker.plate_pos
            logger(
                f"Bot {bot_id}: [SUBMIT MOVE] Moving to plate at ({px}, {py}) to pickup"
            )

            if self.move_towards(controller, bot_id, px, py):
                logger(
                    f"Bot {bot_id}: [SUBMIT PICKUP] Picking up completed plate from ({px}, {py})"
                )
                if controller.pickup(bot_id, px, py):
                    logger(f"Bot {bot_id}: [SUBMIT PICKUP SUCCESS] Picked up plate")
                else:
                    logger(
                        f"Bot {bot_id}: [SUBMIT PICKUP FAIL] Failed to pickup plate at ({px}, {py})"
                    )
        else:
            # We are holding the plate, verify it's the right one
            if held_item.get("type") != "Plate":
                logger(
                    f"Bot {bot_id}: [SUBMIT ERROR] Holding {held_item.get('type')} instead of Plate!"
                )
                return

            # Move to submit location and submit
            submit = self.bot_selected_locations.get(bot_id, {}).get("submit")
            if submit is None:
                # Fallback to first submit if bot_selected_locations not set
                submit = self.submit_pos[0] if self.submit_pos else None
            if submit is None:
                logger(
                    f"Bot {bot_id}: [SUBMIT ERROR] No accessible submit location found"
                )
                return

            sx, sy = submit
            logger(
                f"Bot {bot_id}: [SUBMIT MOVE] Moving to submit location ({sx}, {sy})"
            )

            if self.move_towards(controller, bot_id, sx, sy):
                logger(
                    f"Bot {bot_id}: [SUBMIT ACTION] Attempting to submit at ({sx}, {sy})"
                )
                if controller.submit(bot_id, sx, sy):
                    logger(
                        f"Bot {bot_id}: [SUBMIT SUCCESS] Order submitted at ({sx}, {sy})!"
                    )
                    # Reset current order
                    if self.current_order.order:
                        self._release_order_claim(
                            self.current_order.order["order_id"], bot_id
                        )
                    self.current_order = ActiveOrder()
                    self.bot_orders[bot_id] = self.current_order
                else:
                    logger(
                        f"Bot {bot_id}: [SUBMIT FAIL] Submit failed at ({sx}, {sy}). Check if adjacent or valid submit tile."
                    )

    def bot_turn(self, controller: RobotController, bot_id: int):
        logger(f"Bot {bot_id}: Entering bot_turn")
        bot_state = controller.get_bot_state(bot_id)
        bx, by = bot_state["x"], bot_state["y"]
        holding = bot_state.get("holding")
        with open("tmp/holding.txt", "a") as f:
            f.write(f"Bot is at ({bx}, {by}) and holding {(holding)}\n")

        # TODO: make the bots not interrupt each other

        if not self.initialized:
            self._initialize_locations(controller)
            self.initialized = True

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
            # Save clean plate position BEFORE cleanup/reset
            saved_plate_pos = None
            if (
                self.current_order.plate_tracker
                and self.current_order.plate_tracker.plate_pos
            ):
                px, py = self.current_order.plate_tracker.plate_pos
                plate_tile = controller.get_tile(controller.get_team(), px, py)
                if plate_tile and plate_tile.item:
                    plate = controller.item_to_public_dict(plate_tile.item)
                    if (
                        plate
                        and plate.get("type") == "Plate"
                        and not plate.get("dirty")
                        and len(plate.get("food", [])) == 0
                    ):
                        saved_plate_pos = (px, py)
                        logger(
                            f"Bot {bot_id}: [PLATE SAVE] Saving clean plate position {saved_plate_pos} before cleanup for next order"
                        )

            self._release_order_claim(self.current_order.order["order_id"], bot_id)
            if not self._cleanup_expired_order_ingredients(controller, bot_id):
                return

            # Store plate position for next order
            if bot_id not in self.bot_claimed_locations:
                self.bot_claimed_locations[bot_id] = {}
            self.bot_claimed_locations[bot_id]["saved_plate_pos"] = saved_plate_pos

            self.current_order = ActiveOrder()
            self.bot_orders[bot_id] = self.current_order

        if not self.current_order.order:
            logger(f"Bot {bot_id}: Branch -> get_order")
            self.get_order(controller, bot_id, active_orders)
        elif self.should_get_plate(controller, bot_id):
            logger(f"Bot {bot_id}: Branch -> get_plate")
            self.get_plate(controller, bot_id)
        elif self.should_get_pan(controller):
            logger(f"Bot {bot_id}: Branch -> get_pan")
            self.get_pan(controller, bot_id)
        elif not all(self.current_order.plate_tracker.ing_on_plate):
            logger(f"Bot {bot_id}: Branch -> prep_ings")
            self.prep_ings(controller, holding, bot_id)
        else:
            logger(f"Bot {bot_id}: Branch -> submit_plate")
            self.submit_plate(controller, holding, bot_id)

        self.bot_cooking[bot_id] = self.cooking
        self.bot_chopping[bot_id] = self.chopping

    def play_turn(self, controller: RobotController):
        self.turn_map = controller.get_map(controller.get_team())
        bots = controller.get_team_bot_ids(controller.get_team())
        for bot_id in bots[:2]:
            self.bot_turn(controller, bot_id)
