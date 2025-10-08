from models.plan import PlanProps, PlanType
from shapely.geometry import Polygon, Point, LineString, MultiPolygon
from shapely.ops import split, unary_union
from shapely.affinity import rotate, translate
from utils import polygon_orientation, line_normals, line_direction, html_to_mtext, format_number
from typing import List, Tuple, Dict, Optional
from pydantic import BaseModel
from dxf import SurveyDXFManager
import math
import random
import numpy as np

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
    """Create a smooth curve through control points using Catmull-Rom spline"""
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


class LayoutPlan(PlanProps):
    _roads_union: Optional[Polygon | MultiPolygon] = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.type != PlanType.LAYOUT:
            raise ValueError("LayoutPlan must have type PlanType.LAYOUT")

        self._frame_x_percent = 0.9
        self._frame_y_percent = 1.5
        self._bounding_box = self.get_bounding_box()
        self._frame_coords = self._setup_frame_coords()
        self._boundary_dict = {coord.id: coord for coord in self.layout_boundary.coordinates}
        if not self._frame_coords:
            raise ValueError("Cannot determine frame coordinates without valid coordinates.")

        self._boundary_polygon = Polygon([(p.easting, p.northing) for p in self.layout_boundary.coordinates])
        self._parcels: List[ParcelInfo] = []
        self._roads: List[Dict] = []
        self._green_spaces: List[Polygon] = []
        self._blocks: List[Polygon] = []

        self._drawer = self._setup_drawer()

    def _setup_drawer(self) -> SurveyDXFManager:
        drawer = SurveyDXFManager(plan_name=self.name, scale=self.get_drawing_scale(), dxf_version=self.dxf_version)
        drawer.setup_layout_layers()
        drawer.setup_font(self.font)
        drawer.setup_beacon_style(self.beacon_type, self.beacon_size)
        return drawer

    def _setup_frame_coords(self):
        min_x, min_y, max_x, max_y = self._bounding_box
        if min_x is None or min_y is None or max_x is None or max_y is None:
            return None

        width = max_x - min_x
        height = max_y - min_y

        margin_x = max(width, height) * self._frame_x_percent
        margin_y = max(height, width) * self._frame_y_percent

        frame_left = min_x - margin_x
        frame_bottom = min_y - margin_y
        frame_right = max_x + margin_x
        frame_top = max_y + margin_y

        return frame_left, frame_bottom, frame_right, frame_top

    def _generate_grid_roads(self):
        """Generate a grid pattern road network"""
        bounds = self._boundary_polygon.bounds
        min_x, min_y, max_x, max_y = bounds

        # Calculate spacing based on block size
        x_spacing = self.layout_parameters.max_block_length + self.layout_parameters.main_road_width
        y_spacing = self.layout_parameters.max_block_width + self.layout_parameters.secondary_road_width

        # Generate main roads (horizontal)
        y = min_y + y_spacing
        while y < max_y - y_spacing:
            road_line = LineString([(min_x - 10, y), (max_x + 10, y)])
            intersection = road_line.intersection(self._boundary_polygon)

            if not intersection.is_empty:
                if intersection.geom_type == 'LineString':
                    coords = list(intersection.coords)
                    self._roads.append({
                        'type': 'main',
                        'centerline': coords,
                        'width': self.layout_parameters.main_road_width,
                        'name': f'Street {len(self._roads) + 1}'
                    })
                elif intersection.geom_type == 'MultiLineString':
                    for line in intersection.geoms:
                        coords = list(line.coords)
                        self._roads.append({
                            'type': 'main',
                            'centerline': coords,
                            'width': self.layout_parameters.main_road_width,
                            'name': f'Street {len(self._roads) + 1}'
                        })
            y += y_spacing

        # Generate secondary roads (vertical)
        x = min_x + x_spacing
        while x < max_x - x_spacing:
            road_line = LineString([(x, min_y - 10), (x, max_y + 10)])
            intersection = road_line.intersection(self._boundary_polygon)

            if not intersection.is_empty:
                if intersection.geom_type == 'LineString':
                    coords = list(intersection.coords)
                    self._roads.append({
                        'type': 'secondary',
                        'centerline': coords,
                        'width': self.layout_parameters.secondary_road_width,
                        'name': f'Avenue {chr(65 + len(self._roads) % 26)}'
                    })
                elif intersection.geom_type == 'MultiLineString':
                    for line in intersection.geoms:
                        coords = list(line.coords)
                        self._roads.append({
                            'type': 'secondary',
                            'centerline': coords,
                            'width': self.layout_parameters.secondary_road_width,
                            'name': f'Avenue {chr(65 + len(self._roads) % 26)}'
                        })
            x += x_spacing

    def _generate_radial_roads(self):
        """Generate a radial pattern road network"""
        centroid = self._boundary_polygon.centroid
        center_x, center_y = centroid.x, centroid.y

        # Generate radial roads
        num_radial = 8
        for i in range(num_radial):
            angle = (i * 360 / num_radial) * math.pi / 180

            # Create a long line from center
            end_x = center_x + math.cos(angle) * 1000
            end_y = center_y + math.sin(angle) * 1000

            road_line = LineString([(center_x, center_y), (end_x, end_y)])
            intersection = road_line.intersection(self._boundary_polygon)

            if not intersection.is_empty and intersection.geom_type == 'LineString':
                coords = list(intersection.coords)
                self._roads.append({
                    'type': 'main',
                    'centerline': coords,
                    'width': self.layout_parameters.main_road_width,
                    'name': f'Radial {i + 1}'
                })

        # Generate circular roads
        bounds = self._boundary_polygon.bounds
        max_radius = min(bounds[2] - bounds[0], bounds[3] - bounds[1]) / 2
        num_circles = 3

        for i in range(1, num_circles + 1):
            radius = (i * max_radius) / (num_circles + 1)
            circle = Point(center_x, center_y).buffer(radius)
            ring = circle.boundary
            intersection = ring.intersection(self._boundary_polygon)

            if not intersection.is_empty:
                if intersection.geom_type == 'LineString':
                    coords = list(intersection.coords)
                    self._roads.append({
                        'type': 'secondary',
                        'centerline': coords,
                        'width': self.layout_parameters.secondary_road_width,
                        'name': f'Ring Road {i}'
                    })
                elif intersection.geom_type == 'MultiLineString':
                    for j, line in enumerate(intersection.geoms):
                        coords = list(line.coords)
                        self._roads.append({
                            'type': 'secondary',
                            'centerline': coords,
                            'width': self.layout_parameters.secondary_road_width,
                            'name': f'Ring Road {i}-{chr(65 + j)}'
                        })

    def _generate_organic_roads(self):
        """Generate an organic/curved road network"""
        bounds = self._boundary_polygon.bounds
        min_x, min_y, max_x, max_y = bounds

        # Generate main curved roads
        num_main_roads = 3
        for i in range(num_main_roads):
            # Create curved path using control points
            t = (i + 1) / (num_main_roads + 1)
            start_y = min_y + t * (max_y - min_y)

            control_points = [
                (min_x, start_y),
                (min_x + (max_x - min_x) * 0.3, start_y + random.uniform(-20, 20)),
                (min_x + (max_x - min_x) * 0.7, start_y + random.uniform(-20, 20)),
                (max_x, start_y + random.uniform(-10, 10))
            ]

            # Create smooth curve through control points
            coords = create_smooth_curve(control_points, 20)
            road_line = LineString(coords)
            intersection = road_line.intersection(self._boundary_polygon)

            if not intersection.is_empty and intersection.geom_type == 'LineString':
                coords = list(intersection.coords)
                self._roads.append({
                    'type': 'main',
                    'centerline': coords,
                    'width': self.layout_parameters.main_road_width,
                    'name': f'Parkway {i + 1}'
                })

        # Add connecting roads
        self._add_organic_connectors()

    def _generate_mixed_roads(self):
        """Generate a mixed pattern combining grid and organic elements"""
        # Start with a partial grid
        self._generate_grid_roads()

        # Add some curved roads
        bounds = self._boundary_polygon.bounds
        min_x, min_y, max_x, max_y = bounds

        # Add diagonal/curved connector
        control_points = [
            (min_x, min_y),
            ((min_x + max_x) / 2, (min_y + max_y) / 2),
            (max_x, max_y)
        ]

        coords = create_smooth_curve(control_points, 30)
        road_line = LineString(coords)
        intersection = road_line.intersection(self._boundary_polygon)

        if not intersection.is_empty and intersection.geom_type == 'LineString':
            coords = list(intersection.coords)
            self._roads.append({
                'type': 'main',
                'centerline': coords,
                'width': self.layout_parameters.main_road_width,
                'name': 'Boulevard'
            })

    def _add_organic_connectors(self):
        """Add connecting roads for organic layout"""
        if len(self._roads) < 2:
            return

        # Connect roads with shorter segments
        for i in range(0, len(self._roads) - 1, 2):
            road1 = self._roads[i]['centerline']
            road2 = self._roads[min(i + 1, len(self._roads) - 1)]['centerline']

            # Find connection points
            mid1 = road1[len(road1) // 2]
            mid2 = road2[len(road2) // 2]

            self._roads.append({
                'type': 'access',
                'centerline': [mid1, mid2],
                'width': self.layout_parameters.access_road_width,
                'name': f'Lane {i + 1}'
            })

    def _generate_blocks(self):
        """Generate blocks from the road network"""
        # Create road polygons
        road_polygons = []
        for road in self._roads:
            centerline = LineString(road['centerline'])
            road_polygon = centerline.buffer(road['width'] / 2)
            road_polygons.append(road_polygon)

        # Union all road polygons
        if road_polygons:
            roads_union = unary_union(road_polygons)

            # Subtract roads from boundary to get blocks
            blocks_area = self._boundary_polygon.difference(roads_union)

            # Extract individual blocks
            if blocks_area.geom_type == 'Polygon':
                self._blocks = [blocks_area]
            elif blocks_area.geom_type == 'MultiPolygon':
                self._blocks = list(blocks_area.geoms)
        else:
            self._blocks = [self._boundary_polygon]

    def _allocate_green_spaces(self):
        """Allocate green spaces within the layout"""
        if not self._blocks:
            return

        total_area = self._boundary_polygon.area
        target_green_area = total_area * (self.layout_parameters.green_space_percentage / 100)
        current_green_area = 0

        # Sort blocks by area (largest first)
        sorted_blocks = sorted(self._blocks, key=lambda b: b.area, reverse=True)

        for block in sorted_blocks:
            if current_green_area >= target_green_area:
                break

            # Allocate center of large blocks as green space
            if block.area > 5000:  # Only for large blocks
                # Create green space in center
                centroid = block.centroid
                green_radius = min(20.0, math.sqrt(block.area) * 0.15)
                green_space = Point(centroid.x, centroid.y).buffer(green_radius)

                # Ensure it's within the block
                green_space = green_space.intersection(block)

                if not green_space.is_empty:
                    self._green_spaces.append(green_space)
                    current_green_area += green_space.area

                    # Remove green space from block for parcel generation
                    idx = self._blocks.index(block)
                    self._blocks[idx] = block.difference(green_space)

    def __generate_parcels_(self):
        """Generate parcels within blocks"""
        parcel_id = 1

        for block_idx, block in enumerate(self._blocks):
            if block.area < self.layout_parameters.min_parcel_area:
                continue

            # Generate parcels for this block
            block_parcels = self._subdivide_block(block, parcel_id)

            for parcel_polygon in block_parcels:
                # Create ParcelInfo
                vertices = list(parcel_polygon.exterior.coords[:-1])
                area = parcel_polygon.area

                # Calculate dimensions
                bounds = parcel_polygon.bounds
                width = bounds[2] - bounds[0]
                depth = bounds[3] - bounds[1]

                # Find street frontage (simplified - using minimum y coordinate edge)
                min_y = min(v[1] for v in vertices)
                frontage = [v for v in vertices if abs(v[1] - min_y) < 0.1]

                # Calculate buildable area (with setbacks)
                buildable = self._calculate_buildable_area(parcel_polygon)

                parcel_info = ParcelInfo(
                    id=f"P{parcel_id:04d}",
                    vertices=vertices,
                    area=area,
                    width=width,
                    depth=depth,
                    centroid=(parcel_polygon.centroid.x, parcel_polygon.centroid.y),
                    street_frontage=frontage,
                    buildable_area=buildable
                )

                self._parcels.append(parcel_info)
                parcel_id += 1

    def _generate_parcels(self):
        """Generate parcels (final plots) within blocks."""
        parcel_id = 1

        for block_idx, block in enumerate(self._blocks):
            if block.area < self.layout_parameters.min_parcel_area:
                continue

            # _subdivide_block now returns final plot polygons clipped to the block
            block_plots = self._subdivide_block(block, parcel_id)

            for plot_polygon in block_plots:
                # Create ParcelInfo
                vertices = list(plot_polygon.exterior.coords[:-1])
                area = plot_polygon.area

                # Calculate dimensions
                bounds = plot_polygon.bounds
                width = bounds[2] - bounds[0]
                depth = bounds[3] - bounds[1]

                # Find street frontage (simplified; can be improved later)
                # min_y = min(v[1] for v in vertices)
                # frontage = [v for v in vertices if abs(v[1] - min_y) < 0.1]
                frontage = self._find_street_frontage(plot_polygon)

                # Calculate buildable area (with setbacks)
                buildable = self._calculate_buildable_area(plot_polygon)

                parcel_info = ParcelInfo(
                    id=f"P{parcel_id:04d}",
                    vertices=vertices,
                    area=area,
                    width=width,
                    depth=depth,
                    centroid=(plot_polygon.centroid.x, plot_polygon.centroid.y),
                    street_frontage=frontage,
                    buildable_area=buildable
                )

                self._parcels.append(parcel_info)
                parcel_id += 1

    def __subdivide_block_(self, block: Polygon, start_id: int) -> List[Polygon]:
        """Subdivide a block into parcels"""
        parcels = []
        bounds = block.bounds
        min_x, min_y, max_x, max_y = bounds

        # Calculate parcel dimensions based on target area
        target_area = (self.layout_parameters.min_parcel_area + self.layout_parameters.max_parcel_area) / 2
        parcel_width = self.layout_parameters.min_parcel_width * 1.2
        parcel_depth = target_area / parcel_width

        # Generate grid of parcels
        current_y = min_y
        while current_y < max_y - parcel_depth / 2:
            current_x = min_x
            while current_x < max_x - parcel_width / 2:
                # Create parcel rectangle
                parcel_rect = Polygon([
                    (current_x, current_y),
                    (current_x + parcel_width, current_y),
                    (current_x + parcel_width, current_y + parcel_depth),
                    (current_x, current_y + parcel_depth)
                ])

                # Check intersection with block
                intersection = parcel_rect.intersection(block)

                if not intersection.is_empty and intersection.area > self.layout_parameters.min_parcel_area:
                    if intersection.geom_type == 'Polygon':
                        parcels.append(intersection)

                current_x += parcel_width
            current_y += parcel_depth

        return parcels

    def _subdivide_block(self, block: Polygon, start_id: int) -> List[Polygon]:
        """
        Subdivide a block into rectangular plots according to layout parameters.
        Handles leftover/remainder using layout_parameters.remainder_strategy:
          - 'separate'   -> leftover becomes its own plot (if large enough)
          - 'add_to_last'-> leftover added to last plot in row/column
          - 'distribute' -> leftover divided equally among the plots
        Returns a list of shapely.Polygon objects (each a final plot).
        """
        parcels: List[Polygon] = []

        min_x, min_y, max_x, max_y = block.bounds
        site_width = max_x - min_x
        site_height = max_y - min_y

        # Desired plot size (use explicit target if provided; fall back to existing logic)
        target_area = (self.layout_parameters.min_parcel_area + self.layout_parameters.max_parcel_area) / 2.0
        plot_width = getattr(self.layout_parameters, "plot_width",
                             max(self.layout_parameters.min_parcel_width * 1.2, 0.1))
        plot_depth = getattr(self.layout_parameters, "plot_depth",
                             max(target_area / max(plot_width, 0.0001), 0.1))

        min_area = self.layout_parameters.min_parcel_area
        strategy = getattr(self.layout_parameters, "remainder_strategy", "separate")

        # Compute integer counts and leftovers (ensure at least 1 column/row)
        n_cols = max(1, int(math.floor(site_width / plot_width)))
        n_rows = max(1, int(math.floor(site_height / plot_depth)))

        leftover_width = site_width - (n_cols * plot_width)
        leftover_height = site_height - (n_rows * plot_depth)

        # Build column widths array depending on strategy
        if abs(leftover_width) < 1e-6:
            col_widths = [plot_width] * n_cols
        else:
            if strategy == "separate":
                # create an extra column if leftover area is meaningful
                if leftover_width * plot_depth >= min_area:
                    col_widths = [plot_width] * n_cols + [leftover_width]
                else:
                    # too small -> add to last
                    col_widths = [plot_width] * n_cols
                    col_widths[-1] += leftover_width
            elif strategy == "add_to_last":
                col_widths = [plot_width] * n_cols
                col_widths[-1] += leftover_width
            elif strategy == "distribute":
                inc = leftover_width / n_cols if n_cols > 0 else 0
                col_widths = [plot_width + inc for _ in range(n_cols)]
            else:
                col_widths = [plot_width] * n_cols

        # Build row heights array depending on strategy
        if abs(leftover_height) < 1e-6:
            row_heights = [plot_depth] * n_rows
        else:
            if strategy == "separate":
                if leftover_height * plot_width >= min_area:
                    row_heights = [plot_depth] * n_rows + [leftover_height]
                else:
                    row_heights = [plot_depth] * n_rows
                    row_heights[-1] += leftover_height
            elif strategy == "add_to_last":
                row_heights = [plot_depth] * n_rows
                row_heights[-1] += leftover_height
            elif strategy == "distribute":
                inc_h = leftover_height / n_rows if n_rows > 0 else 0
                row_heights = [plot_depth + inc_h for _ in range(n_rows)]
            else:
                row_heights = [plot_depth] * n_rows

        # Now tile the block using the computed widths/heights and clip to block polygon
        EPS = 1e-8
        y = min_y
        for h in row_heights:
            x = min_x
            for w in col_widths:
                rect = Polygon([
                    (x, y),
                    (x + w, y),
                    (x + w, y + h),
                    (x, y + h)
                ])
                inter = rect.intersection(block)

                if inter.is_empty:
                    x += w
                    continue

                # handle both Polygon and MultiPolygon intersections
                if inter.geom_type == "Polygon":
                    if inter.area + EPS >= min_area:
                        parcels.append(inter)
                elif inter.geom_type == "MultiPolygon":
                    for poly in inter.geoms:
                        if poly.area + EPS >= min_area:
                            parcels.append(poly)
                else:
                    # ignore tiny slivers (Points/Lines) or very small areas
                    pass

                x += w
            y += h

        # Fallback: if no parcel created (e.g. very small block), return the block itself if big enough
        if not parcels and block.area >= min_area:
            parcels = [block]

        return parcels

    def _calculate_buildable_area(self, parcel: Polygon) -> List[Tuple[float, float]]:
        """Calculate buildable area within a parcel considering setbacks"""
        # Apply setbacks
        buildable = parcel.buffer(-self.layout_parameters.front_setback)

        if buildable.is_empty or buildable.geom_type != 'Polygon':
            return []

        return list(buildable.exterior.coords[:-1])

    def _find_street_frontage(self, plot: Polygon) -> List[Tuple[float, float]]:
        """
        Return coordinates of the frontage edge (side touching a road).
        Falls back to lowest Y edge if no road adjacency found.
        """
        EPS = 1e-6
        frontage: List[Tuple[float, float]] = []

        if self._roads_union is not None:
            max_len = 0.0
            frontage_coords = None

            # Iterate over each edge of the plot
            coords = list(plot.exterior.coords)
            for i in range(len(coords) - 1):
                p1, p2 = coords[i], coords[i + 1]
                edge = LineString([p1, p2])
                # If edge touches a road, consider it frontage
                if edge.buffer(EPS).intersects(self._roads_union):
                    if edge.length > max_len:
                        max_len = edge.length
                        frontage_coords = [p1, p2]

            if frontage_coords:
                frontage = frontage_coords

        # Fallback: use lowest Y edge
        if not frontage:
            coords = list(plot.exterior.coords)
            min_y = min(c[1] for c in coords)
            frontage = [c for c in coords if abs(c[1] - min_y) < 0.1]

        return frontage

    def _get_drawing_extent(self) -> float:
        # get bounding box
        min_x, min_y, max_x, max_y = self._bounding_box
        if min_x is None or min_y is None or max_x is None or max_y is None:
            return 0.0

        width = max_x - min_x
        height = max_y - min_y
        extent = math.sqrt(width ** 2 + height ** 2)
        return extent

    def _add_leg_labels(self, leg, orientation: str):
        """Add distance and bearing labels to a leg."""
        # Angle and positions
        angle_rad = math.atan2(leg.to.northing - leg.from_.northing,
                               leg.to.easting - leg.from_.easting)
        angle_deg = math.degrees(angle_rad)

        # Fractional positions
        first_x = leg.from_.easting + (0.2 * (leg.to.easting - leg.from_.easting))
        first_y = leg.from_.northing + (0.2 * (leg.to.northing - leg.from_.northing))
        last_x = leg.from_.easting + (0.8 * (leg.to.easting - leg.from_.easting))
        last_y = leg.from_.northing + (0.8 * (leg.to.northing - leg.from_.northing))
        mid_x = (leg.from_.easting + leg.to.easting) / 2
        mid_y = (leg.from_.northing + leg.to.northing) / 2

        # Offset text above/below the line
        normals = line_normals((leg.from_.easting, leg.from_.northing), (leg.to.easting, leg.to.northing), orientation)
        offset_distance = self._get_drawing_extent() * 0.02
        offset_inside_x = (normals[0][0] / math.hypot(*normals[0])) * offset_distance
        offset_inside_y = (normals[0][1] / math.hypot(*normals[0])) * offset_distance
        offset_outside_x = (normals[1][0] / math.hypot(*normals[1])) * offset_distance
        offset_outside_y = (normals[1][1] / math.hypot(*normals[1])) * offset_distance

        first_x += offset_outside_x
        first_y += offset_outside_y
        last_x += offset_outside_x
        last_y += offset_outside_y
        mid_x += offset_inside_x
        mid_y += offset_inside_y

        # Text angle adjustment
        text_angle = angle_deg
        if text_angle > 90 or text_angle < -90:
            text_angle += 180

        # Add labels
        self._drawer.add_label(f"{leg.distance:.2f} m", mid_x, mid_y,
                               angle=text_angle, height=self.label_size)
        ld = line_direction(angle_deg)
        if ld == "left → right":
            self._drawer.add_label(f"{format_number(leg.bearing.degrees, "hundredth")}°", first_x, first_y,
                                   angle=text_angle, height=self.label_size)
            self._drawer.add_label(f"{format_number(leg.bearing.minutes, "tenth")}'", last_x, last_y,
                                   angle=text_angle, height=self.label_size)
        else:
            self._drawer.add_label(f"{format_number(leg.bearing.degrees, "hundredth")}°", last_x, last_y,
                                   angle=text_angle, height=self.label_size)
            self._drawer.add_label(f"{format_number(leg.bearing.minutes, "tenth")}'", first_x, first_y,
                                   angle=text_angle, height=self.label_size)

    def _draw_boundary(self):
        if not self.layout_boundary:
            return

        boundary_points = [(coord.easting, coord.northing) for coord in self.layout_boundary.coordinates]
        if not boundary_points:
            return

        self._drawer.add_boundary(boundary_points)
        orientation = polygon_orientation(boundary_points)

        for leg in self.layout_boundary.legs:
            self._add_leg_labels(leg, orientation)

    def _draw_roads(self):
        """Draw roads with proper width and centerlines"""
        for road in self._roads:
            centerline = road['centerline']
            width = road['width']

            # Draw centerline
            points = [(x, y) for x, y in centerline]
            self._drawer.add_road_cl(points)

            # Draw road edges
            road_line = LineString(centerline)
            left_edge = road_line.parallel_offset(width / 2, 'left')
            right_edge = road_line.parallel_offset(width / 2, 'right')

            if not left_edge.is_empty:
                if left_edge.geom_type == 'LineString':
                    points = [(x, y) for x, y in left_edge.coords]
                    self._drawer.add_road(points)

            if not right_edge.is_empty:
                if right_edge.geom_type == 'LineString':
                    points = [(x, y) for x, y in right_edge.coords]
                    self._drawer.add_road(points)

    def _draw_parcels(self):
        """Draw parcels with IDs and dimensions"""
        for parcel in self._parcels:
            # Draw parcel boundary
            points = [(x, y) for x, y in parcel.vertices]
            self._drawer.add_parcel("", points)

            # Draw buildable area if available
            if parcel.buildable_area:
                points = [(x, y) for x, y in parcel.buildable_area]
                self._drawer.add_buildable(points)

            # Add parcel ID at centroid
            self._drawer.add_text(parcel.id, parcel.centroid[0], parcel.centroid[1], 0.5)

            # Add area annotation
            area_text = f"{parcel.area:.1f} m²"
            self._drawer.add_text(area_text, parcel.centroid[0], parcel.centroid[1] - 3, 0.5)

    def _draw_green_spaces(self):
        """Draw green spaces"""
        for green_space in self._green_spaces:
            if green_space.geom_type == 'Polygon':
                coords = list(green_space.exterior.coords[:-1])
                points = [(x, y) for x, y in coords]
                self._drawer.add_greenspace(points, coords)

    def _add_annotations(self):
        """Add dimensions and annotations"""
        # Add overall dimensions
        bounds = self._boundary_polygon.bounds
        min_x, min_y, max_x, max_y = bounds

        # Width dimension
        dim = self._drawer.msp.add_linear_dim(
            base=(min_x, min_y - 10),
            p1=(min_x, min_y),
            p2=(max_x, min_y),
            dimstyle='EZDXF',
            dxfattribs={'layer': 'DIMENSIONS'}
        )
        dim.render()

        # Height dimension
        dim = self._drawer.msp.add_linear_dim(
            base=(min_x - 10, min_y),
            p1=(min_x, min_y),
            p2=(min_x, max_y),
            dimstyle='EZDXF',
            dxfattribs={'layer': 'DIMENSIONS'}
        )
        dim.render()

    def _add_legend(self):
        """Add legend to the drawing"""
        bounds = self._boundary_polygon.bounds
        max_x, min_y = bounds[2], bounds[1]

        # Position legend
        legend_x = max_x + 20
        legend_y = min_y + 50

        # Legend items
        legend_items = [
            ('BOUNDARY', 'Site Boundary', 1),
            ('PARCELS', 'Parcel Lines', 3),
            ('ROADS', 'Road Edges', 7),
            ('ROADS_CL', 'Road Centerlines', 4),
            ('BUILDABLE', 'Buildable Area', 8),
            ('GREEN_SPACE', 'Green Space', 82),
        ]

        # Draw legend box
        box_width = 40
        box_height = len(legend_items) * 5 + 10

        self._drawer.msp.add_lwpolyline([
            (legend_x, legend_y),
            (legend_x + box_width, legend_y),
            (legend_x + box_width, legend_y + box_height),
            (legend_x, legend_y + box_height),
            (legend_x, legend_y)
        ], dxfattribs={'layer': 'TEXT'})

        # Add title
        self._drawer.msp.add_text(
            'LEGEND',
            dxfattribs={
                'layer': 'TEXT',
                'height': 2.0,
                'style': 'STANDARD'
            }
        ).set_placement((legend_x + 2, legend_y + box_height - 5))

        # Add legend items
        current_y = legend_y + box_height - 10
        for layer_name, description, color in legend_items:
            # Draw sample line
            self._drawer.msp.add_line(
                (legend_x + 2, current_y),
                (legend_x + 8, current_y),
                dxfattribs={'layer': layer_name}
            )

            # Add description
            self._drawer.msp.add_text(
                description,
                dxfattribs={
                    'layer': 'TEXT',
                    'height': 1.2,
                    'style': 'STANDARD'
                }
            ).set_placement((legend_x + 10, current_y - 0.5))

            current_y -= 5

    def _get_current_date(self) -> str:
        """Get current date as string"""
        from datetime import datetime
        return datetime.now().strftime('%Y-%m-%d')

    def add_utilities(self):
        """Add utility lines and easements"""
        # Add utility corridors along main roads
        for road in self._roads:
            if road['type'] == 'main':
                centerline = LineString(road['centerline'])

                # Add water line (offset from centerline)
                water_line = centerline.parallel_offset(road['width'] / 3, 'left')
                if not water_line.is_empty and water_line.geom_type == 'LineString':
                    points = [(x, y) for x, y in water_line.coords]
                    self._drawer.msp.add_lwpolyline(points, dxfattribs={'layer': 'UTILITIES', 'color': 5})

                # Add sewer line (offset from centerline)
                sewer_line = centerline.parallel_offset(road['width'] / 3, 'right')
                if not sewer_line.is_empty and sewer_line.geom_type == 'LineString':
                    points = [(x, y) for x, y in sewer_line.coords]
                    self._drawer.msp.add_lwpolyline(points, dxfattribs={'layer': 'UTILITIES', 'color': 94})

    def add_easements(self):
        """Add easements for utilities and access"""
        for parcel in self._parcels:
            parcel_polygon = Polygon(parcel.vertices)

            # Create utility easement along street frontage
            if parcel.street_frontage and len(parcel.street_frontage) >= 2:
                frontage_line = LineString(parcel.street_frontage)
                easement = frontage_line.buffer(2.0)  # 2m easement
                easement = easement.intersection(parcel_polygon)

                if not easement.is_empty and easement.geom_type == 'Polygon':
                    coords = list(easement.exterior.coords[:-1])
                    points = [(x, y) for x, y in coords]
                    self._drawer.msp.add_lwpolyline(points, close=True, dxfattribs={'layer': 'EASEMENTS'})

    def draw_title_block(self):
        """Add title block to the frame."""
        min_x, min_y, max_x, max_y = self._bounding_box
        width, height = max_x - min_x, max_y - min_y

        margin_x, margin_y = max(width, height) * self._frame_x_percent, max(height, width) * self._frame_y_percent
        frame_left, frame_bottom = min_x - margin_x, min_y - margin_y
        frame_right, frame_top = max_x + margin_x, max_y + margin_y

        frame_width = frame_right - frame_left
        frame_center_x = frame_left + (frame_width / 2)

        title_y = frame_top - (margin_y * 0.2)
        self._drawer.draw_title_block(html_to_mtext(self.build_title()),
                                      frame_center_x,
                                      title_y,
                                      frame_width * 0.6,
                                      self.font_size,
                                      graphical_scale_length=(self._frame_coords[2] - self._frame_coords[0]) * 0.4,
                                      area=f"AREA :- {self.layout_boundary.area} SQ.METRES",
                                      origin=f"ORIGIN :- {self.origin.upper()}")

    def draw_footer_boxes(self):
        if len(self.footers) == 0:
            return

        x_min = self._frame_coords[0]
        y_min = self._frame_coords[1]
        x_max = self._frame_coords[2]
        y_max = self._frame_coords[3]

        box_width = (x_max - x_min) / len(self.footers)
        box_height = (y_max - y_min) * 0.2

        for i, footer in enumerate(self.footers):
            x1 = x_min + i * box_width
            x2 = x1 + box_width
            y1 = y_min
            y2 = y1 + box_height
            self._drawer.draw_footer_box(html_to_mtext(footer), x1, y1, x2, y2, self.footer_size)

    def draw_frames(self):
        """Draw outer and offset frames."""
        min_x, min_y, max_x, max_y = self._bounding_box
        width, height = max_x - min_x, max_y - min_y

        margin_x, margin_y = max(width, height) * self._frame_x_percent, max(height, width) * self._frame_y_percent
        frame_left, frame_bottom = min_x - margin_x, min_y - margin_y
        frame_right, frame_top = max_x + margin_x, max_y + margin_y
        self._drawer.draw_frame(frame_left, frame_bottom, frame_right, frame_top)

        # offset_x, offset_y = max(width, height) * (self._frame_x_percent + 0.03), max(height, width) * (
        #             self._frame_y_percent + 0.03)
        # self._drawer.draw_frame(min_x - offset_x, min_y - offset_y,
        #                         max_x + offset_x, max_y + offset_y)

    def draw_north_arrow(self):
        if len(self.layout_boundary.coordinates) == 0:
            return

        coord = self._boundary_dict[self.layout_boundary.coordinates[0].id]
        height = (self._frame_coords[3] - self._frame_coords[1]) * 0.07
        self._drawer.draw_north_arrow(coord.easting, self._frame_coords[3] - height, height)

        # for easting label
        width = (self._frame_coords[2] - self._frame_coords[0]) * 0.1

        self._drawer.add_north_arrow_label((self._frame_coords[0], coord.northing),
                                           (self._frame_coords[0] + width, coord.northing), f"{coord.easting}mE",
                                           self.label_size)
        self._drawer.add_north_arrow_label((self._frame_coords[2], coord.northing),
                                           (self._frame_coords[2] - width, coord.northing), "",
                                           self.label_size)

        # for northing label
        northing_label_y = self._frame_coords[1]
        if len(self.footers) > 0:
            northing_label_y = northing_label_y + ((self._frame_coords[3] - self._frame_coords[1]) * 0.2)

        self._drawer.add_north_arrow_label((coord.easting, northing_label_y),
                                           (coord.easting, northing_label_y + height), f"{coord.northing}mN",
                                           self.label_size)
        self._drawer.draw_north_arrow_cross(coord.easting, coord.northing, self.beacon_size * 3)

    def draw_beacons(self):
        if not self.layout_boundary:
            return

        check = []
        for coord in self.layout_boundary.coordinates:
            if coord.id in check:
                continue
            self._drawer.draw_beacon(coord.easting, coord.northing, 0, self.label_size, self._get_drawing_extent(),
                                     coord.id)
            check.append(coord.id)

    def draw(self):
        # Generate road network based on subdivision type
        if self.layout_parameters.subdivision_type == "grid":
            self._generate_grid_roads()
        elif self.layout_parameters.subdivision_type == "radial":
            self._generate_radial_roads()
        elif self.layout_parameters.subdivision_type == "organic":
            self._generate_organic_roads()
        else:  # mixed
            self._generate_mixed_roads()

        # Generate blocks from road network
        self._generate_blocks()

        # Add green spaces if required
        if self.layout_parameters.include_green_spaces:
            self._allocate_green_spaces()

        # Generate parcels within blocks
        self._generate_parcels()

        # Draw all elements
        self._draw_boundary()
        self._draw_roads()
        self._draw_parcels()
        # self._draw_green_spaces()
        # self._add_annotations()
        self.draw_frames()
        self.draw_title_block()
        self.draw_footer_boxes()
        self.draw_north_arrow()
        self.draw_beacons()
        # self._add_legend()

    def save_dxf(self, file_path: str):
        self._drawer.save_dxf(file_path)

    def save(self) -> str:
        return self._drawer.save(paper_size=self.page_size, orientation=self.page_orientation)


