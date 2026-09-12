"""A factual resource projection; allocations and ancestor totals are not charges."""
from consumer.contracts import public_record


class UsageView:
    def __init__(self, core):
        self.core = core

    def summary(self):
        journal, scope = self.core.journal, self.core.scope
        with journal.transaction():
            # Tool receipts become exactly one scheduler work_usage receipt.
            self.core.scheduler.reconcile_usage(scope)
            actual, direct = {}, {}
            receipts = journal.list("work_usage", scope)
            for receipt in receipts:
                work_total = direct.setdefault(receipt.value["work_id"], {})
                for resource, amount in receipt.value["resources"].items():
                    actual[resource] = actual.get(resource, 0) + amount
                    work_total[resource] = work_total.get(resource, 0) + amount
            claims = [public_record(row) for row in journal.list("work_claims", scope) if row.value["state"] == "claimed"]
            held = {}
            for claim in claims:
                for resource, amount in claim["resources"].items():
                    held[resource] = held.get(resource, 0) + amount
            work = journal.list("work", scope)
            return {"actual": actual, "receipt_count": len(receipts), "direct_work_usage": direct,
                    "unresolved_resources": held, "unresolved_claims": [
                        {key: claim[key] for key in ("id", "revision", "work_id", "resources")} for claim in claims],
                    "overruns": [row.id for row in work if row.value["budget_overrun"]],
                    "active_allocations": [{"work_id": row.id, "parent_id": row.value["parent_id"],
                        "resources": row.value["resources"], "state": row.value["state"]} for row in work
                        if row.value["state"] in {"queued", "running", "waiting"}],
                    "cost_estimate": None, "settlement": "not_configured"}
