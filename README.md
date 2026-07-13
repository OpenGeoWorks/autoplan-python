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
| Route | `POST /route/plan` | Longitudinal profile with station/elevation grid |
| Layout | `POST /layout/plan` | Subdivision layouts (experimental, under development) |

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

Deployment to [Fly.io](https://fly.io) is configured in `fly.toml` and
`.github/workflows/fly.yml`.

## Notes

- Fonts: text styles reference the font by file name (e.g.
  `Times New Roman.ttf`). Install the fonts you use in the runtime
  environment or PDF output falls back to a default font.
- The drawing is scaled so the output plan is at the requested scale
  (`scale`, default 1:1000); the graphical scale bar labels true ground
  distances.

## License

MIT — see [LICENSE](LICENSE).
