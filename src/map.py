"""map.py"""

from game_constants import TileType, Team
from tiles import Tile
from typing import List, Tuple


class Map:
    """
    Is a map that details the environment

    Convention: bottom-left is [0][0], top-right is [width-1][height-1]

    self.tiles[x][y]
                    y == height  ----->
    x == width    [[# # # # # # # #],
        |          [# # # # # # # #],
        |          [# # # # # # # #],
        |          [# # # # # # # #],
        v          [# # # # # # # #]]

    The actual map is rotated counterclockwise, note for rendering

       ^           # # # # #
       |           # # # # #
       |           # # # # #
    y == height    # # # # #
                   # # # # #
                   # # # # #
                   # # # # #
                   # # # # #

                   x == width -->
    """

    def __init__(
        self,
        width: int = 32,
        height: int = 32,
        tiles: List[List[Tile]] = None,
        team: Team = Team.RED,
        orders: List = None,
    ):
        self.width = width
        self.height = height
        self.tiles = tiles
        if self.tiles is None:
            self.tiles = [
                [Tile(TileType.FLOOR) for x in range(self.height)]
                for x in range(self.width)
            ]

        self.team = team

        self.orders = orders  # orders will be in the form list of ([items on plate], start turn, end turn, coins given, coins penalized)
        if self.orders is None:
            self.orders = []

    def in_bounds(self, x: int, y: int) -> bool:
        """
        checks if self.tiles[x][y] is in bounds,
        noting that x is "width" and y is "height"
        """
        return (0 <= x and x < self.width) and (0 <= y and y < self.height)

    def is_tile_name(self, x: int, y: int, tile_name: str) -> bool:
        """checks if location (x, y) on the map is of a certain tile_type"""

        if not self.in_bounds(x, y):
            return False

        return self.tiles[x][y].tile_name == tile_name

    def is_tile_walkable(self, x: int, y: int) -> bool:
        """checks if location (x, y) is walkable"""
        if not self.in_bounds(x, y):
            return False

        return self.tiles[x][y].is_walkable

    def is_tile_dangerous(self, x: int, y: int) -> bool:
        """checks if location (x, y) is dangerous"""
        if not self.in_bounds(x, y):
            return False

        return self.tiles[x][y].is_dangerous

    def is_tile_placeable(self, x: int, y: int) -> bool:
        """checks if location (x, y) is placeable"""
        if not self.in_bounds(x, y):
            return False

        return self.tiles[x][y].is_placeable

    def is_tile_interactable(self, x: int, y: int) -> bool:
        """checks if location (x, y) is interactable"""
        if not self.in_bounds(x, y):
            return False

        return self.tiles[x][y].is_interactable

    def to_2d_list(self):
        """
        converts the map into a 2D list of tile dictionaries containing full state
        """
        return [[tile.to_dict() for tile in row] for row in self.tiles]
