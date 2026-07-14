# Survey Plan Generator

A web service that turns raw field data from engineering and cadastral surveys
into ready-to-use survey plans. Plans are drawn as DXF (via
[ezdxf](https://ezdxf.mozman.at/)), converted to DWG with the
[ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter)
so they can be edited in AutoCAD, rendered to PDF, and uploaded as a ZIP bundle.

This service is drawing-only: it validates a plan payload, generates the
drawing, and returns a download URL. User management, projects, and persistence
are handled by a separate API server.

## Supported plan types

| Type | Endpoint | Description |
|------|----------|-------------|
| Cadastral | `POST /cadastral/plan` | Property beacons, parcel boundaries, bearing/distance labels |
| Topographic | `POST /topographic/plan` | Spot heights, site boundary, TIN/grid contours |
| Route | `POST /route/plan` | Plan-and-profile sheet (see below) |
| Layout | `POST /layout/plan` | Estate subdivision schemes (see below) |

### Route plans

Route plans are drawn as the industry-standard **plan-and-profile sheet**:

- **Plan view (horizontal alignment)** — drawn when the payload carries
  station coordinates (`coordinates` whose ids match the `elevations` ids).
  The route is rotated to run left-to-right above the profile, with chainage
  ticks/labels, right-of-way edges (`route_parameters.right_of_way_width`),
  and a north arrow rotated to match.
- **Longitudinal profile** — existing ground level against chainage over a
  station/elevation grid, at the scales in
  `longitudinal_profile_parameters`.

Payloads without station coordinates draw the profile only (backward
compatible).

### Layout plans

Layout plans work in two modes:

- **Draw mode** — the payload provides the plot corner coordinate register
  (`coordinates`), the `plots` (corner ids per plot, with block/number/use),
  and optionally `roads`; the scheme is drawn as given.
- **Generate mode** — only the perimeter (`layout_boundary`) and design
  parameters (`layout_parameters`) are provided. The subdivision is designed
  automatically using the standard Nigerian pattern: a major spine road along
  the site's long axis, cross streets limiting block length, double-loaded
  blocks of frontage x depth plots (default 15 m x 30 m), commercial plots
  along the spine, open-space and facility reservations, and per-block plot
  numbering with a land-use schedule table.

Either way the exported ZIP includes `setting_out_coordinates.csv` — the
coordinates of every boundary beacon, plot corner, and road centerline point,
ready for field setting-out.

Perimeter bearings/distances are computed upstream by the AutoPlan API and
arrive in the payload as `layout_boundary.legs`; when absent, plans are
drawn without the perimeter leg labels.

All endpoints accept a JSON payload described by `models/plan.py`
(`PlanProps`) and respond with:

```json
{ "message": "Cadastral plan generated", "filename": "<plan name>", "url": "<zip url>" }
```

Invalid payloads return `400` with validation details. See
`tests/smoke_test.py` for complete example payloads for every plan type.

## Project structure

```
app.py            Flask entry point and endpoints
gunicorn.conf.py  Production server settings (timeouts, worker recycling)
dxf_manager.py    Low-level DXF drawing primitives (ezdxf wrapper)
models/plan.py    Pydantic models: the JSON contract for plan payloads
plans/base.py     Shared drawing logic (frame, title block, footers, north arrow)
plans/*.py        One generator per plan type
utils.py          Geometry and HTML→MText helpers
upload.py         Cloudinary upload helper
tests/            Smoke test with sample payloads
```

## Running locally

Requirements: Python 3.11+ and, for DWG output, the ODA File Converter on
your `PATH` (DXF generation and the smoke test work without it).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your Cloudinary URL

# generate sample plans without any credentials
python tests/smoke_test.py out/

# run the API
python app.py
```

## Configuration

| Variable | Purpose | Default |
|----------|---------|---------|
| `CLOUDINARY_URL` | Upload target for generated bundles | required for `save()` |
| `PORT` | HTTP port | `8080` |
| `WEB_CONCURRENCY` | Gunicorn workers | `1` |
| `GUNICORN_TIMEOUT` | Request timeout (seconds) | `300` |
| `GUNICORN_MAX_REQUESTS` | Requests per worker before recycling | `50` |

Worker recycling is deliberate: plan generation allocates large numpy/ezdxf
buffers and CPython rarely returns that memory to the OS, so long-lived
workers slowly grow. Recycling keeps memory bounded on small machines.

## Docker

The Dockerfile installs the ODA File Converter and runs the service under
Gunicorn:

```bash
docker build -t survey-plan-generator .
docker run --env-file .env -p 8080:8080 survey-plan-generator
```

## Deployment

Pushes to `main` trigger `.github/workflows/prod.yml`, which:

1. builds the Docker image and pushes it to Docker Hub as
   `<DOCKER_USERNAME>/autoplan-python:latest`, then
2. connects to the production Ubuntu server over SSH and restarts the
   service with Docker Compose (`docker compose pull && up -d`).

The workflow needs these repository secrets: `DOCKER_USERNAME`,
`DOCKER_PASSWORD`, `SERVER_HOST`, `SERVER_USERNAME`, `SERVER_SSH_KEY`,
and `SERVER_PORT`.

## Notes

- Fonts: text styles reference the font by file name (e.g.
  `Times New Roman.ttf`). Install the fonts you use in the runtime
  environment or PDF output falls back to a default font.
- The drawing is scaled so the output plan is at the requested scale
  (`scale`, default 1:1000); the graphical scale bar labels true ground
  distances.

## License

MIT — see [LICENSE](LICENSE).
