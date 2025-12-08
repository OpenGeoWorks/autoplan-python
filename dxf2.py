import os
import ezdxf
from ezdxf import bbox, colors
from ezdxf.enums import TextEntityAlignment, MTextEntityAlignment
from ezdxf.tools.text import MTextEditor
from ezdxf.addons.drawing import Frontend, RenderContext, pymupdf, layout, config
from ezdxf.addons import odafc
from datetime import datetime
from typing import List, Tuple
from upload import upload_file
import tempfile
import math
import re
import uuid
import zipfile

class SurveyDXFManager:
    def __init__(self, plan_name: str = "Survey Plan", scale: float = 1000, dxf_version="R2000"):
        self.plan_name = plan_name
        self.scale = scale
        self.doc = ezdxf.new(dxfversion=dxf_version)
        self.msp = self.doc.modelspace()
        self.setup_layers()

        # set units
        self.doc.header["$INSUNITS"] = 6  # meters
        self.doc.header["$LUNITS"] = 2  # Decimal
        self.doc.header["$LUPREC"] = 3  # 3 decimal places
        self.doc.header["$AUNITS"] = 1  # Degrees/minutes/seconds
        self.doc.header["$AUPREC"] = 3  # 0d00'00"
        self.doc.header["$ANGBASE"] = 90.0  # set 0° direction to North
        self.dxf_version = dxf_version

    def setup_layers(self):
        self.doc.layers.add(name="LABELS", color=colors.BLACK)
        self.doc.layers.add(name="FRAME", color=colors.BLACK)
        self.doc.layers.add(name="TITLE_BLOCK", color=colors.BLACK)
        self.doc.layers.add(name="FOOTER", color=colors.BLACK)
        self.doc.layers.add(name="NORTH_ARROW", color=colors.BLACK)

    def setup_cadastral_layers(self):
        self.doc.layers.add(name="BEACONS", color=colors.BLACK)
        self.doc.layers.add(name="PARCELS", color=colors.RED)

    def setup_topographic_layers(self):
        self.doc.layers.add(name="BEACONS", color=colors.BLACK)
        self.doc.layers.add(name="BOUNDARY", color=colors.RED)
        self.doc.layers.add('CONTOUR_MAJOR', true_color=ezdxf.colors.rgb2int((127, 31, 0)), linetype="Continuous",
                            lineweight=35)
        self.doc.layers.add('CONTOUR_MINOR', true_color=ezdxf.colors.rgb2int((127, 31, 0)), linetype="Continuous",
                            lineweight=18)
        self.doc.layers.add('CONTOUR_LABELS', true_color=ezdxf.colors.rgb2int((127, 31, 0)))
        self.doc.layers.add('TIN_MESH', color=colors.GRAY, linetype="Continuous",
                            lineweight=9)
        self.doc.layers.add('GRID_MESH', color=colors.LIGHT_GRAY, linetype="Dot",
                            lineweight=9)
        self.doc.layers.add('SPOT_HEIGHTS', true_color=ezdxf.colors.rgb2int((205, 105, 40)), linetype="Continuous",
                            lineweight=25)

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
        self.doc.layers.add(name="EASEMENTS", true_color=ezdxf.colors.rgb2int((255, 165, 0)), linetype="DASHDOT",
                            lineweight=18)
        self.doc.layers.add(name="BUILDABLE", color=colors.GRAY, linetype="DASHDOT", lineweight=18)

    def setup_route_layers(self):
        self.doc.layers.add(name="GRID", color=colors.BLACK)
        self.doc.layers.add(name="F-GRID", color=colors.YELLOW, linetype="DASHDOT")
        self.doc.layers.add(name="TEXT", color=colors.BLUE)
        self.doc.layers.add(name="PROFILE", color=colors.RED)

    def setup_font(self, font_name: str = "Times New Roman"):
        # Add a new text style with the specified font
        self.doc.styles.add('SURVEY_TEXT', font=f'{font_name}.ttf')

    def setup_beacon_style(self, type_: str = "box", size: float = 1.0):
        # Point styles (using blocks)
        block = self.doc.blocks.new(name='BEACON_POINT')
        radius = size * 0.2  # inner hatch radius
        half = size / 2  # half-size for square

        # Filled (solid hatch) circle
        if type_ == "circle":
            block.add_circle((0, 0), radius=size * 0.5)

            # Hatched inner circle
            hatch = block.add_hatch(color=7)  # 7 = black/white
            path = hatch.paths.add_edge_path()
            path.add_arc((0, 0), radius=radius, start_angle=0, end_angle=360)
        elif type_ == "box":
            # Square boundary
            block.add_lwpolyline(
                [(-half, -half), (half, -half), (half, half), (-half, half)],
                close=True
            )

            # Hatched inner circle
            hatch = block.add_hatch(color=7)
            path = hatch.paths.add_edge_path()
            path.add_arc((0, 0), radius=radius, start_angle=0, end_angle=360)
        elif type_ == "dot":
            # Just hatched circle (no boundary)
            hatch = block.add_hatch(color=7)
            path = hatch.paths.add_edge_path()
            path.add_arc((0, 0), radius=radius, start_angle=0, end_angle=360)

    def setup_topo_point_style(self, size: float = 1):
        # Point styles (using blocks)
        block = self.doc.blocks.new(name='TOPO_POINT')

        # cross only
        block.add_line((-size, -size), (size, size))
        block.add_line((-size, size), (size, -size))
        block.add_point((0, 0), dxfattribs={"true_color": ezdxf.colors.rgb2int((205, 105, 40))})  # Green

    def add_beacon(self, x: float, y: float):
        self.msp.add_blockref(
            'BEACON_POINT',
            (x, y),
            dxfattribs={'layer': 'BEACONS'}
        )

    def add_label(self, x: float, y: float, text: str, text_height: float = 1.0, alignment=MTextEntityAlignment.MIDDLE_CENTER, rotation: float = 0.0):
        text = self.msp.add_mtext(text=text, dxfattribs={'style': 'SURVEY_TEXT', 'layer': 'LABELS',})
        text.dxf.attachment_point = alignment
        text.dxf.char_height = text_height
        text.set_location((x, y))
        text.set_rotation(rotation)

    def add_parcel(self, points: List[Tuple[float, float]]):
        self.msp.add_lwpolyline(points, close=True, dxfattribs={
            'layer': 'PARCELS'
        })

    def add_boundary(self, points: List[Tuple[float, float]]):
        self.msp.add_lwpolyline(points, close=True, dxfattribs={
            'layer': 'BOUNDARY'
        })

    def draw_north_arrow(self, x: float, y: float, height: float = 100.0):
        # create a block for the north arrow
        block = self.doc.blocks.new(name='NORTH_ARROW')

        arrow_size = height * 0.4
        bulge = math.tan(math.radians(250) / 4) * -1
        block.add_lwpolyline(
            [(0, 0), (0, height), (-arrow_size / 2, height - arrow_size, bulge), (-arrow_size / 2, height - (arrow_size * 2))],
            format='xyb', dxfattribs={'color': 5}
        )

        # add text above arrow
        block.add_text(
            "U",
            dxfattribs={
                'height': height * 0.2,
                'color': 5,
                'style': 'SURVEY_TEXT',
            }
        ).set_placement(
            ( -height * 0.3, height - (height * 0.2)),
            align=TextEntityAlignment.MIDDLE_CENTER
        )

        block.add_text(
            "N",
            dxfattribs={
                'height': height * 0.2,
                'color': 5,
                'style': 'SURVEY_TEXT',
            }
        ).set_placement(
            (height * 0.2, height - (height * 0.2)),
            align=TextEntityAlignment.MIDDLE_CENTER
        )

        # add to modelspace
        self.msp.add_blockref(
            'NORTH_ARROW',
            (x, y),
            dxfattribs={'layer': 'NORTH_ARROW'}
        )

    def draw_north_arrow_cross(self, x: float, y: float, length: float = 100.0):
        # create a block for the north arrow
        block = self.doc.blocks.new(name='NORTH_ARROW_CROSS')

        half = length / 2
        block.add_line((-half, 0), (half, 0))
        block.add_line((0, -half), (0, half))

        # add to modelspace
        self.msp.add_blockref(
            'NORTH_ARROW_CROSS',
            (x, y),
            dxfattribs={'layer': 'NORTH_ARROW'}
        )

    def add_north_arrow_line(self, start: Tuple[float, float], stop: Tuple[float, float]):
        self.msp.add_line(start, stop, dxfattribs={'color': 5, 'layer': 'NORTH_ARROW'})

    def add_nort_arrow_label(self, x: float, y: float, label: str, text_height: float = 1.0, alignment=TextEntityAlignment.TOP_LEFT, rotation: float = 0.0):
        text = self.msp.add_mtext(text=label, dxfattribs={'style': 'SURVEY_TEXT', 'layer': 'NORTH_ARROW'})
        text.dxf.attachment_point = alignment
        text.dxf.char_height = text_height
        text.set_location((x, y))
        text.set_rotation(rotation)

    def graphical_scale_block(self, length: float = 1000.0):
        height = length * 0.05  # 5% of length

        interval = length / 5  # 5 intervals

        # Create a block for the graphical scale
        block = self.doc.blocks.new(name='GRAPHICAL_SCALE')

        # draw large rectangle
        block.add_lwpolyline(
            [(0, 0), (length, 0), (length, height), (0, height)],
            close=True,
            dxfattribs={'color': 7}  # Black/White
        )

        # draw middle line
        block.add_line(
            (0, height / 2),
            (length, height / 2),
            dxfattribs={'color': 7}
        )

        text_interval = 1000 / 10 / 5

        # draw interval lines
        to_shade = "up"
        for i in range(6):
            x_ = i * interval
            line_height = height * 1.5
            block.add_line(
                (x_, 0),
                (x_, line_height),
                dxfattribs={'color': 7}
            )

            text = f"{int((i - 1) * text_interval)}"
            alignment = TextEntityAlignment.TOP_CENTER
            if i == 0:
                text = f"Meters {int(text_interval)}"
                alignment = TextEntityAlignment.TOP_RIGHT
            if i == 5:
                text = f"{int((i - 1) * text_interval)} Meters"
                alignment = TextEntityAlignment.TOP_LEFT

            # add text above line
            block.add_text(
                text,
                dxfattribs={
                    'height': height * 0.5,
                    'color': 7,
                    'style': 'SURVEY_TEXT'
                }
            ).set_placement(
                (x_, height * 2.3),
                align=alignment
            )

            if i == 5:
                continue

            if i == 0:
                mini_interval = interval / 2
                for j in range(2):
                    mini_x = j * mini_interval
                    if to_shade == "up":
                        # shade first upper half
                        hatch = block.add_hatch(color=7)
                        hatch.paths.add_polyline_path([(mini_x, height / 2), (mini_x + mini_interval, height / 2),
                                                       (mini_x + mini_interval, height), (mini_x, height)])
                        to_shade = "down"
                    else:
                        # shade lower half
                        hatch = block.add_hatch(color=7)
                        hatch.paths.add_polyline_path(
                            [(mini_x, 0), (mini_x + mini_interval, 0), (mini_x + mini_interval, height / 2),
                             (mini_x, height / 2)])
                        to_shade = "up"
            else:
                if to_shade == "up":
                    hatch = block.add_hatch(color=7)
                    hatch.paths.add_polyline_path(
                        [(x_, height / 2), (x_ + interval, height / 2), (x_ + interval, height), (x_, height)])
                    to_shade = "down"
                else:
                    hatch = block.add_hatch(color=7)
                    hatch.paths.add_polyline_path(
                        [(x_, 0), (x_ + interval, 0), (x_ + interval, height / 2), (x_, height / 2)])
                    to_shade = "up"

    def draw_title_block(self, x: float, y: float, width: float, text: str, text_height: float = 1.0, graphical_scale_length: float = 1000.0, origin: str = "", area: str = ""):
        block = self.doc.blocks.new(name='TITLE_BLOCK')

        title_mtext = block.add_mtext(
            text=f"{MTextEditor.UNDERLINE_START}{text}{MTextEditor.UNDERLINE_STOP}",
            dxfattribs={'style': 'SURVEY_TEXT'},
        )
        title_mtext.dxf.attachment_point = ezdxf.enums.MTextEntityAlignment.TOP_CENTER
        title_mtext.dxf.char_height = text_height
        title_mtext.dxf.width = width

        # add block to modelspace
        title_ref = self.msp.add_blockref(
            'TITLE_BLOCK',
            (x, y),
            dxfattribs={'layer': 'TITLE_BLOCK'}
        )

        title_box = bbox.extents(title_ref.virtual_entities())
        title_min_y = title_box.extmin.y
        title_min_x = title_box.extmin.x
        title_max_x = title_box.extmax.x

        title_length = title_max_x - title_min_x
        graphical_x = title_min_x + ((title_length / 2) - (graphical_scale_length / 2))

        # draw graphical scale below title
        self.graphical_scale_block(graphical_scale_length)
        graphical_ref = self.msp.add_blockref(
            'TITLE_BLOCK',
            (graphical_x, (title_min_y - (graphical_scale_length * 0.05 * 3))),
            dxfattribs={'layer': 'TITLE_BLOCK'}
        )
        graphical_box = bbox.extents(graphical_ref.virtual_entities())
        graphical_min_y = graphical_box.extmin.y

        # add origin and area below graphical scale
        origin_text = ""
        if origin != "" and area != "":
            origin_text = f"{MTextEditor.UNDERLINE_START}\C1;{area}{MTextEditor.NEW_LINE}\C5;{origin}{MTextEditor.UNDERLINE_STOP}"
        elif origin != "":
            origin_text = f"{MTextEditor.UNDERLINE_START}\C5;{origin}{MTextEditor.UNDERLINE_STOP}"
        elif area != "":
            origin_text = f"{MTextEditor.UNDERLINE_START}\C1;{area}{MTextEditor.UNDERLINE_STOP}"

        if origin_text != "":
            origin_mtext = self.msp.add_mtext(
                text=origin_text,
                dxfattribs={'style': 'SURVEY_TEXT'},
            )
            origin_mtext.dxf.attachment_point = ezdxf.enums.MTextEntityAlignment.TOP_CENTER
            origin_mtext.dxf.char_height = text_height
            origin_mtext.dxf.width = width
            origin_mtext.set_location((x, graphical_min_y - ((graphical_scale_length * 0.05) / 3)))

    def draw_footer_box(self, text: str, min_x, min_y, max_x, max_y, font_size: float = 1.0):
        """Draw a rectangle given min and max coordinates"""
        self.msp.add_lwpolyline([
            (min_x, min_y),
            (max_x, min_y),
            (max_x, max_y),
            (min_x, max_y)
        ], close=True, dxfattribs={
            'layer': 'FOOTER',
        })

        # add text inside box
        footer_mtext = self.msp.add_mtext(
            text=text,
            dxfattribs={
                'layer': 'FOOTER',
                'style': 'SURVEY_TEXT',
                # 'height': (max_y - min_y) * 0.8,
            }
        )
        footer_mtext.dxf.attachment_point = ezdxf.enums.MTextEntityAlignment.TOP_LEFT
        footer_mtext.dxf.width = (max_x - min_x) * 0.9
        # set location in top-left corner with some padding
        footer_mtext.set_location((min_x + (0.05 * (max_x - min_x)), max_y - (0.1 * (max_y - min_y))))
        footer_mtext.dxf.char_height = font_size

    def draw_frame(self, min_x, min_y, max_x, max_y):
        """Draw a rectangle given min and max coordinates"""
        self.msp.add_lwpolyline([
            (min_x, min_y),
            (max_x, min_y),
            (max_x, max_y),
            (min_x, max_y)
        ], close=True, dxfattribs={
            'layer': 'FRAME',
        })

    def add_spot_height(self, x: float, y: float, z: float):
        self.msp.add_blockref(
            'TOPO_POINT',
            (x, y, z),
            dxfattribs={'layer': 'SPOT_HEIGHTS'}
        )

    def add_spot_height_label(self, x: float, y: float, z: float, label: str, text_height: float = 1.0, alignment=MTextEntityAlignment.MIDDLE_CENTER, rotation: float = 0.0):
        text = self.msp.add_mtext(text=label, dxfattribs={'style': 'SURVEY_TEXT', 'layer': 'SPOT_HEIGHTS'})
        text.dxf.attachment_point = alignment
        text.dxf.char_height = text_height
        text.set_location((x, y, z))
        text.set_rotation(rotation)

    def add_tin_mesh(self, points: List[Tuple[float, float, float]]):
        # Add as 3D polyline
        self.msp.add_polyline3d(
            points,
            dxfattribs={'layer': 'TIN_MESH'}
        )

    def add_grid_mesh(self, points: List[Tuple[float, float, float]]):
        # Add as 3D polyline
        self.msp.add_polyline3d(
            points,
            dxfattribs={'layer': 'GRID_MESH'}
        )

    def add_grid_mesh_label(self, x: float, y: float, z: float, label: str, text_height: float = 1.0, alignment=MTextEntityAlignment.MIDDLE_CENTER, rotation: float = 0.0):
        text = self.msp.add_mtext(text=label, dxfattribs={'style': 'SURVEY_TEXT', 'layer': 'GRID_MESH'})
        text.dxf.attachment_point = alignment
        text.dxf.char_height = text_height
        text.set_location((x, y, z))
        text.set_rotation(rotation)

    def add_grid_mesh_border(self, points: List[Tuple[float, float, float]]):
        self.msp.add_polyline3d(
            points,
            dxfattribs={
                'layer': 'GRID_MESH',
                'lineweight': 25  # Slightly thicker for border
            }
        )

    def add_grid_mesh_corner_coords(self, x: float, y: float, z: float, label: str, text_height: float = 1.0, alignment=MTextEntityAlignment.MIDDLE_CENTER, rotation: float = 0.0):
        text = self.msp.add_mtext(text=label, dxfattribs={'style': 'SURVEY_TEXT', 'layer': 'GRID_MESH'})
        text.dxf.attachment_point = alignment
        text.dxf.char_height = text_height
        text.set_location((x, y, z))
        text.set_rotation(rotation)

    def add_3d_contour(self, points: List[Tuple[float, float, float]], layer = "CONTOUR_MINOR"):
        # Add as 3D polyline
        self.msp.add_polyline3d(
            points,
            dxfattribs={'layer': layer}
        )

    def add_contour_label(self, x: float, y: float, z: float, label: str, text_height: float = 1.0, alignment=MTextEntityAlignment.MIDDLE_CENTER, rotation: float = 0.0):
        text = self.msp.add_mtext(text=label, dxfattribs={'style': 'SURVEY_TEXT', 'layer': 'CONTOUR_LABELS'})
        text.dxf.attachment_point = alignment
        text.dxf.char_height = text_height
        text.set_location((x, y, z))
        text.set_rotation(rotation)

    def add_spline(self, points: List[Tuple[float, float, float]], layer="CONTOUR_MINOR"):
        # Add as 3D polyline
        self.msp.add_spline(
            points,
            degree=3,
            dxfattribs={'layer': layer}
        )

    def add_grid_line(self, x1: float, y1: float, x2: float, y2: float):
        self.msp.add_line((x1, y1), (x2, y2), dxfattribs={'layer': 'GRID'})

    def add_f_grid_line(self, x1: float, y1: float, x2: float, y2: float):
        self.msp.add_line((x1, y1), (x2, y2), dxfattribs={'layer': 'F-GRID'})

    def add_profile(self, points: List[Tuple[float, float]]):
        self.msp.add_spline(points, dxfattribs={'layer': 'PROFILE'})

    def toggle_layer(self, layer: str, state: bool):
        layerEntity = self.doc.layers.get(layer)
        layerEntity.off() if state is False else layerEntity.on()

    def get_filename(self):
        plan_name = self.plan_name.lower()
        plan_name = re.sub(r"\s+", "_",plan_name)
        plan_name = re.sub(r"[^a-z0-9._-]", "", plan_name)
        plan_name = re.sub(r"_+", "_", plan_name)
        return f"{plan_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    def save_dxf(self, filepath: str = None):
        """Save the DXF document to a file"""
        if filepath:
            self.doc.saveas(filepath)
            return
        dxf_path = f"{self.get_filename()}.dxf"
        self.doc.saveas(dxf_path)

    def save_pdf(self, filepath: str = None, paper_size: str = "A4", orientation: str = "portrait"):
        # Paper sizes in mm
        paper_sizes = {
            "A4": (210, 297),
            "A3": (297, 420),
            "A5": (148, 210),
            "Letter": (216, 279),
            "Legal": (216, 356),
        }

        # Default to A4 if not found
        width, height = paper_sizes.get(paper_size.upper(), (210, 297))

        # Apply orientation
        if orientation.lower() == "landscape":
            width, height = height, width

        # Rendering
        context = RenderContext(self.doc)
        backend = pymupdf.PyMuPdfBackend()
        cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE)
        frontend = Frontend(context, backend, config=cfg)
        frontend.draw_layout(self.msp)

        # Create page with margins (20 mm here, can be parameterized)
        page = layout.Page(width, height, layout.Units.mm, margins=layout.Margins.all(20))

        # Output path
        if not filepath:
            filepath = f"{self.get_filename()}.pdf"

        # Save PDF
        pdf_bytes = backend.get_pdf_bytes(page)
        with open(filepath, "wb") as f:
            f.write(pdf_bytes)

    def save_dwg(self, dxf_filepath: str, filepath: str = None):
        if not filepath:
            filepath = f"{self.get_filename()}.dwg"
        odafc.convert(dxf_filepath, filepath, version=self.dxf_version)

    # def save(self, paper_size: str = "A4", orientation: str = "portrait"):
    #     # with tempfile.TemporaryDirectory() as tmpdir:
    #     filename = self.get_filename()
    #     dxf_path = os.path.join("", f"{filename}.dxf")
    #     dwg_path =  os.path.join("", f"{filename}.dwg")
    #     pdf_path =  os.path.join("", f"{filename}.pdf")
    #     zip_path = os.path.join("", f"{filename}.zip")
    #
    #     self.save_dxf(dxf_path)
    #     self.save_dwg(dxf_path, dwg_path)
    #     self.save_pdf(pdf_path, paper_size=paper_size, orientation=orientation)
    #
    #     # Create a ZIP file containing all three formats
    #     with zipfile.ZipFile(zip_path, "w") as zipf:
    #         zipf.write(dxf_path, os.path.basename(dxf_path))
    #         zipf.write(dwg_path, os.path.basename(dwg_path))
    #         zipf.write(pdf_path, os.path.basename(pdf_path))
    #
    #     # url = upload_file(zip_path, folder="survey_plans", file_name=filename)
    #     # if url is None:
    #     #     raise Exception("Upload failed")
    #     return "url"

    def save(self, paper_size: str = "A4", orientation: str = "portrait"):
        with tempfile.TemporaryDirectory() as tmpdir:
            filename = self.get_filename()
            dxf_path = os.path.join(tmpdir, f"{filename}.dxf")
            dwg_path =  os.path.join(tmpdir, f"{filename}.dwg")
            pdf_path =  os.path.join(tmpdir, f"{filename}.pdf")
            zip_path = os.path.join(tmpdir, f"{filename}.zip")

            self.save_dxf(dxf_path)
            self.save_dwg(dxf_path, dwg_path)
            self.save_pdf(pdf_path, paper_size=paper_size, orientation=orientation)

            # Create a ZIP file containing all three formats
            with zipfile.ZipFile(zip_path, "w") as zipf:
                zipf.write(dxf_path, os.path.basename(dxf_path))
                zipf.write(dwg_path, os.path.basename(dwg_path))
                zipf.write(pdf_path, os.path.basename(pdf_path))

            url = upload_file(zip_path, folder="survey_plans", file_name=filename)
            if url is None:
                raise Exception("Upload failed")
            return url





