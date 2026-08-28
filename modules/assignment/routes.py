"""
routes.py -- assignment module's one carve-out beyond generic CRUD.

Availability = equipment with no OPEN assignment (released_at IS NULL).
This is a cross-table query, which crud.py's single-table model can't
express -- hence a dedicated route rather than a table.json rule.

NOTE: /api/equipment/available only filters the create-form dropdown.
It is not enforced at the API layer -- direct POST/PUT calls can
double-book equipment. Deliberate: CLI/API access is assumed to be a
trusted, informed caller for this module.
"""

from flask import Blueprint, jsonify
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy import Table


def build_routes(tables: dict[str, Table], engine: Engine) -> Blueprint:
    equipment = tables["equipment"]
    assignment = tables["assignment"]
    bp = Blueprint("assignment_routes", __name__)

    @bp.get("/api/equipment/available")
    def available_equipment():
        open_assignments = (
            select(assignment.c.equipment_id)
            .where(assignment.c.released_at.is_(None))
        )
        with engine.connect() as conn:
            rows = conn.execute(
                select(equipment).where(equipment.c.id.notin_(open_assignments))
            ).mappings().all()
        return jsonify([dict(r) for r in rows])

    return bp
