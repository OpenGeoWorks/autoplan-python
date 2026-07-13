"""Shared behaviour for all survey plan generators.

``BasePlan`` owns everything that is common to every plan type: computing
the drawing frame from the data bounding box, and drawing the frame, title
block, footer boxes, north arrow, and bearing/distance leg labels.
"""

import math
from typing import ClassVar, Optional

from dxf_manager import SurveyDXFManager
from models.plan import CoordinateProps, PlanProps, PlanType, TraverseLegProps
from utils import format_number, html_to_mtext, line_direction, line_normals

# Margins around the data bounding box, as a fraction of its larger side.
FRAME_X_PERCENT = 0.9
FRAME_Y_PERCENT = 1.5

# Fraction of the frame height reserved for footer boxes.
FOOTER_HEIGHT_PERCENT = 0.18


class BasePlan(PlanProps):
    #: Concrete subclasses set this so payloads with the wrong ``type`` fail fast.
    expected_type: ClassVar[Optional[PlanType]] = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.expected_type is not None and self.type != self.expected_type:
            raise ValueError(f"{type(self).__name__} must have type '{self.expected_type.value}'")

        self._frame_x_percent = FRAME_X_PERCENT
        self._frame_y_percent = FRAME_Y_PERCENT
        self._bounding_box = self._compute_bounding_box()
        self._frame_coords = self._setup_frame_coords()
        if not self._frame_coords:
            raise ValueError("Cannot determine frame coordinates without valid coordinates.")
        self._drawer = self._setup_drawer()

    # ------------------------------------------------------------------
    # Setup hooks
    # ------------------------------------------------------------------
    def _compute_bounding_box(self):
        return self.get_bounding_box()

    def _setup_drawer(self) -> SurveyDXFManager:
        drawer = SurveyDXFManager(
            plan_name=self.name,
            scale=self.get_drawing_scale(),
            dxf_version=self.dxf_version,
        )
        drawer.setup_font(self.font)
        self._setup_layers(drawer)
        return drawer

    def _setup_layers(self, drawer: SurveyDXFManager):
        """Add the plan-type specific layers and block styles."""
        raise NotImplementedError

    def _setup_frame_coords(self):
        min_x, min_y, max_x, max_y = self._bounding_box
        if min_x is None or min_y is None or max_x is None or max_y is None:
            return None

        width = max_x - min_x
        height = max_y - min_y

        margin_x = max(width, height) * self._frame_x_percent
        margin_y = max(width, height) * self._frame_y_percent

        return min_x - margin_x, min_y - margin_y, max_x + margin_x, max_y + margin_y

    def _get_drawing_extent(self) -> float:
        """Diagonal of the data bounding box, used to size labels and offsets."""
        min_x, min_y, max_x, max_y = self._bounding_box
        if min_x is None or min_y is None or max_x is None or max_y is None:
            return 0.0
        return math.hypot(max_x - min_x, max_y - min_y)

    # ------------------------------------------------------------------
    # Title block hooks
    # ------------------------------------------------------------------
    def _area_text(self) -> str:
        """Area line of the title block; empty string hides it."""
        return ""

    def _origin_text(self) -> str:
        return f"ORIGIN :- {self.origin.upper()}"

    # ------------------------------------------------------------------
    # Shared drawing routines
    # ------------------------------------------------------------------
    def draw_frames(self):
        frame_left, frame_bottom, frame_right, frame_top = self._frame_coords
        self._drawer.draw_frame(frame_left, frame_bottom, frame_right, frame_top)

    def draw_title_block(self):
        frame_left, frame_bottom, frame_right, frame_top = self._frame_coords
        min_x, min_y, max_x, max_y = self._bounding_box

        margin_y = frame_top - max_y
        frame_width = frame_right - frame_left
        frame_center_x = frame_left + (frame_width / 2)
        title_y = frame_top - (margin_y * 0.2)

        self._drawer.draw_title_block(
            html_to_mtext(self.build_title(), font=self.font),
            frame_center_x,
            title_y,
            frame_width * 0.6,
            self.font_size,
            graphical_scale_length=frame_width * 0.4,
            area=self._area_text(),
            origin=self._origin_text(),
        )

    def draw_footer_boxes(self):
        if not self.footers:
            return

        x_min, y_min, x_max, y_max = self._frame_coords
        box_width = (x_max - x_min) / len(self.footers)
        box_height = (y_max - y_min) * FOOTER_HEIGHT_PERCENT

        for i, footer in enumerate(self.footers):
            x1 = x_min + i * box_width
            self._drawer.draw_footer_box(
                html_to_mtext(footer, font=self.font),
                x1, y_min, x1 + box_width, y_min + box_height, self.footer_size,
            )

    def add_leg_labels(self, leg: TraverseLegProps, orientation: str):
        """Label a traverse leg with its distance (inside) and bearing (outside)."""
        dx = leg.to.easting - leg.from_.easting
        dy = leg.to.northing - leg.from_.northing
        if dx == 0 and dy == 0:
            return

        angle_deg = math.degrees(math.atan2(dy, dx))

        # Positions along the leg: bearing degrees at 20%, minutes at 80%,
        # distance at the midpoint.
        first_x = leg.from_.easting + 0.2 * dx
        first_y = leg.from_.northing + 0.2 * dy
        last_x = leg.from_.easting + 0.8 * dx
        last_y = leg.from_.northing + 0.8 * dy
        mid_x = (leg.from_.easting + leg.to.easting) / 2
        mid_y = (leg.from_.northing + leg.to.northing) / 2

        # Offset the labels perpendicular to the leg: distance towards the
        # inside of the polygon, bearing towards the outside.
        inside, outside = line_normals(
            (leg.from_.easting, leg.from_.northing),
            (leg.to.easting, leg.to.northing),
            orientation,
        )
        offset_distance = self._get_drawing_extent() * 0.02
        length = math.hypot(*inside)
        inside = (inside[0] / length * offset_distance, inside[1] / length * offset_distance)
        outside = (outside[0] / length * offset_distance, outside[1] / length * offset_distance)

        first_x += outside[0]
        first_y += outside[1]
        last_x += outside[0]
        last_y += outside[1]
        mid_x += inside[0]
        mid_y += inside[1]

        # Keep text upright
        text_angle = angle_deg
        if text_angle > 90 or text_angle < -90:
            text_angle += 180

        if leg.distance is not None:
            self._drawer.add_label(f"{leg.distance:.2f}m", mid_x, mid_y,
                                   angle=text_angle, height=self.label_size)

        if leg.bearing is None:
            return

        degrees_label = f"{format_number(leg.bearing.degrees, 'hundredth')}°"
        minutes_label = f"{format_number(leg.bearing.minutes, 'tenth')}'"

        # Bearings read along the line direction, so swap ends when the
        # line runs right-to-left.
        if line_direction(angle_deg) == "left → right":
            degrees_pos, minutes_pos = (first_x, first_y), (last_x, last_y)
        else:
            degrees_pos, minutes_pos = (last_x, last_y), (first_x, first_y)

        self._drawer.add_label(degrees_label, *degrees_pos, angle=text_angle, height=self.label_size)
        self._drawer.add_label(minutes_label, *minutes_pos, angle=text_angle, height=self.label_size)

    # ------------------------------------------------------------------
    # North arrow
    # ------------------------------------------------------------------
    def _north_arrow_reference(self) -> Optional[CoordinateProps]:
        """Coordinate the north arrow and grid lines are anchored to."""
        return None

    def draw_north_arrow(self):
        coord = self._north_arrow_reference()
        if coord is None:
            return

        frame_left, frame_bottom, frame_right, frame_top = self._frame_coords
        height = (frame_top - frame_bottom) * 0.07
        self._drawer.draw_north_arrow(coord.easting, frame_top - height, height)

        # easting label along the top of the frame
        width = (frame_right - frame_left) * 0.1
        self._drawer.add_north_arrow_label(
            (frame_left, coord.northing), (frame_left + width, coord.northing),
            f"{coord.easting}mE", self.label_size,
        )
        self._drawer.add_north_arrow_label(
            (frame_right, coord.northing), (frame_right - width, coord.northing),
            "", self.label_size,
        )

        # northing label, raised above the footer boxes when present
        northing_label_y = frame_bottom
        if self.footers:
            northing_label_y += (frame_top - frame_bottom) * FOOTER_HEIGHT_PERCENT

        self._drawer.add_north_arrow_label(
            (coord.easting, northing_label_y), (coord.easting, northing_label_y + height),
            f"{coord.northing}mN", self.label_size, "vertical",
        )
        self._drawer.draw_north_arrow_cross(coord.easting, coord.northing, self.beacon_size * 3)

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    def draw(self):
        raise NotImplementedError

    def save_dxf(self, file_path: str):
        self._drawer.save_dxf(file_path)

    def save(self) -> str:
        return self._drawer.save(paper_size=self.page_size, orientation=self.page_orientation)
