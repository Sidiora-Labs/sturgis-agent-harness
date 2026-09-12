"""Separate proactive policy decisions and bounded context selection."""

from datetime import datetime
import hashlib
import math
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agent.awareness import required, text, timestamp
from agent.protocol import CoreError, canonical_json, new_id
from agent.state import Journal


KINDS = {"instructions", "situation", "user_input", "conversation", "tool_observation", "memory", "runtime_status"}
WORKLOADS = {"idle", "background", "interactive", "saturated"}


class Attention:
    def __init__(self, journal: Journal):
        self.journal = journal

    def evaluate_initiative(self, scope, candidate_id, *, policy=None, workload="idle", now=None):
        policy = {} if policy is None else policy
        allowed = {"mode", "quiet_hours", "notification_limit", "minimum_score", "duplicate_seconds"}
        if not isinstance(policy, dict) or set(policy) - allowed or not isinstance(workload, str) or workload not in WORKLOADS:
            raise CoreError("invalid_policy", "Unknown attention policy or workload")
        mode, limit, threshold, duplicate_seconds = policy.get("mode", "suggest"), policy.get("notification_limit", 20), policy.get("minimum_score", .5), policy.get("duplicate_seconds", 3600)
        if not isinstance(mode, str) or mode not in {"disabled", "remember", "suggest", "bounded"} or type(limit) is not int or limit < 0 or type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 <= threshold <= 1 or type(duplicate_seconds) not in (int, float) or not math.isfinite(duplicate_seconds) or duplicate_seconds < 0:
            raise CoreError("invalid_policy", "Attention mode or limits are invalid")
        now = timestamp(now)
        quiet, zone = False, ZoneInfo("UTC")
        hours = policy.get("quiet_hours")
        if hours is not None:
            if not isinstance(hours, dict) or set(hours) != {"timezone", "start_minute", "end_minute"} or any(type(hours[key]) is not int or not 0 <= hours[key] < 1440 for key in ("start_minute", "end_minute")):
                raise CoreError("invalid_policy", "Quiet hours require timezone and valid minute boundaries")
            try:
                zone = ZoneInfo(hours["timezone"])
            except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
                raise CoreError("invalid_policy", "Quiet-hours timezone is unavailable") from exc
            local = datetime.fromtimestamp(now, zone)
            minute, start, end = local.hour * 60 + local.minute, hours["start_minute"], hours["end_minute"]
            quiet = start <= minute < end if start < end else minute >= start or minute < end
        with self.journal.transaction():
            candidate = required(self.journal, "initiative", candidate_id, scope)
            day = datetime.fromtimestamp(now, zone).date().isoformat()
            previous_action = self.journal.get("attention_recent", candidate.value["action_key"], scope)
            duplicate = previous_action is not None and previous_action.value["candidate_id"] != candidate_id and 0 <= now - previous_action.value["at"] < duplicate_seconds
            expired = now >= candidate.value["expires_at"]
            key = hashlib.sha256(canonical_json([candidate_id, policy, workload, day, quiet, duplicate, expired]).encode()).hexdigest()
            prior = self.journal.get("attention_proactive", key, scope)
            if prior:
                return prior
            budget_key = f"{candidate.value['channel']}:{day}"
            budget = self.journal.get("attention_notifications", budget_key, scope)
            used = budget.value["count"] if budget else 0
            value = candidate.value["score"]
            if expired or duplicate:
                decision, reason = "ignore", "expired" if expired else "duplicate"
            elif mode in {"disabled", "remember"}:
                decision, reason = "remember", "autonomy policy"
            elif quiet or used >= limit:
                decision, reason = "batch", "quiet hours" if quiet else "notification budget"
            elif workload in {"interactive", "saturated"}:
                decision, reason = "schedule", "foreground workload"
            elif value < threshold / 2:
                decision, reason = "remember", "low expected benefit"
            elif value < threshold:
                decision, reason = "notify", "useful observation"
            elif mode == "suggest":
                decision, reason = "ask", "suggestion requires a decision"
            else:
                decision, reason = "start_bounded_work", "eligible within configured autonomy"
            body = {"kind": "proactive", "candidate_id": candidate_id, "candidate_revision": candidate.revision, "decision": decision, "eligible": decision == "start_bounded_work", "score": value, "reason": reason, "policy": policy, "workload": workload, "at": now}
            row = self.journal.put("attention_proactive", key, scope, body)
            if decision in {"ask", "notify"}:
                self.journal.put("attention_notifications", budget_key, scope, {"count": used + 1}, budget.revision if budget else 0)
            if decision in {"ask", "notify", "start_bounded_work"}:
                self.journal.put("attention_recent", candidate.value["action_key"], scope, {"candidate_id": candidate_id, "at": now}, previous_action.revision if previous_action else 0)
            self.journal.append(scope, "attention.decided", {"id": row.id, **body}, source="attention")
            return row

    def select_context(self, scope, items, *, budget, required_ids=(), work_id):
        text(work_id, "work_id", 256)
        if type(budget) is not int or budget < 0 or not isinstance(items, list) or len(items) > 4096:
            raise CoreError("invalid_context", "Context candidates and budget must be bounded")
        if not isinstance(required_ids, (list, tuple, set)) or any(not isinstance(value, str) for value in required_ids):
            raise CoreError("invalid_context", "Required context identities must be strings")
        ids, mandatory = set(), set(required_ids)
        for item in items:
            if not isinstance(item, dict) or not {"id", "kind", "tokens"} <= set(item):
                raise CoreError("invalid_context", "Context item requires identity, kind and measured token count")
            text(item["id"], "source_id", 512)
            priority = item.get("priority", 0)
            if item["id"] in ids or not isinstance(item["kind"], str) or item["kind"] not in KINDS or type(item["tokens"]) is not int or item["tokens"] < 0 or type(priority) not in (float, int) or not math.isfinite(priority) or type(item.get("required", False)) is not bool:
                raise CoreError("invalid_context", "Context item has invalid or duplicate fields")
            canonical_json(item)
            ids.add(item["id"])
            if item.get("required") or item["kind"] in {"instructions", "user_input"}:
                mandatory.add(item["id"])
        if not mandatory <= ids:
            raise CoreError("missing_context", "Required source is absent", ids=sorted(mandatory - ids))
        selected, deferred, used = [], [], 0
        ordered = sorted(enumerate(items), key=lambda pair: (pair[1]["id"] not in mandatory, -pair[1].get("priority", 0), pair[0]))
        for _, item in ordered:
            if used + item["tokens"] <= budget:
                selected.append({"id": item["id"], "kind": item["kind"], "tokens": item["tokens"], "range": item.get("range"), "source_revision": item.get("source_revision")})
                used += item["tokens"]
            else:
                deferred.append({"id": item["id"], "reason": "token_budget", "required": item["id"] in mandatory})
        body = {"kind": "context", "work_id": work_id, "selected": selected, "deferred": deferred, "budget": budget, "used_tokens": used, "can_invoke": not any(item["required"] for item in deferred)}
        with self.journal.transaction():
            row = self.journal.put("attention_context", new_id("selection"), scope, body)
            self.journal.append(scope, "attention.selected", {"id": row.id, **body}, source="attention")
            return row
