"""HTTP routes for Meridian Compute, mounted on the workspace API.

Reads need the viewer role, running work needs operator, and anything that
spends or earns money (renting, listing, API keys) needs admin. Platform
routes are for the operators of this Meridian Cloud (`admin_emails`).
"""

from __future__ import annotations

import csv
import io
from typing import Optional

from fastapi import Body, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from .accounts import ROLE_RANK, Principal
from .compute import ComputeService, err
from .templates import list_templates
from .web import Ctx, admin, api_router, operator, principal, viewer, w


def _compute(request: Request) -> ComputeService:
    value: ComputeService = request.app.state.compute
    return value


# ── public feed (no sign-in) ──────────────────────────────────────────────
@api_router.get("/public/offers")
def public_offers(request: Request):
    # The one /api route that may be cached: the cloud's middleware sets no-store on the rest.
    return JSONResponse(_compute(request).public_offers(), headers={"Cache-Control": "public, max-age=30"})


# ── marketplace ───────────────────────────────────────────────────────────
@w.get("/market/offers")
def offers(request: Request, mine: bool = Query(False), ctx: Ctx = Depends(viewer)):
    return {"offers": _compute(request).list_offers(ctx[1], mine=mine)}


@w.get("/market/listable-nodes")
def listable_nodes(request: Request, ctx: Ctx = Depends(viewer)):
    return {"nodes": _compute(request).listable_nodes(ctx[1])}


@w.post("/market/offers", status_code=201)
def create_offer(request: Request, body: dict = Body(...), ctx: Ctx = Depends(admin)):
    return _compute(request).create_offer(ctx[1], ctx[0].email, body)


@w.patch("/market/offers/{offer_id}")
def update_offer(request: Request, offer_id: str, body: dict = Body(...), ctx: Ctx = Depends(admin)):
    return _compute(request).update_offer(ctx[1], ctx[0].email, offer_id, body)


@w.delete("/market/offers/{offer_id}")
def retire_offer(request: Request, offer_id: str, ctx: Ctx = Depends(admin)):
    _compute(request).retire_offer(ctx[1], ctx[0].email, offer_id)
    return Response(status_code=204)


@w.get("/market/rentals")
def rentals(request: Request, ctx: Ctx = Depends(viewer)):
    return {"rentals": _compute(request).list_rentals(ctx[1])}


@w.post("/market/rentals", status_code=201)
def rent(request: Request, body: dict = Body(...), ctx: Ctx = Depends(admin)):
    return _compute(request).rent(ctx[1], ctx[0].email, str(body.get("offer_id", "")))


@w.post("/market/rentals/{rental_id}/release")
def release(request: Request, rental_id: str, ctx: Ctx = Depends(admin)):
    return _compute(request).release(ctx[1], ctx[0].email, rental_id)


# ── jobs ──────────────────────────────────────────────────────────────────
@w.get("/jobs")
def jobs(request: Request, status: Optional[str] = Query(None), limit: int = Query(100), ctx: Ctx = Depends(viewer)):
    return {"jobs": _compute(request).list_jobs(ctx[1], status=status, limit=limit)}


@w.post("/jobs", status_code=201)
def submit_job(request: Request, body: dict = Body(...), ctx: Ctx = Depends(operator)):
    may_rent = ROLE_RANK[ctx[2]] >= ROLE_RANK["admin"]
    return _compute(request).submit_job(ctx[1], ctx[0].email, body, may_rent=may_rent)


@w.get("/job-templates")
def job_templates(ctx: Ctx = Depends(viewer)):
    return {"templates": list_templates()}


@w.get("/jobs/{job_id}")
def job(request: Request, job_id: str, ctx: Ctx = Depends(viewer)):
    # A service's bearer token is a credential: operators and up only.
    reveal = ROLE_RANK[ctx[2]] >= ROLE_RANK["operator"]
    return _compute(request).job_detail(ctx[1], job_id, reveal=reveal)


@w.post("/jobs/{job_id}/cancel")
def cancel_job(request: Request, job_id: str, ctx: Ctx = Depends(operator)):
    return _compute(request).cancel_job(ctx[1], ctx[0].email, job_id)


# ── billing ───────────────────────────────────────────────────────────────
@w.get("/billing")
def billing(request: Request, limit: int = Query(200), ctx: Ctx = Depends(viewer)):
    return _compute(request).billing(ctx[1], limit=limit)


@w.get("/compute/overview")
def compute_overview(request: Request, ctx: Ctx = Depends(viewer)):
    return _compute(request).overview(ctx[1])


# ── usage and quotas ──────────────────────────────────────────────────────
_CSV_COLUMNS = ("email", "name", "group", "gpu_hours_own", "gpu_hours_rented", "jobs", "quota_gpu_hours",
                "remaining_gpu_hours")


def _csv_cell(value):
    """A spreadsheet runs a cell that starts with = + - @ as a formula: defuse names a member chose."""
    return "'" + value if isinstance(value, str) and value[:1] in ("=", "+", "-", "@") else value


@w.get("/usage")
def usage(request: Request, month: Optional[str] = Query(None), format: str = Query("json"),
          ctx: Ctx = Depends(viewer)):
    if format not in ("json", "csv"):
        raise err("INVALID_FIELD", "format must be json or csv")
    data = _compute(request).usage(ctx[1], month)
    if format == "json":
        return data
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(_CSV_COLUMNS)
    for p in data["people"]:
        writer.writerow([_csv_cell("" if p[c] is None else p[c]) for c in _CSV_COLUMNS])
    return Response(out.getvalue(), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="meridian-usage-{data["month"]}.csv"',
    })


@w.patch("/members/{user_id}/compute")
def member_compute(request: Request, user_id: str, body: dict = Body(...), ctx: Ctx = Depends(admin)):
    return _compute(request).set_member_compute(ctx[1], ctx[0].email, user_id, body)


@w.patch("/compute-settings")
def compute_settings(request: Request, body: dict = Body(...), ctx: Ctx = Depends(admin)):
    return _compute(request).set_compute_settings(ctx[1], ctx[0].email, body)


# ── API keys ──────────────────────────────────────────────────────────────
@w.get("/api-keys")
def api_keys(request: Request, ctx: Ctx = Depends(admin)):
    return {"keys": _compute(request).list_api_keys(ctx[1])}


@w.post("/api-keys", status_code=201)
def create_api_key(request: Request, body: dict = Body(...), ctx: Ctx = Depends(admin)):
    token, view = _compute(request).create_api_key(ctx[1], ctx[0].email, body)
    return {**view, "token": token}  # shown once


@w.delete("/api-keys/{key_id}")
def revoke_api_key(request: Request, key_id: str, ctx: Ctx = Depends(admin)):
    _compute(request).revoke_api_key(ctx[1], ctx[0].email, key_id)
    return Response(status_code=204)


# ── platform (operators of this Meridian Cloud) ───────────────────────────
@api_router.get("/platform/summary")
def platform_summary(request: Request, p: Principal = Depends(principal)):
    return _compute(request).platform_summary(p.email)


@api_router.post("/platform/credits", status_code=201)
def platform_credit(request: Request, body: dict = Body(...), p: Principal = Depends(principal)):
    return _compute(request).platform_credit(p.email, body)


@api_router.post("/platform/payouts", status_code=201)
def platform_payout(request: Request, body: dict = Body(...), p: Principal = Depends(principal)):
    return _compute(request).platform_payout(p.email, body)
