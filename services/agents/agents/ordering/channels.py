"""Connectors to the systems that take a purchase order: the tools the channel sub-agents use.

One connector per kind of target system:

  sap_erp          the hospital network's ERP (SAP S/4HANA purchase order API). The ERP
                   creates the purchase order and sends it on to the supplier.
  edi_x12          an EDI 850 purchase order straight to the supplier (they answer with a
                   997 acknowledgement).
  supplier_portal  the supplier's own ordering API.
  email            the order agent's drafted request, for a person to send. It cannot be
                   rejected, so the orchestrator agent keeps it as the last resort.

The agents decide (subagents.py, orchestrator.py); a connector only does what an agent
asks through a tool, in code:

  prepare(ctx)          build the payload from the approved order. An ERP or EDI payload
                        has to be exact, so it is built from the approved order, never
                        typed by a model.
  check(payload, ctx)   the payload says what was approved (quantity, date, reference,
                        required fields). A connector refuses to submit a payload that fails.
  submit(payload, ctx)  hand it to the target system. In "simulate" mode (the MVP) the
                        target's answer is simulated from the supplier's settings and
                        nothing leaves the machine; "live" needs a real connector.

Adding a target system (Oracle, Workday, Ariba, a GPO portal) is one more class here, a
sub-agent prompt in subagents.py, and one line in mock/orders/supplier_channels.json.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime


class LiveConnectorMissing(Exception):
    """Live mode was asked for but this channel has no real connector configured."""


@dataclass
class OrderContext:
    """Everything a channel needs about one approved order."""

    order_id: str
    plan_id: str
    din: str
    drug: str
    site_id: str
    site: str
    supplier_id: str
    supplier: str
    qty: int
    expected_date: date
    now: datetime
    settings: dict = field(default_factory=dict)      # this supplier's entry in supplier_channels.json
    erp: dict = field(default_factory=dict)           # the hospital network's ERP settings
    request: dict | None = None                       # the order agent's drafted request (subject, body)


@dataclass
class Response:
    status: str                  # accepted | rejected | ready_to_send
    external_ref: str | None
    detail: dict
    note: str


def _ref(prefix: str, order_id: str, digits: int = 8) -> str:
    """A stable, realistic-looking reference for a simulated answer (same order, same reference)."""

    return prefix + str(int(hashlib.sha1(order_id.encode()).hexdigest(), 16) % 10**digits).zfill(digits)


def _simulated(ctx: OrderContext, accepted: Response, channel: "Channel") -> Response:
    """The answer the supplier's settings script for this channel: accept (default) or reject with a reason."""

    script = (ctx.settings.get("simulate") or {}).get(channel.name) or {}
    if script.get("answer") == "reject":
        reason = script.get("reason") or "The target system refused the order."
        return Response("rejected", None, {"error": reason}, f"{channel.target} rejected it: {reason}")
    return accepted


class Channel:
    name = "channel"
    target = "Target system"
    confirms = ""          # how and how fast the target confirms an order (the orchestrator agent weighs it)

    def prepare(self, ctx: OrderContext) -> dict:
        raise NotImplementedError

    def check(self, payload: dict, ctx: OrderContext) -> list[str]:
        raise NotImplementedError

    def submit(self, payload: dict, ctx: OrderContext, mode: str) -> Response:
        if mode != "simulate":
            raise LiveConnectorMissing(f"No live connector for {self.target}; configure one before using live mode.")
        return self._simulate(payload, ctx)

    def _simulate(self, payload: dict, ctx: OrderContext) -> Response:
        raise NotImplementedError


class SapErpChannel(Channel):
    """The hospital network's ERP: SAP S/4HANA purchase order API (A_PurchaseOrder)."""

    name = "sap_erp"
    target = "Hospital ERP: SAP S/4HANA"
    confirms = "SAP returns a purchase order number at once and transmits it to the supplier in the next batch (about an hour)."

    def prepare(self, ctx: OrderContext) -> dict:
        erp = ctx.erp
        return {
            "CompanyCode": erp.get("company_code"),
            "PurchaseOrderType": "NB",
            "Supplier": ctx.settings.get("vendor"),
            "PurchasingOrganization": erp.get("purchasing_org"),
            "PurchasingGroup": erp.get("purchasing_group"),
            "DocumentCurrency": erp.get("currency", "CAD"),
            "YY1_PlatformReference": ctx.order_id,
            "to_PurchaseOrderItem": [{
                "PurchaseOrderItem": "10",
                "Material": f"DIN-{ctx.din}",
                "PurchaseOrderItemText": ctx.drug,
                "Plant": (erp.get("plants") or {}).get(ctx.site_id),
                "OrderQuantity": str(ctx.qty),
                "PurchaseOrderQuantityUnit": "EA",
                "to_ScheduleLine": [{"ScheduleLineDeliveryDate": ctx.expected_date.isoformat(),
                                     "ScheduleLineOrderQuantity": str(ctx.qty)}],
            }],
        }

    def check(self, payload: dict, ctx: OrderContext) -> list[str]:
        problems = [f"SAP field {name} is missing." for name in
                    ("CompanyCode", "Supplier", "PurchasingOrganization", "PurchasingGroup") if not payload.get(name)]
        [item] = payload["to_PurchaseOrderItem"]
        if not item.get("Plant"):
            problems.append(f"No SAP plant is set up for hospital {ctx.site_id}.")
        if item["OrderQuantity"] != str(ctx.qty) or item["to_ScheduleLine"][0]["ScheduleLineOrderQuantity"] != str(ctx.qty):
            problems.append(f"The SAP quantity does not match the approved {ctx.qty} units.")
        if item["to_ScheduleLine"][0]["ScheduleLineDeliveryDate"] != ctx.expected_date.isoformat():
            problems.append("The SAP delivery date does not match the approved date.")
        if payload.get("YY1_PlatformReference") != ctx.order_id:
            problems.append("The SAP order does not carry the platform's order reference.")
        return problems

    def _simulate(self, payload: dict, ctx: OrderContext) -> Response:
        number = _ref("45", ctx.order_id)
        return _simulated(ctx, Response("accepted", number, {"PurchaseOrder": number, "PurchasingProcessingStatus": "05"},
                                        f"SAP created purchase order {number} and sends it to {ctx.supplier}."), self)


class EdiChannel(Channel):
    """EDI X12 850 purchase order, sent to the supplier's EDI mailbox."""

    name = "edi_x12"
    target = "Supplier EDI (X12 850)"
    confirms = "The supplier's EDI system acknowledges with a 997 within minutes; an 855 order acknowledgement follows."

    def prepare(self, ctx: OrderContext) -> dict:
        s = ctx.settings
        day, at = ctx.now.strftime("%y%m%d"), ctx.now.strftime("%H%M")
        control = _ref("", ctx.order_id, 9)
        segments = [
            f"ISA*00*          *00*          *ZZ*{s.get('sender_id', 'HOSPITAL'):<15}*ZZ*{s.get('partner_id', ''):<15}*{day}*{at}*U*00401*{control}*0*P*>",
            f"GS*PO*{s.get('sender_id', 'HOSPITAL')}*{s.get('partner_id', '')}*{ctx.now:%Y%m%d}*{at}*1*X*004010",
            "ST*850*0001",
            f"BEG*00*SA*{ctx.order_id}**{ctx.now:%Y%m%d}",
            f"DTM*002*{ctx.expected_date:%Y%m%d}",
            f"N1*ST*{ctx.site}*92*{ctx.site_id}",
            f"PO1*1*{ctx.qty}*EA***N4*{ctx.din}*PD*{ctx.drug}",
            "CTT*1",
            "SE*7*0001",  # segments from ST to SE
            "GE*1*1",
            f"IEA*1*{control}",
        ]
        return {"standard": "X12 004010 850", "partner_id": s.get("partner_id"), "segments": segments}

    def check(self, payload: dict, ctx: OrderContext) -> list[str]:
        seg = {line.split("*")[0]: line.split("*") for line in payload["segments"]}
        problems = []
        if not payload.get("partner_id"):
            problems.append("No EDI trading partner ID is set up for this supplier.")
        if seg["BEG"][3] != ctx.order_id:
            problems.append("The EDI order does not carry the platform's order reference.")
        if seg["PO1"][2] != str(ctx.qty):
            problems.append(f"The EDI quantity does not match the approved {ctx.qty} units.")
        if seg["DTM"][2] != f"{ctx.expected_date:%Y%m%d}":
            problems.append("The EDI delivery date does not match the approved date.")
        return problems

    def _simulate(self, payload: dict, ctx: OrderContext) -> Response:
        return _simulated(ctx, Response("accepted", f"997-{_ref('', ctx.order_id, 6)}", {"ack": "997", "AK9": "A"},
                                        f"{ctx.supplier} acknowledged the EDI 850 (997 accepted)."), self)


class SupplierPortalChannel(Channel):
    """The supplier's own ordering API."""

    name = "supplier_portal"
    target = "Supplier portal API"
    confirms = "The portal answers at once with the supplier's order number, or refuses with a reason."

    def prepare(self, ctx: OrderContext) -> dict:
        return {"account": ctx.settings.get("account"), "customer_reference": ctx.order_id,
                "ship_to": {"site_id": ctx.site_id, "name": ctx.site},
                "lines": [{"din": ctx.din, "description": ctx.drug, "quantity": ctx.qty,
                           "need_by": ctx.expected_date.isoformat()}]}

    def check(self, payload: dict, ctx: OrderContext) -> list[str]:
        [line] = payload["lines"]
        problems = []
        if not payload.get("account"):
            problems.append("No portal account is set up for this supplier.")
        if line["quantity"] != ctx.qty:
            problems.append(f"The portal quantity does not match the approved {ctx.qty} units.")
        if line["need_by"] != ctx.expected_date.isoformat() or payload["customer_reference"] != ctx.order_id:
            problems.append("The portal order does not match the approved date and reference.")
        return problems

    def _simulate(self, payload: dict, ctx: OrderContext) -> Response:
        ref = _ref("WEB-", ctx.order_id, 7)
        return _simulated(ctx, Response("accepted", ref, {"orderId": ref, "status": "RECEIVED"},
                                        f"{ctx.supplier}'s portal accepted order {ref}."), self)


class EmailChannel(Channel):
    """The order agent's drafted request, for procurement to send. Never sent from here."""

    name = "email"
    target = "Email for procurement to send"
    confirms = "Nothing is confirmed until procurement sends it and the supplier replies; slowest, and needs a person."

    def prepare(self, ctx: OrderContext) -> dict:
        request = ctx.request or {}
        return {"to": ctx.supplier, "subject": request.get("subject") or f"Purchase order {ctx.order_id}",
                "body": request.get("message_draft") or
                        f"Please supply {ctx.qty} units of {ctx.drug} for {ctx.site} by {ctx.expected_date:%B %d}. "
                        f"Reference {ctx.order_id}."}

    def check(self, payload: dict, ctx: OrderContext) -> list[str]:
        from services.agents.agents.order_agent import RequestDraft, WatchedOrder, check_request

        order = WatchedOrder(order_id=ctx.order_id, plan_id=ctx.plan_id, din=ctx.din, site_id=ctx.site_id,
                             supplier_id=ctx.supplier_id, supplier=ctx.supplier, qty=ctx.qty, status="approved",
                             expected_date=ctx.expected_date, approved_at=ctx.now, approver_role="procurement")
        return check_request(RequestDraft(subject=payload["subject"], body=payload["body"]), order)

    def submit(self, payload: dict, ctx: OrderContext, mode: str) -> Response:
        # Same in both modes: a person sends the email and records the order as placed.
        return Response("ready_to_send", None, {}, f"Email to {ctx.supplier} is ready for procurement to send.")


CHANNELS: dict[str, Channel] = {c.name: c for c in (SapErpChannel(), EdiChannel(), SupplierPortalChannel(), EmailChannel())}
