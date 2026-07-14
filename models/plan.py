"""Pydantic models describing survey plan payloads.

These models define the JSON contract between this service and its callers
(e.g. the TypeScript API server that handles users and persistence).
"""

from enum import Enum
from typing import List, Optional, Union
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
    degrees: Optional[int] = 0
    minutes: Optional[int] = 0
    seconds: Optional[float] = 0.0
    decimal: Optional[float] = 0.0


class TraverseLegProps(BaseModel):
    from_: CoordinateProps = Field(alias="from")
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
    minimum_distance: float = 0.1  # 0.1 to 0.5
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


class LayoutPlotProps(BaseModel):
    """A single plot in a layout: corner beacon ids referencing the plan's
    coordinate register, in polygon order."""
    block: str = ""
    number: Union[int, str] = ""
    ids: List[str] = []
    area: Optional[float] = None
    use: str = "residential"  # residential | commercial | open_space | <facility>

    def label(self) -> str:
        if self.block:
            return f"Block {self.block} Plot {self.number}"
        return f"Plot {self.number}"


class LayoutRoadProps(BaseModel):
    """A road defined by centerline beacon ids in the coordinate register."""
    name: str = ""
    width: float = 9.0
    centerline_ids: List[str] = []


class LayoutPlotParams(BaseModel):
    """Standard plot module, quoted as frontage x depth (15 x 30 = 450 sqm)."""
    frontage: float = 15.0  # meters along the road
    depth: float = 30.0  # meters
    min_area: float = 400.0  # drop edge remainders smaller than this
    remainder_strategy: str = "add_to_last"  # add_to_last | separate | distribute


class LayoutRoadParams(BaseModel):
    major_width: float = 15.0  # spine road right-of-way
    collector_width: float = 12.0
    access_width: float = 9.0
    corner_radius: float = 6.0
    major_road_name: str = ""


class LayoutBlockParams(BaseModel):
    double_loaded: bool = True  # two plot rows back-to-back per block
    max_length: float = 180.0  # block length before a cross street
    orientation: str = "auto"  # auto | ns | ew


class LayoutReserveParams(BaseModel):
    open_space_percent: float = 10.0
    commercial_along_major: bool = True
    facilities: List[str] = []  # e.g. ["school", "market"]


class LayoutNumberingParams(BaseModel):
    scheme: str = "block_plot"  # Block A Plot 1 ...
    block_labels: str = "alphabetic"
    plot_start: int = 1


class LayoutParameters(BaseModel):
    """Design parameters for auto-generating a subdivision layout."""
    plot: LayoutPlotParams = Field(default_factory=LayoutPlotParams)
    roads: LayoutRoadParams = Field(default_factory=LayoutRoadParams)
    blocks: LayoutBlockParams = Field(default_factory=LayoutBlockParams)
    reserves: LayoutReserveParams = Field(default_factory=LayoutReserveParams)
    numbering: LayoutNumberingParams = Field(default_factory=LayoutNumberingParams)


class LongitudinalProfileParameters(BaseModel):
    horizontal_scale: float = 1.0  # drawing units per metre of chainage
    vertical_scale: float = 1.0  # drawing units per metre of elevation
    profile_origin: List[float] = [0.0, 0.0]
    station_interval: float = 10.0  # metres
    elevation_interval: float = 1.0
    starting_chainage: float = 0.0


class RouteParameters(BaseModel):
    """Plan-view (horizontal alignment) settings for route surveys.

    The plan view is drawn when the payload carries station coordinates
    (``coordinates`` entries whose ids match the ``elevations`` ids).
    """
    right_of_way_width: float = 30.0  # metres, total corridor width
    show_plan_view: bool = True
    show_chainage_labels: bool = True


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
    font_size: float = 12
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
    topographic_setting: TopographicSettingProps = Field(default_factory=TopographicSettingProps)
    topographic_boundary: Optional[TopographicBoundaryProps] = None
    layout_boundary: Optional[LayoutBoundaryProps] = None
    layout_parameters: LayoutParameters = Field(default_factory=LayoutParameters)
    plots: Optional[List[LayoutPlotProps]] = None
    roads: Optional[List[LayoutRoadProps]] = None
    longitudinal_profile_parameters: Optional[LongitudinalProfileParameters] = None
    route_parameters: RouteParameters = Field(default_factory=RouteParameters)
    footers: List[str] = []
    footer_size: float = 0.5
    dxf_version: str = "R2000"

    def get_drawing_scale(self) -> float:
        """Drawing-unit multiplier so that geometry is drawn at 1:1000 base."""
        if not self.scale:
            return 1.0
        return 1000 / self.scale

    def get_bounding_box(self) -> tuple:
        """Bounding box (min_x, min_y, max_x, max_y) of all plan coordinates.

        Returns a tuple of ``None`` values when the plan has no coordinates.
        """
        xs, ys = [], []

        if self.coordinates:
            xs = [p.easting for p in self.coordinates]
            ys = [p.northing for p in self.coordinates]

        if self.type == PlanType.TOPOGRAPHIC and self.topographic_boundary is not None:
            xs += [p.easting for p in self.topographic_boundary.coordinates]
            ys += [p.northing for p in self.topographic_boundary.coordinates]

        if self.type == PlanType.LAYOUT and self.layout_boundary is not None:
            xs += [p.easting for p in self.layout_boundary.coordinates]
            ys += [p.northing for p in self.layout_boundary.coordinates]

        if not xs or not ys:
            return None, None, None, None

        return min(xs), min(ys), max(xs), max(ys)

    def get_route_plan_bounding_box(self) -> Optional[tuple]:
        """Bounding box of the longitudinal profile, in drawing coordinates."""
        if self.type != PlanType.ROUTE or not self.elevations or self.longitudinal_profile_parameters is None:
            return None

        params = self.longitudinal_profile_parameters
        min_elev = min(e.elevation for e in self.elevations)
        max_elev = max(e.elevation for e in self.elevations)
        chainage_length = params.station_interval * (len(self.elevations) - 1)

        min_x = params.profile_origin[0]
        min_y = params.profile_origin[1]
        max_x = min_x + chainage_length * params.horizontal_scale
        max_y = min_y + (max_elev - min_elev) * params.vertical_scale

        return min_x, min_y, max_x, max_y

    def build_title(self) -> str:
        """Compose the plan title block as an HTML fragment."""
        soup = BeautifulSoup(self.title.upper(), "html.parser")

        for line in (
            self.address.upper() if self.address else None,
            self.local_govt.upper() if self.local_govt else None,
            f"{self.state.upper()} STATE" if self.state else None,
            f"SCALE :- 1 : {int(self.scale)}" if self.scale else None,
        ):
            if line:
                p = soup.new_tag("p")
                p.string = line
                soup.append(p)

        return str(soup)
