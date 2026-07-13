"""Route survey (longitudinal profile) plan generator.

Draws the ground profile as a spline over a station/elevation grid, with a
table of chainages and ground elevations below the graph.
"""

import math
from typing import ClassVar

from ezdxf.enums import TextEntityAlignment

from dxf_manager import SurveyDXFManager
from models.plan import PlanType
from plans.base import BasePlan


class RoutePlan(BasePlan):
    expected_type: ClassVar[PlanType] = PlanType.ROUTE

    def _compute_bounding_box(self):
        box = self.get_route_plan_bounding_box()
        if box is None:
            raise ValueError("Route plans require elevations and longitudinal profile parameters.")
        return box

    def _setup_layers(self, drawer: SurveyDXFManager):
        drawer.setup_route_layers()

    def get_elevation_interval(self) -> float:
        """Pick a 'nice' grid interval that yields roughly 8 elevation lines."""
        elevations = [e.elevation for e in self.elevations]
        elev_range = max(elevations) - min(elevations)
        if elev_range <= 0:
            return 1.0

        raw_interval = elev_range / 8
        exp = math.floor(math.log10(raw_interval))
        frac = raw_interval / 10 ** exp
        if frac < 1.5:
            nice = 1
        elif frac < 3:
            nice = 2
        elif frac < 7:
            nice = 5
        else:
            nice = 10
        return nice * 10 ** exp

    def _elevation_grid_range(self):
        """Grid elevation range: the data range padded by itself on both sides,
        snapped outwards to whole intervals."""
        params = self.longitudinal_profile_parameters
        interval = params.elevation_interval or self.get_elevation_interval()

        elevations = [e.elevation for e in self.elevations]
        min_elev, max_elev = min(elevations), max(elevations)
        pad = (max_elev - min_elev) or interval

        elev_start = math.floor((min_elev - pad) / interval) * interval
        elev_end = math.ceil((max_elev + pad) / interval) * interval
        return elev_start, elev_end, interval, min_elev

    def _elevation_to_y(self, elevation: float) -> float:
        """Y drawing coordinate of an elevation; the profile origin maps to
        the minimum ground elevation."""
        params = self.longitudinal_profile_parameters
        min_elev = min(e.elevation for e in self.elevations)
        return params.profile_origin[1] + (elevation - min_elev) * params.vertical_scale

    def draw_grid(self):
        params = self.longitudinal_profile_parameters
        elev_start, elev_end, interval, _ = self._elevation_grid_range()

        min_x, _, max_x, _ = self._bounding_box
        min_y = self._elevation_to_y(elev_start)
        max_y = self._elevation_to_y(elev_end)

        # Graph box
        self._drawer.add_grid_line(min_x, min_y, max_x, min_y)
        self._drawer.add_grid_line(min_x, min_y, min_x, max_y)
        self._drawer.add_grid_line(max_x, min_y, max_x, max_y)
        self._drawer.add_grid_line(min_x, max_y, max_x, max_y)

        # Table below the graph: a STATION row and a Ground Elev row
        table_width = (max_x - min_x) * 0.2
        table_height = (max_y - min_y) * 0.25
        row_height = table_height / 2
        text_offset_x = table_width * 0.1
        text_offset_y = row_height * 0.1

        table_left = min_x - table_width
        table_bottom = min_y - table_height

        self._drawer.add_grid_line(table_left, min_y, max_x, min_y)
        self._drawer.add_grid_line(table_left, min_y, table_left, table_bottom)
        self._drawer.add_grid_line(table_left, table_bottom, max_x, table_bottom)
        self._drawer.add_grid_line(max_x, table_bottom, max_x, min_y)
        self._drawer.add_grid_line(table_left, min_y - row_height, max_x, min_y - row_height)

        self._drawer.add_text("STATION", table_left + text_offset_x, table_bottom + text_offset_y,
                              self.label_size, alignment=TextEntityAlignment.MIDDLE_CENTER)
        self._drawer.add_text("Ground Elev", table_left + text_offset_x, min_y - row_height + text_offset_y,
                              self.label_size, alignment=TextEntityAlignment.MIDDLE_CENTER)

        # Vertical station lines with chainage and elevation values
        x = min_x
        for elevation in self.elevations:
            self._drawer.add_grid_line(x, table_bottom, x, max_y)
            self._drawer.add_text(elevation.chainage, x - (text_offset_x / 4), table_bottom + text_offset_y,
                                  self.label_size, alignment=TextEntityAlignment.MIDDLE_CENTER, rotation=90)
            self._drawer.add_text(f"{elevation.elevation}", x - (text_offset_x / 4),
                                  min_y - row_height + text_offset_y,
                                  self.label_size, alignment=TextEntityAlignment.MIDDLE_CENTER, rotation=90)
            x += params.station_interval * params.horizontal_scale

        # Horizontal elevation lines with labels on the left
        steps = round((elev_end - elev_start) / interval)
        for i in range(steps + 1):
            elevation = elev_start + i * interval
            y = self._elevation_to_y(elevation)
            self._drawer.add_f_grid_line(min_x, y, max_x, y)
            self._drawer.add_text(f"{elevation:g}", min_x - text_offset_x, y,
                                  self.label_size, alignment=TextEntityAlignment.MIDDLE_LEFT)

    def draw_profile_line(self):
        params = self.longitudinal_profile_parameters
        x0 = params.profile_origin[0]

        points = [
            (x0 + i * params.station_interval * params.horizontal_scale,
             self._elevation_to_y(e.elevation))
            for i, e in enumerate(self.elevations)
        ]
        self._drawer.add_profile(points)

    def draw(self):
        self.draw_grid()
        self.draw_profile_line()
        self.draw_frames()
        self.draw_title_block()
        self.draw_footer_boxes()
