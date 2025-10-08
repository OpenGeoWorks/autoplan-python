from ezdxf.enums import TextEntityAlignment

from dxf import SurveyDXFManager
from models.plan import PlanProps, PlanType
import math
from utils import polygon_orientation, line_normals, line_direction, html_to_mtext

def frange(start, stop, step):
    x = start
    eps = step * 1e-6
    while x <= stop + eps:
        yield x
        x += step

class RoutePlan(PlanProps):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.type != PlanType.ROUTE:
            raise ValueError("RoutePlan must have type PlanType.ROUTE")

        self._frame_x_percent = 0.9
        self._frame_y_percent = 1.5
        self._bounding_box = self.get_route_plan_bounding_box()
        self._frame_coords = self._setup_frame_coords()
        self._drawer = self._setup_drawer()

    def _setup_drawer(self) -> SurveyDXFManager:
        drawer = SurveyDXFManager(plan_name=self.name, scale=self.get_drawing_scale(), dxf_version=self.dxf_version)
        drawer.setup_route_layers()
        drawer.setup_font(self.font)
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

    def get_elevation_interval(self):
        max_elev = max([e.elevation for e in self.elevations])
        min_elev = min([e.elevation for e in self.elevations])
        elev_range = max_elev - min_elev

        target_lines = 8
        raw_interval = elev_range / target_lines

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

    def draw_frames(self):
        """Draw outer and offset frames."""
        min_x, min_y, max_x, max_y = self._bounding_box
        width, height = max_x - min_x, max_y - min_y

        margin_x, margin_y = max(width, height) * self._frame_x_percent, max(height, width) * self._frame_y_percent
        frame_left, frame_bottom = min_x - margin_x, min_y - margin_y
        frame_right, frame_top = max_x + margin_x, max_y + margin_y
        self._drawer.draw_frame(frame_left, frame_bottom, frame_right, frame_top)

        # offset_x, offset_y = max(width, height) * (self._frame_x_percent + 0.03), max(height, width) * (self._frame_y_percent + 0.03)
        # self._drawer.draw_frame(min_x - offset_x, min_y - offset_y,
        #                   max_x + offset_x, max_y + offset_y)

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
                                      area="",
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

    def draw_grid(self):
        # elevation_interval = self.get_elevation_interval()
        elevation_interval = self.longitudinal_profile_parameters.elevation_interval
        min_x, min_y, max_x, max_y = self._bounding_box

        # add padding below and above
        padding = max_y - min_y
        min_y = min_y - padding
        max_y = max_y + padding

        # draw box for graph
        self._drawer.add_grid_line(min_x, min_y, max_x, min_y)
        self._drawer.add_grid_line(min_x, min_y, min_x, max_y)
        self._drawer.add_grid_line(max_x, min_y, max_x, max_y)
        self._drawer.add_grid_line(min_x, max_y, max_x, max_y)

        # draw text box
        texbox_offset_x = (max_x - min_x) * 0.2
        texbox_offset_y = padding
        text_offset_x = texbox_offset_x * 0.1
        text_offset_y = (texbox_offset_y / 2) * 0.1

        self._drawer.add_grid_line(min_x - texbox_offset_x, min_y, max_x, min_y)
        self._drawer.add_grid_line(min_x - texbox_offset_x, min_y, min_x - texbox_offset_x, min_y - texbox_offset_y)
        self._drawer.add_grid_line(min_x - texbox_offset_x, min_y - texbox_offset_y, max_x, min_y - texbox_offset_y)
        self._drawer.add_text("STATION", min_x - texbox_offset_x + text_offset_x, min_y - texbox_offset_y + text_offset_y, self.label_size, alignment=TextEntityAlignment.MIDDLE_CENTER)
        self._drawer.add_grid_line(max_x, min_y - texbox_offset_y, max_x, min_y)
        self._drawer.add_grid_line(min_x - texbox_offset_x, min_y - (texbox_offset_y / 2), max_x, min_y - (texbox_offset_y / 2))
        self._drawer.add_text("Ground Elev", min_x - texbox_offset_x + text_offset_x, min_y - (texbox_offset_y / 2) + text_offset_y, self.label_size, alignment=TextEntityAlignment.MIDDLE_CENTER)

        # draw vertical lines
        x = min_x
        for elev in self.elevations:
            self._drawer.add_grid_line(x, min_y - texbox_offset_y, x, max_y)
            self._drawer.add_text(elev.chainage, x - (text_offset_x / 4), min_y - texbox_offset_y + text_offset_y, self.label_size, alignment=TextEntityAlignment.MIDDLE_CENTER, rotation=90)
            self._drawer.add_text(f"{elev.elevation}", x - (text_offset_x / 4), min_y - (texbox_offset_y / 2) + text_offset_y, self.label_size, alignment=TextEntityAlignment.MIDDLE_CENTER, rotation=90)
            x = x + (self.longitudinal_profile_parameters.station_interval * self.longitudinal_profile_parameters.horizontal_scale)

        min_elev = min([e.elevation for e in self.elevations])
        max_elev = max([e.elevation for e in self.elevations])

        elev_start = math.floor((min_elev - (max_elev - min_elev)) / elevation_interval) * elevation_interval
        elev_end = math.ceil((max_elev + (max_elev - min_elev)) / elevation_interval) * elevation_interval

        arr = frange(elev_start, elev_end + 0.0001, elevation_interval)
        arr = list(arr)
        for elev in frange(elev_start, elev_end + 0.0001, elevation_interval):
            self._drawer.add_text(f"{elev}", min_x - text_offset_x, min_y + ((elev - arr[0]) * self.longitudinal_profile_parameters.vertical_scale), self.label_size, alignment=TextEntityAlignment.MIDDLE_LEFT, rotation=0)

        # draw horizontal lines
        y = min_y
        elev_int = self.get_elevation_interval()
        for elev in self.elevations:
            self._drawer.add_f_grid_line(min_x, y, max_x, y)
            y = y + (elev_int * self.longitudinal_profile_parameters.vertical_scale)


    def draw_profile_line(self):
        stations = [self.longitudinal_profile_parameters.starting_chainage + i * self.longitudinal_profile_parameters.station_interval for i in range(len(self.elevations))]
        elevations = [e.elevation for e in self.elevations]

        min_station = self.longitudinal_profile_parameters.starting_chainage
        min_elev = min(elevations)

        x0 = self.longitudinal_profile_parameters.profile_origin[0]
        y0 = self.longitudinal_profile_parameters.profile_origin[1]

        points = [((st - min_station) * self.longitudinal_profile_parameters.horizontal_scale + x0, (elev - min_elev) * self.longitudinal_profile_parameters.vertical_scale + y0) for st, elev in
                  zip(stations, elevations)]

        self._drawer.add_profile(points)

    def draw(self):
        # Draw elements
        self.draw_grid()
        self.draw_profile_line()
        self.draw_frames()
        self.draw_title_block()
        self.draw_footer_boxes()

    def save_dxf(self, file_path: str):
        self._drawer.save_dxf(file_path)

    def save(self) -> str:
        return self._drawer.save(paper_size=self.page_size, orientation=self.page_orientation)




