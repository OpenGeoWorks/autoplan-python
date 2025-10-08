from enum import Enum
from typing import List, Optional, Union, Tuple
from datetime import datetime
from pydantic import BaseModel, Field
from bs4 import BeautifulSoup

# ---------- Enums ----------
class PlanType(str, Enum):
    CADASTRAL = "cadastral"
    LAYOUT = "layout"
    TOPOGRAPHIC = "topographic"
    ROUTE = "route"

class PlanOrigin(str, Enum):
    UTM_ZONE_31 = "utm_zone_31"

class BeaconType(str, Enum):
    DOT = "dot"
    CIRCLE = "circle"
    BOX = "box"
    NONE = "none"

class PageSize(str, Enum):
    A4 = "A4"
    A3 = "A3"
    A2 = "A2"

class PageOrientation(str, Enum):
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"

# ---------- Supporting models ----------
class CoordinateProps(BaseModel):
    id: str = ""
    northing: Optional[float] = 0.0
    easting: Optional[float] = 0.0
    elevation: Optional[float] = 0.0

class BearingProps(BaseModel):
    degrees: Optional[int] = 0.0
    minutes: Optional[int] = 0.0
    seconds: Optional[float] = 0.0
    decimal: Optional[float] = 0.0

class TraverseLegProps(BaseModel):
    from_: CoordinateProps = Field(alias="from")  # 👈 use alias
    to: CoordinateProps
    bearing: Optional[BearingProps] = None
    observed_angle: Optional[BearingProps] = None
    distance: Optional[float] = None

class ParcelProps(BaseModel):
    name: str
    ids: List[str]
    area: Optional[float] = None  # in square meters
    legs: List[TraverseLegProps] = []

class ElevationProps(BaseModel):
    id: Optional[str] = None
    elevation: float
    chainage: str

class TopographicSettingProps(BaseModel):
    show_spot_heights: bool = True
    point_label_scale: float = 1.0
    show_contours: bool = True
    contour_interval: float = 1.0
    major_contour: float = 5.0
    minimum_distance: float = 0.1 # 0.1 to 0.5
    show_contours_labels: bool = True
    contour_label_scale: float = 1.0
    show_boundary: bool = True
    boundary_label_scale: float = 1.0
    tin: Optional[bool] = False
    grid: Optional[bool] = False
    show_mesh: Optional[bool] = False

class TopographicBoundaryProps(BaseModel):
    coordinates: List[CoordinateProps] = []
    area: Optional[float] = None
    legs: Optional[List[TraverseLegProps]] = []

class LayoutBoundaryProps(BaseModel):
    coordinates: List[CoordinateProps] = []
    area: Optional[float] = None
    legs: Optional[List[TraverseLegProps]] = []

class LayoutParameters(BaseModel):
    """Parameters for layout generation"""
    # Road Parameters
    main_road_width: float = 15.0  # meters
    secondary_road_width: float = 12.0  # meters
    access_road_width: float = 9.0  # meters

    # Parcel parameters
    min_parcel_area: float = 450.0  # square meters
    max_parcel_area: float = 1000.0  # square meters
    min_parcel_width: float = 15.0  # meters
    min_parcel_depth: float = 25.0  # meters
    target_parcel_ratio: float = 1.5  # depth/width ratio

    # Subdivision parameters
    subdivision_type: str = "grid"  # "grid", "radial", "organic", "mixed"
    road_hierarchy: bool = True
    include_cul_de_sacs: bool = True
    include_green_spaces: bool = True
    green_space_percentage: float = 10.0  # percentage of total are

    # Setbacks
    front_setback: float = 6.0  # meters
    side_setback: float = 3.0  # meters
    rear_setback: float = 3.0  # meters

    # Corner radius for road intersections
    corner_radius: float = 5.0  # meters

    # Block parameters
    max_block_length: float = 200.0  # meters
    max_block_width: float = 100.0  # meters

class LongitudinalProfileParameters(BaseModel):
    horizontal_scale: float = 1.0  # units-per-metre
    vertical_scale: float = 1.0  # units-per-metre
    profile_origin: List[float] = [0.0, 0.0]
    station_interval: float = 10.0  # metres
    elevation_interval: float = 1.0
    starting_chainage: float = 0.0

# ---------- Main Plan Model ----------
class PlanProps(BaseModel):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    user: Union[str, dict]
    project: Union[str, dict]
    name: str
    type: PlanType = PlanType.CADASTRAL
    font: str = "Times New Roman"
    font_size: int = 12
    coordinates: Optional[List[CoordinateProps]] = None
    elevations: Optional[List[ElevationProps]] = None
    parcels: Optional[List[ParcelProps]] = None
    title: str = "Untitled Plan"
    address: str = ""
    local_govt: str = ""
    state: str = ""
    plan_number: str = ""
    origin: PlanOrigin = PlanOrigin.UTM_ZONE_31
    scale: float = 1000
    beacon_type: BeaconType = BeaconType.BOX
    beacon_size: float = 0.3
    label_size: float = 1.0
    personel_name: str = ""
    surveyor_name: str = ""
    page_size: PageSize = PageSize.A4
    page_orientation: PageOrientation = PageOrientation.PORTRAIT
    topographic_setting: Optional[TopographicSettingProps] = None
    topographic_boundary: Optional[TopographicBoundaryProps] = None
    layout_boundary: Optional[LayoutBoundaryProps] = None
    layout_parameters: Optional[LayoutParameters] = None
    longitudinal_profile_parameters: Optional[LongitudinalProfileParameters] = None
    footers: List[str] = []
    footer_size: float = 0.5
    dxf_version: str = "R2000"

    def get_drawing_scale(self):
        if not self.scale:
            return 1.0
        return 1000 / self.scale

    def get_extent(self) -> float:
        # get bounding box
        min_x, min_y, max_x, max_y = self.get_bounding_box()
        if min_x is None or min_y is None or max_x is None or max_y is None:
            return 0.0

        width = max_x - min_x
        height = max_y - min_y
        extent = max(width, height)
        return extent

    def get_bounding_box(self) -> Optional[tuple]:
        xs, ys = [], []

        if self.coordinates is not None:
            xs = [p.easting for p in self.coordinates]
            ys = [p.northing for p in self.coordinates]

        if self.type == PlanType.TOPOGRAPHIC and self.topographic_boundary is not None:
            xs += [p.easting for p in self.topographic_boundary.coordinates]
            ys += [p.northing for p in self.topographic_boundary.coordinates]

        if self.type == PlanType.LAYOUT and self.layout_boundary is not None:
            xs += [p.easting for p in self.layout_boundary.coordinates]
            ys += [p.northing for p in self.layout_boundary.coordinates]


        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        return min_x, min_y, max_x, max_y

    def get_route_plan_bounding_box(self) -> Optional[tuple]:
        if self.type != PlanType.ROUTE or self.elevations is None or self.longitudinal_profile_parameters is None:
            return None

        # get max elevation
        min_elev = min([e.elevation for e in self.elevations])
        max_elev = max([e.elevation for e in self.elevations])
        max_chainage = self.longitudinal_profile_parameters.starting_chainage + self.longitudinal_profile_parameters.station_interval * (len(self.elevations) - 1)
        min_chainage = self.longitudinal_profile_parameters.starting_chainage

        min_x = self.longitudinal_profile_parameters.profile_origin[0]
        min_y = self.longitudinal_profile_parameters.profile_origin[1]
        max_x = min_x + (max_chainage - min_chainage) * self.longitudinal_profile_parameters.horizontal_scale
        max_y = min_y + (max_elev - min_elev) * self.longitudinal_profile_parameters.vertical_scale

        return min_x, min_y, max_x, max_y



    def build_title(self) -> str:
        soup = BeautifulSoup(self.title.upper(), "html.parser")

        if self.address:
            p1 = soup.new_tag("p")
            p1.string = self.address.upper()
            soup.append(p1)

        if self.local_govt:
            p2 = soup.new_tag("p")
            p2.string = self.local_govt.upper()
            soup.append(p2)

        if self.state:
            p3 = soup.new_tag("p")
            p3.string = f"{self.state.upper()} STATE"
            soup.append(p3)

        if self.scale:
            p4 = soup.new_tag("p")
            p4.string = f"SCALE :- 1 : {int(self.scale)}"
            soup.append(p4)

        return soup.prettify()
