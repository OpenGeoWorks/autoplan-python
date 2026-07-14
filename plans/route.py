"""Route survey plan generator: a plan-and-profile sheet.

The industry-standard route survey drawing combines two views on one sheet:

- **Plan view (horizontal alignment)** — the route centerline drawn from the
  station coordinates, rotated so the route runs left to right, with chainage
  ticks/labels, right-of-way edges, and a north arrow rotated to match.
- **Longitudinal profile** — existing ground level against chainage on an
  exaggerated vertical scale, over a station/elevation grid, with a
  station/ground-level table beneath it.

Long routes make this sheet extremely wide relative to its height, so the
geometry here is deliberate about readability:

- station text is sized against the station spacing and labelled every
  *n*-th station (a stride) when stations are too dense to label all;
- the table rows are sized from the text they hold, not the profile height;
- the frame hugs the content with route-specific margins instead of the
  generic square-ish plan margins.

The plan view is drawn when the payload carries station coordinates
(``coordinates`` whose ids match the ``elevations`` ids) and
``route_parameters.show_plan_view`` is enabled; otherwise the sheet contains
the profile only (backward compatible with older payloads).
"""

import logging
import math
from typing import ClassVar, List, Optional, Tuple

from ezdxf.enums import TextEntityAlignment
from shapely.geometry import LineString

from dxf_manager import SurveyDXFManager
from models.plan import PlanType
from plans.base import BasePlan

logger = logging.getLogger(__name__)

Point2 = Tuple[float, float]

# Average character width as a fraction of text height (Times-like fonts)
CHAR_W = 0.7


class RoutePlan(BasePlan):
    expected_type: ClassVar[PlanType] = PlanType.ROUTE

    # ------------------------------------------------------------------
    # Sheet geometry
    # ------------------------------------------------------------------
    def _compute_bounding_box(self):
        box = self.get_route_plan_bounding_box()
        if box is None:
            raise ValueError("Route plans require elevations and longitudinal profile parameters.")

        params = self.longitudinal_profile_parameters
        elev_start, elev_end, interval, _ = self._elevation_grid_range()

        min_x, _, max_x, _ = box
        width = max(max_x - min_x, 1e-6)
        grid_bottom = self._elevation_to_y(elev_start)
        grid_top = self._elevation_to_y(elev_end)

        spacing = params.station_interval * (params.horizontal_scale or 1.0)

        # Station text: readable relative to the sheet, clamped by the user's
        # label size; labels use a stride when stations are packed tighter
        # than the text needs.
        station_text = min(self.label_size, width * 0.008)
        station_text = max(station_text, width * 0.0045)
        stride = max(1, math.ceil((station_text * 1.9) / max(spacing, 1e-6)))

        chain_chars = max((len(e.chainage or "") for e in self.elevations), default=5)
        value_chars = max((len(f"{e.elevation:g}") for e in self.elevations), default=5)

        # Table rows sized from the rotated text they hold
        chain_row = chain_chars * CHAR_W * station_text + station_text * 1.4
        value_row = value_chars * CHAR_W * station_text + station_text * 1.4
        header_chars = len("GROUND ELEV")
        table_width = max(width * 0.08, header_chars * CHAR_W * station_text + 2 * station_text)

        self._grid = {
            "min_x": min_x,
            "max_x": max_x,
            "grid_top": grid_top,
            "grid_bottom": grid_bottom,
            "elev_start": elev_start,
            "elev_end": elev_end,
            "interval": interval,
            "spacing": spacing,
            "station_text": station_text,
            "stride": stride,
            "chain_row": chain_row,
            "value_row": value_row,
            "table_width": table_width,
            "table_bottom": grid_bottom - chain_row - value_row,
            "width": width,
        }

        content = (min_x - table_width, self._grid["table_bottom"], max_x, grid_top)

        # Plan view band above the grid
        self._plan_points: List[Point2] = []
        self._plan_rotation_deg = 0.0
        self._plan_band: Optional[Tuple[float, float, float, float]] = None
        self._prepare_plan_view()

        if self._plan_band is None:
            return content

        bmin_x, bmin_y, bmax_x, bmax_y = self._plan_band
        return (
            min(content[0], bmin_x),
            min(content[1], bmin_y),
            max(content[2], bmax_x),
            max(content[3], bmax_y),
        )

    def _setup_frame_coords(self):
        """Route sheets are far wider than tall; hug the content instead of
        using the generic square-ish plan margins. The bottom margin is
        solved so the footer band (18% of frame height) clears the table."""
        min_x, min_y, max_x, max_y = self._bounding_box
        content_w = max_x - min_x
        content_h = max_y - min_y

        margin_x = content_w * 0.08
        margin_top = content_w * 0.21
        # bottom >= 0.18 * frame_height + clearance, frame_height depends on bottom
        margin_bottom = (0.18 * (content_h + margin_top) + content_w * 0.03) / (1 - 0.18)

        return min_x - margin_x, min_y - margin_bottom, max_x + margin_x, max_y + margin_top

    def _setup_layers(self, drawer: SurveyDXFManager):
        drawer.setup_route_layers()

    # ------------------------------------------------------------------
    # Profile geometry helpers
    # ------------------------------------------------------------------
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
        """Grid elevation range: the data range padded by half of itself on
        both sides, snapped outwards to whole intervals."""
        params = self.longitudinal_profile_parameters
        interval = params.elevation_interval or self.get_elevation_interval()

        elevations = [e.elevation for e in self.elevations]
        min_elev, max_elev = min(elevations), max(elevations)
        pad = ((max_elev - min_elev) or interval) * 0.5

        elev_start = math.floor((min_elev - pad) / interval) * interval
        elev_end = math.ceil((max_elev + pad) / interval) * interval
        return elev_start, elev_end, interval, min_elev

    def _elevation_to_y(self, elevation: float) -> float:
        """Y drawing coordinate of an elevation; the profile origin maps to
        the minimum ground elevation."""
        params = self.longitudinal_profile_parameters
        min_elev = min(e.elevation for e in self.elevations)
        return params.profile_origin[1] + (elevation - min_elev) * params.vertical_scale

    # ------------------------------------------------------------------
    # Plan view (horizontal alignment)
    # ------------------------------------------------------------------
    def _prepare_plan_view(self):
        """Transform the station coordinates into a band above the profile.

        Strip-map convention: every station is plotted at its chainage
        position — the same x as its profile column — while its lateral
        offset from the route's chord is preserved. Plan and profile are
        therefore exactly the same length and align station-for-station.
        """
        if not self.route_parameters.show_plan_view or not self.coordinates:
            return

        coord_map = {c.id: c for c in self.coordinates}
        entries = [(i, coord_map[e.id]) for i, e in enumerate(self.elevations) if e.id in coord_map]
        if len(entries) < 2:
            logger.warning("Plan view skipped: fewer than 2 station coordinates match elevation ids.")
            return
        if len(entries) < len(self.elevations):
            logger.warning("Plan view: %d of %d stations have coordinates.",
                           len(entries), len(self.elevations))

        params = self.longitudinal_profile_parameters
        hscale = params.horizontal_scale or 1.0
        grid = self._grid

        # Lateral offsets from the first-to-last chord
        first, last = entries[0][1], entries[-1][1]
        chord_x = last.easting - first.easting
        chord_y = last.northing - first.northing
        chord = math.hypot(chord_x, chord_y)
        if chord < 1e-6:
            logger.warning("Plan view skipped: route start and end coincide.")
            return
        ux, uy = chord_x / chord, chord_y / chord
        angle = math.atan2(chord_y, chord_x)

        across = [
            (-(c.easting - first.easting) * uy + (c.northing - first.northing) * ux) * hscale
            for _, c in entries
        ]

        row_half = (self.route_parameters.right_of_way_width / 2) * hscale
        chain_chars = max((len(e.chainage or "") for e in self.elevations), default=5)
        tick_half = max(row_half * 0.25, grid["station_text"] * 0.6)
        # Chainage labels extend past the ticks; reserve their reach in the band
        label_reach = tick_half * 1.5 + chain_chars * CHAR_W * grid["station_text"]
        pad = row_half + label_reach

        grid_top = grid["grid_top"]
        grid_height = grid_top - grid["grid_bottom"]
        gap = max(grid_height * 0.35, grid["width"] * 0.04)

        min_ly = min(across)
        ty = grid_top + gap + pad - min_ly

        self._plan_points = [
            (grid["min_x"] + i * grid["spacing"], a + ty) for (i, _), a in zip(entries, across)
        ]
        self._plan_rotation_deg = math.degrees(angle)
        self._plan_tick_half = tick_half

        xs = [p[0] for p in self._plan_points]
        ys = [p[1] for p in self._plan_points]

        # Reserve room for the rotated north arrow to the right of the band
        # and for the PLAN view header above it.
        arrow_h = grid["width"] * 0.035
        header_h = min(grid["width"] * 0.012, self.label_size * 1.5)
        band_top = max(ys) + pad
        band_bottom = min(ys) - pad
        self._plan_arrow = (max(xs) + grid["width"] * 0.02, (band_top + band_bottom) / 2 - arrow_h / 2, arrow_h)
        self._plan_header = ((min(xs) + max(xs)) / 2, band_top + header_h * 0.8, header_h)
        self._plan_band = (
            min(xs),
            band_bottom,
            max(xs) + grid["width"] * 0.02 + arrow_h * 1.2,
            band_top + header_h * 2.4,
        )

    def draw_plan_view(self):
        if not self._plan_points:
            return

        grid = self._grid
        params = self.longitudinal_profile_parameters
        hscale = params.horizontal_scale or 1.0
        row_half = (self.route_parameters.right_of_way_width / 2) * hscale
        band = self._plan_band

        # Centerline
        self._drawer.add_polyline(self._plan_points, "ALIGNMENT")

        # Right-of-way edges
        if row_half > 0:
            centerline = LineString(self._plan_points)
            for side in ("left", "right"):
                edge = centerline.parallel_offset(row_half, side)
                segments = [edge] if edge.geom_type == "LineString" else \
                    [g for g in getattr(edge, "geoms", []) if g.geom_type == "LineString"]
                for segment in segments:
                    if not segment.is_empty:
                        self._drawer.add_polyline(list(segment.coords), "ROW")

        # Station ticks (every station) and chainage labels (strided)
        text_h = grid["station_text"]
        stride = grid["stride"]
        tick_half = self._plan_tick_half
        n = len(self._plan_points)
        for i, (x, y) in enumerate(self._plan_points):
            before = self._plan_points[max(i - 1, 0)]
            after = self._plan_points[min(i + 1, n - 1)]
            seg_angle = math.atan2(after[1] - before[1], after[0] - before[0])
            nx, ny = -math.sin(seg_angle), math.cos(seg_angle)  # unit normal

            self._drawer.add_polyline(
                [(x - nx * tick_half, y - ny * tick_half), (x + nx * tick_half, y + ny * tick_half)],
                "STATIONS",
            )

            if self.route_parameters.show_chainage_labels and i % stride == 0:
                # Label along the outward normal, anchored past the tick end.
                label_angle = math.degrees(math.atan2(ny, nx))
                alignment = TextEntityAlignment.MIDDLE_LEFT
                if label_angle > 90 or label_angle < -90:
                    label_angle += 180
                    alignment = TextEntityAlignment.MIDDLE_RIGHT
                self._drawer.add_text(
                    self.elevations[i].chainage if i < len(self.elevations) else "",
                    x + nx * tick_half * 1.5,
                    y + ny * tick_half * 1.5,
                    text_h,
                    rotation=label_angle,
                    alignment=alignment,
                )

        # View header and rotated north arrow (inside the reserved band area)
        header_x, header_y, header_h = self._plan_header
        self._drawer.add_text("PLAN", header_x, header_y, header_h,
                              alignment=TextEntityAlignment.BOTTOM_CENTER)

        arrow_x, arrow_y, arrow_h = self._plan_arrow
        self._drawer.draw_north_arrow(arrow_x, arrow_y, arrow_h,
                                      rotation=-self._plan_rotation_deg)

    # ------------------------------------------------------------------
    # Longitudinal profile
    # ------------------------------------------------------------------
    def draw_grid(self):
        grid = self._grid
        min_x, max_x = grid["min_x"], grid["max_x"]
        grid_top, grid_bottom = grid["grid_top"], grid["grid_bottom"]
        text_h = grid["station_text"]
        stride = grid["stride"]

        # Graph box
        self._drawer.add_grid_line(min_x, grid_bottom, max_x, grid_bottom)
        self._drawer.add_grid_line(min_x, grid_bottom, min_x, grid_top)
        self._drawer.add_grid_line(max_x, grid_bottom, max_x, grid_top)
        self._drawer.add_grid_line(min_x, grid_top, max_x, grid_top)

        # Table below the graph: chainage row (bottom) and ground level row
        table_left = min_x - grid["table_width"]
        value_row_top = grid_bottom
        value_row_bottom = grid_bottom - grid["value_row"]
        chain_row_bottom = grid["table_bottom"]

        self._drawer.add_grid_line(table_left, value_row_top, max_x, value_row_top)
        self._drawer.add_grid_line(table_left, value_row_bottom, max_x, value_row_bottom)
        self._drawer.add_grid_line(table_left, chain_row_bottom, max_x, chain_row_bottom)
        self._drawer.add_grid_line(table_left, value_row_top, table_left, chain_row_bottom)
        self._drawer.add_grid_line(max_x, value_row_top, max_x, chain_row_bottom)
        self._drawer.add_grid_line(min_x, value_row_top, min_x, chain_row_bottom)

        header_x = table_left + text_h
        self._drawer.add_text("GROUND ELEV", header_x,
                              (value_row_top + value_row_bottom) / 2, text_h,
                              alignment=TextEntityAlignment.MIDDLE_LEFT)
        self._drawer.add_text("STATION", header_x,
                              (value_row_bottom + chain_row_bottom) / 2, text_h,
                              alignment=TextEntityAlignment.MIDDLE_LEFT)

        # Station verticals every station; values/chainages every stride
        params = self.longitudinal_profile_parameters
        x = min_x
        for i, elevation in enumerate(self.elevations):
            self._drawer.add_grid_line(x, chain_row_bottom, x, grid_top)
            if i % stride == 0:
                self._drawer.add_text(
                    f"{elevation.elevation:g}", x,
                    value_row_bottom + text_h * 0.7, text_h,
                    alignment=TextEntityAlignment.BOTTOM_CENTER, rotation=90)
                self._drawer.add_text(
                    elevation.chainage, x,
                    chain_row_bottom + text_h * 0.7, text_h,
                    alignment=TextEntityAlignment.BOTTOM_CENTER, rotation=90)
            x += grid["spacing"]

        # Horizontal elevation lines with labels on the left (strided when
        # the interval spacing is tighter than the text)
        interval = grid["interval"]
        vscale = params.vertical_scale or 1.0
        elev_text = min(text_h, interval * vscale * 0.6)
        elev_stride = max(1, math.ceil((elev_text * 2.4) / max(interval * vscale, 1e-6)))

        steps = round((grid["elev_end"] - grid["elev_start"]) / interval)
        for i in range(steps + 1):
            elevation = grid["elev_start"] + i * interval
            y = self._elevation_to_y(elevation)
            self._drawer.add_f_grid_line(min_x, y, max_x, y)
            if i % elev_stride == 0:
                self._drawer.add_text(f"{elevation:g}", min_x - elev_text * 0.6, y,
                                      elev_text, alignment=TextEntityAlignment.MIDDLE_RIGHT)

        # View header (only needed when the sheet also has a plan view)
        if self._plan_points:
            header_h = min(grid["width"] * 0.012, self.label_size * 1.5)
            self._drawer.add_text("LONGITUDINAL SECTION", (min_x + max_x) / 2,
                                  grid_top + (grid_top - grid_bottom) * 0.12,
                                  header_h, alignment=TextEntityAlignment.MIDDLE_CENTER)

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
        self.draw_plan_view()
        self.draw_frames()
        self.draw_title_block()
        self.draw_footer_boxes()
