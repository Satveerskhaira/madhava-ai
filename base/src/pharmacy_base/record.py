"""Claim record adapter: the only pharmacy-specific code the kernel needs.

Tells the kernel which claim fields hold the status, reject codes, amounts and
dates, and derives the fields rules read but the claims API does not return
directly (days since enrollment, days since the last fill).
"""

from __future__ import annotations

from typing import Any

from rulegraph.tools.toolbox import RecordView, as_date

# amount component -> claim pricing field
PRICE_FIELDS = {"copay": "copay", "coinsurance": "coinsurance", "deductible": "deductibleApplied"}


class ClaimAdapter:
    def view(self, raw: dict[str, Any]) -> RecordView:
        claim = dict(raw)
        dos = as_date(claim["dos"])
        member = claim.setdefault("member", {})
        if member.get("enrollmentDate"):
            member["daysSinceEnrollment"] = (dos - as_date(member["enrollmentDate"])).days
        history = claim.setdefault("history", {})
        if history.get("lastFillDate"):
            history["daysSinceLastFill"] = (dos - as_date(history["lastFillDate"])).days
        pricing = claim.get("pricing", {})
        status = claim.get("status", "unknown")
        return RecordView(
            record=claim,
            status=status,
            adverse=status == "rejected",
            outcome_tokens=[f"reject:{c}" for c in claim.get("rejectCodes", [])],
            amount_tokens=[f"price:{comp}" for comp, fld in PRICE_FIELDS.items() if (pricing.get(fld) or 0) > 0],
            reference_id=claim["planId"],
            service_date=dos,
            decision_date=as_date(claim.get("adjudicatedAt", claim["dos"])),
            summary={"drug_name": claim.get("drug", {}).get("name"), "channel": claim.get("pharmacy", {}).get("channel")},
        )
