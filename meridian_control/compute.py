"""Meridian Compute: GPU marketplace, per-second billing, and job scheduling.

Three things live here, on top of the node fleet the control plane already
manages:

- **Marketplace.** A provider workspace lists a node's GPUs at a price per
  GPU-hour. A consumer workspace rents the listing and gets those GPUs
  exclusively until it releases them.
- **Billing.** A rental is metered per second. The consumer pays the provider's
  price plus Meridian's fee (`market_fee_bps`, 2% by default); the provider
  earns the price; the fee goes to the platform account. Money is integer
  paise in an append-only ledger. Balances are prepaid.
- **Jobs.** A workspace runs digest-pinned containers on its own nodes or on
  GPUs it rents: a `job` runs to completion, a `service` runs until stopped
  and exposes a port. A job with several replicas is placed all-or-nothing and
  each replica is told its rank and its peers, which is what distributed
  training needs to rendezvous.

The control plane owns each node's desired snapshot. Engine assignments still
come from the node's owner; the `jobs` list is composed here, so a tenant can
never edit a provider's engines and a provider can never read or change a
tenant's job.

What this module does not do: take payments (credits are recorded by a
platform admin against an external payment reference), issue tax invoices, or
hold secrets for jobs. See docs/COMPUTE.md.
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Iterator, Optional, TypeVar, cast

from sqlalchemy import func, select, update

from . import validation
from .accounts import ROLE_RANK
from .config import ControlConfig
from .models import (
    PLATFORM_ACCOUNT,
    ApiKey,
    AuditEvent,
    DesiredSnapshotRow,
    Job,
    JobReplica,
    LedgerEntry,
    MarketOffer,
    Membership,
    Node,
    ObservationRow,
    Rental,
    User,
    Workspace,
)
from .security import new_id, sha256_hex
from .service import ControlServiceError
from .templates import apply_template

log = logging.getLogger("meridian_control.compute")

CURRENCY = "INR"
IMAGE_REF = re.compile(r"^[a-z0-9]([a-z0-9._:-]*[a-z0-9])?(/[a-z0-9]([a-z0-9._-]*[a-z0-9])?)+@sha256:[0-9a-f]{64}$")
MASKED = "********"  # an environment value shown to a role that may not read it
TERMINAL_JOB = ("succeeded", "failed", "cancelled")
TERMINAL_REPLICA = frozenset({"Succeeded", "Failed", "Cancelled"})
REPLICA_PHASES = frozenset({"Pending", "Starting", "Running"}) | TERMINAL_REPLICA
API_KEY_ROLES = ("viewer", "operator", "admin")
_F = TypeVar("_F", bound=Callable[..., Any])
MAX_ACTIVE_JOBS = 200
MAX_PRICE_PAISE = 100_000_00  # ₹1,00,000 per GPU-hour: a typo guard, not a market opinion
MIN_PRICE_PAISE = 100  # ₹1
MAX_GPU_HOUR_QUOTA = 1_000_000
PUBLIC_FEED_SECONDS = 30  # how long the public listings feed is served from memory
PUBLIC_FEED_MAX_OFFERS = 100  # the feed lists at most this many offers (cheapest available first)
# What a node's owner may see of a tenant's job: that it exists and what it holds.
_OBSERVED_KEEP = ("job_id", "replica", "phase", "started_at", "finished_at")
_DESIRED_KEEP = ("job_id", "replica", "kind", "device_ids", "port", "desired_phase")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo is not None else d.replace(tzinfo=timezone.utc)


def _iso(d: Optional[datetime]) -> Optional[str]:
    return _aware(d).astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if d else None


def month_bounds(now: datetime, month: Optional[str] = None) -> tuple[str, datetime, datetime]:
    """(YYYY-MM, first instant, first instant of the next month) in UTC."""
    if month is None:
        year, mon = now.year, now.month
    else:
        m = re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2])", month)
        if m is None or not 2000 <= int(m.group(1)) <= 2100:
            raise err("INVALID_FIELD", "month must look like 2026-11")
        year, mon = int(m.group(1)), int(m.group(2))
    start = datetime(year, mon, 1, tzinfo=timezone.utc)
    end = datetime(year + (mon == 12), mon % 12 + 1, 1, tzinfo=timezone.utc)
    return f"{year:04d}-{mon:02d}", start, end


def err(code: str, message: str, status: int = 400) -> ControlServiceError:
    return ControlServiceError(code, message, http_status=status)


# --- money -------------------------------------------------------------------
def rental_due(price_paise_per_gpu_hour: int, gpus: int, seconds: int) -> int:
    """What the provider has earned after `seconds`. Integer paise, rounded down:
    the consumer is never charged for a fraction of a paisa not yet used."""
    return price_paise_per_gpu_hour * gpus * max(0, seconds) // 3600


def fee_for(amount_paise: int, fee_bps: int) -> int:
    """Meridian's fee on an amount, rounded half up to a paisa."""
    return (amount_paise * fee_bps + 5000) // 10000


def _int(body: dict, key: str, default: int, lo: int, hi: int) -> int:
    v = body.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise err("INVALID_FIELD", f"{key} must be an integer between {lo} and {hi}")
    return v


def _text(body: dict, key: str, lo: int, hi: int, default: Optional[str] = None, multiline: bool = False) -> str:
    return validation.clean_text(body.get(key, default), key, lo, hi, multiline=multiline)


def _clamp(d: datetime, lo: datetime, hi: datetime) -> datetime:
    return max(lo, min(d, hi))


def _masked_env(env: dict) -> dict:
    return {k: MASKED for k in env}


def _hours(seconds: int) -> float:
    return round(seconds / 3600, 2)


def _quota(value: Any, name: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_GPU_HOUR_QUOTA:
        raise err("INVALID_FIELD", f"{name} must be a whole number of GPU-hours, 0 to {MAX_GPU_HOUR_QUOTA}, or null")
    return value


def _locked(fn: _F) -> _F:
    """Run a method holding the service lock, so passes that read then write
    placement or billing state never interleave."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    return cast(_F, wrapper)


class ComputeService:
    def __init__(self, session_factory, config: ControlConfig, secret: bytes, now: Callable[[], datetime] = _utcnow):
        self._sf = session_factory
        self._c = config
        self._secret = secret
        self._now = now
        # shortcut: in-process lock, so run compute on one control replica; move to a DB advisory lock for multi-replica Postgres  # noqa: E501
        self._lock = threading.RLock()
        self._public_feed: Optional[tuple[datetime, dict]] = None

    # ------------------------------------------------------------------ nodes
    @contextmanager
    def _isolated(self, s, kind: str, item_id: str) -> Iterator[None]:
        """Run one item of a background pass in a savepoint. If it fails, undo only
        that item and keep going: one bad node, offer, rental or job never stops
        metering or scheduling for everyone else. Logs ids and the error type only."""
        try:
            with s.begin_nested():
                yield
        except Exception as e:  # noqa: BLE001 - isolation is the point
            log.error("compute: skipped %s %s this pass (%s)", kind, item_id, type(e).__name__)

    def _online(self, node: Node, now: datetime) -> bool:
        return (not node.revoked) and node.lease_expires_at is not None and now < _aware(node.lease_expires_at)

    def _lost(self, node: Optional[Node], now: datetime) -> bool:
        """The node is gone, revoked, or silent for longer than the grace period."""
        return node is None or node.revoked or node.lease_expires_at is None or (
            now - _aware(node.lease_expires_at) > timedelta(seconds=self._c.node_lost_seconds)
        )

    @staticmethod
    def _capacity(s, node_id: str) -> dict:
        """The node's last reported capacity, validated; {} if it has none or it is invalid."""
        obs = s.get(ObservationRow, node_id)
        return validation.stored_capacity(obs.observation, node_id) if obs is not None else {}

    @staticmethod
    def _gpu_facts(node: Node, device_ids: Iterable[str], cap: dict) -> tuple[str, int]:
        """(model, VRAM per GPU) for a set of devices, from the node's inventory;
        falls back to the committed VRAM budget when the inventory lacks them."""
        inv = {g["device_id"]: g for g in validation.stored_inventory(node.inventory)}
        ids = list(device_ids)
        models = [str(inv[d].get("model") or "") for d in ids if d in inv]
        vram = [int(inv[d].get("memory_total_bytes") or 0) for d in ids if d in inv]
        alloc = cap.get("allocatable_vram_bytes") or {}
        if not vram or not all(vram):
            vram = [int(alloc.get(d, 0)) for d in ids]
        model = max(set(models), key=models.count) if models else ""
        return model, (min(vram) if vram else 0)

    @staticmethod
    def _snapshot_load(s, node_id: str) -> int:
        """Upper bound, in characters, on the job part of a node's desired snapshot."""
        total = 0
        for r in s.scalars(select(JobReplica).where(JobReplica.node_id == node_id, JobReplica.released.is_(False))):
            job = s.get(Job, r.job_id)
            if job is not None:
                total += validation.spec_size(list(job.args or []), dict(job.env or {}), job.replicas)[0]
        return total

    def _held(self, s, node_id: str) -> tuple[set[str], set[int]]:
        """Devices and ports held on a node by replicas it has not released."""
        devices: set[str] = set()
        ports: set[int] = set()
        for r in s.scalars(select(JobReplica).where(JobReplica.node_id == node_id, JobReplica.released.is_(False))):
            devices.update(r.device_ids or [])
            if r.port:
                ports.add(r.port)
        return devices, ports

    @staticmethod
    def _active_rental_for_offer(s, offer_id: str) -> Optional[Rental]:
        row: Optional[Rental] = s.scalar(
            select(Rental).where(Rental.offer_id == offer_id, Rental.status == "active")
        )
        return row

    def _audit(self, s, workspace_id: str, actor: str, kind: str, detail: str = "", node_id: str = "") -> None:
        s.add(AuditEvent(
            workspace_id=workspace_id, actor=actor, kind=kind, detail=detail[:1000], node_id=node_id,
            created_at=self._now(),
        ))

    # ----------------------------------------------------------------- ledger
    @staticmethod
    def _balance(s, account: str) -> int:
        total = s.scalar(select(func.coalesce(func.sum(LedgerEntry.amount_paise), 0)).where(
            LedgerEntry.workspace_id == account
        ))
        return int(total or 0)

    def _post(self, s, account: str, kind: str, amount: int, *, rental_id: Optional[str] = None,
              reference: str = "", memo: str = "", actor: str = "") -> None:
        if amount == 0:
            return
        s.add(LedgerEntry(
            workspace_id=account, kind=kind, amount_paise=amount, rental_id=rental_id,
            reference=reference[:200], memo=memo[:400], actor=actor, created_at=self._now(),
        ))

    def _due(self, r: Rental, now: datetime) -> tuple[int, int]:
        """(provider amount, fee) owed for the whole rental up to `now`."""
        end = _aware(r.ended_at) if r.ended_at is not None else now
        seconds = int((end - _aware(r.started_at)).total_seconds())
        amount = rental_due(r.price_paise_per_gpu_hour, r.gpu_count, seconds)
        return amount, fee_for(amount, r.fee_bps)

    def _unposted(self, r: Rental, now: datetime) -> tuple[int, int]:
        amount, fee = self._due(r, now)
        return amount - r.billed_paise, fee - r.fee_paise

    def _meter(self, s, r: Rental, now: datetime, final: bool = False) -> None:
        """Post what the rental has accrued since it was last metered. Amounts are
        the cumulative total minus what is already posted, so repeated passes
        (and concurrent control replicas) can never double-charge or drift."""
        if not final and (now - _aware(r.billed_through)).total_seconds() < self._c.meter_interval_seconds:
            return
        for _attempt in range(2):
            d_amount, d_fee = self._unposted(r, now)
            memo = f"{r.gpu_count} GPU × {_iso(_aware(r.billed_through))} → {_iso(now)}"
            # Compare-and-swap on what we loaded: another session may have metered
            # since, and SQLite takes no row lock on read. Only the winner posts.
            won = s.execute(
                update(Rental).where(
                    Rental.id == r.id, Rental.billed_paise == r.billed_paise, Rental.fee_paise == r.fee_paise
                ).values(billed_paise=r.billed_paise + d_amount, fee_paise=r.fee_paise + d_fee, billed_through=now)
                .execution_options(synchronize_session=False)
            ).rowcount == 1
            if won:
                self._post(s, r.consumer_workspace_id, "rental_charge", -d_amount, rental_id=r.id, memo=memo)
                self._post(s, r.consumer_workspace_id, "platform_fee", -d_fee, rental_id=r.id, memo=memo)
                self._post(s, r.provider_workspace_id, "rental_earning", d_amount, rental_id=r.id, memo=memo)
                self._post(s, PLATFORM_ACCOUNT, "fee_revenue", d_fee, rental_id=r.id, memo=memo)
            s.refresh(r)
            if won:
                return

    def _spendable(self, s, workspace_id: str, now: datetime) -> int:
        """Balance minus what active rentals have accrued but not yet posted."""
        pending = 0
        for r in s.scalars(select(Rental).where(
            Rental.consumer_workspace_id == workspace_id, Rental.status == "active"
        )):
            pending += sum(self._unposted(r, now))
        return self._balance(s, workspace_id) - pending

    def billing(self, workspace_id: str, limit: int = 200) -> dict:
        now = self._now()
        with self._sf() as s:
            totals = {
                kind: int(total) for kind, total in s.execute(
                    select(LedgerEntry.kind, func.sum(LedgerEntry.amount_paise))
                    .where(LedgerEntry.workspace_id == workspace_id).group_by(LedgerEntry.kind)
                )
            }
            burn = accrued_spend = accrued_earn = 0
            for r in s.scalars(select(Rental).where(Rental.status == "active").where(
                (Rental.consumer_workspace_id == workspace_id) | (Rental.provider_workspace_id == workspace_id)
            )):
                hourly = r.price_paise_per_gpu_hour * r.gpu_count
                d_amount, d_fee = self._unposted(r, now)
                if r.consumer_workspace_id == workspace_id:
                    burn += hourly + fee_for(hourly, r.fee_bps)
                    accrued_spend += d_amount + d_fee
                else:
                    accrued_earn += d_amount
            balance = self._balance(s, workspace_id)
            entries = s.scalars(
                select(LedgerEntry).where(LedgerEntry.workspace_id == workspace_id)
                .order_by(LedgerEntry.id.desc()).limit(max(1, min(limit, 1000)))
            )
            return {
                "currency": CURRENCY,
                "fee_bps": self._c.market_fee_bps,
                "balance_paise": balance,
                # Accrued on running rentals, posted at the next metering pass.
                "accrued_spend_paise": accrued_spend,
                "accrued_earnings_paise": accrued_earn,
                "available_paise": balance - accrued_spend,
                "burn_paise_per_hour": burn,
                "totals_paise": totals,
                "entries": [
                    {
                        "id": e.id, "kind": e.kind, "amount_paise": e.amount_paise, "rental_id": e.rental_id,
                        "reference": e.reference, "memo": e.memo, "created_at": _iso(e.created_at),
                    }
                    for e in entries
                ],
            }

    # --------------------------------------------------------------- platform
    def is_platform_admin(self, email: str) -> bool:
        return bool(email) and email.lower() in self._c.admin_emails

    def _require_platform(self, email: str) -> None:
        if not self.is_platform_admin(email):
            raise err("NOT_FOUND", "not found", 404)

    @staticmethod
    def _workspace_ref(s, ref: str) -> Workspace:
        ws = s.get(Workspace, ref) or s.scalar(select(Workspace).where(Workspace.slug == ref))
        if ws is None:
            raise err("NOT_FOUND", "workspace not found", 404)
        found: Workspace = ws
        return found

    def platform_credit(self, actor: str, body: dict) -> dict:
        """Record money a workspace paid outside Meridian (bank transfer, a
        payment link). `reference` is the proof: a UTR or payment id."""
        self._require_platform(actor)
        amount = _int(body, "amount_paise", 0, 1, 10**11)
        reference = _text(body, "reference", 4, 200)
        with self._sf() as s:
            ws = self._workspace_ref(s, str(body.get("workspace", "")))
            dup = s.scalar(select(LedgerEntry).where(LedgerEntry.kind == "topup", LedgerEntry.reference == reference))
            if dup is not None:
                raise err("DUPLICATE_REFERENCE", "a credit with this payment reference already exists", 409)
            memo = validation.strip_control(str(body.get("memo", "")))[:400]
            self._post(s, ws.id, "topup", amount, reference=reference, memo=memo, actor=actor)
            self._audit(s, ws.id, actor, "credit_added", f"{amount} paise ref={reference}")
            s.commit()
            return {"workspace_id": ws.id, "balance_paise": self._balance(s, ws.id)}

    def platform_payout(self, actor: str, body: dict) -> dict:
        """Record that a provider's earnings were paid out to their bank."""
        self._require_platform(actor)
        amount = _int(body, "amount_paise", 0, 1, 10**11)
        reference = _text(body, "reference", 4, 200)
        now = self._now()
        with self._sf() as s:
            ws = self._workspace_ref(s, str(body.get("workspace", "")))
            dup = s.scalar(select(LedgerEntry).where(LedgerEntry.kind == "payout", LedgerEntry.reference == reference))
            if dup is not None:
                raise err("DUPLICATE_REFERENCE", "a payout with this reference already exists", 409)
            if self._spendable(s, ws.id, now) < amount:
                raise err("INSUFFICIENT_FUNDS", "payout exceeds the workspace's available balance", 409)
            memo = validation.strip_control(str(body.get("memo", "")))[:400]
            self._post(s, ws.id, "payout", -amount, reference=reference, memo=memo, actor=actor)
            self._audit(s, ws.id, actor, "payout_recorded", f"{amount} paise ref={reference}")
            s.commit()
            return {"workspace_id": ws.id, "balance_paise": self._balance(s, ws.id)}

    def platform_summary(self, actor: str) -> dict:
        self._require_platform(actor)
        with self._sf() as s:
            balances = {
                ws: int(total) for ws, total in s.execute(
                    select(LedgerEntry.workspace_id, func.sum(LedgerEntry.amount_paise))
                    .group_by(LedgerEntry.workspace_id)
                )
            }
            gmv = s.scalar(select(func.coalesce(func.sum(LedgerEntry.amount_paise), 0)).where(
                LedgerEntry.kind == "rental_earning"
            ))
            return {
                "currency": CURRENCY,
                "fee_bps": self._c.market_fee_bps,
                "fee_revenue_paise": balances.get(PLATFORM_ACCOUNT, 0),
                "gross_rental_paise": int(gmv or 0),
                "active_rentals": s.scalar(select(func.count()).select_from(Rental).where(Rental.status == "active")),
                "listed_offers": s.scalar(
                    select(func.count()).select_from(MarketOffer).where(MarketOffer.status == "listed")
                ),
                "workspaces": [
                    {"id": w.id, "slug": w.slug, "name": w.name, "balance_paise": balances.get(w.id, 0)}
                    for w in s.scalars(select(Workspace).order_by(Workspace.created_at))
                ],
            }

    # ----------------------------------------------------------------- offers
    def _offer_availability(self, s, o: MarketOffer, now: datetime) -> tuple[bool, str]:
        try:
            return self._offer_availability_unsafe(s, o, now)
        except Exception as e:  # noqa: BLE001 - one listing never breaks the marketplace
            log.error("compute: availability of offer %s failed (%s)", o.id, type(e).__name__)
            return False, "unavailable"

    def _offer_availability_unsafe(self, s, o: MarketOffer, now: datetime) -> tuple[bool, str]:
        if o.status != "listed":
            return False, o.status
        if self._active_rental_for_offer(s, o.id) is not None:
            return False, "rented"
        node = s.get(Node, o.node_id)
        if node is None or not self._online(node, now):
            return False, "offline"
        if not self._capacity(s, o.node_id).get("jobs_enabled"):
            return False, "jobs are not enabled on the host"
        held, _ = self._held(s, o.node_id)
        free = self._capacity(s, o.node_id).get("free_devices")
        if held & set(o.device_ids or []) or (isinstance(free, list) and not set(o.device_ids or []) <= set(free)):
            return False, "in use by the provider"
        return True, ""

    def _offer_view(self, s, o: MarketOffer, viewer_ws: str, now: datetime) -> dict:
        ws = s.get(Workspace, o.workspace_id)
        available, why = self._offer_availability(s, o, now)
        hourly = o.price_paise_per_gpu_hour * o.gpu_count
        fee = fee_for(hourly, self._c.market_fee_bps)
        mine = o.workspace_id == viewer_ws
        view = {
            "id": o.id, "visibility": o.visibility, "title": o.title, "description": o.description, "region": o.region,
            "gpu_model": o.gpu_model, "gpu_count": o.gpu_count, "vram_bytes_per_gpu": o.vram_bytes_per_gpu,
            "price_paise_per_gpu_hour": o.price_paise_per_gpu_hour,
            "fee_bps": self._c.market_fee_bps,
            # What renting the whole listing costs per hour, fee included.
            "hourly_paise": hourly, "hourly_fee_paise": fee, "hourly_total_paise": hourly + fee,
            "status": o.status, "available": available, "unavailable_reason": why,
            "provider": {"name": ws.name if ws else "", "slug": ws.slug if ws else ""},
            "mine": mine, "created_at": _iso(o.created_at), "updated_at": _iso(o.updated_at),
        }
        if mine:
            view["node_id"] = o.node_id
            view["device_ids"] = list(o.device_ids or [])
            # Stored as workspace ids; shown as each partner's current slug.
            view["shared_with"] = self._slugs(s, list(o.shared_with or []))
        return view

    @staticmethod
    def _may_see(o: MarketOffer, workspace_id: str) -> bool:
        """A private listing is for its owner and the workspaces it is shared with (by workspace id)."""
        return o.visibility != "private" or o.workspace_id == workspace_id or workspace_id in (o.shared_with or [])

    @staticmethod
    def _slugs(s, workspace_ids: list[str]) -> list[str]:
        if not workspace_ids:
            return []
        rows = s.execute(select(Workspace.id, Workspace.slug).where(Workspace.id.in_(workspace_ids)))
        return sorted(str(slug) for _id, slug in rows)

    def _sharing(self, s, workspace_id: str, body: dict, visibility: str, shared: list) -> tuple[str, list[str]]:
        """Validated (visibility, shared_with) after applying `body` over the current values.
        `shared_with` in a request names workspaces by slug (or id); it is stored as workspace ids,
        so renaming or re-slugging a workspace can never hand its access to someone else."""
        visibility = body.get("visibility", visibility)
        if visibility not in ("public", "private"):
            raise err("INVALID_FIELD", "visibility must be 'public' or 'private'")
        if visibility == "public":
            return visibility, []
        if "shared_with" not in body:
            return visibility, sorted(set(shared))
        refs = body["shared_with"]
        if not isinstance(refs, list) or len(refs) > 50 or not all(isinstance(x, str) for x in refs):
            raise err("INVALID_FIELD", "shared_with must be a list of at most 50 workspace slugs")
        ids: set[str] = set()
        for ref in sorted({x.strip().lower() for x in refs if x.strip()}):
            ws = s.get(Workspace, ref) or s.scalar(select(Workspace).where(Workspace.slug == ref))
            if ws is None:
                raise err("UNKNOWN_WORKSPACE", f"there is no workspace with the slug {ref!r}")
            ids.add(ws.id)
        ids.discard(workspace_id)
        return visibility, sorted(ids)

    def list_offers(self, workspace_id: str, mine: bool = False) -> list[dict]:
        now = self._now()
        with self._sf() as s:
            q = select(MarketOffer).where(MarketOffer.status != "retired")
            q = q.where(MarketOffer.workspace_id == workspace_id) if mine else q.where(MarketOffer.status == "listed")
            offers = [
                self._offer_view(s, o, workspace_id, now) for o in s.scalars(q) if self._may_see(o, workspace_id)
            ]
        # Cheapest available first: the comparison a renter actually makes.
        offers.sort(key=lambda o: (not o["available"], o["price_paise_per_gpu_hour"], o["title"]))
        return offers

    def public_offers(self) -> dict:
        """What the public website shows: public, listed offers and a summary, with no node, device or
        workspace ids. Cached in-process for 30 seconds, so a busy page cannot load the database."""
        now = self._now()
        cached = self._public_feed
        if cached is not None and 0 <= (now - cached[0]).total_seconds() < PUBLIC_FEED_SECONDS:
            return cached[1]
        offers: list[dict] = []
        with self._sf() as s:
            q = select(MarketOffer).where(MarketOffer.status == "listed", MarketOffer.visibility == "public")
            for o in s.scalars(q):
                hourly = o.price_paise_per_gpu_hour * o.gpu_count
                offers.append({
                    "title": o.title, "region": o.region, "gpu_model": o.gpu_model, "gpu_count": o.gpu_count,
                    "vram_bytes_per_gpu": o.vram_bytes_per_gpu, "price_paise_per_gpu_hour": o.price_paise_per_gpu_hour,
                    "hourly_total_paise": hourly + fee_for(hourly, self._c.market_fee_bps),
                    # No provider name: providers have not agreed to be named on the public site.
                    "available": self._offer_availability(s, o, now)[0],
                })
        offers.sort(key=lambda o: (not o["available"], o["price_paise_per_gpu_hour"], o["title"]))
        free = [o for o in offers if o["available"]]  # the summary covers every listing, not only those shown
        by_model: dict[str, int] = {}
        for o in free:
            by_model[o["gpu_model"]] = by_model.get(o["gpu_model"], 0) + o["gpu_count"]
        feed = {
            "offers": offers[:PUBLIC_FEED_MAX_OFFERS],
            "total_offers": len(offers),
            "summary": {
                "available_gpus": sum(o["gpu_count"] for o in free), "gpus_by_model": dict(sorted(by_model.items())),
                "regions": sorted({o["region"] for o in free}),
                "lowest_price_paise_per_gpu_hour": min((o["price_paise_per_gpu_hour"] for o in free), default=None),
            },
        }
        self._public_feed = (now, feed)
        return feed

    def listable_nodes(self, workspace_id: str) -> list[dict]:
        """The workspace's nodes and whether each can be listed, for the form."""
        now = self._now()
        with self._sf() as s:
            listed = {
                o.node_id for o in s.scalars(select(MarketOffer).where(
                    MarketOffer.workspace_id == workspace_id, MarketOffer.status != "retired"
                ))
            }
            out = []
            for node in s.scalars(select(Node).where(Node.workspace_id == workspace_id, Node.revoked.is_(False))):
                cap = self._capacity(s, node.node_id)
                devices = sorted((cap.get("allocatable_vram_bytes") or {}).keys())
                model, vram = self._gpu_facts(node, devices, cap)
                reason = ""
                if node.node_id in listed:
                    reason = "already listed"
                elif not self._online(node, now):
                    reason = "offline"
                elif not cap.get("jobs_enabled"):
                    reason = "jobs are not enabled in the node's policy"
                elif not devices:
                    reason = "no GPUs committed in the node's policy"
                out.append({
                    "node_id": node.node_id, "display_name": node.display_name, "gpu_model": model,
                    "gpu_count": len(devices), "vram_bytes_per_gpu": vram, "listable": not reason, "reason": reason,
                })
            return out

    @_locked
    def create_offer(self, workspace_id: str, actor: str, body: dict) -> dict:
        now = self._now()
        title = _text(body, "title", 3, 120)
        region = _text(body, "region", 2, 80)
        description = _text(body, "description", 0, 2000, default="", multiline=True)
        node_id = str(body.get("node_id", ""))
        with self._sf() as s:
            visibility, shared = self._sharing(s, workspace_id, body, "public", [])
            # Sharing with a partner can be free; a public listing keeps the ₹1 minimum.
            price = _int(body, "price_paise_per_gpu_hour", -1, 0 if visibility == "private" else MIN_PRICE_PAISE,
                         MAX_PRICE_PAISE)
            node = s.get(Node, node_id)
            if node is None or node.workspace_id != workspace_id or node.revoked:
                raise err("NODE_NOT_FOUND", "unknown node", 404)
            if s.scalar(select(MarketOffer).where(MarketOffer.node_id == node_id, MarketOffer.status != "retired")):
                raise err("ALREADY_LISTED", "this node already has a listing", 409)
            if not self._online(node, now):
                raise err("NODE_OFFLINE", "the node must be online to be listed", 409)
            cap = self._capacity(s, node_id)
            if not cap.get("jobs_enabled"):
                raise err(
                    "JOBS_DISABLED",
                    "the node's policy does not allow jobs; set policy.jobs_enabled on the host first", 409,
                )
            devices = sorted((cap.get("allocatable_vram_bytes") or {}).keys())
            if not devices:
                raise err("NO_GPUS", "the node reports no GPUs it will commit", 409)
            free = cap.get("free_devices")
            if isinstance(free, list) and not set(devices) <= set(free):
                raise err("DEVICES_BUSY", "stop the engines or jobs using these GPUs before listing", 409)
            model, vram = self._gpu_facts(node, devices, cap)
            offer = MarketOffer(
                id=new_id("off"), workspace_id=workspace_id, node_id=node_id, title=title, description=description,
                region=region, gpu_model=model or "GPU", gpu_count=len(devices), vram_bytes_per_gpu=vram,
                device_ids=devices, price_paise_per_gpu_hour=price, status="listed", created_by=actor,
                visibility=visibility, shared_with=shared, created_at=now, updated_at=now,
            )
            s.add(offer)
            self._audit(s, workspace_id, actor, "offer_listed",
                        f"{offer.id} {price} paise/GPU-hour {visibility} {','.join(shared)}", node_id)
            s.commit()
            return self._offer_view(s, offer, workspace_id, now)

    def _own_offer(self, s, workspace_id: str, offer_id: str) -> MarketOffer:
        o = s.get(MarketOffer, offer_id)
        if o is None or o.workspace_id != workspace_id or o.status == "retired":
            raise err("NOT_FOUND", "listing not found", 404)
        found: MarketOffer = o
        return found

    @_locked
    def update_offer(self, workspace_id: str, actor: str, offer_id: str, body: dict) -> dict:
        """Edit a listing. A price change applies to new rentals only: a running
        rental keeps the price it started at. Sharing is the exception: a rental by a
        workspace the listing is no longer shared with, or a free rental on a listing
        that turned public, ends now (`sharing_ended`) and its jobs stop."""
        now = self._now()
        with self._sf() as s:
            o = self._own_offer(s, workspace_id, offer_id)
            if "title" in body:
                o.title = _text(body, "title", 3, 120)
            if "region" in body:
                o.region = _text(body, "region", 2, 80)
            if "description" in body:
                o.description = _text(body, "description", 0, 2000, multiline=True)
            o.visibility, o.shared_with = self._sharing(s, workspace_id, body, o.visibility, list(o.shared_with or []))
            if "price_paise_per_gpu_hour" in body:
                o.price_paise_per_gpu_hour = _int(
                    body, "price_paise_per_gpu_hour", 0, 0 if o.visibility == "private" else MIN_PRICE_PAISE,
                    MAX_PRICE_PAISE,
                )
            if o.visibility == "public" and o.price_paise_per_gpu_hour < MIN_PRICE_PAISE:
                raise err("INVALID_FIELD", "a public listing must cost at least ₹1 per GPU-hour: set a price")
            if "status" in body:
                if body["status"] not in ("listed", "paused"):
                    raise err("INVALID_FIELD", "status must be 'listed' or 'paused'")
                o.status = body["status"]
            o.updated_at = now
            self._audit(s, workspace_id, actor, "offer_updated", f"{o.id} status={o.status} {o.visibility}", o.node_id)
            r = self._active_rental_for_offer(s, o.id)
            if r is not None and (
                not self._may_see(o, r.consumer_workspace_id)
                or (o.visibility == "public" and r.price_paise_per_gpu_hour < MIN_PRICE_PAISE)
            ):
                s.flush()
                # No actor on the partner's side: the provider's admin email is not theirs to see.
                self._publish(s, self._end_rental(s, r, "sharing_ended", now))
            s.commit()
            return self._offer_view(s, o, workspace_id, now)

    @_locked
    def retire_offer(self, workspace_id: str, actor: str, offer_id: str) -> None:
        with self._sf() as s:
            o = self._own_offer(s, workspace_id, offer_id)
            if self._active_rental_for_offer(s, o.id) is not None:
                raise err("RENTED", "end the active rental before removing the listing", 409)
            o.status, o.updated_at = "retired", self._now()
            self._audit(s, workspace_id, actor, "offer_retired", o.id, o.node_id)
            s.commit()

    # ---------------------------------------------------------------- rentals
    def _rental_view(self, s, r: Rental, viewer_ws: str, now: datetime) -> dict:
        offer = s.get(MarketOffer, r.offer_id)
        role = "consumer" if r.consumer_workspace_id == viewer_ws else "provider"
        other = s.get(Workspace, r.provider_workspace_id if role == "consumer" else r.consumer_workspace_id)
        node = s.get(Node, r.node_id)
        amount, fee = self._due(r, now)
        hourly = r.price_paise_per_gpu_hour * r.gpu_count
        end = _aware(r.ended_at) if r.ended_at else now
        return {
            "id": r.id, "role": role, "status": r.status, "end_reason": r.end_reason,
            "offer": {
                "id": r.offer_id, "title": offer.title if offer else "", "region": offer.region if offer else "",
                "gpu_model": offer.gpu_model if offer else "",
                "vram_bytes_per_gpu": offer.vram_bytes_per_gpu if offer else 0,
            },
            "counterparty": other.name if other else "",
            "gpu_count": r.gpu_count,
            "price_paise_per_gpu_hour": r.price_paise_per_gpu_hour, "fee_bps": r.fee_bps,
            "hourly_total_paise": hourly + (fee_for(hourly, r.fee_bps) if role == "consumer" else 0),
            # Consumer: what it has cost, fee included. Provider: what it has earned.
            "amount_paise": amount + fee if role == "consumer" else amount,
            "fee_paise": fee if role == "consumer" else 0,
            "seconds": int((end - _aware(r.started_at)).total_seconds()),
            "node_online": bool(node and self._online(node, now)),
            "release_after_job_id": r.release_after_job_id if role == "consumer" else None,
            "started_at": _iso(r.started_at), "ended_at": _iso(r.ended_at),
        }

    def list_rentals(self, workspace_id: str, limit: int = 100) -> list[dict]:
        now = self._now()
        with self._sf() as s:
            q = select(Rental).where(
                (Rental.consumer_workspace_id == workspace_id) | (Rental.provider_workspace_id == workspace_id)
            ).order_by(Rental.started_at.desc()).limit(max(1, min(limit, 500)))
            return [self._rental_view(s, r, workspace_id, now) for r in s.scalars(q)]

    def _rent(self, s, workspace_id: str, actor: str, offer_id: str, now: datetime,
              release_after_job_id: Optional[str] = None) -> Rental:
        # Row lock on Postgres so two renters cannot both win; SQLite has one writer.
        offer = s.scalar(select(MarketOffer).where(MarketOffer.id == offer_id).with_for_update())
        if offer is None or offer.status != "listed" or not self._may_see(offer, workspace_id):
            raise err("NOT_FOUND", "listing not found", 404)  # a private listing does not reveal that it exists
        if offer.workspace_id == workspace_id:
            raise err("OWN_LISTING", "this is your own node; run jobs on it directly, at no charge", 409)
        available, why = self._offer_availability(s, offer, now)
        if not available:
            raise err("UNAVAILABLE", f"this listing is not available: {why}", 409)
        hourly = offer.price_paise_per_gpu_hour * offer.gpu_count
        need = hourly + fee_for(hourly, self._c.market_fee_bps)
        have = self._spendable(s, workspace_id, now)
        if need > 0 and have < need:  # a free listing needs no credit
            raise ControlServiceError(
                "INSUFFICIENT_FUNDS",
                f"renting needs one hour of credit up front (₹{need / 100:.2f}); available ₹{max(have, 0) / 100:.2f}",
                http_status=402,
            )
        r = Rental(
            id=new_id("rnt"), offer_id=offer.id, node_id=offer.node_id,
            provider_workspace_id=offer.workspace_id, consumer_workspace_id=workspace_id,
            device_ids=list(offer.device_ids or []), gpu_count=offer.gpu_count,
            price_paise_per_gpu_hour=offer.price_paise_per_gpu_hour, fee_bps=self._c.market_fee_bps,
            status="active", started_at=now, billed_through=now, billed_paise=0, fee_paise=0,
            release_after_job_id=release_after_job_id, created_by=actor, end_reason="",
        )
        s.add(r)
        self._audit(s, workspace_id, actor, "rental_started", f"{r.id} offer={offer.id}")
        self._audit(s, offer.workspace_id, "", "rental_started", f"{r.id} offer={offer.id}", offer.node_id)
        s.flush()
        return r

    @_locked
    def rent(self, workspace_id: str, actor: str, offer_id: str) -> dict:
        now = self._now()
        with self._sf() as s:
            r = self._rent(s, workspace_id, actor, offer_id, now)
            s.commit()
            return self._rental_view(s, r, workspace_id, now)

    def _end_rental(self, s, r: Rental, reason: str, now: datetime, actor: str = "",
                    ended_at: Optional[datetime] = None) -> set[str]:
        """Stop the meter, post the final amounts, and stop the tenant's jobs on
        that node. Returns nodes whose desired snapshot must be republished.
        `ended_at` bills up to an earlier moment (a node that went silent)."""
        if r.status != "active":
            return set()
        end = ended_at or now
        # Claim the transition first: only one session may end (and finally meter) a rental.
        claimed = s.execute(
            update(Rental).where(Rental.id == r.id, Rental.status == "active")
            .values(status="ended", ended_at=end, end_reason=reason).execution_options(synchronize_session=False)
        ).rowcount == 1
        s.refresh(r)
        if not claimed:
            return set()
        self._meter(s, r, end, final=True)
        dirty: set[str] = set()
        on_node = select(JobReplica.job_id).where(JobReplica.node_id == r.node_id, JobReplica.released.is_(False))
        jobs = s.scalars(select(Job).where(
            Job.workspace_id == r.consumer_workspace_id, Job.status.notin_(TERMINAL_JOB),
            (Job.rental_id == r.id) | Job.id.in_(on_node),
        ))
        for job in list(jobs):
            if job.id == r.release_after_job_id and reason == "job_finished":
                continue
            status = "cancelled" if reason == "released" else "failed"
            why = f"the rental ended ({reason.replace('_', ' ')})"
            dirty |= self._finish_job(s, job, status, why, now, cascade=False)
        self._audit(s, r.consumer_workspace_id, actor, "rental_ended", f"{r.id} reason={reason}")
        self._audit(s, r.provider_workspace_id, "", "rental_ended", f"{r.id} reason={reason}", r.node_id)
        return dirty

    @_locked
    def release(self, workspace_id: str, actor: str, rental_id: str) -> dict:
        """End a rental. The consumer releases it; the provider can also end it
        (maintenance), which fails the tenant's running jobs."""
        now = self._now()
        with self._sf() as s:
            r = s.get(Rental, rental_id)
            if r is None or workspace_id not in (r.consumer_workspace_id, r.provider_workspace_id):
                raise err("NOT_FOUND", "rental not found", 404)
            if r.status != "active":
                raise err("ENDED", "this rental has already ended", 409)
            reason = "released" if r.consumer_workspace_id == workspace_id else "provider_ended"
            dirty = self._end_rental(s, r, reason, now, actor)
            self._publish(s, dirty)
            s.commit()
            return self._rental_view(s, r, workspace_id, now)

    # ------------------------------------------------------------------- jobs
    def service_token(self, job_id: str) -> str:
        """Bearer token a service is told to require. Derived, never stored."""
        mac = hmac.new(self._secret, f"service-token:{job_id}".encode(), hashlib.sha256).hexdigest()
        return "mst_" + mac[:40]

    def _validate_job(self, body: dict) -> dict:
        kind = body.get("kind", "job")
        if kind not in ("job", "service"):
            raise err("INVALID_FIELD", "kind must be 'job' or 'service'")
        image = body.get("image")
        if not isinstance(image, str) or len(image) > 512 or not IMAGE_REF.match(image):
            raise err(
                "INVALID_IMAGE",
                "image must be pinned by digest, like docker.io/pytorch/pytorch@sha256:<64 hex>. "
                "A tag can change after you submit; a digest cannot.",
            )
        args = validation.check_args(body.get("args", []))
        env = validation.check_env(body.get("env", {}))
        replicas = _int(body, "replicas", 1, 1, 64)
        if kind == "service" and replicas != 1:
            raise err("INVALID_FIELD", "a service runs as one replica")
        # Placeholders expand on the node (`{{peers}}` grows with replicas): bound the result now.
        validation.check_spec_size(args, env, replicas)
        default_name = image.split("@")[0].rsplit("/", 1)[-1]
        return {
            "name": _text(body, "name", 1, 120, default=default_name), "kind": kind, "image": image,
            "args": list(args), "env": dict(env), "replicas": replicas,
            "gpus_per_replica": _int(body, "gpus_per_replica", 1, 0, 16),
            "min_vram_bytes": _int(body, "min_vram_gb", 0, 0, 1024) * 1024**3,
            "timeout_seconds": _int(body, "timeout_seconds", 0, 0, 30 * 86400),
            # Peers must reach each other to rendezvous, and a service is its port.
            "expose_port": bool(body.get("expose_port", kind == "service" or replicas > 1)),
        }

    @_locked
    def submit_job(self, workspace_id: str, actor: str, body: dict, may_rent: bool = False,
                   on_behalf_of: str = "") -> dict:
        """`actor` is who submitted (an email, or `key:<name>` for an API key); `on_behalf_of` is the
        member an API key acts for. Usage and the quota count against that member, recorded on the job."""
        now = self._now()
        person = on_behalf_of or actor
        spec = self._validate_job(apply_template(body))
        offer_id, rental_id = body.get("offer_id"), body.get("rental_id")
        if offer_id and rental_id:
            raise err("INVALID_FIELD", "give offer_id or rental_id, not both")
        with self._sf() as s:
            active = s.scalar(select(func.count()).select_from(Job).where(
                Job.workspace_id == workspace_id, Job.status.notin_(TERMINAL_JOB)
            ))
            if (active or 0) >= MAX_ACTIVE_JOBS:
                raise err("LIMIT", f"a workspace can have at most {MAX_ACTIVE_JOBS} unfinished jobs", 429)
            if not offer_id and not rental_id:
                hit = self._quota_hit(s, workspace_id, person, now)
                if hit:
                    raise err("QUOTA_EXCEEDED", hit, 409)
            job = Job(id=new_id("job"), workspace_id=workspace_id, status="queued", error="", created_by=actor,
                      attributed_to=person, created_at=now, **spec)
            need = spec["replicas"] * spec["gpus_per_replica"]
            if offer_id:
                # Rent for exactly this job: the rental ends when the job does.
                if not may_rent:
                    raise err("FORBIDDEN", "renting requires the admin role or higher", 403)
                offer = s.get(MarketOffer, str(offer_id))
                visible = offer is not None and self._may_see(offer, workspace_id)
                if offer is not None and visible and need > offer.gpu_count:
                    raise err("TOO_LARGE", f"the job needs {need} GPUs; this listing has {offer.gpu_count}", 409)
                job.rental_id = self._rent(s, workspace_id, actor, str(offer_id), now, release_after_job_id=job.id).id
            elif rental_id:
                r = s.get(Rental, str(rental_id))
                rented = s.get(MarketOffer, r.offer_id) if r is not None else None
                if (r is None or r.consumer_workspace_id != workspace_id or r.status != "active"
                        or rented is None or not self._may_see(rented, workspace_id)):
                    raise err("NOT_FOUND", "no active rental with that id", 404)
                if need > r.gpu_count:
                    raise err("TOO_LARGE", f"the job needs {need} GPUs; this rental has {r.gpu_count}", 409)
                job.rental_id = r.id
            s.add(job)
            self._audit(s, workspace_id, actor, "job_submitted", f"{job.id} {job.kind} {job.name}")
            s.flush()
            dirty = self._place(s, job, now)
            self._publish(s, dirty)
            s.commit()
            return self._job_view(s, job, full=True, reveal=True)

    @_locked
    def cancel_job(self, workspace_id: str, actor: str, job_id: str) -> dict:
        now = self._now()
        with self._sf() as s:
            job = self._own_job(s, workspace_id, job_id)
            if job.status in TERMINAL_JOB:
                raise err("FINISHED", f"this job already {job.status}", 409)
            dirty = self._finish_job(s, job, "cancelled", "cancelled by " + (actor or "an operator"), now)
            self._audit(s, workspace_id, actor, "job_cancelled", job.id)
            self._publish(s, dirty)
            s.commit()
            return self._job_view(s, job, full=True, reveal=True)

    @staticmethod
    def _own_job(s, workspace_id: str, job_id: str) -> Job:
        job = s.get(Job, job_id)
        if job is None or job.workspace_id != workspace_id:
            raise err("NOT_FOUND", "job not found", 404)
        found: Job = job
        return found

    @staticmethod
    def _replicas(s, job_id: str) -> list[JobReplica]:
        return list(s.scalars(select(JobReplica).where(JobReplica.job_id == job_id).order_by(JobReplica.rank)))

    def _job_view(self, s, job: Job, full: bool = False, reveal: bool = False) -> dict:
        now = self._now()
        replicas = self._replicas(s, job.id)
        end = _aware(job.finished_at) if job.finished_at else now
        view: dict[str, Any] = {
            "id": job.id, "name": job.name, "kind": job.kind, "image": job.image, "status": job.status,
            "message": job.error, "replicas": job.replicas, "gpus_per_replica": job.gpus_per_replica,
            "min_vram_bytes": job.min_vram_bytes, "timeout_seconds": job.timeout_seconds,
            "rental_id": job.rental_id, "created_by": job.created_by, "created_at": _iso(job.created_at),
            "started_at": _iso(job.started_at), "finished_at": _iso(job.finished_at),
            "seconds": int((end - _aware(job.started_at)).total_seconds()) if job.started_at else 0,
            "phases": [r.phase for r in replicas],
        }
        if full:
            # Environment values can be credentials: operators and up only; viewers see the names.
            env = dict(job.env or {})
            names = {
                n.node_id: n.display_name for n in s.scalars(
                    select(Node).where(Node.node_id.in_([r.node_id for r in replicas] or [""]))
                )
            }
            view.update({
                "args": list(job.args or []), "env": env if reveal else _masked_env(env),
                "expose_port": job.expose_port,
                "placements": [
                    {
                        "rank": r.rank, "node_id": r.node_id, "node_name": names.get(r.node_id, ""),
                        "gpus": len(r.device_ids or []), "phase": r.phase, "exit_code": r.exit_code,
                        "reason": r.reason, "message": r.message, "endpoint": validation.valid_endpoint(r.endpoint),
                        "started_at": _iso(r.started_at), "finished_at": _iso(r.finished_at),
                        "log_tail": r.log_tail,
                    }
                    for r in replicas
                ],
            })
            if job.kind == "service":
                endpoint = next((e for e in (validation.valid_endpoint(r.endpoint) for r in replicas) if e), "")
                view["service"] = {"endpoint": endpoint, "token": self.service_token(job.id) if reveal else None}
        return view

    def list_jobs(self, workspace_id: str, status: Optional[str] = None, limit: int = 100) -> list[dict]:
        with self._sf() as s:
            q = select(Job).where(Job.workspace_id == workspace_id).order_by(Job.created_at.desc())
            if status == "active":
                q = q.where(Job.status.notin_(TERMINAL_JOB))
            elif status:
                q = q.where(Job.status == status)
            return [self._job_view(s, j) for j in s.scalars(q.limit(max(1, min(limit, 500))))]

    def job_detail(self, workspace_id: str, job_id: str, reveal: bool = False) -> dict:
        with self._sf() as s:
            return self._job_view(s, self._own_job(s, workspace_id, job_id), full=True, reveal=reveal)

    # ------------------------------------------------------- usage and quotas
    @staticmethod
    def _key_owners(s, workspace_id: str) -> dict[str, str]:
        """API key name -> the email that created it, for names with exactly one creator."""
        seen: dict[str, set[str]] = {}
        for name, by in s.execute(select(ApiKey.name, ApiKey.created_by).where(ApiKey.workspace_id == workspace_id)):
            seen.setdefault(name, set()).add(by)
        return {n: next(iter(e)) for n, e in seen.items() if len(e) == 1 and next(iter(e))}

    @staticmethod
    def _person(owners: dict[str, str], job: Job) -> str:
        """Who a job counts against: the member recorded at submit. Jobs from before that was recorded
        fall back to the submitter, or the creator of the API key that submitted it."""
        if job.attributed_to:
            return job.attributed_to
        by = job.created_by
        return owners.get(by[4:], by) if by.startswith("key:") else by

    def _gpu_seconds(self, s, workspace_id: str, start: datetime, end: datetime, now: datetime,
                     person: Optional[str] = None) -> Iterable[tuple[str, str, int, int]]:
        """(person, job id, own GPU-seconds, rented GPU-seconds) for each replica that started,
        clipped to [start, end). A replica still running counts up to `now`. Each replica counts
        its own GPUs, so a job with R replicas of G GPUs uses R × G GPU-seconds per second."""
        q = select(JobReplica, Job).join(Job, Job.id == JobReplica.job_id).where(
            Job.workspace_id == workspace_id, JobReplica.started_at.is_not(None), JobReplica.started_at < end,
            (Job.finished_at.is_(None)) | (Job.finished_at >= start),
        )
        owners = self._key_owners(s, workspace_id)
        if person is not None:
            legacy = [person] + [f"key:{n}" for n, e in owners.items() if e == person]
            q = q.where((Job.attributed_to == person) | (Job.attributed_to.is_(None) & Job.created_by.in_(legacy)))
        owned = {n for (n,) in s.execute(select(Node.node_id).where(Node.workspace_id == workspace_id))}
        for r, job in s.execute(q):
            first = max(_aware(r.started_at), start)
            last = min(_aware(r.finished_at or job.finished_at or now), end)
            seconds = max(0, int((last - first).total_seconds())) * job.gpus_per_replica
            rented = bool(job.rental_id) or r.node_id not in owned
            yield self._person(owners, job), job.id, 0 if rented else seconds, seconds if rented else 0

    def _quota_hit(self, s, workspace_id: str, person: str, now: datetime) -> str:
        """Why `person` (a member's email) may not start another job on the workspace's own nodes, or ''."""
        quota = s.scalar(select(Membership.gpu_hour_quota).join(User, User.id == Membership.user_id).where(
            Membership.workspace_id == workspace_id, User.email == person
        ))
        if quota is None:
            ws = s.get(Workspace, workspace_id)
            quota = ws.default_gpu_hour_quota if ws is not None else None
        if quota is None:
            return ""
        label, start, end = month_bounds(now)
        used = sum(own for _by, _job, own, _rented in self._gpu_seconds(s, workspace_id, start, end, now, person))
        if used < quota * 3600:
            return ""
        return (
            f"monthly GPU-hour quota reached: {used / 3600:.2f} of {quota} GPU-hours used in {label}; "
            f"it resets on {end.date().isoformat()} (UTC)"
        )

    def usage(self, workspace_id: str, month: Optional[str] = None, only: Optional[str] = None) -> dict:
        """GPU-hours per person, group and workspace for a UTC month. With `only` (a member's email),
        the report keeps that member's own row and the workspace totals: what a non-admin may see."""
        now = self._now()
        label, start, end = month_bounds(now, month)
        with self._sf() as s:
            ws = s.get(Workspace, workspace_id)
            default = ws.default_gpu_hour_quota if ws is not None else None
            people: dict[str, dict] = {
                u.email: {"email": u.email, "name": u.name, "group": m.group or "", "quota": m.gpu_hour_quota,
                          "own": 0, "rented": 0, "jobs": set()}
                for u, m in s.execute(
                    select(User, Membership).join(Membership, Membership.user_id == User.id)
                    .where(Membership.workspace_id == workspace_id).order_by(User.email)
                )
            }
            for person, job_id, own, rented in self._gpu_seconds(s, workspace_id, start, end, now):
                row = people.setdefault(person, {"email": person, "name": "", "group": "", "quota": None,
                                                 "own": 0, "rented": 0, "jobs": set()})
                row["own"] += own
                row["rented"] += rented
                row["jobs"].add(job_id)
        rows: list[dict] = []
        groups: dict[str, dict] = {}
        for p in sorted(people.values(), key=lambda p: (-p["own"] - p["rented"], p["email"])):
            quota = p["quota"] if p["quota"] is not None else default
            rows.append({
                "email": p["email"], "name": p["name"], "group": p["group"],
                "gpu_hours_own": _hours(p["own"]), "gpu_hours_rented": _hours(p["rented"]), "jobs": len(p["jobs"]),
                "quota_gpu_hours": quota,
                "remaining_gpu_hours": None if quota is None else max(0.0, round(quota - p["own"] / 3600, 2)),
            })
            g = groups.setdefault(p["group"], {"group": p["group"], "own": 0, "rented": 0, "jobs": 0, "members": 0})
            g["own"] += p["own"]
            g["rented"] += p["rented"]
            g["jobs"] += len(p["jobs"])
            g["members"] += 1
        totals = {"own": sum(g["own"] for g in groups.values()), "rented": sum(g["rented"] for g in groups.values()),
                  "jobs": sum(g["jobs"] for g in groups.values())}

        def shape(g: dict) -> dict:
            return {**{k: v for k, v in g.items() if k not in ("own", "rented")},
                    "gpu_hours_own": _hours(g["own"]), "gpu_hours_rented": _hours(g["rented"])}

        return {
            "month": label, "period_start": _iso(start), "period_end": _iso(end),
            "scope": "workspace" if only is None else "self",
            "default_gpu_hour_quota": default,
            "people": rows if only is None else [r for r in rows if r["email"] == only],
            # A group can be one person, so group rows are for admins too.
            "groups": [] if only is not None else [
                shape(g) for g in sorted(groups.values(), key=lambda g: (-g["own"] - g["rented"], g["group"]))
            ],
            "totals": shape(totals),
        }

    def set_member_compute(self, workspace_id: str, actor: str, user_id: str, body: dict) -> dict:
        """Set a member's group and monthly GPU-hour quota. Only the keys present change."""
        with self._sf() as s:
            m = s.get(Membership, (workspace_id, user_id))
            if m is None:
                raise err("NOT_FOUND", "member not found", 404)
            if "group" in body:
                group = body["group"]
                if group is not None and (not isinstance(group, str) or len(group.strip()) > 64):
                    raise err("INVALID_FIELD", "group must be at most 64 characters")
                m.group = (validation.clean_text(group, "group", 0, 64) or None) if isinstance(group, str) else None
            if "gpu_hour_quota" in body:
                m.gpu_hour_quota = _quota(body["gpu_hour_quota"], "gpu_hour_quota")
            self._audit(s, workspace_id, actor, "member_compute_changed",
                        f"{user_id} group={m.group or ''} quota={m.gpu_hour_quota}")
            s.commit()
            return {"user_id": user_id, "group": m.group or "", "gpu_hour_quota": m.gpu_hour_quota}

    def set_compute_settings(self, workspace_id: str, actor: str, body: dict) -> dict:
        if "default_gpu_hour_quota" not in body:
            raise err("INVALID_FIELD", "default_gpu_hour_quota is required (a number of GPU-hours, or null)")
        with self._sf() as s:
            ws = s.get(Workspace, workspace_id)
            assert ws is not None
            ws.default_gpu_hour_quota = _quota(body["default_gpu_hour_quota"], "default_gpu_hour_quota")
            self._audit(s, workspace_id, actor, "compute_settings_changed", f"default={ws.default_gpu_hour_quota}")
            s.commit()
            return {"default_gpu_hour_quota": ws.default_gpu_hour_quota}

    # -------------------------------------------------------------- scheduling
    def _candidates(self, s, job: Job, now: datetime, own_nodes: bool = True) -> list[dict]:
        """Nodes this job may use, with the devices and ports free on each."""
        ws = job.workspace_id
        if job.rental_id:
            pinned = s.get(Rental, job.rental_id)
            rentals = [pinned] if pinned is not None and pinned.status == "active" else []
            own: list[Node] = []
        else:
            rentals = list(s.scalars(select(Rental).where(
                Rental.consumer_workspace_id == ws, Rental.status == "active"
            ).order_by(Rental.started_at)))
            own = list(s.scalars(select(Node).where(Node.workspace_id == ws, Node.revoked.is_(False))
                                 .order_by(Node.created_at))) if own_nodes else []
        out: list[Optional[dict]] = []

        def consider(node: Optional[Node], allowed: Optional[set[str]], blocked: set[str]) -> None:
            if node is None or not self._online(node, now):
                return
            try:
                out.append(self._candidate(s, node, job, allowed, blocked))
            except Exception as e:  # noqa: BLE001 - one bad node never blocks placement on the others
                log.error("compute: skipped node %s as a candidate (%s)", node.node_id, type(e).__name__)

        for node in own:
            # GPUs on the marketplace are for sale: the owner's own new jobs stay off
            # them, so a renter never finds a listed GPU busy.
            blocked: set[str] = set()
            for o in s.scalars(select(MarketOffer).where(MarketOffer.node_id == node.node_id)):
                if o.status == "listed" or self._active_rental_for_offer(s, o.id) is not None:
                    blocked.update(o.device_ids or [])
            consider(node, None, blocked)
        for r in rentals:
            consider(s.get(Node, r.node_id), set(r.device_ids or []), set())
        return [c for c in out if c is not None]

    def _candidate(self, s, node: Node, job: Job, allowed: Optional[set[str]], blocked: set[str]) -> Optional[dict]:
        """One node's free devices and ports for `job`, from its validated capacity."""
        cap = self._capacity(s, node.node_id)
        if not cap.get("jobs_enabled"):
            return None
        held_devices, held_ports = self._held(s, node.node_id)
        alloc: dict[str, int] = cap.get("allocatable_vram_bytes") or {}
        free = cap.get("free_devices")
        pool = list(free) if isinstance(free, list) else [d for d, v in alloc.items() if v > 0]
        inv = {g["device_id"]: g for g in validation.stored_inventory(node.inventory)}
        devices = []
        for d in sorted(pool):
            if d in held_devices or d in blocked or (allowed is not None and d not in allowed):
                continue
            vram = (inv.get(d) or {}).get("memory_total_bytes") or alloc.get(d, 0)
            if vram >= job.min_vram_bytes:
                devices.append(d)
        start, stop = cap.get("job_port_range") or (0, 0)  # validated: at most MAX_JOB_PORTS ports
        ports = [p for p in range(start, stop) if p not in held_ports]
        return {"node": node, "devices": devices, "ports": ports, "host": cap.get("advertise_host") or "",
                "load": self._snapshot_load(s, node.node_id)}

    def _place(self, s, job: Job, now: datetime) -> set[str]:
        """Place every replica or none. Returns the nodes that changed."""
        if job.rental_id:
            r = s.get(Rental, job.rental_id)
            if r is None or r.status != "active":
                return self._finish_job(s, job, "failed", "the rental this job was pinned to has ended", now)
        # A member over their monthly quota keeps their queued job off the workspace's own nodes.
        person = ""
        if not job.rental_id:
            person = job.attributed_to or self._person(self._key_owners(s, job.workspace_id), job)
        over = self._quota_hit(s, job.workspace_id, person, now) if person else ""
        candidates = self._candidates(s, job, now, own_nodes=not over)
        size = validation.spec_size(list(job.args or []), dict(job.env or {}), job.replicas)[0]
        plan: list[tuple[dict, list[str], int]] = []
        for _rank in range(job.replicas):
            for c in candidates:
                if len(c["devices"]) < job.gpus_per_replica or (job.expose_port and not c["ports"]):
                    continue
                if c["load"] + size > validation.MAX_SNAPSHOT_JOB_BYTES:
                    continue  # the node's desired snapshot would grow past its limit
                c["load"] += size
                devices = [c["devices"].pop(0) for _ in range(job.gpus_per_replica)]
                plan.append((c, devices, c["ports"].pop(0) if job.expose_port else 0))
                break
            else:
                gpus = job.replicas * job.gpus_per_replica
                where = "the rented GPUs" if job.rental_id else "this workspace's nodes and rentals"
                if over:
                    job.error = "waiting: monthly GPU-hour quota used"
                    return set()
                job.error = (
                    f"waiting for capacity: needs {gpus} free GPU{'s' if gpus != 1 else ''} on {where}"
                    if gpus else f"waiting for a node that runs jobs in {where}"
                )
                return set()
        for rank, (c, devices, port) in enumerate(plan):
            s.add(JobReplica(
                job_id=job.id, rank=rank, node_id=c["node"].node_id, device_ids=devices, port=port,
                host=c["host"], phase="Pending", reason="", message="", endpoint="", log_tail="", released=False,
                scheduled_at=now,
            ))
        job.status, job.error = "scheduled", ""
        s.flush()
        return {c["node"].node_id for c, _, _ in plan}

    def _finish_job(self, s, job: Job, status: str, message: str, now: datetime, cascade: bool = True) -> set[str]:
        """Move a job to a terminal status. Its replicas stay in their nodes'
        snapshots, marked Stopped, until each node confirms it stopped."""
        if job.status in TERMINAL_JOB:
            return set()
        job.status, job.error, job.finished_at = status, message[:1024], now
        dirty = {r.node_id for r in self._replicas(s, job.id) if not r.released}
        if cascade:
            for r in s.scalars(select(Rental).where(Rental.release_after_job_id == job.id, Rental.status == "active")):
                dirty |= self._end_rental(s, r, "job_finished", now)
        return dirty

    def _advance(self, s, now: datetime) -> set[str]:
        """One pass over unfinished work: detect lost nodes, settle job status,
        enforce deadlines and timeouts, release finished replicas, place what is
        queued. Each job and replica is isolated: one that fails is skipped."""
        dirty: set[str] = set()
        placed = select(Job).where(Job.status.in_(("scheduled", "running"))).order_by(Job.created_at)
        for job in list(s.scalars(placed)):
            with self._isolated(s, "job", job.id):
                dirty |= self._settle(s, job, now)

        # A finished job's replica leaves the snapshot once its node has stopped it
        # (or the node is gone and cannot be holding the GPU for us any more).
        pending_release = select(JobReplica).join(Job, Job.id == JobReplica.job_id).where(
            JobReplica.released.is_(False), Job.status.in_(TERMINAL_JOB)
        )
        for r in list(s.scalars(pending_release)):
            with self._isolated(s, "replica", f"{r.job_id}/{r.rank}"):
                node = s.get(Node, r.node_id)
                if r.phase in TERMINAL_REPLICA or self._lost(node, now):
                    if r.phase not in TERMINAL_REPLICA:
                        r.phase, r.reason, r.finished_at = "Cancelled", "NodeLost", now
                    r.released = True
                    dirty.add(r.node_id)
        s.flush()

        for job in list(s.scalars(select(Job).where(Job.status == "queued").order_by(Job.created_at))):
            with self._isolated(s, "job", job.id):
                dirty |= self._place(s, job, now)
        return dirty

    def _settle(self, s, job: Job, now: datetime) -> set[str]:
        """Fold a placed job's replica phases into its status. Deadlines run on the
        control plane's clock: a node's reported times and phases can end a job
        early (it stopped) but never keep it, or a rental pinned to it, alive."""
        replicas = self._replicas(s, job.id)
        if not replicas:
            return set()
        for r in replicas:
            if r.scheduled_at is None:
                r.scheduled_at = now  # placed before the control plane recorded placement times
            if r.phase in TERMINAL_REPLICA:
                continue
            node = s.get(Node, r.node_id)
            if self._lost(node, now):
                r.phase, r.reason, r.finished_at = "Failed", "NodeLost", now
                r.message = "the node stopped reporting while this replica was running"
        phases = [r.phase for r in replicas]
        failed = next((r for r in replicas if r.phase in ("Failed", "Cancelled")), None)
        if failed is not None:
            detail = failed.message or failed.reason or "stopped"
            return self._finish_job(s, job, "failed", f"replica {failed.rank}: {detail}", now)
        if all(p == "Succeeded" for p in phases):
            if job.kind == "service":
                return self._finish_job(s, job, "failed", "the service exited", now)
            return self._finish_job(s, job, "succeeded", "", now)
        if job.status == "scheduled" and all(p in ("Running", "Succeeded") for p in phases):
            job.status = "running"
        if job.started_at is None and any(p != "Pending" for p in phases):
            # Replica start times are already clamped to when the control plane heard of them.
            job.started_at = min((_aware(r.started_at) for r in replicas if r.started_at), default=now)
        deadline = self._c.job_start_deadline_seconds
        stuck = next((r for r in replicas if r.phase in ("Pending", "Starting")
                      and (now - _aware(r.scheduled_at)).total_seconds() > deadline), None)
        if stuck is not None:
            return self._finish_job(s, job, "failed", f"replica {stuck.rank} did not start within "
                                    f"{deadline // 60} minutes of being placed", now)
        if job.timeout_seconds and job.started_at and (
            (now - _aware(job.started_at)).total_seconds() > job.timeout_seconds
        ):
            return self._finish_job(s, job, "failed", f"timed out after {job.timeout_seconds}s", now)
        return set()

    # -------------------------------------------------------- desired snapshot
    def jobs_for_node(self, s, node_id: str) -> list[dict]:
        """The `jobs` list of a node's desired snapshot (contracts/v1)."""
        out: list[dict] = []
        size = 0
        q = select(JobReplica).where(JobReplica.node_id == node_id, JobReplica.released.is_(False)) \
            .order_by(JobReplica.job_id, JobReplica.rank)
        for r in s.scalars(q):
            job = s.get(Job, r.job_id)
            if job is None:
                continue
            item = self._replica_spec(s, job, r)
            size += len(json.dumps(item))
            if size > validation.MAX_SNAPSHOT_JOB_BYTES:
                # Placement keeps snapshots under the limit; only rows from before it can get here.
                log.error("compute: desired snapshot for node %s is over its size limit; left out %s/%s",
                          node_id, r.job_id, r.rank)
                continue
            out.append(item)
        return out

    def _replica_spec(self, s, job: Job, r: JobReplica) -> dict:
        peers = [validation.host_port(p.host if validation.valid_host(p.host) else "127.0.0.1", p.port)
                 for p in self._replicas(s, job.id) if p.port]
        ctx = {
            "rank": str(r.rank), "replicas": str(job.replicas), "port": str(r.port or ""),
            "peers": ",".join(peers), "master": peers[0] if peers else "", "job_id": job.id,
            "service_token": self.service_token(job.id) if job.kind == "service" else "",
        }
        # One pass: text substituted in is never scanned again for placeholders.
        env = {k: validation.render(v, ctx) for k, v in (job.env or {}).items()}
        env.update({
            "MERIDIAN_JOB_ID": job.id, "MERIDIAN_JOB_NAME": job.name,
            "MERIDIAN_JOB_RANK": ctx["rank"], "MERIDIAN_JOB_REPLICAS": ctx["replicas"],
        })
        if r.port:
            env.update({"MERIDIAN_JOB_PORT": ctx["port"], "MERIDIAN_JOB_PEERS": ctx["peers"],
                        "MERIDIAN_JOB_MASTER": ctx["master"]})
        if job.kind == "service":
            env["MERIDIAN_SERVICE_TOKEN"] = ctx["service_token"]
        return {
            "job_id": job.id, "replica": r.rank, "kind": job.kind, "image": job.image,
            "args": [validation.render(a, ctx) for a in (job.args or [])], "env": env,
            "device_ids": list(r.device_ids or []), "memory_limit_bytes": 0, "port": r.port,
            "timeout_seconds": job.timeout_seconds,
            "desired_phase": "Stopped" if job.status in TERMINAL_JOB else "Running",
        }

    def merge_owner_snapshot(self, s, node_id: str, snapshot: dict) -> dict:
        """The node owner publishes engine assignments; jobs are ours. Whatever
        the owner sent under `jobs` is replaced, so an owner can neither inject
        a job nor drop a tenant's."""
        taken: set[str] = set()
        for o in s.scalars(select(MarketOffer).where(MarketOffer.node_id == node_id, MarketOffer.status == "listed")):
            taken.update(o.device_ids or [])
        for r in s.scalars(select(Rental).where(Rental.node_id == node_id, Rental.status == "active")):
            taken.update(r.device_ids or [])
        for a in snapshot.get("assignments") or []:
            if isinstance(a, dict) and a.get("desired_phase") == "Running":
                ids = (a.get("resources") or {}).get("device_ids") or []
                if taken & set(ids):
                    raise err("DEVICES_LISTED", "these GPUs are listed or rented; pause the listing first", 409)
        merged = {k: v for k, v in snapshot.items() if k != "jobs"}
        jobs = self.jobs_for_node(s, node_id)
        if jobs:
            merged["jobs"] = jobs
        return merged

    def _publish(self, s, node_ids: Iterable[str]) -> None:
        """Publish a new desired generation for each node whose jobs changed."""
        for node_id in sorted(set(node_ids)):
            with self._isolated(s, "snapshot", node_id):
                self._publish_one(s, node_id)

    def _publish_one(self, s, node_id: str) -> None:
        node = s.get(Node, node_id)
        if node is None:
            return
        latest = s.scalar(
            select(DesiredSnapshotRow).where(DesiredSnapshotRow.node_id == node_id)
            .order_by(DesiredSnapshotRow.generation.desc())
        )
        previous = dict(latest.snapshot) if latest is not None else {}
        jobs = self.jobs_for_node(s, node_id)
        if jobs == (previous.get("jobs") or []):
            return
        generation = node.desired_generation + 1
        snapshot: dict[str, Any] = {
            "node_id": node_id, "generation": generation,
            "assignments": previous.get("assignments") or [],
        }
        if jobs:
            snapshot["jobs"] = jobs
        canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
        snapshot["snapshot_hash"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
        s.add(DesiredSnapshotRow(node_id=node_id, generation=generation, snapshot=snapshot, created_at=self._now()))
        node.desired_generation = generation

    # ------------------------------------------------------------ observation
    @_locked
    def on_observation(self, node_id: str) -> None:
        """A node reported. Fold its replica states in, then settle and schedule."""
        now = self._now()
        with self._sf() as s:
            obs = s.get(ObservationRow, node_id)
            reported = validation.stored_observation(obs.observation, node_id).get("jobs", []) if obs else []
            for item in reported:
                with self._isolated(s, "report", f"{node_id}/{item.get('job_id')}"):
                    self._ingest(s, node_id, item, now)
            has_work = s.scalar(select(func.count()).select_from(Job).where(Job.status.notin_(TERMINAL_JOB)))
            has_unreleased = s.scalar(
                select(func.count()).select_from(JobReplica).where(JobReplica.released.is_(False))
            )
            if has_work or has_unreleased:
                self._publish(s, self._advance(s, now))
            s.commit()

    def _ingest(self, s, node_id: str, item: dict, now: datetime) -> None:
        """Fold one validated replica report in. The node's clock is not trusted: a
        time it reports is clamped into [placed, received], and the endpoint is built
        here from the validated host and the port the control plane assigned."""
        r = s.get(JobReplica, (str(item.get("job_id", "")), item.get("replica", -1)))
        if r is None or r.node_id != node_id or r.released:
            return
        phase = item.get("phase")
        if phase not in REPLICA_PHASES or r.phase in TERMINAL_REPLICA:
            return  # a finished replica never moves again
        r.phase = phase
        code = item.get("exit_code")
        r.exit_code = code if isinstance(code, int) and not isinstance(code, bool) else None
        r.reason = str(item.get("reason") or "")[:256]
        r.message = str(item.get("message") or "")[:1024]
        host = r.host if validation.valid_host(r.host) else ""
        r.endpoint = f"http://{validation.host_port(host, r.port)}" if item.get("endpoint") and host and r.port else ""
        r.log_tail = str(item.get("log_tail") or "")[-8192:]
        placed = _aware(r.scheduled_at) if r.scheduled_at else now
        if r.started_at is None and phase != "Pending":
            r.started_at = _clamp(_parse(item.get("started_at")) or now, placed, now)
        if r.finished_at is None and phase in TERMINAL_REPLICA:
            began = _aware(r.started_at) if r.started_at else placed
            r.finished_at = _clamp(_parse(item.get("finished_at")) or now, began, now)

    @_locked
    def tick(self) -> None:
        """Periodic pass: meter rentals, end those out of credit, settle jobs."""
        now = self._now()
        with self._sf() as s:
            dirty: set[str] = set()
            broke: set[str] = set()
            for r in list(s.scalars(select(Rental).where(Rental.status == "active").with_for_update())):
                with self._isolated(s, "rental", r.id):
                    node = s.get(Node, r.node_id)
                    if self._lost(node, now):
                        # Bill only to the node's last lease, never before what is already billed.
                        lease = node.lease_expires_at if node is not None else None
                        end = min(_aware(lease), now) if lease else _aware(r.billed_through)
                        end = max(end, _aware(r.started_at), _aware(r.billed_through))
                        dirty |= self._end_rental(s, r, "node_lost", now, ended_at=end)
                        continue
                    self._meter(s, r, now)
                    if (r.price_paise_per_gpu_hour > 0 and r.consumer_workspace_id not in broke
                            and self._spendable(s, r.consumer_workspace_id, now) <= 0):
                        broke.add(r.consumer_workspace_id)
            for ws in broke:
                for r in list(s.scalars(select(Rental).where(
                    Rental.consumer_workspace_id == ws, Rental.status == "active", Rental.price_paise_per_gpu_hour > 0
                ))):
                    with self._isolated(s, "rental", r.id):
                        dirty |= self._end_rental(s, r, "insufficient_funds", now)
            dirty |= self._advance(s, now)
            self._publish(s, dirty)
            s.commit()

    # --------------------------------------------------------------- redaction
    def redact_node_detail(self, workspace_id: str, detail: dict, reveal: bool = False) -> dict:
        """A node's owner sees that a tenant's job occupies a GPU, never what it
        runs: image, arguments, environment and logs are the tenant's. Within the
        workspace, rendered arguments and environment values (service tokens,
        credentials) are for operators and up (`reveal`); a viewer sees the names."""
        with self._sf() as s:
            own = {j for (j,) in s.execute(select(Job.id).where(Job.workspace_id == workspace_id))}

        def scrub(job: Any, keep: tuple[str, ...]) -> Any:
            if not isinstance(job, dict):
                return job
            if job.get("job_id") not in own:
                return {**{k: job[k] for k in keep if k in job}, "tenant": True}
            if reveal:
                return job
            masked = dict(job)
            if isinstance(job.get("env"), dict):
                masked["env"] = _masked_env(job["env"])
            if isinstance(job.get("args"), list):
                masked["args"] = [MASKED for _ in job["args"]]
            return masked

        observation = detail.get("observation")
        if isinstance(observation, dict) and isinstance(observation.get("jobs"), list):
            detail["observation"] = {
                **observation,
                "jobs": [scrub(j, _OBSERVED_KEEP) for j in observation["jobs"]],
            }
        desired = detail.get("desired")
        if isinstance(desired, dict) and isinstance((desired.get("snapshot") or {}).get("jobs"), list):
            snap = desired["snapshot"]
            detail["desired"] = {
                **desired,
                "snapshot": {
                    **snap,
                    "jobs": [scrub(j, _DESIRED_KEEP) for j in snap["jobs"]],
                },
            }
        return detail

    # ---------------------------------------------------------------- API keys
    def create_api_key(self, workspace_id: str, actor: str, body: dict, on_behalf_of: str = "",
                       caller_role: str = "owner") -> tuple[str, dict]:
        """`created_by` is the member responsible for the key: the signed-in admin, or, when an
        admin key makes a key, the member behind that key. Its jobs count against them.
        A key is never minted above the caller's own (effective) role."""
        name = _text(body, "name", 1, 120)
        role = body.get("role", "operator")
        if role not in API_KEY_ROLES:
            raise err("INVALID_FIELD", f"role must be one of {', '.join(API_KEY_ROLES)}")
        if ROLE_RANK[role] > ROLE_RANK.get(caller_role, -1):
            raise err("FORBIDDEN", "you cannot create a key with a role above your own", 403)
        token = "mck_" + secrets.token_urlsafe(32)
        with self._sf() as s:
            count = s.scalar(select(func.count()).select_from(ApiKey).where(
                ApiKey.workspace_id == workspace_id, ApiKey.revoked.is_(False)
            ))
            if (count or 0) >= 50:
                raise err("LIMIT", "a workspace can have at most 50 active API keys", 400)
            key = ApiKey(
                id=new_id("key"), workspace_id=workspace_id, name=name, role=role, token_hash=sha256_hex(token),
                prefix=token[:10], created_by=on_behalf_of or actor, created_at=self._now(),
            )
            s.add(key)
            self._audit(s, workspace_id, actor, "api_key_created", f"{key.id} role={role}")
            s.commit()
            return token, self._key_view(key)

    @staticmethod
    def _key_view(k: ApiKey) -> dict:
        return {
            "id": k.id, "name": k.name, "role": k.role, "prefix": k.prefix, "created_by": k.created_by,
            "created_at": _iso(k.created_at), "last_used_at": _iso(k.last_used_at), "revoked": k.revoked,
        }

    def list_api_keys(self, workspace_id: str) -> list[dict]:
        with self._sf() as s:
            q = select(ApiKey).where(ApiKey.workspace_id == workspace_id).order_by(ApiKey.created_at.desc())
            return [self._key_view(k) for k in s.scalars(q)]

    def revoke_api_key(self, workspace_id: str, actor: str, key_id: str) -> None:
        with self._sf() as s:
            k = s.get(ApiKey, key_id)
            if k is None or k.workspace_id != workspace_id:
                raise err("NOT_FOUND", "API key not found", 404)
            k.revoked = True
            self._audit(s, workspace_id, actor, "api_key_revoked", k.id)
            s.commit()

    def authenticate_api_key(self, token: str) -> Optional[tuple[str, str, str, str]]:
        """(workspace_id, effective role, label, the member who created it) for a live key, else None.
        A key acts for its creator: its role is capped by the creator's role now, and it stops
        working when the creator leaves the workspace or is disabled."""
        if not token.startswith("mck_"):
            return None
        now = self._now()
        with self._sf() as s:
            k = s.scalar(select(ApiKey).where(ApiKey.token_hash == sha256_hex(token)))
            if k is None or k.revoked:
                return None
            member = s.scalar(select(Membership.role).join(User, User.id == Membership.user_id).where(
                Membership.workspace_id == k.workspace_id, User.email == k.created_by, User.disabled.is_(False),
            ))
            if member is None:
                return None
            role = min(k.role, str(member), key=lambda r: ROLE_RANK.get(r, -1))
            # Coarse last-used stamp: at most one write a minute per key.
            if k.last_used_at is None or (now - _aware(k.last_used_at)).total_seconds() > 60:
                k.last_used_at = now
                s.commit()
            return k.workspace_id, role, f"key:{k.name}", k.created_by

    # ---------------------------------------------------------------- overview
    def overview(self, workspace_id: str) -> dict:
        now = self._now()
        with self._sf() as s:
            by_status = {
                st: int(n) for st, n in s.execute(
                    select(Job.status, func.count()).where(Job.workspace_id == workspace_id).group_by(Job.status)
                )
            }
            renting = list(s.scalars(select(Rental).where(
                Rental.consumer_workspace_id == workspace_id, Rental.status == "active"
            )))
            providing = list(s.scalars(select(Rental).where(
                Rental.provider_workspace_id == workspace_id, Rental.status == "active"
            )))
            members = s.scalar(
                select(func.count()).select_from(Membership).where(Membership.workspace_id == workspace_id)
            )
            return {
                "jobs": by_status,
                "rented_gpus": sum(r.gpu_count for r in renting),
                "gpus_rented_out": sum(r.gpu_count for r in providing),
                "listings": s.scalar(select(func.count()).select_from(MarketOffer).where(
                    MarketOffer.workspace_id == workspace_id, MarketOffer.status == "listed"
                )),
                "balance_paise": self._balance(s, workspace_id),
                "available_paise": self._spendable(s, workspace_id, now),
                "members": members,
                "currency": CURRENCY,
            }


def _parse(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None
