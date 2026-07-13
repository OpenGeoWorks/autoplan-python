"""Layout (subdivision) survey plan generator.

Status: experimental / under active development. Generates a road network
over the site boundary, subdivides the remaining blocks into plots, and
draws the result. Road patterns: grid, radial, organic, or mixed.
"""

import math
import random
from typing import ClassVar, Dict, List, Optional, Tuple

import numpy as np
from pydantic import BaseModel
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from dxf_manager import SurveyDXFManager
from models.plan import CoordinateProps, PlanType
from plans.base import BasePlan
from utils import polygon_orientation


class ParcelInfo(BaseModel):
    """Information about a generated parcel"""
    id: str
    vertices: List[Tuple[float, float]]
    area: float
    width: float
    depth: float
    centroid: Tuple[float, float]
    street_frontage: List[Tuple[float, float]]
    buildable_area: List[Tuple[float, float]]


def create_smooth_curve(control_points: List[Tuple[float, float]],
                        num_points: int) -> List[Tuple[float, float]]:
    """Create a smooth curve through control points using a Catmull-Rom spline."""
    if len(control_points) < 4:
        return control_points

    result = []
    for i in range(len(control_points) - 3):
        p0, p1, p2, p3 = control_points[i:i + 4]
        for t in np.linspace(0, 1, num_points // (len(control_points) - 3)):
            t2 = t * t
            t3 = t2 * t

            x = 0.5 * ((2 * p1[0]) +
                       (-p0[0] + p2[0]) * t +
                       (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 +
                       (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)

            y = 0.5 * ((2 * p1[1]) +
                       (-p0[1] + p2[1]) * t +
                       (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 +
                       (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)

            result.append((x, y))

    return result


class LayoutPlan(BasePlan):
    expected_type: ClassVar[PlanType] = PlanType.LAYOUT

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.layout_boundary or not self.layout_boundary.coordinates:
            raise ValueError("Layout plans require a layout boundary with coordinates.")
        if self.layout_parameters is None:
            raise ValueError("Layout plans require layout parameters.")

        self._boundary_dict = {coord.id: coord for coord in self.layout_boundary.coordinates}
        self._boundary_polygon = Polygon(
            [(p.easting, p.northing) for p in self.layout_boundary.coordinates]
        )
        self._parcels: List[ParcelInfo] = []
        self._roads: List[Dict] = []
        self._green_spaces: List[Polygon] = []
        self._blocks: List[Polygon] = []
        self._roads_union: Optional[Polygon | MultiPolygon] = None

    def _setup_layers(self, drawer: SurveyDXFManager):
        drawer.setup_layout_layers()
        drawer.setup_beacon_style(self.beacon_type, self.beacon_size)

    def _area_text(self) -> str:
        if self.layout_boundary.area is not None:
            return f"AREA :- {self.layout_boundary.area} SQ.METRES"
        return ""

    def _north_arrow_reference(self) -> Optional[CoordinateProps]:
        if not self.layout_boundary.coordinates:
            return None
        return self.layout_boundary.coordinates[0]

    # ------------------------------------------------------------------
    # Road network generation
    # ------------------------------------------------------------------
    def _append_road_intersection(self, intersection, road_type: str, width: float, name_prefix: str):
        """Store the parts of a road centerline that fall inside the boundary."""
        if intersection.is_empty:
            return

        if intersection.geom_type == "LineString":
            lines = [intersection]
        elif intersection.geom_type == "MultiLineString":
            lines = list(intersection.geoms)
        else:
            return

        for line in lines:
            self._roads.append({
                "type": road_type,
                "centerline": list(line.coords),
                "width": width,
                "name": f"{name_prefix} {len(self._roads) + 1}",
            })

    def _generate_grid_roads(self):
        """Generate a grid pattern road network."""
        min_x, min_y, max_x, max_y = self._boundary_polygon.bounds
        params = self.layout_parameters

        x_spacing = params.max_block_length + params.main_road_width
        y_spacing = params.max_block_width + params.secondary_road_width

        # Main roads (horizontal)
        y = min_y + y_spacing
        while y < max_y - y_spacing:
            road_line = LineString([(min_x - 10, y), (max_x + 10, y)])
            self._append_road_intersection(
                road_line.intersection(self._boundary_polygon),
                "main", params.main_road_width, "Street",
            )
            y += y_spacing

        # Secondary roads (vertical)
        x = min_x + x_spacing
        while x < max_x - x_spacing:
            road_line = LineString([(x, min_y - 10), (x, max_y + 10)])
            self._append_road_intersection(
                road_line.intersection(self._boundary_polygon),
                "secondary", params.secondary_road_width, "Avenue",
            )
            x += x_spacing

    def _generate_radial_roads(self):
        """Generate a radial pattern road network."""
        centroid = self._boundary_polygon.centroid
        center_x, center_y = centroid.x, centroid.y
        params = self.layout_parameters

        # Radial roads out from the centroid
        num_radial = 8
        for i in range(num_radial):
            angle = (i * 360 / num_radial) * math.pi / 180
            end_x = center_x + math.cos(angle) * 1000
            end_y = center_y + math.sin(angle) * 1000

            road_line = LineString([(center_x, center_y), (end_x, end_y)])
            self._append_road_intersection(
                road_line.intersection(self._boundary_polygon),
                "main", params.main_road_width, "Radial",
            )

        # Concentric ring roads
        bounds = self._boundary_polygon.bounds
        max_radius = min(bounds[2] - bounds[0], bounds[3] - bounds[1]) / 2
        num_circles = 3

        for i in range(1, num_circles + 1):
            radius = (i * max_radius) / (num_circles + 1)
            ring = Point(center_x, center_y).buffer(radius).boundary
            self._append_road_intersection(
                ring.intersection(self._boundary_polygon),
                "secondary", params.secondary_road_width, "Ring Road",
            )

    def _generate_organic_roads(self):
        """Generate an organic/curved road network."""
        min_x, min_y, max_x, max_y = self._boundary_polygon.bounds
        params = self.layout_parameters

        num_main_roads = 3
        for i in range(num_main_roads):
            t = (i + 1) / (num_main_roads + 1)
            start_y = min_y + t * (max_y - min_y)

            control_points = [
                (min_x, start_y),
                (min_x + (max_x - min_x) * 0.3, start_y + random.uniform(-20, 20)),
                (min_x + (max_x - min_x) * 0.7, start_y + random.uniform(-20, 20)),
                (max_x, start_y + random.uniform(-10, 10)),
            ]

            coords = create_smooth_curve(control_points, 20)
            road_line = LineString(coords)
            self._append_road_intersection(
                road_line.intersection(self._boundary_polygon),
                "main", params.main_road_width, "Parkway",
            )

        self._add_organic_connectors()

    def _generate_mixed_roads(self):
        """Generate a mixed pattern combining grid and organic elements."""
        self._generate_grid_roads()

        min_x, min_y, max_x, max_y = self._boundary_polygon.bounds
        control_points = [
            (min_x, min_y),
            ((min_x + max_x) / 2, (min_y + max_y) / 2),
            (max_x, max_y),
        ]

        coords = create_smooth_curve(control_points, 30)
        road_line = LineString(coords)
        self._append_road_intersection(
            road_line.intersection(self._boundary_polygon),
            "main", self.layout_parameters.main_road_width, "Boulevard",
        )

    def _add_organic_connectors(self):
        """Add connecting roads for organic layout."""
        if len(self._roads) < 2:
            return

        for i in range(0, len(self._roads) - 1, 2):
            road1 = self._roads[i]["centerline"]
            road2 = self._roads[min(i + 1, len(self._roads) - 1)]["centerline"]

            mid1 = road1[len(road1) // 2]
            mid2 = road2[len(road2) // 2]

            self._roads.append({
                "type": "access",
                "centerline": [mid1, mid2],
                "width": self.layout_parameters.access_road_width,
                "name": f"Lane {i + 1}",
            })

    # ------------------------------------------------------------------
    # Blocks, green spaces, parcels
    # ------------------------------------------------------------------
    def _generate_blocks(self):
        """Generate blocks by subtracting the road network from the boundary."""
        road_polygons = [
            LineString(road["centerline"]).buffer(road["width"] / 2)
            for road in self._roads
        ]

        if not road_polygons:
            self._blocks = [self._boundary_polygon]
            return

        self._roads_union = unary_union(road_polygons)
        blocks_area = self._boundary_polygon.difference(self._roads_union)

        if blocks_area.geom_type == "Polygon":
            self._blocks = [blocks_area]
        elif blocks_area.geom_type == "MultiPolygon":
            self._blocks = list(blocks_area.geoms)

    def _allocate_green_spaces(self):
        """Allocate green spaces within the largest blocks."""
        if not self._blocks:
            return

        total_area = self._boundary_polygon.area
        target_green_area = total_area * (self.layout_parameters.green_space_percentage / 100)
        current_green_area = 0.0

        for block in sorted(self._blocks, key=lambda b: b.area, reverse=True):
            if current_green_area >= target_green_area:
                break
            if block.area <= 5000:  # only for large blocks
                continue

            centroid = block.centroid
            green_radius = min(20.0, math.sqrt(block.area) * 0.15)
            green_space = Point(centroid.x, centroid.y).buffer(green_radius).intersection(block)

            if not green_space.is_empty:
                self._green_spaces.append(green_space)
                current_green_area += green_space.area

                # Remove the green space from the block before parcel generation
                idx = self._blocks.index(block)
                self._blocks[idx] = block.difference(green_space)

    def _generate_parcels(self):
        """Generate parcels (final plots) within blocks."""
        parcel_id = 1

        for block in self._blocks:
            if block.area < self.layout_parameters.min_parcel_area:
                continue

            for plot_polygon in self._subdivide_block(block):
                vertices = list(plot_polygon.exterior.coords[:-1])
                bounds = plot_polygon.bounds

                self._parcels.append(ParcelInfo(
                    id=f"P{parcel_id:04d}",
                    vertices=vertices,
                    area=plot_polygon.area,
                    width=bounds[2] - bounds[0],
                    depth=bounds[3] - bounds[1],
                    centroid=(plot_polygon.centroid.x, plot_polygon.centroid.y),
                    street_frontage=self._find_street_frontage(plot_polygon),
                    buildable_area=self._calculate_buildable_area(plot_polygon),
                ))
                parcel_id += 1

    def _subdivide_block(self, block: Polygon) -> List[Polygon]:
        """Subdivide a block into rectangular plots according to layout parameters.

        Leftover strips are handled by ``layout_parameters.remainder_strategy``:
          - 'separate'    -> leftover becomes its own plot (if large enough)
          - 'add_to_last' -> leftover added to the last plot in the row/column
          - 'distribute'  -> leftover divided equally among the plots
        """
        params = self.layout_parameters
        parcels: List[Polygon] = []

        min_x, min_y, max_x, max_y = block.bounds
        site_width = max_x - min_x
        site_height = max_y - min_y

        target_area = (params.min_parcel_area + params.max_parcel_area) / 2.0
        plot_width = getattr(params, "plot_width", max(params.min_parcel_width * 1.2, 0.1))
        plot_depth = getattr(params, "plot_depth", max(target_area / max(plot_width, 0.0001), 0.1))

        min_area = params.min_parcel_area
        strategy = getattr(params, "remainder_strategy", "separate")

        n_cols = max(1, int(math.floor(site_width / plot_width)))
        n_rows = max(1, int(math.floor(site_height / plot_depth)))

        def sizes_with_leftover(count: int, size: float, leftover: float, cross_size: float) -> List[float]:
            """Sizes of the rows/columns after applying the remainder strategy."""
            sizes = [size] * count
            if abs(leftover) < 1e-6:
                return sizes
            if strategy == "separate":
                if leftover * cross_size >= min_area:
                    return sizes + [leftover]
                sizes[-1] += leftover
            elif strategy == "add_to_last":
                sizes[-1] += leftover
            elif strategy == "distribute":
                sizes = [size + leftover / count] * count
            return sizes

        col_widths = sizes_with_leftover(n_cols, plot_width, site_width - n_cols * plot_width, plot_depth)
        row_heights = sizes_with_leftover(n_rows, plot_depth, site_height - n_rows * plot_depth, plot_width)

        # Tile the block and clip each rectangle to the block polygon
        EPS = 1e-8
        y = min_y
        for h in row_heights:
            x = min_x
            for w in col_widths:
                rect = Polygon([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])
                inter = rect.intersection(block)

                if inter.geom_type == "Polygon":
                    if inter.area + EPS >= min_area:
                        parcels.append(inter)
                elif inter.geom_type == "MultiPolygon":
                    parcels.extend(poly for poly in inter.geoms if poly.area + EPS >= min_area)
                # ignore empty intersections and slivers (points/lines)

                x += w
            y += h

        # Very small blocks yield no tiles; keep the block itself if big enough
        if not parcels and block.area >= min_area:
            parcels = [block]

        return parcels

    def _calculate_buildable_area(self, parcel: Polygon) -> List[Tuple[float, float]]:
        """Calculate the buildable area within a parcel considering setbacks."""
        # TODO: apply front/side/rear setbacks per edge instead of a uniform buffer
        buildable = parcel.buffer(-self.layout_parameters.front_setback)

        if buildable.is_empty or buildable.geom_type != "Polygon":
            return []

        return list(buildable.exterior.coords[:-1])

    def _find_street_frontage(self, plot: Polygon) -> List[Tuple[float, float]]:
        """Return the edge of the plot touching a road (longest one wins).

        Falls back to the lowest-Y edge if the plot touches no road.
        """
        EPS = 1e-6
        frontage: List[Tuple[float, float]] = []

        if self._roads_union is not None:
            max_len = 0.0
            coords = list(plot.exterior.coords)
            for i in range(len(coords) - 1):
                p1, p2 = coords[i], coords[i + 1]
                edge = LineString([p1, p2])
                if edge.buffer(EPS).intersects(self._roads_union) and edge.length > max_len:
                    max_len = edge.length
                    frontage = [p1, p2]

        if not frontage:
            coords = list(plot.exterior.coords)
            min_y = min(c[1] for c in coords)
            frontage = [c for c in coords if abs(c[1] - min_y) < 0.1]

        return frontage

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------
    def _draw_boundary(self):
        boundary_points = [(coord.easting, coord.northing) for coord in self.layout_boundary.coordinates]
        if not boundary_points:
            return

        self._drawer.add_boundary(boundary_points)
        orientation = polygon_orientation(boundary_points)

        for leg in self.layout_boundary.legs or []:
            self.add_leg_labels(leg, orientation)

    def _draw_roads(self):
        """Draw roads with centerlines and offset edges."""
        for road in self._roads:
            centerline = road["centerline"]
            width = road["width"]

            self._drawer.add_road_cl(list(centerline))

            road_line = LineString(centerline)
            for side in ("left", "right"):
                edge = road_line.parallel_offset(width / 2, side)
                if not edge.is_empty and edge.geom_type == "LineString":
                    self._drawer.add_road(list(edge.coords))

    def _draw_parcels(self):
        """Draw parcels with IDs and area annotations."""
        for parcel in self._parcels:
            self._drawer.add_parcel(list(parcel.vertices))

            if parcel.buildable_area:
                self._drawer.add_buildable(list(parcel.buildable_area))

            self._drawer.add_text(parcel.id, parcel.centroid[0], parcel.centroid[1], 0.5)
            self._drawer.add_text(f"{parcel.area:.1f} m²",
                                  parcel.centroid[0], parcel.centroid[1] - 3, 0.5)

    def _draw_green_spaces(self):
        for green_space in self._green_spaces:
            if green_space.geom_type == "Polygon":
                self._drawer.add_greenspace(list(green_space.exterior.coords[:-1]))

    def draw_beacons(self):
        seen = set()
        for coord in self.layout_boundary.coordinates:
            if coord.id in seen:
                continue
            seen.add(coord.id)
            self._drawer.draw_beacon(coord.easting, coord.northing, 0,
                                     self.label_size, self._get_drawing_extent(), coord.id)

    def draw(self):
        # Generate the road network for the requested subdivision pattern
        generators = {
            "grid": self._generate_grid_roads,
            "radial": self._generate_radial_roads,
            "organic": self._generate_organic_roads,
            "mixed": self._generate_mixed_roads,
        }
        generators.get(self.layout_parameters.subdivision_type, self._generate_mixed_roads)()

        self._generate_blocks()

        if self.layout_parameters.include_green_spaces:
            self._allocate_green_spaces()

        self._generate_parcels()

        # Draw all elements
        self._draw_boundary()
        self._draw_roads()
        self._draw_parcels()
        self.draw_frames()
        self.draw_title_block()
        self.draw_footer_boxes()
        self.draw_north_arrow()
        self.draw_beacons()
