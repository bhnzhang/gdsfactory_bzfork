from typing import List
from warnings import warn

import numpy as np

import gdsfactory as gf
from gdsfactory import Port
from gdsfactory.component import Component
from gdsfactory.routing import get_route_from_waypoints
from gdsfactory.routing.manhattan import route_manhattan
from gdsfactory.types import CrossSectionSpec, Route


class Node:
    def __init__(self, parent=None, position: tuple = ()):
        """Initializes a node. A node is a point on the grid."""
        self.parent = parent  # parent node of current node
        self.position = position  # position of current node

        self.g = 0  # distance between current node and start node
        self.h = 0  # distance between current node and end node
        self.f = self.g + self.h  # cost of the node (sum of g and h)


def astar_routing(
    c: Component,
    input_port: Port,
    output_port: Port,
    resolution: float = 1,
    cross_section: CrossSectionSpec = "strip",
    **kwargs,
) -> Route:
    """A* routing function. Finds a route avoiding components in a component `c` between two ports.

    Args:
        c: Component the route, and ports belong to.
        input_port: input.
        output_port: output.
        resolution: discretization resolution. A lower resolution can help avoid accidental overlapping between
                    the route and components, but can result in large number of turns.
        cross_section: spec.
        kwargs: cross_section settings.
    """
    cross_section = gf.get_cross_section(cross_section, **kwargs)
    grid, x, y = _generate_grid(c, resolution)

    # Tell the algorithm which start and end directions to follow based on port orientation
    input_orientation = {
        0.0: (resolution, 0),
        90.0: (0, resolution),
        180.0: (-resolution, 0),
        270.0: (0, -resolution),
        None: (0, 0),
    }[input_port.orientation]

    output_orientation = {
        0.0: (resolution, 0),
        90.0: (0, resolution),
        180.0: (-resolution, 0),
        270.0: (0, -resolution),
        None: (0, 0),
    }[output_port.orientation]

    # Instantiate nodes
    start_node = Node(
        None,
        (
            round(input_port.x + input_orientation[0]),
            round(input_port.y + input_orientation[1]),
        ),
    )
    start_node.g = start_node.h = start_node.f = 0

    end_node = Node(
        None,
        (
            round(output_port.x + output_orientation[0]),
            round(output_port.y + output_orientation[1]),
        ),
    )
    end_node.g = end_node.h = end_node.f = 0

    # Add the start node
    open_list = [start_node]
    closed = []

    while open_list:
        # Current node
        current_index = 0
        for index in range(len(open_list)):
            if open_list[index].f < open_list[current_index].f:
                current_index = index

        # Pop current off open_list list, add to closed list
        current_node = open_list[current_index]
        closed.append(open_list.pop(current_index))

        # Reached end port
        if (
            current_node.position[0] == end_node.position[0]
            and current_node.position[1] == end_node.position[1]
        ):
            points = []
            current = current_node

            # trace back path from end node to start node
            while current is not None:
                points.append(current.position)
                current = current.parent
            # reverse to get true path
            points = points[::-1]

            # add the start and end ports
            points.insert(0, input_port.center)
            points.append(output_port.center)

            # return route from points
            return get_route_from_waypoints(points, cross_section=cross_section)

        # Generate neighbours
        neighbours = _generate_neighbours(
            grid=grid,
            x=x,
            y=y,
            current_node=current_node,
            resolution=resolution,
        )

        # Loop through neighbours
        for neighbour in neighbours:

            for closed_neighbour in closed:
                if neighbour == closed_neighbour:
                    continue

            # Compute f, g, h
            neighbour.g = current_node.g + resolution
            neighbour.h = ((neighbour.position[0] - end_node.position[0]) ** 2) + (
                (neighbour.position[1] - end_node.position[1]) ** 2
            )
            neighbour.f = neighbour.g + neighbour.h

            # neighbour is already in the open_list
            for open_list_node in open_list:
                if neighbour == open_list_node and neighbour.g > open_list_node.g:
                    continue

            # Add the neighbour to open_list
            open_list.append(neighbour)

    warn("A* algorithm failed, resorting to Manhattan routing. Watch for overlaps.")
    return route_manhattan(input_port, output_port, cross_section=cross_section)


def _generate_grid(c: Component, resolution: float = 0.5) -> np.ndarray:
    """Generate discretization grid that the algorithm will step through."""
    bbox = c.bbox
    x, y = np.meshgrid(
        np.linspace(
            bbox[0][0],
            bbox[1][0],
            int((bbox[1][0] - bbox[0][0]) / resolution),
            endpoint=True,
        ),
        np.linspace(
            bbox[0][1],
            bbox[1][1],
            int((bbox[1][1] - bbox[0][1]) / resolution),
            endpoint=True,
        ),
    )  # discretize component space
    x, y = x[0], y[:, 0]  # weed out copies
    grid = np.zeros(
        (len(x), len(y))
    )  # mapping from gdsfactory's x-, y- coordinate to grid vertex

    # assign 1 for obstacles
    for ref in c.references:
        bbox = ref.bbox
        xmin = np.abs(x - bbox[0][0]).argmin()
        xmax = np.abs(x - bbox[1][0]).argmin()
        ymin = np.abs(y - bbox[0][1]).argmin()
        ymax = np.abs(y - bbox[1][1]).argmin()

        grid[xmin:xmax, ymin:ymax] = 1

    return np.ndarray.round(grid, 3), np.ndarray.round(x, 3), np.ndarray.round(y, 3)


def _generate_neighbours(
    current_node: Node,
    grid,
    x: np.ndarray,
    y: np.ndarray,
    resolution: float,
) -> List[Node]:
    """Generate neighbours of a node."""
    neighbours = []

    for new_position in [
        (0, -resolution),
        (0, resolution),
        (-resolution, 0),
        (resolution, 0),
    ]:  # Adjacent nodes along Manhattan path

        # Get node position
        node_position = (
            current_node.position[0] + new_position[0],
            current_node.position[1] + new_position[1],
        )

        # Make sure within range and not in obstacle
        if (
            node_position[0] > x.max()
            or node_position[0] < x.min()
            or node_position[1] > y.max()
            or node_position[1] < y.min()
        ):
            continue

        if (
            grid[
                next(
                    i
                    for i, _ in enumerate(x)
                    if np.isclose(_, node_position[0], atol=1)
                )
            ][
                next(
                    i
                    for i, _ in enumerate(y)
                    if np.isclose(_, node_position[1], atol=1)
                )
            ]
            == 1.0
        ):
            continue

        # Create new node
        new_node = Node(current_node, node_position)

        # Append
        neighbours.append(new_node)

    return neighbours


if __name__ == "__main__":
    c = gf.Component()

    # mzi_ = c << gf.components.mzi()
    # mzi_2 = c << gf.components.mzi()

    # mzi_2.move(destination=(100, -10))
    # rect3 = c << gf.components.rectangle(size=(7.5, 9))
    # rect3.move(destination=(82.5, -9.5))
    rect1 = c << gf.components.rectangle()
    rect2 = c << gf.components.rectangle()
    rect3 = c << gf.components.rectangle((2, 2))
    rect2.move(destination=(8, 4))
    rect3.move(destination=(5.5, 1.5))

    port1 = Port(
        "o1", 0, rect1.center + (0, 3), cross_section=gf.get_cross_section("strip")
    )
    port2 = port1.copy("o2")
    port2.orientation = 180
    port2.center = rect2.center + (0, -3)
    c.add_ports([port1, port2])
    c.show(show_ports=True)

    route = astar_routing(c, port1, port2, radius=0.5, width=0.5)
    # route = route_manhattan(port1, port2, radius=0.5, width=0.5)
    # route = astar_routing(c, mzi_.ports["o2"], mzi_2.ports["o1"], radius=0.5)
    # route = route_manhattan(mzi_.ports["o2"], mzi_2.ports["o1"], radius=0.5)
    c.add(route.references)

    c.show(show_ports=True)
