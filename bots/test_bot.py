"""
test_bot.py - Simple 2-Bot Collaborative System

Bot 0 (Chef): Handles cooking, assembly, and submission
Bot 1 (Prep): Handles shopping, chopping, and staging
"""

from collections import deque
from typing import Tuple, Optional, Dict, List, Any

from game_constants import Team, FoodType, ShopCosts
from robot_controller import RobotController
from item import Food, Plate, Pan


class BotPlayer:
    def __init__(self, map_copy):
        self.map = map_copy

        # Cached tile locations
        self.assembly_counter = None    # Chef's workspace
        self.staging_counter = None     # Prep drops ingredients here
        self.chopping_counter = None    # Prep uses this for chopping
        self.cooker_pos = None          # Single cooker for simplicity
        self.shop_pos = None
        self.submit_pos = None
        self.trash_pos = None
        self.all_counters = []          # List of all counter positions

        # Shared state between bots
        self.current_order = None
        self.staged_ingredients = {}    # {food_name: (x, y)}
        self.cooking_timers = {}        # {cooker_pos: start_turn}

        # Bot-specific states
        self.chef_state = "INIT"
        self.prep_state = "INIT"

        # Prep's shopping list
        self.shopping_list = []
        self.plate_bought = False

        # Chef's held items tracking
        self.chef_plate_pos = None      # Where chef put plate down
        self.cookable_food = None       # Which food needs cooking
        self.non_cookable_foods = []    # Foods to add directly to plate

    def _initialize_locations(self, controller: RobotController):
        """Find and cache important tile locations once"""
        if self.shop_pos is not None:
            return  # Already initialized

        m = controller.get_map()

        # Find all important tiles
        for x in range(m.width):
            for y in range(m.height):
                tile = m.tiles[x][y]
                tile_name = tile.tile_name

                if tile_name == "SHOP" and self.shop_pos is None:
                    self.shop_pos = (x, y)
                elif tile_name == "SUBMIT" and self.submit_pos is None:
                    self.submit_pos = (x, y)
                elif tile_name == "TRASH" and self.trash_pos is None:
                    self.trash_pos = (x, y)
                elif tile_name == "COOKER" and self.cooker_pos is None:
                    self.cooker_pos = (x, y)
                elif tile_name == "COUNTER":
                    self.all_counters.append((x, y))

        # Assign counters to different purposes
        if len(self.all_counters) >= 3:
            self.chopping_counter = self.all_counters[0]
            self.staging_counter = self.all_counters[1]
            self.assembly_counter = self.all_counters[2]
        elif len(self.all_counters) >= 2:
            self.chopping_counter = self.all_counters[0]
            self.staging_counter = self.all_counters[1]
            self.assembly_counter = self.all_counters[1]  # Reuse staging for assembly
        elif len(self.all_counters) >= 1:
            # Fallback: use same counter for all (will have issues)
            self.chopping_counter = self.all_counters[0]
            self.staging_counter = self.all_counters[0]
            self.assembly_counter = self.all_counters[0]

        # Debug: log counter assignments
        with open('/tmp/counter_debug.txt', 'w') as f:
            f.write(f"All counters found: {self.all_counters}\n")
            f.write(f"Chopping counter: {self.chopping_counter}\n")
            f.write(f"Staging counter: {self.staging_counter}\n")
            f.write(f"Assembly counter: {self.assembly_counter}\n")

    def play_turn(self, controller: RobotController):
        """Main entry point called each turn"""
        self._initialize_locations(controller)

        bots = controller.get_team_bot_ids(controller.get_team())

        # Debug to file on turns 0, 50, 100
        if controller.get_turn() in [0, 50, 100, 200]:
            with open(f'/tmp/test_bot_debug_turn_{controller.get_turn()}.txt', 'w') as f:
                f.write(f"Turn: {controller.get_turn()}\n")
                f.write(f"Bots: {bots} (count: {len(bots)})\n")
                f.write(f"Chef state: {self.chef_state}\n")
                f.write(f"Prep state: {self.prep_state}\n")
                f.write(f"Current order: {self.current_order}\n")
                f.write(f"Staged ingredients: {self.staged_ingredients}\n")
                f.write(f"Shopping list: {self.shopping_list}\n")
                f.write(f"\nLocations:\n")
                f.write(f"  shop_pos: {self.shop_pos}\n")
                f.write(f"  cooker_pos: {self.cooker_pos}\n")
                f.write(f"  assembly_counter: {self.assembly_counter}\n")
                f.write(f"  staging_counter: {self.staging_counter}\n")
                f.write(f"  submit_pos: {self.submit_pos}\n")

        if len(bots) == 0:
            return  # No bots

        if len(bots) >= 2:
            # Bot 0 is Chef, Bot 1 is Prep
            self.chef_turn(controller, bots[0])
            self.prep_turn(controller, bots[1])
        else:
            # Single bot fallback - just do prep work
            self.prep_turn(controller, bots[0])

    # ==================== PATHFINDING HELPERS ====================

    def get_bfs_path(self, controller: RobotController, start: Tuple[int, int],
                     target_predicate) -> Optional[Tuple[int, int]]:
        """BFS pathfinding - returns first step (dx, dy) or None"""
        queue = deque([(start, [])])
        visited = set([start])
        w, h = self.map.width, self.map.height

        # Get all bot positions to avoid collisions
        bot_positions = set()
        for bot_id in controller.get_team_bot_ids(controller.get_team()):
            bot = controller.get_bot_state(bot_id)
            if bot:
                bot_positions.add((bot['x'], bot['y']))

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
                        if controller.get_map().is_tile_walkable(nx, ny) and (nx, ny) not in bot_positions:
                            visited.add((nx, ny))
                            queue.append(((nx, ny), path + [(dx, dy)]))

        return None

    def move_towards(self, controller: RobotController, bot_id: int,
                     target_x: int, target_y: int) -> bool:
        """Move bot toward target. Returns True if adjacent, False otherwise"""
        bot_state = controller.get_bot_state(bot_id)
        if not bot_state:
            return False

        bx, by = bot_state['x'], bot_state['y']

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

    # ==================== ORDER MANAGEMENT ====================

    def get_food_type_by_name(self, food_name: str) -> Optional[FoodType]:
        """Convert food name string to FoodType enum"""
        food_map = {
            "EGG": FoodType.EGG,
            "ONIONS": FoodType.ONIONS,
            "MEAT": FoodType.MEAT,
            "NOODLES": FoodType.NOODLES,
            "SAUCE": FoodType.SAUCE,
        }
        return food_map.get(food_name)

    def create_shopping_list(self, order: Dict) -> List:
        """Create shopping list from order requirements"""
        shopping_list = []

        # Add plate first (if not already bought)
        if not self.plate_bought:
            shopping_list.append(ShopCosts.PLATE)

        # Add each required food
        for food_name in order['required']:
            food_type = self.get_food_type_by_name(food_name)
            if food_type:
                shopping_list.append(food_type)

        return shopping_list

    def categorize_ingredients(self, order: Dict):
        """Separate cookable vs non-cookable ingredients"""
        self.cookable_food = None
        self.non_cookable_foods = []

        for food_name in order['required']:
            food_type = self.get_food_type_by_name(food_name)
            if food_type:
                if food_type.can_cook and self.cookable_food is None:
                    self.cookable_food = food_name
                else:
                    self.non_cookable_foods.append(food_name)

    def all_ingredients_ready(self) -> bool:
        """Check if all ingredients are staged"""
        if not self.current_order:
            return False

        required = set(self.current_order['required'])
        staged = set(self.staged_ingredients.keys())

        return required.issubset(staged)

    # ==================== COOKING TIMER MANAGEMENT ====================

    def is_cooking_done(self, controller: RobotController) -> bool:
        """Check if food has cooked for 20+ turns"""
        if not self.cooking_timers:
            return False

        current_turn = controller.get_turn()
        for cooker_pos, start_turn in self.cooking_timers.items():
            elapsed = current_turn - start_turn
            if elapsed >= 20:
                return True
        return False

    def is_burning_soon(self, controller: RobotController) -> bool:
        """Check if food will burn soon (38+ turns)"""
        if not self.cooking_timers:
            return False

        current_turn = controller.get_turn()
        for cooker_pos, start_turn in self.cooking_timers.items():
            elapsed = current_turn - start_turn
            if elapsed >= 38:
                return True
        return False

    # ==================== CHEF BOT (Bot 0) ====================

    def chef_turn(self, controller: RobotController, bot_id: int):
        """Chef handles cooking, assembly, and submission"""
        bot_state = controller.get_bot_state(bot_id)
        bx, by = bot_state['x'], bot_state['y']
        holding = bot_state.get('holding')

        # Safety check - ensure locations are initialized
        if not self.shop_pos or not self.cooker_pos:
            return

        if self.chef_state == "INIT":
            # Check if there's already a pan on the cooker (Prep bought it)
            tile = controller.get_tile(controller.get_team(), *self.cooker_pos)
            if tile and isinstance(tile.item, Pan):
                self.chef_state = "WAITING"
            else:
                # Just wait for Prep to buy and place the pan
                pass

        elif self.chef_state == "WAITING":
            # Once PLATE is staged, categorize ingredients and start workflow
            # Don't wait for all ingredients since counter can only hold one item
            if "PLATE" in self.staged_ingredients:
                self.categorize_ingredients(self.current_order)
                # Pick up plate first to free the counter, OR
                # If there's cookable food and all ingredients are ready, cook first
                if self.all_ingredients_ready() and self.cookable_food:
                    # All ingredients ready, cook first
                    self.chef_state = "GET_COOKABLE"
                else:
                    # Get plate to free the counter for Prep
                    self.chef_state = "GET_PLATE"

        elif self.chef_state == "GET_PLATE":
            # Pickup plate from staging
            if not holding:
                # Find the plate at staging counter
                if self.move_towards(controller, bot_id, *self.staging_counter):
                    # Try to pickup plate
                    tile = controller.get_tile(controller.get_team(), *self.staging_counter)
                    if tile and isinstance(tile.item, Plate):
                        if controller.pickup(bot_id, *self.staging_counter):
                            # Remove plate from staged ingredients
                            if "PLATE" in self.staged_ingredients:
                                del self.staged_ingredients["PLATE"]
                            self.chef_state = "ASSEMBLE_NON_COOKED"
            else:
                # Already holding plate
                self.chef_state = "ASSEMBLE_NON_COOKED"

        elif self.chef_state == "ASSEMBLE_NON_COOKED":
            # Add non-cookable ingredients to plate
            if controller.get_turn() <= 50:
                with open('/tmp/chef_debug.txt', 'a') as f:
                    f.write(f"Turn {controller.get_turn()}: ASSEMBLE_NON_COOKED\n")
                    f.write(f"  non_cookable_foods: {self.non_cookable_foods}\n")
                    f.write(f"  cookable_food: {self.cookable_food}\n")
                    f.write(f"  staged_ingredients: {self.staged_ingredients}\n")
                    f.write(f"  all_ingredients_ready: {self.all_ingredients_ready()}\n")

            if self.non_cookable_foods:
                food_name = self.non_cookable_foods[0]
                if food_name in self.staged_ingredients:
                    food_pos = self.staged_ingredients[food_name]

                    if self.move_towards(controller, bot_id, *food_pos):
                        if controller.add_food_to_plate(bot_id, *food_pos):
                            # Successfully added
                            self.non_cookable_foods.pop(0)
                            del self.staged_ingredients[food_name]
                            if controller.get_turn() <= 50:
                                with open('/tmp/chef_debug.txt', 'a') as f:
                                    f.write(f"  Successfully added {food_name} to plate\n")
                else:
                    # Food not staged yet, wait
                    if controller.get_turn() <= 50:
                        with open('/tmp/chef_debug.txt', 'a') as f:
                            f.write(f"  Waiting for {food_name} to be staged\n")
            else:
                # All non-cooked items added
                # Now add cooked food if it's staged
                if self.cookable_food and self.cookable_food in self.staged_ingredients:
                    # Cooked food is ready, add it to plate
                    food_pos = self.staged_ingredients[self.cookable_food]
                    if self.move_towards(controller, bot_id, *food_pos):
                        if controller.add_food_to_plate(bot_id, *food_pos):
                            del self.staged_ingredients[self.cookable_food]
                            # All done, submit
                            self.chef_state = "SUBMIT"
                elif self.cookable_food:
                    # Cookable food not staged yet (shouldn't happen in new flow, but just in case)
                    self.chef_state = "PLACE_PLATE"
                else:
                    # No cooking needed, submit directly
                    self.chef_state = "SUBMIT"

        elif self.chef_state == "PLACE_PLATE":
            # With only one counter, keep holding the plate instead of placing it
            # This frees the counter for Prep to chop ingredients
            # We'll place the plate later when we need to pick up cooked food
            self.chef_state = "WAIT_FOR_CHOPPING"

        elif self.chef_state == "WAIT_FOR_CHOPPING":
            # Wait for Prep to chop and stage the cookable ingredient
            # Chef is holding the plate during this time
            if self.cookable_food and self.cookable_food in self.staged_ingredients:
                # Food is staged on the counter. Place plate on COOKER tile temporarily
                # (The cooker has a pan on it, not the food itself)
                if self.move_towards(controller, bot_id, *self.cooker_pos):
                    # Try to place plate on cooker - this might swap with the pan
                    # We'll handle that later
                    controller.place(bot_id, *self.cooker_pos)
                    # Regardless of success, transition to get the food
                    self.chef_state = "GET_COOKABLE"
            # else: keep waiting with plate in hand

        elif self.chef_state == "GET_COOKABLE":
            # Pickup the cookable ingredient
            if not holding and self.cookable_food:
                if self.cookable_food in self.staged_ingredients:
                    food_pos = self.staged_ingredients[self.cookable_food]

                    if self.move_towards(controller, bot_id, *food_pos):
                        if controller.pickup(bot_id, *food_pos):
                            del self.staged_ingredients[self.cookable_food]
                            self.chef_state = "START_COOK"

        elif self.chef_state == "START_COOK":
            # Place food on cooker to start cooking
            if self.move_towards(controller, bot_id, *self.cooker_pos):
                if controller.place(bot_id, *self.cooker_pos):
                    # Record cooking start time
                    self.cooking_timers[self.cooker_pos] = controller.get_turn()
                    # Remove from staged ingredients
                    if self.cookable_food in self.staged_ingredients:
                        del self.staged_ingredients[self.cookable_food]
                    self.chef_state = "WAIT_COOK"

        elif self.chef_state == "WAIT_COOK":
            # Wait for cooking to complete
            if self.is_cooking_done(controller):
                self.chef_state = "RETRIEVE_COOKED"
            elif self.is_burning_soon(controller):
                # Emergency retrieve to avoid burning
                self.chef_state = "RETRIEVE_COOKED"

        elif self.chef_state == "RETRIEVE_COOKED":
            # Take cooked food from pan
            if not holding:
                if self.move_towards(controller, bot_id, *self.cooker_pos):
                    if controller.take_from_pan(bot_id, *self.cooker_pos):
                        # Clear cooking timer
                        if self.cooker_pos in self.cooking_timers:
                            del self.cooking_timers[self.cooker_pos]
                        self.chef_state = "STAGE_COOKED_FOOD"

        elif self.chef_state == "STAGE_COOKED_FOOD":
            # Place cooked food on counter temporarily
            if holding:
                if self.move_towards(controller, bot_id, *self.assembly_counter):
                    if controller.place(bot_id, *self.assembly_counter):
                        # Add to staged ingredients so we can add it to plate later
                        if self.cookable_food:
                            self.staged_ingredients[self.cookable_food] = self.assembly_counter
                        self.chef_state = "GET_PLATE"
            else:
                # Not holding anything, go get plate
                self.chef_state = "GET_PLATE"

        elif self.chef_state == "GET_PLATE_BACK":
            # Pickup the plate we left on counter
            if holding:
                # Need to put down cooked food first
                if self.move_towards(controller, bot_id, *self.assembly_counter):
                    # Place cooked food on counter temporarily
                    if controller.place(bot_id, *self.assembly_counter):
                        self.chef_state = "GET_PLATE_BACK"
            else:
                # Pickup plate
                if self.chef_plate_pos:
                    if self.move_towards(controller, bot_id, *self.chef_plate_pos):
                        tile = controller.get_tile(controller.get_team(), *self.chef_plate_pos)
                        if tile and isinstance(tile.item, Plate):
                            if controller.pickup(bot_id, *self.chef_plate_pos):
                                self.chef_state = "ADD_COOKED"

        elif self.chef_state == "ADD_COOKED":
            # Add cooked food to plate
            if self.move_towards(controller, bot_id, *self.assembly_counter):
                tile = controller.get_tile(controller.get_team(), *self.assembly_counter)
                if tile and isinstance(tile.item, Food):
                    if controller.add_food_to_plate(bot_id, *self.assembly_counter):
                        self.chef_state = "SUBMIT"

        elif self.chef_state == "SUBMIT":
            # Submit the completed order
            if holding and isinstance(holding, dict) and holding.get('type') == 'Plate':
                if self.move_towards(controller, bot_id, *self.submit_pos):
                    if controller.submit(bot_id, *self.submit_pos):
                        # Order completed!
                        self.current_order = None
                        self.cookable_food = None
                        self.non_cookable_foods = []
                        self.chef_plate_pos = None
                        self.chef_state = "WAITING"

    # ==================== PREP BOT (Bot 1) ====================

    def prep_turn(self, controller: RobotController, bot_id: int):
        """Prep handles shopping, chopping, and staging"""
        bot_state = controller.get_bot_state(bot_id)
        bx, by = bot_state['x'], bot_state['y']
        holding = bot_state.get('holding')

        # Log every turn
        if controller.get_turn() <= 100:
            with open('/tmp/prep_debug.txt', 'a') as f:
                f.write(f"Turn {controller.get_turn()}: prep_turn bot={bot_id}, state={self.prep_state}, holding={type(holding).__name__ if holding else None}\n")

        # Safety check - ensure locations are initialized
        if not self.shop_pos or not self.staging_counter:
            return

        if self.prep_state == "INIT":
            # Check if cooker already has a pan
            tile = controller.get_tile(controller.get_team(), *self.cooker_pos)
            if tile and isinstance(tile.item, Pan):
                if controller.get_turn() <= 100:
                    with open('/tmp/prep_debug.txt', 'a') as f:
                        f.write(f"Turn {controller.get_turn()}: INIT - Cooker already has Pan, skipping to SELECT_ORDER\n")
                self.prep_state = "SELECT_ORDER"
            else:
                # Cooker doesn't have a pan, buy one
                if controller.get_turn() <= 100:
                    with open('/tmp/prep_debug.txt', 'a') as f:
                        f.write(f"Turn {controller.get_turn()}: INIT state, holding={holding}\n")

                if not holding:
                    if self.move_towards(controller, bot_id, *self.shop_pos):
                        if controller.get_team_money(controller.get_team()) >= ShopCosts.PAN.buy_cost:
                            if controller.buy(bot_id, ShopCosts.PAN, *self.shop_pos):
                                if controller.get_turn() <= 100:
                                    with open('/tmp/prep_debug.txt', 'a') as f:
                                        f.write(f"  Bought PAN\n")
                                self.prep_state = "INIT_PLACE_PAN"
                else:
                    if controller.get_turn() <= 100:
                        with open('/tmp/prep_debug.txt', 'a') as f:
                            f.write(f"  Already holding something, skip to INIT_PLACE_PAN\n")
                    self.prep_state = "INIT_PLACE_PAN"

        elif self.prep_state == "INIT_PLACE_PAN":
            # Place pan on cooker for Chef
            if controller.get_turn() <= 100:
                with open('/tmp/prep_debug.txt', 'a') as f:
                    f.write(f"Turn {controller.get_turn()}: INIT_PLACE_PAN, holding={holding}\n")

            if holding:
                if self.move_towards(controller, bot_id, *self.cooker_pos):
                    # Check what's on the cooker before placing
                    tile = controller.get_tile(controller.get_team(), *self.cooker_pos)
                    cooker_item = tile.item if tile else None

                    if controller.get_turn() <= 100:
                        with open('/tmp/prep_debug.txt', 'a') as f:
                            f.write(f"  Cooker already has: {cooker_item}\n")

                    success = controller.place(bot_id, *self.cooker_pos)

                    # Check what bot is holding after place
                    new_state = controller.get_bot_state(bot_id)
                    new_holding = new_state.get('holding')

                    if controller.get_turn() <= 100:
                        with open('/tmp/prep_debug.txt', 'a') as f:
                            f.write(f"  Place result: {success}\n")
                            f.write(f"  After place, holding: {new_holding}\n")

                    # Only transition if bot successfully placed and is no longer holding anything
                    if success and not new_holding:
                        self.prep_state = "SELECT_ORDER"
                    elif new_holding:
                        # Bot swapped items, still holding something - drop it somewhere
                        if controller.get_turn() <= 100:
                            with open('/tmp/prep_debug.txt', 'a') as f:
                                f.write(f"  Bot still holding after place, need to drop it\n")
                        # Try to place on staging counter
                        if self.move_towards(controller, bot_id, *self.staging_counter):
                            controller.place(bot_id, *self.staging_counter)
            else:
                self.prep_state = "SELECT_ORDER"

        elif self.prep_state == "SELECT_ORDER":
            # Pick first available active order
            orders = controller.get_orders()
            active_orders = [o for o in orders if o['is_active']]

            if active_orders and not self.current_order:
                self.current_order = active_orders[0]
                self.shopping_list = self.create_shopping_list(self.current_order)

                if controller.get_turn() <= 100:
                    with open('/tmp/prep_debug.txt', 'a') as f:
                        f.write(f"Turn {controller.get_turn()}: SELECT_ORDER\n")
                        f.write(f"  Order: {self.current_order['required']}\n")
                        f.write(f"  Shopping list: {self.shopping_list}\n")

                self.prep_state = "BUY_INGREDIENT"

        elif self.prep_state == "BUY_INGREDIENT":
            # Buy next item from shopping list
            if holding:
                # Bot is holding something - shouldn't happen, but handle it
                if controller.get_turn() % 20 == 0:  # Log occasionally
                    with open('/tmp/prep_debug.txt', 'a') as f:
                        f.write(f"Turn {controller.get_turn()}: ERROR - Prep holding {holding} when trying to buy\n")
                self.prep_state = "PROCESS_INGREDIENT"
                return

            if not self.shopping_list:
                # Shopping complete
                self.prep_state = "COMPLETE"
                return

            item = self.shopping_list[0]

            # Debug logging every turn for first 30 turns
            bot_state = controller.get_bot_state(bot_id)
            if controller.get_turn() <= 100:
                with open('/tmp/prep_debug.txt', 'a') as f:
                    f.write(f"Turn {controller.get_turn()}: Bot at ({bot_state['x']}, {bot_state['y']}), ")
                    f.write(f"Distance to shop {self.shop_pos}: {max(abs(bot_state['x'] - self.shop_pos[0]), abs(bot_state['y'] - self.shop_pos[1]))}\n")

            # Move to shop and buy
            is_adjacent = self.move_towards(controller, bot_id, *self.shop_pos)

            if controller.get_turn() <= 100:
                with open('/tmp/prep_debug.txt', 'a') as f:
                    f.write(f"  -> After move_towards: is_adjacent={is_adjacent}\n")

            if is_adjacent:
                # Check if we have enough money
                cost = item.buy_cost
                money = controller.get_team_money(controller.get_team())

                if controller.get_turn() <= 100:
                    with open('/tmp/prep_debug.txt', 'a') as f:
                        f.write(f"  Adjacent! Money: {money}, Cost: {cost}, Enough: {money >= cost}\n")

                if money >= cost:
                    success = controller.buy(bot_id, item, *self.shop_pos)

                    if controller.get_turn() <= 100:
                        with open('/tmp/prep_debug.txt', 'a') as f:
                            f.write(f"  Buy result: {success}\n")
                            if success:
                                f.write(f"  SUCCESS! Bought {item}, moving to PROCESS_INGREDIENT\n")

                    if success:
                        self.shopping_list.pop(0)

                        # Mark plate as bought
                        if item == ShopCosts.PLATE:
                            self.plate_bought = True

                        self.prep_state = "PROCESS_INGREDIENT"
                else:
                    if controller.get_turn() <= 100:
                        with open('/tmp/prep_debug.txt', 'a') as f:
                            f.write(f"  Not enough money! Have {money}, need {cost}\n")

        elif self.prep_state == "PROCESS_INGREDIENT":
            # Chop if needed, otherwise stage directly
            if controller.get_turn() <= 100:
                with open('/tmp/prep_debug.txt', 'a') as f:
                    f.write(f"Turn {controller.get_turn()}: In PROCESS_INGREDIENT, holding={holding}\n")

            if holding:
                # Check if it's food that needs chopping
                # holding is a dict with fields like {'type': 'Food', 'food_name': 'MEAT', 'chopped': False, ...}
                is_food = isinstance(holding, dict) and holding.get('type') == 'Food'
                if is_food:
                    food_name = holding.get('food_name')
                    chopped = holding.get('chopped', False)
                    # Check if this food type can be chopped
                    food_type = self.get_food_type_by_name(food_name)
                    can_chop = food_type.can_chop if food_type else False

                    if can_chop and not chopped:
                        if controller.get_turn() <= 100:
                            with open('/tmp/prep_debug.txt', 'a') as f:
                                f.write(f"  -> Needs chopping, going to CHOP_INGREDIENT\n")
                        self.prep_state = "CHOP_INGREDIENT"
                    else:
                        if controller.get_turn() <= 100:
                            with open('/tmp/prep_debug.txt', 'a') as f:
                                f.write(f"  -> No chopping needed, going to STAGE_INGREDIENT\n")
                        self.prep_state = "STAGE_INGREDIENT"
                else:
                    # Not food (e.g., Plate), go directly to staging
                    if controller.get_turn() <= 100:
                        with open('/tmp/prep_debug.txt', 'a') as f:
                            f.write(f"  -> No chopping needed, going to STAGE_INGREDIENT\n")
                    self.prep_state = "STAGE_INGREDIENT"
            else:
                if controller.get_turn() <= 100:
                    with open('/tmp/prep_debug.txt', 'a') as f:
                        f.write(f"  -> Not holding anything! Going back to BUY_INGREDIENT\n")
                # Nothing to process
                self.prep_state = "BUY_INGREDIENT"

        elif self.prep_state == "CHOP_INGREDIENT":
            # Place on chopping counter and chop
            if holding:
                # Move to chopping counter
                if self.move_towards(controller, bot_id, *self.chopping_counter):
                    # Check if counter is empty first
                    tile = controller.get_tile(controller.get_team(), *self.chopping_counter)
                    if tile and tile.item is None:
                        # Counter is empty, place item
                        if controller.place(bot_id, *self.chopping_counter):
                            # Now chop it
                            self.prep_state = "DO_CHOP"
                    # else: wait for counter to be empty
            else:
                self.prep_state = "BUY_INGREDIENT"

        elif self.prep_state == "DO_CHOP":
            # Perform the chop action
            if self.move_towards(controller, bot_id, *self.chopping_counter):
                if controller.chop(bot_id, *self.chopping_counter):
                    self.prep_state = "PICKUP_CHOPPED"

        elif self.prep_state == "PICKUP_CHOPPED":
            # Pickup the chopped ingredient
            if not holding:
                if self.move_towards(controller, bot_id, *self.chopping_counter):
                    if controller.pickup(bot_id, *self.chopping_counter):
                        self.prep_state = "STAGE_INGREDIENT"

        elif self.prep_state == "STAGE_INGREDIENT":
            # Place ingredient at staging box for Chef
            if controller.get_turn() <= 100:
                with open('/tmp/prep_debug.txt', 'a') as f:
                    f.write(f"Turn {controller.get_turn()}: In STAGE_INGREDIENT, holding={holding}\n")

            if holding:
                is_adjacent = self.move_towards(controller, bot_id, *self.staging_counter)

                if controller.get_turn() <= 100:
                    bot_state = controller.get_bot_state(bot_id)
                    with open('/tmp/prep_debug.txt', 'a') as f:
                        f.write(f"  Bot at ({bot_state['x']}, {bot_state['y']}), staging_counter at {self.staging_counter}\n")
                        f.write(f"  Is adjacent: {is_adjacent}\n")

                if is_adjacent:
                    # Check if counter is empty first
                    tile = controller.get_tile(controller.get_team(), *self.staging_counter)
                    counter_has_item = tile and tile.item is not None

                    if controller.get_turn() <= 100:
                        with open('/tmp/prep_debug.txt', 'a') as f:
                            f.write(f"  Counter has item: {counter_has_item}\n")

                    if counter_has_item:
                        # Counter is full, wait for Chef to pick it up
                        if controller.get_turn() <= 100:
                            with open('/tmp/prep_debug.txt', 'a') as f:
                                f.write(f"  Counter full, waiting...\n")
                        return  # Wait this turn

                    success = controller.place(bot_id, *self.staging_counter)

                    if controller.get_turn() <= 100:
                        with open('/tmp/prep_debug.txt', 'a') as f:
                            f.write(f"  Place result: {success}\n")

                    if success:
                        # Record what was staged
                        # holding is a dict with 'type' field
                        if isinstance(holding, dict):
                            item_type = holding.get('type')
                            if item_type == 'Food':
                                food_name = holding.get('food_name')
                                self.staged_ingredients[food_name] = self.staging_counter
                                if controller.get_turn() <= 100:
                                    with open('/tmp/prep_debug.txt', 'a') as f:
                                        f.write(f"  Staged food: {food_name}\n")
                            elif item_type == 'Plate':
                                self.staged_ingredients["PLATE"] = self.staging_counter
                                if controller.get_turn() <= 100:
                                    with open('/tmp/prep_debug.txt', 'a') as f:
                                        f.write(f"  Staged PLATE\n")

                        self.prep_state = "BUY_INGREDIENT"
            else:
                self.prep_state = "BUY_INGREDIENT"

        elif self.prep_state == "COMPLETE":
            # All ingredients staged, wait for Chef to finish
            # Check if Chef has submitted and we need a new order
            if not self.current_order:
                self.plate_bought = False
                self.staged_ingredients = {}
                self.prep_state = "SELECT_ORDER"
