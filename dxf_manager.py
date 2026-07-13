"""Low-level DXF drawing manager.

Wraps ezdxf to provide the drawing primitives used by the plan generators
(beacons, parcels, contours, frames, title blocks, ...). All coordinates
passed to this class are in model units (metres); they are multiplied by
``scale`` so that the finished drawing is at the requested plan scale.
"""

import logging
import math
import os
import re
import tempfile
import uuid
import zipfile
from datetime import datetime
from typing import List, Optional, Tuple

import ezdxf
from ezdxf import bbox, colors
from ezdxf.addons import odafc
from ezdxf.addons.drawing import Frontend, RenderContext, config, layout, pymupdf
from ezdxf.enums import TextEntityAlignment
from ezdxf.tools.text import MTextEditor

from upload import upload_file

logger = logging.getLogger(__name__)

# Paper sizes in mm (width, height) for portrait orientation.
PAPER_SIZES = {
    "A0": (841, 1189),
    "A1": (594, 841),
    "A2": (420, 594),
    "A3": (297, 420),
    "A4": (210, 297),
    "A5": (148, 210),
    "LETTER": (216, 279),
    "LEGAL": (216, 356),
}


def nice_round(value: float) -> float:
    """Round a positive value to the nearest 'nice' number (1, 2, 2.5, 5 x 10^k)."""
    if value <= 0:
        return 1.0
    exp = math.floor(math.log10(value))
    frac = value / 10 ** exp
    nice = min((1.0, 2.0, 2.5, 5.0, 10.0), key=lambda n: abs(n - frac))
    return nice * 10 ** exp


class SurveyDXFManager:
    def __init__(self, plan_name: str = "Survey Plan", scale: float = 1.0, dxf_version: str = "R2000"):
        self.plan_name = plan_name
        self.scale = scale
        self.dxf_version = dxf_version
        self.doc = ezdxf.new(dxfversion=dxf_version)
        self.msp = self.doc.modelspace()
        self.setup_layers()

        # Document units
        self.doc.header["$INSUNITS"] = 6  # meters
        self.doc.header["$LUNITS"] = 2  # decimal
        self.doc.header["$LUPREC"] = 3  # 3 decimal places
        self.doc.header["$AUNITS"] = 1  # degrees/minutes/seconds
        self.doc.header["$AUPREC"] = 3  # 0d00'00"
        self.doc.header["$ANGBASE"] = 90.0  # 0 degrees points North

    # ------------------------------------------------------------------
    # Layer / style setup
    # ------------------------------------------------------------------
    def setup_layers(self):
        self.doc.layers.add(name="LABELS", color=colors.BLACK)
        self.doc.layers.add(name="FRAME", color=colors.BLACK)
        self.doc.layers.add(name="TITLE_BLOCK", color=colors.BLACK)
        self.doc.layers.add(name="FOOTER", color=colors.BLACK)

    def setup_cadastral_layers(self):
        self.doc.layers.add(name="BEACONS", color=colors.BLACK)
        self.doc.layers.add(name="PARCELS", color=colors.RED)

    def setup_topographic_layers(self):
        self.doc.layers.add(name="BEACONS", color=colors.BLACK)
        self.doc.layers.add(name="BOUNDARY", color=colors.RED)
        self.doc.layers.add("CONTOUR_MAJOR", true_color=colors.rgb2int((127, 31, 0)),
                            linetype="Continuous", lineweight=35)
        self.doc.layers.add("CONTOUR_MINOR", true_color=colors.rgb2int((127, 31, 0)),
                            linetype="Continuous", lineweight=18)
        self.doc.layers.add("CONTOUR_LABELS", true_color=colors.rgb2int((127, 31, 0)))
        self.doc.layers.add("TIN_MESH", color=colors.GRAY, linetype="Continuous", lineweight=9)
        self.doc.layers.add("GRID_MESH", color=colors.LIGHT_GRAY, linetype="Dot", lineweight=9)
        self.doc.layers.add("SPOT_HEIGHTS", true_color=colors.rgb2int((205, 105, 40)),
                            linetype="Continuous", lineweight=25)

    def setup_layout_layers(self):
        self.doc.layers.add(name="BEACONS", color=colors.BLACK)
        self.doc.layers.add(name="BOUNDARY", color=colors.RED, linetype="CONTINUOUS", lineweight=50)
        self.doc.layers.add(name="PARCELS", color=colors.GREEN, linetype="CONTINUOUS", lineweight=25)
        self.doc.layers.add(name="ROADS", color=colors.BLACK, linetype="CONTINUOUS", lineweight=35)
        self.doc.layers.add(name="ROADS_CL", color=colors.CYAN, linetype="DASHDOT", lineweight=18)
        self.doc.layers.add(name="SETBACKS", color=colors.MAGENTA, linetype="DASHED", lineweight=18)
        self.doc.layers.add(name="DIMENSIONS", color=colors.YELLOW, linetype="CONTINUOUS", lineweight=18)
        self.doc.layers.add(name="TEXT", color=colors.BLACK, linetype="CONTINUOUS", lineweight=18)
        self.doc.layers.add(name="GREEN_SPACE", color=colors.GREEN, linetype="CONTINUOUS", lineweight=25)
        self.doc.layers.add(name="UTILITIES", color=colors.BLUE, linetype="DASHED", lineweight=18)
        self.doc.layers.add(name="EASEMENTS", true_color=colors.rgb2int((255, 165, 0)),
                            linetype="DASHDOT", lineweight=18)
        self.doc.layers.add(name="BUILDABLE", color=colors.GRAY, linetype="DASHDOT", lineweight=18)

    def setup_route_layers(self):
        self.doc.layers.add(name="GRID", color=colors.BLACK)
        self.doc.layers.add(name="F-GRID", color=colors.YELLOW, linetype="DASHDOT")
        self.doc.layers.add(name="TEXT", color=colors.BLUE)
        self.doc.layers.add(name="PROFILE", color=colors.RED)

    def setup_font(self, font_name: str = "Times New Roman"):
        self.doc.styles.add("SURVEY_TEXT", font=f"{font_name}.ttf")

    def setup_beacon_style(self, type_: str = "box", size: float = 1.0):
        size = size * self.scale
        block = self.doc.blocks.new(name="BEACON_POINT")
        radius = size * 0.2  # inner hatch radius
        half = size / 2

        if type_ == "circle":
            block.add_circle((0, 0), radius=size * 0.5)
        elif type_ == "box":
            block.add_lwpolyline(
                [(-half, -half), (half, -half), (half, half), (-half, half)],
                close=True,
            )
        elif type_ == "none":
            return

        # Hatched inner circle shared by all visible styles
        hatch = block.add_hatch(color=7)
        path = hatch.paths.add_edge_path()
        path.add_arc((0, 0), radius=radius, start_angle=0, end_angle=360)

    def setup_topo_point_style(self, size: float = 1.0):
        size = size * self.scale
        block = self.doc.blocks.new(name="TOPO_POINT")

        # cross with a colored point at the center
        block.add_line((-size, -size), (size, size))
        block.add_line((-size, size), (size, -size))
        block.add_point((0, 0), dxfattribs={"true_color": colors.rgb2int((205, 105, 40))})

    # ------------------------------------------------------------------
    # Drawing primitives
    # ------------------------------------------------------------------
    def draw_beacon(self, x: float, y: float, z: float = 0,
                    text_height: float = 1.0, extent: float = 1000, label: Optional[str] = None):
        """Add a beacon point with an optional label offset from the point."""
        x, y, z = x * self.scale, y * self.scale, z * self.scale
        text_height = text_height * self.scale

        self.msp.add_blockref("BEACON_POINT", (x, y, z), dxfattribs={"layer": "BEACONS"})

        if label is not None:
            offset = self.scale * extent * 0.01
            self.msp.add_text(
                label,
                dxfattribs={"layer": "LABELS", "height": text_height, "style": "SURVEY_TEXT"},
            ).set_placement((x + offset, y + offset))

    def add_parcel(self, points: List[Tuple[float, float]]):
        points = [(x * self.scale, y * self.scale) for x, y, *_ in points]
        self.msp.add_lwpolyline(points, close=True, dxfattribs={"layer": "PARCELS"})

    def add_boundary(self, points: List[Tuple[float, float]]):
        points = [(x * self.scale, y * self.scale) for x, y, *_ in points]
        self.msp.add_lwpolyline(points, close=True, dxfattribs={"layer": "BOUNDARY"})

    def add_buildable(self, points: List[Tuple[float, float]]):
        points = [(x * self.scale, y * self.scale) for x, y, *_ in points]
        self.msp.add_lwpolyline(points, close=True, dxfattribs={"layer": "BUILDABLE"})

    def add_road_cl(self, points: List[Tuple[float, float]]):
        points = [(x * self.scale, y * self.scale) for x, y, *_ in points]
        self.msp.add_lwpolyline(points, dxfattribs={"layer": "ROADS_CL"})

    def add_road(self, points: List[Tuple[float, float]]):
        points = [(x * self.scale, y * self.scale) for x, y, *_ in points]
        self.msp.add_lwpolyline(points, dxfattribs={"layer": "ROADS"})

    def add_greenspace(self, points: List[Tuple[float, float]]):
        points = [(x * self.scale, y * self.scale) for x, y, *_ in points]
        self.msp.add_lwpolyline(points, close=True, dxfattribs={"layer": "GREEN_SPACE"})

        hatch = self.msp.add_hatch(dxfattribs={"layer": "GREEN_SPACE"})
        hatch.set_pattern_fill("ANSI31", scale=0.5)
        hatch.paths.add_polyline_path(points, is_closed=True)

    def add_label(self, text: str, x: float, y: float, angle: float = 0.0, height: float = 1.0):
        """Add centered single-line text on the LABELS layer."""
        x, y = x * self.scale, y * self.scale
        height = height * self.scale

        self.msp.add_text(
            text,
            dxfattribs={
                "layer": "LABELS",
                "height": height,
                "style": "SURVEY_TEXT",
                "rotation": angle,
            },
        ).set_placement((x, y), align=TextEntityAlignment.MIDDLE_CENTER)

    def add_text(self, text: str, x: float, y: float, height: float = 1.0,
                 rotation: float = 0.0, alignment=TextEntityAlignment.TOP_LEFT):
        """Add single-line text on the TEXT layer."""
        x, y = x * self.scale, y * self.scale
        height = height * self.scale

        self.msp.add_text(
            text,
            dxfattribs={
                "layer": "TEXT",
                "height": height,
                "style": "SURVEY_TEXT",
                "rotation": rotation,
            },
        ).set_placement((x, y), align=alignment)

    # ------------------------------------------------------------------
    # North arrow
    # ------------------------------------------------------------------
    def draw_north_arrow(self, x: float, y: float, height: float = 100.0):
        height = height * self.scale
        x, y = x * self.scale, y * self.scale

        if "NORTH_ARROW" not in self.doc.blocks:
            block = self.doc.blocks.new(name="NORTH_ARROW")

            arrow_size = height * 0.4
            bulge = math.tan(math.radians(250) / 4) * -1
            block.add_lwpolyline(
                [(0, 0), (0, height), (-arrow_size / 2, height - arrow_size, bulge),
                 (-arrow_size / 2, height - (arrow_size * 2))],
                format="xyb", dxfattribs={"color": 5},
            )

            block.add_text(
                "U", dxfattribs={"height": height * 0.2, "color": 5, "style": "SURVEY_TEXT"},
            ).set_placement((-height * 0.3, height - (height * 0.2)),
                            align=TextEntityAlignment.MIDDLE_CENTER)

            block.add_text(
                "N", dxfattribs={"height": height * 0.2, "color": 5, "style": "SURVEY_TEXT"},
            ).set_placement((height * 0.2, height - (height * 0.2)),
                            align=TextEntityAlignment.MIDDLE_CENTER)

        self.msp.add_blockref("NORTH_ARROW", (x, y))

    def add_north_arrow_label(self, start: Tuple[float, float], stop: Tuple[float, float],
                              label: str = "", height: float = 100.0, orientation: str = "horizontal"):
        height = height * self.scale
        x, y = start[0] * self.scale, start[1] * self.scale
        stop_x, stop_y = stop[0] * self.scale, stop[1] * self.scale

        self.msp.add_line((x, y), (stop_x, stop_y), dxfattribs={"color": 5})

        placement_x = x + 1
        placement_y = y + 1
        if orientation == "vertical":
            placement_x = x - 1

        if label:
            angle = math.degrees(math.atan2(stop_y - y, stop_x - x))
            self.msp.add_text(
                label,
                dxfattribs={"height": height, "color": 5, "style": "SURVEY_TEXT", "rotation": angle},
            ).set_placement((placement_x, placement_y), align=TextEntityAlignment.TOP_LEFT)

    def draw_north_arrow_cross(self, x: float, y: float, length: float = 100.0):
        x, y = x * self.scale, y * self.scale
        length = length * self.scale
        half = length / 2

        self.msp.add_line((x - half, y), (x + half, y), dxfattribs={"color": 5})
        self.msp.add_line((x, y - half), (x, y + half), dxfattribs={"color": 5})

    # ------------------------------------------------------------------
    # Graphical scale & title block
    # ------------------------------------------------------------------
    def draw_graphical_scale(self, x: float, y: float, length: float = 1000.0):
        """Draw a scale bar around ``length`` model-metres long at (x, y).

        The bar has 5 intervals; the interval is snapped to a 'nice' ground
        distance so the tick labels reflect true distances on the plan.
        """
        # Snap the interval to a nice ground distance and rebuild the length
        interval_m = nice_round(length / 5)
        length = interval_m * 5

        X, Y = x * self.scale, y * self.scale
        length = length * self.scale
        height = length * 0.05  # bar height, 5% of length
        interval = length / 5

        block_name = f"GRAPHICAL_SCALE_{len(self.doc.blocks)}"
        block = self.doc.blocks.new(name=block_name)

        # outer rectangle and middle line
        block.add_lwpolyline(
            [(0, 0), (length, 0), (length, height), (0, height)],
            close=True, dxfattribs={"color": 7},
        )
        block.add_line((0, height / 2), (length, height / 2), dxfattribs={"color": 7})

        def label_for(i: int) -> str:
            # Bar starts one interval before zero (the subdivided cell).
            return f"{(i - 1) * interval_m:g}"

        to_shade = "up"
        for i in range(6):
            tick_x = i * interval
            block.add_line((tick_x, 0), (tick_x, height * 1.5), dxfattribs={"color": 7})

            text = label_for(i)
            alignment = TextEntityAlignment.TOP_CENTER
            if i == 0:
                text = f"Meters {interval_m:g}"
                alignment = TextEntityAlignment.TOP_RIGHT
            if i == 5:
                text = f"{label_for(i)} Meters"
                alignment = TextEntityAlignment.TOP_LEFT

            block.add_text(
                text,
                dxfattribs={"height": height * 0.5, "color": 7, "style": "SURVEY_TEXT"},
            ).set_placement((tick_x, height * 2.3), align=alignment)

            if i == 5:
                continue

            # Alternate shading of upper/lower halves; the first cell is
            # subdivided into two half-interval cells.
            sub_intervals = 2 if i == 0 else 1
            sub_width = interval / sub_intervals
            for j in range(sub_intervals):
                sub_x = tick_x + j * sub_width
                if to_shade == "up":
                    corners = [(sub_x, height / 2), (sub_x + sub_width, height / 2),
                               (sub_x + sub_width, height), (sub_x, height)]
                    to_shade = "down"
                else:
                    corners = [(sub_x, 0), (sub_x + sub_width, 0),
                               (sub_x + sub_width, height / 2), (sub_x, height / 2)]
                    to_shade = "up"
                hatch = block.add_hatch(color=7)
                hatch.paths.add_polyline_path(corners)

        return self.msp.add_blockref(block_name, (X, Y), dxfattribs={"layer": "TITLE_BLOCK"})

    def draw_title_block(self, text: str, x: float, y: float, width: float,
                         title_height: float = 1.0, graphical_scale_length: float = 1000.0,
                         origin: str = "", area: str = ""):
        x, y = x * self.scale, y * self.scale
        title_height = title_height * self.scale
        width = width * self.scale
        graphical_scale_length = graphical_scale_length * self.scale

        block = self.doc.blocks.new(name="TITLE_BLOCK")
        title_mtext = block.add_mtext(
            text=f"{MTextEditor.UNDERLINE_START}{text}{MTextEditor.UNDERLINE_STOP}",
            dxfattribs={"style": "SURVEY_TEXT"},
        )
        title_mtext.dxf.attachment_point = ezdxf.enums.MTextEntityAlignment.TOP_CENTER
        title_mtext.dxf.char_height = title_height
        title_mtext.dxf.width = width

        title_ref = self.msp.add_blockref("TITLE_BLOCK", (x, y), dxfattribs={"layer": "TITLE_BLOCK"})

        title_box = bbox.extents(title_ref.virtual_entities())
        title_min_y = title_box.extmin.y
        title_min_x = title_box.extmin.x
        title_max_x = title_box.extmax.x

        title_length = title_max_x - title_min_x
        graphical_x = title_min_x + ((title_length / 2) - (graphical_scale_length / 2))

        # graphical scale below the title
        graphical_ref = self.draw_graphical_scale(
            graphical_x / self.scale,
            (title_min_y - (graphical_scale_length * 0.05 * 3)) / self.scale,
            graphical_scale_length / self.scale,
        )
        graphical_box = bbox.extents(graphical_ref.virtual_entities())
        graphical_min_y = graphical_box.extmin.y

        # area and origin lines below the graphical scale
        lines = []
        if area:
            lines.append(rf"\C1;{area}")
        if origin:
            lines.append(rf"\C5;{origin}")
        if not lines:
            return

        origin_mtext = self.msp.add_mtext(
            text=f"{MTextEditor.UNDERLINE_START}{MTextEditor.NEW_LINE.join(lines)}{MTextEditor.UNDERLINE_STOP}",
            dxfattribs={"style": "SURVEY_TEXT"},
        )
        origin_mtext.dxf.attachment_point = ezdxf.enums.MTextEntityAlignment.TOP_CENTER
        origin_mtext.dxf.char_height = title_height
        origin_mtext.dxf.width = width
        origin_mtext.set_location((x, graphical_min_y - ((graphical_scale_length * 0.05) / 3)))

    # ------------------------------------------------------------------
    # Frames & footers
    # ------------------------------------------------------------------
    def draw_footer_box(self, text: str, min_x, min_y, max_x, max_y, font_size: float = 1.0):
        """Draw a footer rectangle with MText content inside."""
        font_size = font_size * self.scale
        min_x, min_y = min_x * self.scale, min_y * self.scale
        max_x, max_y = max_x * self.scale, max_y * self.scale

        self.msp.add_lwpolyline(
            [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)],
            close=True, dxfattribs={"layer": "FOOTER"},
        )

        footer_mtext = self.msp.add_mtext(
            text=text,
            dxfattribs={"layer": "FOOTER", "style": "SURVEY_TEXT"},
        )
        footer_mtext.dxf.attachment_point = ezdxf.enums.MTextEntityAlignment.TOP_LEFT
        footer_mtext.dxf.width = (max_x - min_x) * 0.9
        footer_mtext.dxf.char_height = font_size
        # top-left corner with some padding
        footer_mtext.set_location((min_x + (0.05 * (max_x - min_x)), max_y - (0.1 * (max_y - min_y))))

    def draw_frame(self, min_x, min_y, max_x, max_y):
        """Draw a rectangular frame given min and max coordinates."""
        min_x, min_y = min_x * self.scale, min_y * self.scale
        max_x, max_y = max_x * self.scale, max_y * self.scale

        self.msp.add_lwpolyline(
            [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)],
            close=True, dxfattribs={"layer": "FRAME"},
        )

    # ------------------------------------------------------------------
    # Topographic primitives
    # ------------------------------------------------------------------
    def draw_topo_point(self, x: float, y: float, z: float = 0,
                        label: Optional[str] = None, text_height: float = 1.0):
        x, y, z = x * self.scale, y * self.scale, z * self.scale
        text_height = text_height * self.scale

        self.msp.add_blockref("TOPO_POINT", (x, y, z), dxfattribs={"layer": "SPOT_HEIGHTS"})

        if label is not None:
            offset = 0.25 * text_height
            self.msp.add_text(
                label,
                dxfattribs={
                    "layer": "SPOT_HEIGHTS",
                    "height": text_height,
                    "style": "SURVEY_TEXT",
                    "color": 7,
                },
            ).set_placement((x + offset, y + offset, z + offset))

    def _scale3d(self, points: List[Tuple[float, float, float]]):
        return [(x * self.scale, y * self.scale, z * self.scale) for x, y, z in points]

    def add_tin_mesh(self, points: List[Tuple[float, float, float]]):
        self.msp.add_polyline3d(self._scale3d(points), dxfattribs={"layer": "TIN_MESH"})

    def add_grid_mesh(self, points: List[Tuple[float, float, float]]):
        self.msp.add_polyline3d(self._scale3d(points), dxfattribs={"layer": "GRID_MESH"})

    def add_grid_mesh_border(self, points: List[Tuple[float, float, float]]):
        self.msp.add_polyline3d(
            self._scale3d(points),
            dxfattribs={"layer": "GRID_MESH", "lineweight": 25},
        )

    def add_grid_mesh_label(self, x: float, y: float, z: float, label: str,
                            text_height: float = 1.0, rotation: float = 0.0):
        x, y, z = x * self.scale, y * self.scale, z * self.scale
        text_height = text_height * self.scale

        self.msp.add_text(label, dxfattribs={
            "layer": "GRID_MESH",
            "height": text_height,
            "style": "SURVEY_TEXT",
            "rotation": rotation,
        }).set_placement((x, y, z))

    def add_3d_contour(self, points: List[Tuple[float, float, float]], layer: str = "CONTOUR_MINOR"):
        self.msp.add_polyline3d(self._scale3d(points), dxfattribs={"layer": layer})

    def add_spline(self, points: List[Tuple[float, float, float]], layer: str = "CONTOUR_MINOR"):
        self.msp.add_spline(self._scale3d(points), degree=3, dxfattribs={"layer": layer})

    def add_contour_label(self, x: float, y: float, z: float, label: str, text_height: float = 1.0):
        x, y, z = x * self.scale, y * self.scale, z * self.scale
        text_height = text_height * self.scale

        self.msp.add_text(label, dxfattribs={
            "layer": "CONTOUR_LABELS",
            "height": text_height,
        }).set_placement((x, y, z), align=TextEntityAlignment.MIDDLE_CENTER)

    # ------------------------------------------------------------------
    # Route (longitudinal profile) primitives
    # ------------------------------------------------------------------
    def add_grid_line(self, x1: float, y1: float, x2: float, y2: float):
        self.msp.add_line(
            (x1 * self.scale, y1 * self.scale),
            (x2 * self.scale, y2 * self.scale),
            dxfattribs={"layer": "GRID"},
        )

    def add_f_grid_line(self, x1: float, y1: float, x2: float, y2: float):
        self.msp.add_line(
            (x1 * self.scale, y1 * self.scale),
            (x2 * self.scale, y2 * self.scale),
            dxfattribs={"layer": "F-GRID"},
        )

    def add_profile(self, points: List[Tuple[float, float]]):
        points = [(x * self.scale, y * self.scale) for x, y in points]
        self.msp.add_spline(points, dxfattribs={"layer": "PROFILE"})

    # ------------------------------------------------------------------
    # Layer visibility & output
    # ------------------------------------------------------------------
    def toggle_layer(self, layer: str, state: bool):
        layer_entity = self.doc.layers.get(layer)
        if state:
            layer_entity.on()
        else:
            layer_entity.off()

    def get_filename(self) -> str:
        plan_name = self.plan_name.lower()
        plan_name = re.sub(r"\s+", "_", plan_name)
        plan_name = re.sub(r"[^a-z0-9._-]", "", plan_name)
        plan_name = re.sub(r"_+", "_", plan_name)
        return f"{plan_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    def save_dxf(self, filepath: Optional[str] = None):
        if not filepath:
            filepath = f"{self.get_filename()}.dxf"
        self.doc.saveas(filepath)

    def save_pdf(self, filepath: Optional[str] = None, paper_size: str = "A4", orientation: str = "portrait"):
        width, height = PAPER_SIZES.get(paper_size.upper(), PAPER_SIZES["A4"])
        if orientation.lower() == "landscape":
            width, height = height, width

        context = RenderContext(self.doc)
        backend = pymupdf.PyMuPdfBackend()
        cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE)
        frontend = Frontend(context, backend, config=cfg)
        frontend.draw_layout(self.msp)

        page = layout.Page(width, height, layout.Units.mm, margins=layout.Margins.all(20))

        if not filepath:
            filepath = f"{self.get_filename()}.pdf"

        pdf_bytes = backend.get_pdf_bytes(page)
        with open(filepath, "wb") as f:
            f.write(pdf_bytes)

    def save_dwg(self, dxf_filepath: str, filepath: Optional[str] = None):
        """Convert a saved DXF to DWG using the ODA File Converter."""
        if not filepath:
            filepath = f"{self.get_filename()}.dwg"
        odafc.convert(dxf_filepath, filepath, version=self.dxf_version)

    def save(self, paper_size: str = "A4", orientation: str = "portrait") -> str:
        """Export DXF + DWG + PDF, zip them, and upload the archive.

        Returns the public URL of the uploaded ZIP archive.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            filename = self.get_filename()
            dxf_path = os.path.join(tmpdir, f"{filename}.dxf")
            dwg_path = os.path.join(tmpdir, f"{filename}.dwg")
            pdf_path = os.path.join(tmpdir, f"{filename}.pdf")
            zip_path = os.path.join(tmpdir, f"{filename}.zip")

            self.save_dxf(dxf_path)
            self.save_dwg(dxf_path, dwg_path)
            self.save_pdf(pdf_path, paper_size=paper_size, orientation=orientation)

            with zipfile.ZipFile(zip_path, "w") as zipf:
                zipf.write(dxf_path, os.path.basename(dxf_path))
                zipf.write(dwg_path, os.path.basename(dwg_path))
                zipf.write(pdf_path, os.path.basename(pdf_path))

            url = upload_file(zip_path, folder="survey_plans", file_name=filename)
            if url is None:
                raise RuntimeError("Failed to upload generated plan archive")
            return url
