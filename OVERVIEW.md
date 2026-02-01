# Carnegie Cookoff - Game Overview & Strategy Guide

## Game Concept

Carnegie Cookoff is a competitive cooking simulation where two teams (RED and BLUE) control bot chefs in separate kitchens. Each team must fulfill orders by buying ingredients, processing food, and submitting completed dishes while managing limited resources. Teams can also sabotage each other mid-game.

**Victory Condition**: The team with the most coins at the end of 500 turns wins.

---

## Core Game Mechanics

### Resource Management
- **Starting Money**: 150 coins per team
- **Passive Income**: 1 coin per turn
- **Time Limit**: 500 turns (0.5 seconds per turn execution)

### Bot Actions (Per Turn)
Each bot can perform:
1. **One movement** (to any adjacent cell, including diagonals - Chebyshev distance 1)
2. **One action** (buy, chop, cook, pickup, place, submit, etc.)

Actions must target tiles within Chebyshev distance 1 of the bot.

### The Kitchen Map

**Tile Types:**
- `.` **Floor**: Walkable space
- `#` **Wall**: Blocks movement
- `C` **Counter**: Place items, chop ingredients
- `K` **Cooker**: Cook food (requires Pan)
- `S` **Sink**: Wash dirty plates
- `T` **SinkTable**: Retrieve clean plates
- `R` **Trash**: Dispose of unwanted items
- `U` **Submit**: Submit completed orders
- `$` **Shop**: Purchase ingredients and equipment
- `B` **Box**: Store multiple items of the same type
- `b` **Bot Spawn**: Starting position for both teams

---

## Ingredients & Items

### Ingredients (Food Types)

| Ingredient | ID | Cost | Choppable | Cookable | Notes |
|-----------|-----|------|-----------|----------|-------|
| Egg | 0 | 20 | No | Yes | Cheapest cookable |
| Onion | 1 | 30 | Yes | No | Cheap prep ingredient |
| Meat | 2 | 80 | Yes | Yes | Expensive, both processable |
| Noodles | 3 | 40 | No | No | Medium cost, ready-to-use |
| Sauce | 4 | 10 | No | No | Cheapest ingredient |

### Items (Equipment)

| Item | Cost | Purpose |
|------|------|---------|
| Plate | 2 | Hold multiple food items for submission |
| Pan | 4 | Required for cooking on Cooker tiles |

---

## Food Processing

### Chopping
- Required for: Onions, Meat
- Location: Counter tile
- Process: `chop(bot_id, target_x, target_y)`
- State change: `food.chopped = True`

### Cooking
- Required for: Egg, Meat
- Location: Cooker tile with Pan
- **Cooking Stages**:
  - Stage 0: Raw (0-19 ticks)
  - Stage 1: **Cooked** (20-39 ticks) ✓ Ready to submit
  - Stage 2: **Burnt** (40+ ticks) ✗ Must trash
- **IMPORTANT**: Cooking progresses automatically at 1 tick per turn
- **Critical Detail**: When you `place()` food on a pan at a cooker, cooking starts automatically

---

## Order System

### Order Structure
From the example map (`map1.txt`):
```
start=0  duration=200  required=NOODLES,MEAT  reward=10000 penalty=3
```

**Order Fields:**
- `order_id`: Unique identifier
- `required`: List of food types needed
- `created_turn`: When order appears
- `expires_turn`: Deadline (created_turn + duration)
- `reward`: Money earned on successful submission
- `penalty`: Money lost if order expires
- `claimed_by`: Bot ID that claimed it
- `completed_turn`: When it was fulfilled

### Order Fulfillment Rules

Orders specify required food types. The submission must match:
1. **Food Type**: Correct ingredient (by `food_id`)
2. **Processing**:
   - If ingredient `can_chop`, it must be chopped
   - If ingredient `can_cook`, it must be cooked to stage 1 (not burnt!)

**Example**: Order requires `NOODLES,MEAT`
- NOODLES: No processing needed (can_chop=False, can_cook=False)
- MEAT: Must be chopped AND cooked to stage 1 (can_chop=True, can_cook=True)

---

## Plate Management

### Clean Plates
- Purchase from Shop (2 coins) OR
- Retrieve from SinkTable after washing

### Assembling Dishes
1. Get clean plate
2. Add food items using `add_food_to_plate(bot_id, target_x, target_y)`
   - Can add food from counter/cooker to held plate
   - Can add held food to plate on counter

### Dirty Plates
- After submission, plates become dirty
- Process: Place in Sink → Wash (2 ticks) → Appears at SinkTable

---

## Sabotage Mechanics

### Switch System
- **Activation Turn**: 250 (default, specified in map)
- **Duration**: 100 turns (turns 250-349)
- **Usage**: One-time switch per team via `switch_maps()`
- **Effect**: Teleports all bots to enemy kitchen

### Sabotage Tactics
- Trash enemy's cooking food (intercept cooked items)
- Trash their prepared plates
- Disrupt their workflow
- Steal items from their boxes

---

## Codebase Structure

### Core Files

**`src/game.py`**
- Main entry point
- Runs the game loop
- Handles both teams' turns

**`src/game_state.py`**
- Maintains global game state
- Tracks bots, money, orders, maps
- Order matching logic

**`src/robot_controller.py`**
- **THE API YOUR BOT USES**
- Enforces turn rules (1 move + 1 action)
- Validates all actions
- Provides state access methods

**`src/game_constants.py`**
- Game configuration
- Food types, tile types, costs
- Turn limits: 500 total, switch at 250

**`src/item.py`**
- Food, Plate, Pan classes
- Food states: chopped, cooked_stage

**`src/tiles.py`**
- Tile class definitions
- Tile-specific logic

**`src/map.py`**
- Map representation
- Pathfinding utilities

### Your Bot Structure

```python
class BotPlayer:
    def __init__(self, map_copy):
        # Initialize your strategy
        pass

    def play_turn(self, controller: RobotController):
        # Called every turn
        # Control all your bots
        pass
```

---

## Strategic Considerations

### 1. **Order Selection & Prioritization**

**Current Bot Weakness**: Only makes one dish (noodles + cooked meat)

**Strategic Approaches**:
- **Greedy**: Always pursue highest reward-to-cost ratio
- **Deadline-Aware**: Prioritize orders close to expiring
- **Balanced**: Mix high-value and quick orders

**Order Profitability Analysis**:
- Calculate: (reward - ingredient_cost - item_cost) / time_required
- Consider cooking time (20 ticks minimum for cooked foods)
- Account for preparation steps (chopping, plate assembly)

### 2. **Multi-Bot Coordination**

**Current Bot Weakness**: Only first bot works; others wander randomly

**Coordination Strategies**:

**Role Specialization**:
- **Chef Bot**: Focus on cooking (manage cookers)
- **Prep Bot**: Handle chopping, plate assembly
- **Runner Bot**: Buy ingredients, submit orders

**Pipeline Architecture**:
- Bot 1: Buy ingredients → Place on counter
- Bot 2: Chop/Cook → Prepare food
- Bot 3: Assemble plates → Submit

**Parallel Production**:
- Multiple bots working on different orders simultaneously
- Requires careful resource management

### 3. **Resource Management**

**Cash Flow Optimization**:
- **Early Game** (Turns 0-100):
  - Focus on cheap orders (Egg-based, Sauce-based)
  - Build capital before expensive orders
  - Buy equipment: at least 1 Pan, 2-3 Plates

- **Mid Game** (Turns 100-250):
  - Pursue high-value orders
  - Maintain ingredient buffer
  - Prepare for sabotage phase

- **Late Game** (Turns 250-500):
  - Balance offense (sabotage) vs defense (production)
  - Rush remaining orders before expiration

**Inventory Management**:
- Use Boxes to store bulk ingredients
- Keep backup ingredients for common orders
- Pre-chop ingredients during downtime

### 4. **Cooking Optimization**

**Critical Timing**:
- Food cooks automatically at 1 tick/turn
- Cooked stage: exactly ticks 20-39
- **Must remove before tick 40 or it burns!**

**Optimal Strategies**:
- Track cooking start time: `start_turn = current_turn`
- Calculate removal time: `removal_turn = start_turn + 20`
- Queue another bot to retrieve at turn 20
- Use multiple pans for parallel cooking

**Cooking Mistakes to Avoid**:
- Forgetting food on cooker (will burn)
- Not having pan ready before buying cookable food
- Inefficient bot routing (cook burns while bot travels)

### 5. **Pathfinding & Movement**

**Current Bot**: Uses BFS for pathfinding (good!)

**Optimization Opportunities**:
- **A* Search**: Faster than BFS for long distances
- **Movement Caching**: Pre-compute paths to common locations
- **Collision Avoidance**: Predict other bots' positions
- **Zone Assignment**: Assign bots to kitchen zones to reduce conflicts

### 6. **State Machine vs. Planning**

**Current Approach**: Hard-coded state machine (16+ states)

**Pros**:
- Simple, predictable
- Easy to debug
- Fast execution

**Cons**:
- Inflexible (only makes one dish)
- Doesn't adapt to orders
- Poor resource utilization

**Alternative: Goal-Oriented Action Planning (GOAP)**:
1. Read available orders
2. Select best order based on criteria
3. Generate action sequence to fulfill it
4. Execute plan step by step
5. Adapt if conditions change

**Hybrid Approach**:
- Use state machine for low-level actions (move to X, chop Y)
- Use planner for high-level decisions (which order, which bot)

### 7. **Sabotage Strategy**

**When to Switch**:
- **Aggressive**: Switch immediately at turn 250
  - Disrupt enemy's production
  - Risk: Your own production stops

- **Conservative**: Switch near end of switch window
  - Complete your orders first
  - Less disruption time for enemy

- **Conditional**: Switch if:
  - You're ahead (deny catch-up)
  - You're behind (disrupt leader)
  - Enemy has high-value orders cooking

**What to Sabotage**:
1. **Priority 1**: Food on cookers (expensive, time-invested)
2. **Priority 2**: Assembled plates (about to submit)
3. **Priority 3**: Chopped ingredients (preparation wasted)
4. **Priority 4**: Raw ingredients (minimal impact)

**Defense Against Sabotage**:
- Complete critical orders before turn 250
- Move valuable items away from easily accessible areas
- Have backup ingredients
- Quickly resume production after enemy leaves (turn 350)

### 8. **Advanced Techniques**

**Order Claiming**:
- Claim orders early (reserve them)
- Prevents enemy from fulfilling them
- Shows commitment to your plan

**Plate Reuse**:
- Efficient: Submit → Wash → Reuse
- Better than constantly buying new plates
- Requires coordination (dishwasher bot)

**Speculative Preparation**:
- Pre-chop ingredients for likely orders
- Pre-buy common ingredients
- Keep food at cooked stage (not burnt)

**Emergency Adaptation**:
- If food burns, immediately trash it
- If order expires, abandon and switch
- If sabotaged, assess damage and replan

---

## Winning Strategy Template

### Early Game (Turns 0-100)
1. Buy 1-2 Pans, 2-3 Plates
2. Fulfill cheap orders (Egg, Sauce-based)
3. Build cash reserve (aim for 300+ coins)
4. Establish production routine

### Mid Game (Turns 100-250)
1. Identify high-value orders
2. Optimize bot coordination
3. Maintain steady order completion
4. Stockpile ingredients in boxes
5. Prepare for switch at turn 250

### Switch Phase (Turns 250-350)
1. **Decision**: Switch immediately or later?
2. If switching: Target enemy's cookers and plates
3. If staying: Rush order completion
4. Monitor enemy's switch timing

### Late Game (Turns 350-500)
1. Complete remaining valuable orders
2. Avoid starting orders you can't finish
3. Manage time carefully (cooking takes 20+ turns)
4. Don't waste money on ingredients you won't use

---

## Metrics to Track

**Performance Indicators**:
- Orders completed / Orders expired
- Average profit per order
- Bot utilization rate (% turns spent on useful actions)
- Wasted resources (burnt food, expired ingredients)
- Time to complete order (from claim to submit)

**Optimization Targets**:
- Minimize bot idle time
- Maximize orders per 100 turns
- Reduce average order completion time
- Increase profit margin per order

---

## Example Improvements to Current Bot

The `duo_noodle_bot.py` has several limitations:

1. **Fixed Recipe**: Only makes Noodles + Cooked Meat
   - **Fix**: Read orders and adapt recipe

2. **Single Bot**: Only bot 0 works
   - **Fix**: Assign roles to both bots

3. **No Order Selection**: Doesn't consider which orders to pursue
   - **Fix**: Implement order prioritization

4. **Fragile State Machine**: One failure cascades
   - **Fix**: Add error recovery states

5. **No Sabotage**: Doesn't use switch mechanic
   - **Fix**: Implement sabotage logic

6. **Inefficient Waiting**: Bot moves to cooker repeatedly checking
   - **Fix**: Calculate exact turn to retrieve food

---

## Key Takeaways

1. **Orders Drive Everything**: Your strategy should revolve around fulfilling profitable orders efficiently

2. **Time is Money**: With 500 turns and passive income, wasting turns costs coins

3. **Cooking is Critical**: Managing cook times prevents burning (most common error)

4. **Coordination Multiplies Efficiency**: Two coordinated bots >> two independent bots

5. **Sabotage is Tactical**: Use it strategically, not as primary strategy

6. **Adaptability Wins**: Rigid strategies fail when orders/opponent behavior varies

7. **Economic Thinking**: Every action has opportunity cost - calculate ROI

---

## Quick Reference: Common Action Sequences

**Buy & Use Ingredient**:
```
1. move_towards(shop)
2. buy(ingredient)
3. move_towards(counter/cooker)
4. place(ingredient)
```

**Chop Ingredient**:
```
1. buy/pickup(choppable_food)
2. move_towards(counter)
3. place(food, counter)
4. chop(counter)
5. pickup(food, counter)
```

**Cook Ingredient**:
```
1. [Ensure pan on cooker]
2. Have cooked food
3. move_towards(cooker)
4. place(food, cooker)  # Starts cooking automatically
5. [Wait exactly 20 turns]
6. take_from_pan(cooker)
```

**Assemble & Submit Plate**:
```
1. [Have clean plate]
2. add_food_to_plate(food_item_1)
3. add_food_to_plate(food_item_2)
4. move_towards(submit)
5. submit(plate)
```

**Wash Plates**:
```
1. [Have dirty plate]
2. move_towards(sink)
3. put_dirty_plate_in_sink()
4. wash_sink()  # Takes 2 turns
5. wash_sink()
6. move_towards(sink_table)
7. take_clean_plate()
```

---

## Debugging Tips

- Use `controller.get_bot_state(bot_id)` to check bot's position and inventory
- Check `controller.get_tile(team, x, y)` to see what's on a tile
- Monitor `controller.get_team_money()` to track economy
- Use `controller.get_orders()` to see active orders
- Add logging to track state transitions
- Test with `--render` flag to visualize behavior

Good luck building your bot!
