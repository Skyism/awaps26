"""tiles.py"""

from game_constants import TileType, FoodType, ShopCosts
from item import Item, Pan, Food, Plate

"""Each class describes the current STATE of a tile. Robot controller describes how the state changes through bot actions"""


class Tile:
    def __init__(self, tile_type: TileType):
        self.tile_name = tile_type.tile_name
        self.tile_id = tile_type.tile_id
        self.is_walkable = tile_type.is_walkable
        self.is_dangerous = tile_type.is_dangerous
        self.is_placeable = tile_type.is_placeable
        self.is_interactable = tile_type.is_interactable

        self.item = None  # what item is on the tile
        self.using = False  # whether the tile is "in use" or not

    def to_dict(self):
        """basic JSON"""
        return {
            "tile_name": self.tile_name,
            "is_walkable": self.is_walkable,
            # no using
        }


class Placeable(Tile):
    """
    Tiles that we can place objects on (ie counters)
    """

    def __init__(self, tile_type: TileType):
        super().__init__(tile_type)
        self.placeable = True


class Interactable(Tile):
    """Tiles that we can interact with (ie cooker)"""

    def __init__(self, tile_type: TileType):
        super().__init__(tile_type)
        self.placeable = True
        self.interactable = True


class Floor(Tile):
    def __init__(self):
        super().__init__(TileType.FLOOR)


class Wall(Tile):
    def __init__(self):
        super().__init__(TileType.WALL)


class Counter(Interactable):
    def __init__(self):
        super().__init__(TileType.COUNTER)
        self.item = None  # only 1 item can be on a counter, None = no item on counter

    def to_dict(self):
        d = super().to_dict()
        d["item"] = (
            self.item.to_dict() if self.item else None
        )  # add item if avail to the tile
        return d


class Box(Interactable):
    def __init__(self):
        super().__init__(TileType.BOX)
        self.item = None  # this is the item to put in that needs to match
        self.count = 0  # if count = 0, self.item needs to be None

    def enforce_invar(self):
        if self.count <= 0:
            self.count = 0
            self.item = None

    def to_dict(self):
        d = super().to_dict()
        d["item"] = self.item.to_dict() if self.item else None  # add item
        d["count"] = self.count  # add count inside the box
        return d


class Sink(Interactable):
    def __init__(self):
        super().__init__(TileType.SINK)
        self.num_dirty_plates = 0
        self.curr_dirty_plate_progress = 0

    def to_dict(self):
        d = super().to_dict()
        d["num_dirty_plates"] = self.num_dirty_plates
        d["curr_dirty_plate_progress"] = self.curr_dirty_plate_progress
        d["using"] = self.using
        return d


class SinkTable(Interactable):
    def __init__(self):
        super().__init__(TileType.SINKTABLE)
        self.num_clean_plates = 0  # user can take clean plates

    def to_dict(self):
        d = super().to_dict()
        d["num_clean_plates"] = self.num_clean_plates
        return d


class Cooker(Interactable):
    def __init__(self):
        super().__init__(TileType.COOKER)
        self.item = Pan()  # empty pan
        self.cook_progress = 0  # ticks every turn

    def to_dict(self):
        d = super().to_dict()
        d["item"] = self.item.to_dict() if self.item else None
        d["cook_progress"] = self.cook_progress
        return d


class Trash(Interactable):
    def __init__(self):
        super().__init__(TileType.TRASH)


class Submit(Interactable):
    def __init__(self):
        super().__init__(TileType.SUBMIT)


class Shop(Interactable):
    def __init__(self):
        super().__init__(TileType.SHOP)
        self.shop_items = set()

        # default is allow every food and shop item
        for food in FoodType:
            self.shop_items.add(food)
        for shop_item in ShopCosts:
            self.shop_items.add(shop_item)

    def to_dict(self):
        d = super().to_dict()
        # shop has all available items for sale (all food, pans, plates)
        return d
