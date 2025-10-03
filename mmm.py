import ezdxf
import numpy as np
from typing import List, Tuple, Dict, Optional
from dataclasses import dataclass
from enum import Enum
import math
from shapely.geometry import Polygon, LineString, Point, MultiPolygon
from shapely.ops import split, unary_union
from shapely.affinity import translate, rotate
import warnings

warnings.filterwarnings('ignore')


class RemainderHandling(Enum):
    """How to handle leftover land after subdivision"""
    SEPARATE_PLOT = "separate"
    ADD_TO_LAST = "add_to_last"
    DISTRIBUTE_EQUALLY = "distribute"


@dataclass
class RoadConfig:
    """Configuration for road network"""
    main_road_width: float = 12.0  # meters
    secondary_road_width: float = 9.0  # meters
    access_road_width: float = 6.0  # meters
    road_spacing: float = 100.0  # approximate spacing between roads
    orientation: str = "grid"  # 'grid', 'radial', or 'organic'
    main_road_angle: float = 0.0  # angle in degrees for main roads


@dataclass
class PlotConfig:
    """Configuration for individual plots"""
    width: float = 15.0  # meters
    depth: float = 30.0  # meters
    min_area: float = 300.0  # square meters
    setback_front: float = 3.0  # meters
    setback_sides: float = 1.5  # meters
    setback_rear: float = 1.5  # meters
    remainder_handling: RemainderHandling = RemainderHandling.ADD_TO_LAST


@dataclass
class LayoutConfig:
    """Overall layout configuration"""
    boundary_coords: List[Tuple[float, float]]
    road_config: RoadConfig
    plot_config: PlotConfig
    green_space_percentage: float = 0.05  # 5% for parks/utilities
    corner_radius: float = 3.0  # radius for road corners


class LayoutSurveyGenerator:
    """Generate layout surveys with parcels, roads, and subdivisions"""

    def __init__(self, config: LayoutConfig):
        self.config = config
        self.doc = ezdxf.new('R2018')
        self.msp = self.doc.modelspace()
        self.setup_layers()
        self.boundary_polygon = Polygon(config.boundary_coords)
        self.parcels = []
        self.roads = []
        self.plots = []

    def setup_layers(self):
        """Create layers for different elements"""
        layers = {
            'BOUNDARY': {'color': 1},  # Red
            'ROADS': {'color': 254},  # Gray
            'PARCELS': {'color': 3},  # Green
            'PLOTS': {'color': 5},  # Blue
            'DIMENSIONS': {'color': 7},  # White
            'LABELS': {'color': 7},  # White
            'SETBACKS': {'color': 8},  # Dark gray
            'UTILITIES': {'color': 6},  # Magenta
        }

        for name, props in layers.items():
            layer = self.doc.layers.add(name)
            layer.color = props['color']

    def generate_road_network(self) -> List[Polygon]:
        """Generate road network based on configuration"""
        roads = []
        bounds = self.boundary_polygon.bounds
        min_x, min_y, max_x, max_y = bounds

        if self.config.road_config.orientation == "grid":
            roads = self._create_grid_roads(min_x, min_y, max_x, max_y)
        elif self.config.road_config.orientation == "radial":
            roads = self._create_radial_roads(min_x, min_y, max_x, max_y)
        else:  # organic
            roads = self._create_organic_roads(min_x, min_y, max_x, max_y)

        # Clip roads to boundary
        clipped_roads = []
        for road in roads:
            intersection = road.intersection(self.boundary_polygon)
            if not intersection.is_empty:
                if isinstance(intersection, Polygon):
                    clipped_roads.append(intersection)
                elif isinstance(intersection, MultiPolygon):
                    clipped_roads.extend(list(intersection.geoms))

        self.roads = clipped_roads
        return clipped_roads

    def _create_grid_roads(self, min_x, min_y, max_x, max_y) -> List[Polygon]:
        """Create a grid pattern of roads"""
        roads = []
        spacing = self.config.road_config.road_spacing
        main_width = self.config.road_config.main_road_width
        secondary_width = self.config.road_config.secondary_road_width

        # Horizontal roads
        y = min_y
        road_count = 0
        while y <= max_y:
            width = main_width if road_count % 3 == 0 else secondary_width
            road = Polygon([
                (min_x - 10, y),
                (max_x + 10, y),
                (max_x + 10, y + width),
                (min_x - 10, y + width)
            ])
            roads.append(road)
            y += spacing
            road_count += 1

        # Vertical roads
        x = min_x
        road_count = 0
        while x <= max_x:
            width = main_width if road_count % 3 == 0 else secondary_width
            road = Polygon([
                (x, min_y - 10),
                (x + width, min_y - 10),
                (x + width, max_y + 10),
                (x, max_y + 10)
            ])
            roads.append(road)
            x += spacing
            road_count += 1

        return roads

    def _create_radial_roads(self, min_x, min_y, max_x, max_y) -> List[Polygon]:
        """Create a radial pattern of roads"""
        roads = []
        center_x = (min_x + max_x) / 2
        center_y = (min_y + max_y) / 2
        max_radius = max(max_x - center_x, max_y - center_y) * 1.5

        # Circular roads
        radius = self.config.road_config.road_spacing
        while radius < max_radius:
            circle_outer = Point(center_x, center_y).buffer(radius + self.config.road_config.main_road_width / 2)
            circle_inner = Point(center_x, center_y).buffer(radius - self.config.road_config.main_road_width / 2)
            road = circle_outer.difference(circle_inner)
            if isinstance(road, Polygon):
                roads.append(road)
            radius += self.config.road_config.road_spacing

        # Radial roads
        num_radials = 8
        for i in range(num_radials):
            angle = (360 / num_radials) * i
            rad = math.radians(angle)

            # Create a long rectangular road from center
            road_length = max_radius
            road_width = self.config.road_config.secondary_road_width

            # Create rectangle at origin
            road = Polygon([
                (-road_width / 2, 0),
                (road_width / 2, 0),
                (road_width / 2, road_length),
                (-road_width / 2, road_length)
            ])

            # Rotate and translate to position
            road = rotate(road, angle, origin=(0, 0))
            road = translate(road, center_x, center_y)
            roads.append(road)

        return roads

    def _create_organic_roads(self, min_x, min_y, max_x, max_y) -> List[Polygon]:
        """Create an organic pattern of roads (simplified curved roads)"""
        roads = []

        # Create main arterial roads with slight curves
        num_horizontal = int((max_y - min_y) / self.config.road_config.road_spacing)
        num_vertical = int((max_x - min_x) / self.config.road_config.road_spacing)

        # Horizontal arterials with sine wave variation
        for i in range(1, num_horizontal):
            y_base = min_y + i * self.config.road_config.road_spacing
            points = []
            for x in np.linspace(min_x - 10, max_x + 10, 20):
                y_offset = 10 * math.sin((x - min_x) / 50)
                points.append((x, y_base + y_offset))

            line = LineString(points)
            road = line.buffer(self.config.road_config.main_road_width / 2)
            roads.append(road)

        # Vertical roads with cosine variation
        for i in range(1, num_vertical):
            x_base = min_x + i * self.config.road_config.road_spacing
            points = []
            for y in np.linspace(min_y - 10, max_y + 10, 20):
                x_offset = 10 * math.cos((y - min_y) / 50)
                points.append((x_base + x_offset, y))

            line = LineString(points)
            road = line.buffer(self.config.road_config.secondary_road_width / 2)
            roads.append(road)

        return roads

    def create_parcels(self) -> List[Polygon]:
        """Create parcels by subtracting roads from boundary"""
        # Union all roads
        if not self.roads:
            self.generate_road_network()

        road_union = unary_union(self.roads)

        # Subtract roads from boundary to get buildable area
        buildable_area = self.boundary_polygon.difference(road_union)

        # Extract individual parcels
        if isinstance(buildable_area, Polygon):
            self.parcels = [buildable_area]
        elif isinstance(buildable_area, MultiPolygon):
            self.parcels = list(buildable_area.geoms)
        else:
            self.parcels = []

        return self.parcels

    def subdivide_parcel(self, parcel: Polygon) -> List[Polygon]:
        """Subdivide a parcel into individual plots"""
        plots = []
        bounds = parcel.bounds
        min_x, min_y, max_x, max_y = bounds

        plot_width = self.config.plot_config.width
        plot_depth = self.config.plot_config.depth

        # Calculate how many plots can fit
        num_cols = int((max_x - min_x) / plot_width)
        num_rows = int((max_y - min_y) / plot_depth)

        if num_cols == 0 or num_rows == 0:
            # Parcel too small for standard plots
            return [parcel]

        # Create grid of plots
        created_plots = []
        for row in range(num_rows):
            for col in range(num_cols):
                x = min_x + col * plot_width
                y = min_y + row * plot_depth

                plot = Polygon([
                    (x, y),
                    (x + plot_width, y),
                    (x + plot_width, y + plot_depth),
                    (x, y + plot_depth)
                ])

                # Check if plot is within parcel
                if parcel.contains(plot):
                    created_plots.append(plot)
                elif parcel.intersects(plot):
                    # Partial plot - clip to parcel boundary
                    intersection = parcel.intersection(plot)
                    if isinstance(intersection, Polygon) and intersection.area > self.config.plot_config.min_area:
                        created_plots.append(intersection)

        # Handle remainder
        all_plots_union = unary_union(created_plots)
        remainder = parcel.difference(all_plots_union)

        if not remainder.is_empty and isinstance(remainder, Polygon):
            if remainder.area > 0:
                if self.config.plot_config.remainder_handling == RemainderHandling.SEPARATE_PLOT:
                    if remainder.area >= self.config.plot_config.min_area:
                        created_plots.append(remainder)
                elif self.config.plot_config.remainder_handling == RemainderHandling.ADD_TO_LAST:
                    if created_plots:
                        # Merge with last plot
                        last_plot = created_plots[-1]
                        merged = unary_union([last_plot, remainder])
                        created_plots[-1] = merged
                elif self.config.plot_config.remainder_handling == RemainderHandling.DISTRIBUTE_EQUALLY:
                    # This is complex - simplified version adds to adjacent plots
                    if created_plots:
                        # Find plots that touch the remainder
                        for i, plot in enumerate(created_plots):
                            if plot.touches(remainder):
                                portion = remainder.intersection(plot.buffer(0.1))
                                if not portion.is_empty:
                                    created_plots[i] = unary_union([plot, portion])
                                    break

        return created_plots

    def add_plot_setbacks(self, plot: Polygon) -> Polygon:
        """Add setbacks to a plot"""
        # Create inset polygon for buildable area
        setback_polygon = plot.buffer(
            -self.config.plot_config.setback_front,
            join_style=2
        )
        return setback_polygon

    def draw_to_dxf(self):
        """Draw all elements to DXF"""
        # Draw boundary
        boundary_points = list(self.config.boundary_coords)
        boundary_points.append(boundary_points[0])  # Close polygon
        self.msp.add_lwpolyline(boundary_points, dxfattribs={'layer': 'BOUNDARY', 'closed': True})

        # Draw roads
        for road in self.roads:
            if isinstance(road, Polygon):
                coords = list(road.exterior.coords)
                self.msp.add_lwpolyline(coords, dxfattribs={
                    'layer': 'ROADS',
                    'closed': True
                })
                # Add hatch for roads
                hatch = self.msp.add_hatch(dxfattribs={'layer': 'ROADS', 'color': 254})
                hatch.paths.add_polyline_path(coords, is_closed=True)
                hatch.set_solid_fill()

        # Draw parcels and plots
        for i, parcel in enumerate(self.parcels):
            # Draw parcel boundary
            parcel_coords = list(parcel.exterior.coords)
            self.msp.add_lwpolyline(parcel_coords, dxfattribs={
                'layer': 'PARCELS',
                'closed': True,
                'color': 3
            })

            # Subdivide and draw plots
            plots = self.subdivide_parcel(parcel)
            for j, plot in enumerate(plots):
                if isinstance(plot, Polygon):
                    plot_coords = list(plot.exterior.coords)
                    self.msp.add_lwpolyline(plot_coords, dxfattribs={
                        'layer': 'PLOTS',
                        'closed': True
                    })

                    # Add plot label
                    centroid = plot.centroid
                    label = f"P{i + 1}-{j + 1}"
                    area = plot.area

                    self.msp.add_text(
                        label,
                        dxfattribs={
                            'layer': 'LABELS',
                            'height': 2,
                            'style': 'Standard'
                        }
                    ).set_placement((centroid.x, centroid.y))

                    self.msp.add_text(
                        f"{area:.1f} m²",
                        dxfattribs={
                            'layer': 'LABELS',
                            'height': 1.5,
                            'style': 'Standard'
                        }
                    ).set_placement((centroid.x, centroid.y - 3))

                    # Draw setbacks
                    setback = self.add_plot_setbacks(plot)
                    if isinstance(setback, Polygon) and not setback.is_empty:
                        setback_coords = list(setback.exterior.coords)
                        self.msp.add_lwpolyline(setback_coords, dxfattribs={
                            'layer': 'SETBACKS',
                            'closed': True,
                            'linetype': 'DASHED'
                        })

        # Add dimensions for some plots
        if self.plots:
            for plot in self.plots[:5]:  # Just first 5 to avoid clutter
                if isinstance(plot, Polygon):
                    coords = list(plot.exterior.coords)
                    if len(coords) >= 4:
                        # Add dimension for width
                        dim = self.msp.add_linear_dim(
                            base=(coords[0][0], coords[0][1] - 5),
                            p1=coords[0],
                            p2=coords[1],
                            dxfattribs={'layer': 'DIMENSIONS'}
                        )
                        dim.render()

                        # Add dimension for depth
                        dim = self.msp.add_linear_dim(
                            base=(coords[0][0] - 5, coords[0][1]),
                            p1=coords[0],
                            p2=coords[3] if len(coords) > 3 else coords[-2],
                            angle=90,
                            dxfattribs={'layer': 'DIMENSIONS'}
                        )
                        dim.render()

    def save(self, filename: str):
        """Save the DXF file"""
        self.doc.saveas(filename)
        print(f"Layout survey saved to {filename}")

    def get_statistics(self) -> Dict:
        """Get statistics about the generated layout"""
        total_plots = sum(len(self.subdivide_parcel(p)) for p in self.parcels)
        total_road_area = sum(r.area for r in self.roads)
        total_plot_area = sum(p.area for p in self.parcels)

        return {
            'total_area': self.boundary_polygon.area,
            'num_parcels': len(self.parcels),
            'num_plots': total_plots,
            'road_area': total_road_area,
            'buildable_area': total_plot_area,
            'road_percentage': (total_road_area / self.boundary_polygon.area) * 100,
            'efficiency': (total_plot_area / self.boundary_polygon.area) * 100
        }


def example_usage():
    """Example of how to use the layout generator"""

    # Define boundary coordinates (example: rectangular site)
    boundary = [
        (0, 0),
        (300, 0),
        (300, 200),
        (0, 200)
    ]

    # Configure roads
    road_config = RoadConfig(
        main_road_width=12.0,
        secondary_road_width=9.0,
        access_road_width=6.0,
        road_spacing=80.0,
        orientation="grid"  # Try 'radial' or 'organic' too
    )

    # Configure plots
    plot_config = PlotConfig(
        width=15.0,
        depth=25.0,
        min_area=250.0,
        setback_front=3.0,
        setback_sides=1.5,
        setback_rear=1.5,
        remainder_handling=RemainderHandling.ADD_TO_LAST
    )

    # Create layout configuration
    layout_config = LayoutConfig(
        boundary_coords=boundary,
        road_config=road_config,
        plot_config=plot_config,
        green_space_percentage=0.05,
        corner_radius=3.0
    )

    # Generate layout
    generator = LayoutSurveyGenerator(layout_config)
    generator.generate_road_network()
    generator.create_parcels()
    generator.draw_to_dxf()

    # Save to file
    generator.save("layout_survey.dxf")

    # Print statistics
    stats = generator.get_statistics()
    print("\nLayout Statistics:")
    print(f"Total Area: {stats['total_area']:.2f} m²")
    print(f"Number of Parcels: {stats['num_parcels']}")
    print(f"Number of Plots: {stats['num_plots']}")
    print(f"Road Area: {stats['road_area']:.2f} m² ({stats['road_percentage']:.1f}%)")
    print(f"Buildable Area: {stats['buildable_area']:.2f} m² ({stats['efficiency']:.1f}%)")


if __name__ == "__main__":
    example_usage()
