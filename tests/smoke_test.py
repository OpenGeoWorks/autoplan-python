"""Smoke test: generate a DXF for every plan type from sample payloads.

Run from the repository root:

    python tests/smoke_test.py [output_dir]

This exercises the full drawing pipeline (validation -> draw -> DXF export)
without the DWG conversion or the Cloudinary upload, so it needs neither the
ODA File Converter nor any credentials.
"""

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plans import CadastralPlan, LayoutPlan, RoutePlan, TopographicPlan

BASE = {
    "id": "smoke-test",
    "created_at": "2026-01-01T00:00:00Z",
    "user": "smoke-tester",
    "project": "smoke-project",
    "title": "Plan of <b>Smoke Test</b> Property",
    "address": "1 Example Close",
    "local_govt": "Eti-Osa",
    "state": "Lagos",
    "scale": 1000,
    "footers": ["<p>Surveyed by <b>Smoke Tester</b></p>", "<p>Checked by QA</p>"],
}

# A 100 m x 80 m parcel in UTM zone 31 coordinates
SQUARE = [
    ("PB1", 543210.0, 712345.0),
    ("PB2", 543310.0, 712345.0),
    ("PB3", 543310.0, 712425.0),
    ("PB4", 543210.0, 712425.0),
]


def leg(a, b):
    (ida, ea, na), (idb, eb, nb) = a, b
    dist = math.hypot(eb - ea, nb - na)
    bearing = math.degrees(math.atan2(eb - ea, nb - na)) % 360
    deg = int(bearing)
    minutes = int((bearing - deg) * 60)
    return {
        "from": {"id": ida, "easting": ea, "northing": na},
        "to": {"id": idb, "easting": eb, "northing": nb},
        "distance": dist,
        "bearing": {"degrees": deg, "minutes": minutes, "decimal": bearing},
    }


def cadastral_payload():
    return BASE | {
        "type": "cadastral",
        "name": "smoke cadastral",
        "coordinates": [
            {"id": i, "easting": e, "northing": n} for i, e, n in SQUARE
        ],
        "parcels": [{
            "name": "Parcel A",
            "ids": [i for i, _, _ in SQUARE],
            "area": 8000.0,
            "legs": [leg(SQUARE[i], SQUARE[(i + 1) % 4]) for i in range(4)],
        }],
    }


def topographic_payload():
    coords = []
    n = 0
    for i in range(6):
        for j in range(6):
            e = 543210.0 + i * 20
            no = 712345.0 + j * 16
            z = 100 + 3 * math.sin(i / 2) + 2 * math.cos(j / 2)
            n += 1
            coords.append({"id": f"T{n}", "easting": e, "northing": no, "elevation": round(z, 2)})
    return BASE | {
        "type": "topographic",
        "name": "smoke topographic",
        "coordinates": coords,
        "topographic_boundary": {
            "coordinates": [{"id": i, "easting": e, "northing": n} for i, e, n in SQUARE],
            "area": 8000.0,
            "legs": [leg(SQUARE[i], SQUARE[(i + 1) % 4]) for i in range(4)],
        },
        "topographic_setting": {
            "tin": True,
            "grid": True,
            "contour_interval": 0.5,
            "major_contour": 2.0,
            "show_mesh": True,
        },
    }


def route_payload():
    # A gently curving route heading roughly north-east, 20 m stations
    coordinates = []
    e, n, heading = 543100.0, 712000.0, math.radians(35)
    for i in range(15):
        coordinates.append({"id": f"CH{i}", "easting": round(e, 3), "northing": round(n, 3)})
        heading += math.radians(2.5)  # slight right-hand curvature
        e += 20 * math.cos(heading)
        n += 20 * math.sin(heading)

    return BASE | {
        "type": "route",
        "name": "smoke route",
        "coordinates": coordinates,
        "elevations": [
            {"id": f"CH{i}", "chainage": f"0+{i * 20:03d}", "elevation": round(100 + 4 * math.sin(i / 3), 2)}
            for i in range(15)
        ],
        "longitudinal_profile_parameters": {
            "horizontal_scale": 1.0,
            "vertical_scale": 5.0,
            "profile_origin": [0.0, 0.0],
            "station_interval": 20.0,
            "elevation_interval": 1.0,
            "starting_chainage": 0.0,
        },
        "route_parameters": {
            "right_of_way_width": 30.0,
            "show_plan_view": True,
        },
    }


def layout_payload():
    boundary = [
        ("LB1", 543000.0, 712000.0),
        ("LB2", 543400.0, 712000.0),
        ("LB3", 543400.0, 712300.0),
        ("LB4", 543000.0, 712300.0),
    ]
    return BASE | {
        "type": "layout",
        "name": "smoke layout",
        "layout_boundary": {
            "coordinates": [{"id": i, "easting": e, "northing": n} for i, e, n in boundary],
            "area": 120000.0,
            "legs": [leg(boundary[i], boundary[(i + 1) % 4]) for i in range(4)],
        },
        "layout_parameters": {
            "plot": {"frontage": 15.0, "depth": 30.0},
            "roads": {"major_road_name": "Main Avenue"},
            "reserves": {"open_space_percent": 10.0, "facilities": ["school"]},
        },
    }


PLANS = [
    (CadastralPlan, cadastral_payload),
    (TopographicPlan, topographic_payload),
    (RoutePlan, route_payload),
    (LayoutPlan, layout_payload),
]


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="fyp_smoke_")
    os.makedirs(out_dir, exist_ok=True)
    failures = 0

    for plan_cls, payload_fn in PLANS:
        label = plan_cls.__name__
        try:
            plan = plan_cls(**payload_fn())
            plan.draw()
            path = os.path.join(out_dir, f"{label}.dxf")
            plan.save_dxf(path)
            size = os.path.getsize(path)
            print(f"OK   {label}: {path} ({size} bytes)")
        except Exception as e:
            failures += 1
            print(f"FAIL {label}: {type(e).__name__}: {e}")

    print(f"\nOutput directory: {out_dir}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
