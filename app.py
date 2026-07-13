"""Flask entry point exposing the plan generation endpoints.

Each endpoint accepts a JSON plan payload (see ``models.plan.PlanProps``),
generates the drawing, and responds with the URL of the uploaded
DXF/DWG/PDF bundle.
"""

import json
import logging
import os

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, jsonify, request
from pydantic import ValidationError

from plans import CadastralPlan, LayoutPlan, RoutePlan, TopographicPlan

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)


def generate_plan(plan_cls, plan_label: str):
    """Validate the request payload, generate the plan, and upload it."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    try:
        plan = plan_cls(**data)
    except ValidationError as e:
        return jsonify({
            "error": "Invalid plan data",
            "details": json.loads(e.json(include_url=False)),
        }), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    plan.draw()
    url = plan.save()
    return jsonify({
        "message": f"{plan_label} plan generated",
        "filename": plan.name,
        "url": url,
    }), 200


@app.get("/")
def home():
    return jsonify({"service": "survey-plan-generator", "status": "ok"})


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/cadastral/plan")
def generate_cadastral_plan():
    return generate_plan(CadastralPlan, "Cadastral")


@app.post("/topographic/plan")
def generate_topographic_plan():
    return generate_plan(TopographicPlan, "Topographic")


@app.post("/layout/plan")
def generate_layout_plan():
    return generate_plan(LayoutPlan, "Layout")


@app.post("/route/plan")
def generate_route_plan():
    return generate_plan(RoutePlan, "Route")


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Resource not found"}), 404


@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": "Something went wrong on our side"}), 500


@app.errorhandler(Exception)
def handle_exception(e):
    app.logger.error("Unhandled exception: %s", e, exc_info=True)
    return jsonify({"error": "An unexpected error occurred"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
