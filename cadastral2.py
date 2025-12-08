import math
from typing import Optional

from dxf2 import SurveyDXFManager
from models.plan import PlanProps, PlanType
from utils import polygon_orientation, line_normals, line_direction, html_to_mtext, format_number

class CadastralPlan(PlanProps):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.type != PlanType.CADASTRAL:
            raise ValueError("CadastralPlan must have type PlanType.CADASTRAL")

        self._frame_x_percent = 0.9
        self._frame_y_percent = 1.5
        self._label_offset_percent = 0.0127 / 2
        self._bounding_box = self.get_bounding_box()
        self._frame_coords = self.get_frame_coordinates()
        if not self._frame_coords:
            raise ValueError("Cannot determine frame coordinates without valid coordinates.")
        self._frame_area_sqrt = self.get_frame_area_sqrt()
        self._coord_dict = {coord.id: coord for coord in self.coordinates}
        self._drawer = self._setup_drawer()


    def get_bounding_box(self) -> Optional[tuple]:
        xs, ys = [], []

        for p in self.coordinates:
            xs.append(p.easting)
            ys.append(p.northing)

        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        return min_x, min_y, max_x, max_y

    def get_frame_coordinates(self):
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

    def get_frame_area_sqrt(self):
        frame_left, frame_bottom, frame_right, frame_top = self._frame_coords
        frameArea = (frame_right - frame_left) * (frame_top - frame_bottom)
        return math.sqrt(frameArea)

    def setup_drawer(self):
        drawer = SurveyDXFManager(plan_name=self.name, scale=self.get_drawing_scale(), dxf_version=self.dxf_version)
        drawer.setup_cadastral_layers()
        drawer.setup_font(self.font)
        drawer.setup_beacon_style(self.beacon_type, self.beacon_size)
        return drawer

    def draw_beacons(self):
        if not self.coordinates:
            return

        for coord in self.coordinates:
            self._drawer.add_beacon(coord.easting, coord.northing)

            # add label
            offset_x = coord.easting + (self._frame_area_sqrt * self._label_offset_percent)
            offset_y = coord.northing + (self._frame_area_sqrt * self._label_offset_percent)
            self._drawer.add_label(offset_x, offset_y, coord.id, self.label_size)

    def draw_parcels(self):
        if not self.parcels or not self.coordinates:
            return

        for parcel in self.parcels:
            parcel_points = [(self._coord_dict[pid].easting, self._coord_dict[pid].northing)
                             for pid in parcel.ids if pid in self._coord_dict]

            if not parcel_points:
                continue







