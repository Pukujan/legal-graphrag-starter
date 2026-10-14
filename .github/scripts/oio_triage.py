#!/usr/bin/env python3
"""Pure OIO issue-log classification plus GitHub Actions label application."""

from __future__ import annotations

import json
import hashlib
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from oio_installer import InstallError, SOURCE_ROOT, _schema_validate, priority_key, validate_project_ontology


ORIGIN_CLASS = {
    "human-direct": 1,
    "human-via-agent": 2,
    "agent-proposed": 3,
    "agent-initiated": 4,
}
ISSUE_TYPES = {"observational", "operational"}
AUTH_LABELS = {"human-direct": "oio-auth:human-direct", "human-via-agent": "oio-auth:human-via-agent"}
ATTESTATION_MARKER = "<!-- oio-account-attestation:v1 "


def priority_label(path: str) -> str:
    """Keep the full project path in the issue body; bound GitHub label length."""
    label = f"priority:{path}"
    if len(label) <= 50:
        return label
    import hashlib

    return f"priority:ref-{hashlib.sha256(path.encode()).hexdigest()[:16]}"


def review_lane(impact: str, likelihood: str, exposure: str, confidence: str, relation: str) -> str:
    """Assign an advisory review lane; it never authorizes or blocks a release."""
    production_evidence = exposure in {"active-production", "intermittent-production"} and likelihood in {"observed", "likely"} and confidence in {"confirmed", "supported"}
    if impact in {"critical", "high"} and production_evidence:
        return "production-risk-review"
    if impact in {"critical", "high"} and relation == "blocks" and confidence in {"confirmed", "supported"}:
        return "release-risk-review"
    if relation in {"blocks", "affects"} and confidence in {"confirmed", "supported"}:
        return "product-outcome-review"
    if impact in {"low", "informational"} or confidence in {"uncertain", "unknown"}:
        return "bounded-or-uncertain-review"
    return "normal-review"


def _sections(body: str) -> dict[str, str]:
    matches = list(re.finditer(r"^###\s+([^\n]+)\s*$", body, re.MULTILINE))
    result: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        label = " ".join(match.group(1).strip().lower().split())
        result[label] = body[match.end() : end].strip()
    return result


def classify_issue(issue: dict, root: Path, repository_id: str | None = None, event: dict | None = None) -> dict:
    """Return deterministic labels and classification warnings for one issue."""
    default_path = root / ".oio/ontology/default.json"
    project_path = root / ".oio/ontology/project.json"
    if not default_path.exists():
        default_path = root / "ontology/default.json"
        project_path = root / "ontology/oio-project.json"
    default = json.loads(default_path.read_text(encoding="utf-8"))
    project = json.loads(project_path.read_text(encoding="utf-8"))
    validate_project_ontology(project, root / (".oio/schemas/v1/project-ontology.schema.json" if (root / ".oio/schemas/v1/project-ontology.schema.json").exists() else "schemas/v1/project-ontology.schema.json"), default)
    if repository_id and project["project_id"].casefold() != repository_id.casefold():
        raise InstallError(f"project ontology repository {project['project_id']!r} does not match event target {repository_id!r}")

    body = issue.get("body") or ""
    sections = _sections(body)
    norm = {key.replace(" ", ""): value for key, value in sections.items()}
    already_labeled = {label.get("name", "") for label in issue.get("labels", [])}
    if "observational-issue" not in already_labeled and "issuetype" not in norm:
        return {"skip": True, "labels": [], "warnings": ["not-an-oio-issue-log"]}
    labels: set[str] = {"observational-issue", "proposal"}
    warnings: list[str] = []

    issue_type = norm.get("issuetype", "").strip().lower()
    if issue_type and issue_type not in ISSUE_TYPES:
        return {"skip": True, "labels": [], "warnings": ["out-of-scope-non-observational-or-operational-record"]}
    if issue_type in ISSUE_TYPES:
        labels.add(f"type:{issue_type}")
    else:
        labels.add("needs-issue-type")
        warnings.append("missing-or-invalid-issue-type")

    origin = norm.get("filerorigin(whoinitiatedthisfiling)", "").strip().lower()
    if origin not in ORIGIN_CLASS:
        labels.add("needs-filer-stamp")
        warnings.append("missing-or-invalid-filer-origin")
        origin = ""
    identity = norm.get("session,authorizationevidence,andinstructionreference", "")
    content_author = norm.get("contentauthoraccountorruntimeidentity", "").strip()
    author_identity_valid = bool(re.fullmatch(r"(?:human:[1-9][0-9]*|agent:[a-z][a-z0-9-]*/[A-Za-z0-9._-]+)", content_author))
    auth_state = norm.get("filingauthorizationevidence", "").strip().lower()
    if len(identity.strip()) < 8 or not author_identity_valid or auth_state not in {"verified", "claimed", "unknown"}:
        labels.add("needs-filer-stamp")
        warnings.append("missing-identity-or-authorization-evidence")
    else:
        # A reporter-controlled body field cannot establish authorization.
        labels.add(f"authorization:{'claimed' if auth_state == 'verified' else auth_state}")
    actor = issue.get("user") or {}
    actor_id = str(actor.get("id", ""))
    actor_login = str(actor.get("login", ""))
    session_match = re.search(r"(?im)^\s*[-*]?\s*session(?: reference)?\s*:\s*(.+?)\s*$", identity)
    session_value = session_match.group(1).strip() if session_match else ""
    account = next((entry for entry in project["account_authority"] if entry["github_user_id"] == actor_id), None)
    account_verified = bool(account)
    if account:
        labels.add(f"account:{account['authority']}")
        if account["login"].casefold() != actor_login.casefold():
            labels.add("needs-account-review")
            warnings.append("account-id-login-mismatch")
            account_verified = False
    elif origin in {"human-direct", "human-via-agent"}:
        labels.add("account:unrecognized")
        warnings.append("human-account-not-in-owner-policy")
    else:
        labels.add("account:agent-or-unmapped")

    effective_origin = origin
    event = event or {}
    attestation = event.get("oio_attestation") or {}
    attestation_label = attestation.get("label", "")
    attestor = attestation.get("actor") or {}
    attestor_id = str(attestor.get("id", ""))
    attestor_login = str(attestor.get("login", ""))
    attestor_account = next((entry for entry in project["account_authority"] if entry["github_user_id"] == attestor_id and entry["login"].casefold() == attestor_login.casefold()), None)
    trusted_attestation = bool(attestor_account and attestor_account["authority"] in {"owner", "approved-collaborator"})
    attests_human_origin = False
    if origin == "human-direct" and not account_verified:
        effective_origin = "agent-proposed"
        labels.add("needs-account-review")
        warnings.append("human-origin-account-not-owner-verified")
    if origin == "human-direct":
        if attestation_label != "oio-auth:human-direct" or not trusted_attestation or attestor_id != actor_id:
            effective_origin = "agent-proposed"
            labels.add("needs-human-verification")
            warnings.append("human-direct-origin-needs-owner-attestation")
        else:
            attests_human_origin = True
    if origin == "human-via-agent":
        director_id = norm.get("directinghumangithubaccountid", "").strip()
        if attestation_label != "oio-auth:human-via-agent" or not trusted_attestation or not director_id or director_id != attestor_id:
            effective_origin = "agent-proposed"
            labels.add("needs-human-verification")
            warnings.append("human-via-agent-origin-needs-direct-github-attestation")
        else:
            # Account authority for this class comes from the separately authenticated directing human.
            account = attestor_account
            labels.difference_update({label for label in labels if label.startswith("account:")})
            labels.add(f"account:{attestor_account['authority']}")
            account_verified = True
            attests_human_origin = True
    if attests_human_origin:
        labels.discard("authorization:claimed")
        labels.add("authorization:verified-account-attestation")
        labels.add(AUTH_LABELS[origin])
    if origin:
        labels.add(f"filer:{origin}")
        labels.add(f"queue:class-{ORIGIN_CLASS[effective_origin]}")
        authority_rank = {"owner": "01", "approved-collaborator": "02", "community": "03"}.get(account["authority"], "99") if account_verified else "99"
        labels.add(f"queue:class-{ORIGIN_CLASS[effective_origin]}:account-{authority_rank}")

    path = norm.get("projectprioritypath", "").strip()
    concept_id = norm.get("projectpriorityconceptid", "").strip()
    if not path:
        # Preserve legacy integer ratings as strings during migration.
        old_rating = next((value for key, value in norm.items() if key.startswith("priorityrating")), "").strip()
        if re.fullmatch(r"(?:[1-9]|[1-9][0-9]|100)", old_rating):
            path = old_rating
            legacy = next((entry for entry in project["priorities"] if entry["path"] == path), None)
            concept_id = legacy["concept_id"] if legacy else ""
    try:
        priority_key(path)
    except ValueError:
        path = ""
    priority = next((entry for entry in project["priorities"] if entry["path"] == path), None)
    if not priority or priority["concept_id"] != concept_id:
        labels.add("needs-priority-definition")
        warnings.append("priority-path-or-concept-not-defined")
    else:
        labels.add(priority_label(path))

    assessment_fields = ["affectedapp,adopter,users,orsystems", "observedconsequenceandworkaround", "exposureduration", "recoveryevidence", "riskiftheobservationremainsunresolved", "riskintroducedbyaproposedchange", "namedproductoutcomeandcostofdelay", "costofdelay", "smallestusefulproductslice"]
    missing_assessment = [field for field in assessment_fields if not norm.get(field, "").strip()]
    impact_band = norm.get("impactifunresolved", "").strip().lower()
    likelihood = norm.get("likelihood", "").strip().lower()
    exposure = norm.get("exposure", "").strip().lower()
    recoverability = norm.get("recoverability", "").strip().lower()
    confidence = norm.get("evidenceconfidence", "").strip().lower()
    if not missing_assessment and impact_band in {"critical", "high", "moderate", "low", "informational"} and likelihood in {"observed", "likely", "plausible", "unlikely", "unknown"} and exposure in {"active-production", "intermittent-production", "limited-test-or-preview", "not-currently-exposed", "unknown"} and recoverability in {"automatic-recovery", "documented-recovery", "manual-recovery", "difficult-or-irreversible", "unknown"} and confidence in {"confirmed", "supported", "uncertain", "unknown"}:
        labels.add(f"impact:{impact_band}")
        labels.add(f"likelihood:{likelihood}")
        labels.add(f"exposure:{exposure}")
        labels.add(f"recoverability:{recoverability}")
        labels.add(f"evidence:{confidence}")
    else:
        labels.add("needs-impact-evidence")
        warnings.append("missing-or-invalid-impact-risk-evidence")

    relation = norm.get("productorreleaserelevance", "").strip().lower()
    if relation in {"blocks", "affects", "supports", "unrelated", "unknown"}:
        if norm.get("namedproductoutcomeandcostofdelay", "").strip() and norm.get("costofdelay", "").strip() and norm.get("smallestusefulproductslice", "").strip():
            labels.add(f"release:{relation}")
        else:
            labels.add("needs-release-relevance")
            warnings.append("missing-named-outcome-delay-cost-or-slice")
    else:
        labels.add("needs-release-relevance")
        warnings.append("missing-release-relevance")

    if "needs-impact-evidence" not in labels and "needs-release-relevance" not in labels:
        lane = review_lane(impact_band, likelihood, exposure, confidence, relation)
        labels.add("review:" + lane)
    else:
        lane = "bounded-or-uncertain-review"

    version_text = norm.get("ontologyversionsused", "").strip()
    expected_versions = f"oio={default['version']}; project={project['version']}"
    version_match = re.fullmatch(r"oio=(\d+\.\d+\.\d+);\s*project=(\d+\.\d+\.\d+)", version_text)
    recorded_versions = version_match.groups() if version_match else ("0.0.0", "0.0.0")
    destination = norm.get("destinationrepository", "").strip()
    required_repository = repository_id or project["project_id"]
    record = {
        "schema_version": "oio.issue-log-record.v1",
        "issue_type": issue_type,
        "project": {"id": project["project_id"], "repository": required_repository},
        "ontology": {"default_version": recorded_versions[0], "project_version": recorded_versions[1]},
        "filer": {
            "origin": origin if origin in ORIGIN_CLASS else "agent-proposed",
            "authenticated_actor": f"{actor_id}:{actor_login}" if actor_id else "unknown",
            "content_author": content_author or "unknown",
            "created_at": issue.get("created_at", ""),
            "directing_human": norm.get("directinghumangithubaccountid", "").strip() if origin == "human-via-agent" else None,
            "authorization_state": "verified-account-attestation" if attests_human_origin else ("unknown" if auth_state == "unknown" else "claimed"),
            "authorization_evidence": (f"GitHub account {attestor_id}:{attestor_login}; event {attestation.get('event_id')}; body-sha256 {attestation.get('body_sha256')}" if attests_human_origin else (identity.strip() or None)),
            "session_reference": session_value or None,
            "destination_repository": destination or required_repository,
        },
        "priority": {
            "status": "resolved" if priority else "unresolved",
            "path": path if priority else None,
            "concept_id": concept_id if priority else None,
        },
        "impact": {
            "affected": [norm.get("affectedapp,adopter,users,orsystems", "unknown")],
            "consequence": norm.get("observedconsequenceandworkaround", "unknown"),
            "breadth": norm.get("affectedapp,adopter,users,orsystems", "unknown"),
            "workaround": norm.get("observedconsequenceandworkaround", "unknown"),
            "impact_band": impact_band if impact_band in {"critical", "high", "moderate", "low", "informational"} else "informational",
            "likelihood": likelihood if likelihood in {"observed", "likely", "plausible", "unlikely", "unknown"} else "unknown",
            "exposure": exposure if exposure in {"active-production", "intermittent-production", "limited-test-or-preview", "not-currently-exposed", "unknown"} else "unknown",
            "recoverability": recoverability if recoverability in {"automatic-recovery", "documented-recovery", "manual-recovery", "difficult-or-irreversible", "unknown"} else "unknown",
            "evidence_confidence": confidence if confidence in {"confirmed", "supported", "uncertain", "unknown"} else "unknown",
            "unresolved_risk": norm.get("riskiftheobservationremainsunresolved", "unknown"),
            "change_risk": norm.get("riskintroducedbyaproposedchange", "unknown"),
            "exposure_duration": norm.get("exposureduration", "unknown"),
            "recovery": norm.get("recoveryevidence", "unknown"),
        },
        "release_relevance": {
            "relation": relation if relation in {"blocks", "affects", "supports", "unrelated", "unknown"} else "unknown",
            "product_outcome": norm.get("namedproductoutcomeandcostofdelay", "unknown"),
            "delay_cost": norm.get("costofdelay", "unknown"),
            "smallest_useful_slice": norm.get("smallestusefulproductslice", "unknown"),
        },
    }
    try:
        _schema_validate(record, root / (".oio/schemas/v1/issue-log-record.schema.json" if (root / ".oio/schemas/v1/issue-log-record.schema.json").exists() else "schemas/v1/issue-log-record.schema.json"))
    except InstallError as exc:
        labels.add("needs-record-schema")
        warnings.append(f"issue-record-schema-invalid:{exc}")
    if destination.casefold() != required_repository.casefold() or version_text != expected_versions:
        labels.add("needs-record-context")
        warnings.append("destination-or-ontology-version-does-not-match-installed-target")

    authority_rank = {"owner": 1, "approved-collaborator": 2, "community": 3}.get(account["authority"], 99) if account_verified and account else 99
    lane_rank = {"production-risk-review": 1, "release-risk-review": 2, "product-outcome-review": 3, "normal-review": 4, "bounded-or-uncertain-review": 5}.get(lane, 9)
    priority_parts = list(priority_key(path)) if priority else [101]
    queue_order_key = [ORIGIN_CLASS[effective_origin] if effective_origin in ORIGIN_CLASS else 99, authority_rank, priority_parts, lane_rank]

    labels.add(f"ontology:oio-{default['version']}")
    labels.add(f"ontology:project-{project['version']}")
    return {"labels": sorted(labels), "warnings": warnings, "origin": origin, "actor_id": actor_id, "actor_login": actor_login, "record": record, "queue_order_key": queue_order_key}


def _api(token: str, method: str, url: str, payload: dict | None = None) -> tuple[int, object]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")


def _get_pages(token: str, url: str) -> list[dict]:
    """Read all pages from a GitHub collection endpoint."""
    result: list[dict] = []
    page = 1
    while True:
        separator = "&" if "?" in url else "?"
        status, response = _api(token, "GET", f"{url}{separator}per_page=100&page={page}")
        if status != 200 or not isinstance(response, list):
            raise RuntimeError(f"could not read GitHub collection: HTTP {status}: {response}")
        result.extend(response)
        if len(response) < 100:
            return result
        page += 1


def _account(project: dict, actor: dict) -> dict | None:
    actor_id = str(actor.get("id", ""))
    actor_login = str(actor.get("login", ""))
    return next((entry for entry in project["account_authority"] if entry["github_user_id"] == actor_id and entry["login"].casefold() == actor_login.casefold()), None)


def _resolve_attestation(issue: dict, event: dict, project: dict, token: str, repository: str) -> dict | None:
    """Verify an account attestation against current labels, timeline, and exact issue body.

    A bot comment persists the event and body digest across workflow runs. Editing the
    issue invalidates that attestation; an authorized account must reapply its label.
    """
    origin_sections = {"".join(k.lower().split()): v for k, v in _sections(issue.get("body") or "").items()}
    origin = " ".join(origin_sections.get("filerorigin(whoinitiatedthisfiling)", "").strip().lower().split())
    expected_label = AUTH_LABELS.get(origin)
    if not expected_label:
        return None
    issue_labels = {item.get("name") for item in issue.get("labels", [])}
    if expected_label not in issue_labels:
        return None

    api_root = f"https://api.github.com/repos/{repository}/issues/{issue['number']}"
    history = _get_pages(token, f"{api_root}/events")
    latest = None
    for item in history:
        if item.get("label", {}).get("name") == expected_label and item.get("event") in {"labeled", "unlabeled"}:
            def event_order(record: dict) -> tuple[str, int]:
                try:
                    event_id = int(record.get("id", 0))
                except (TypeError, ValueError):
                    event_id = 0
                return str(record.get("created_at", "")), event_id
            if latest is None or event_order(item) > event_order(latest):
                latest = item
    if not latest or latest.get("event") != "labeled":
        return None

    actor = latest.get("actor") or {}
    mapped = _account(project, actor)
    if not mapped or mapped["authority"] not in {"owner", "approved-collaborator"}:
        return None
    opener_id = str((issue.get("user") or {}).get("id", ""))
    if origin == "human-direct" and str(actor.get("id", "")) != opener_id:
        return None
    if origin == "human-via-agent":
        sections = {"".join(k.lower().split()): v for k, v in _sections(issue.get("body") or "").items()}
        if sections.get("directinghumangithubaccountid", "").strip() != str(actor.get("id", "")):
            return None

    digest = hashlib.sha256((issue.get("body") or "").encode("utf-8")).hexdigest()
    event_id = str(latest.get("id", ""))
    if not event_id:
        return None
    marker = f"{ATTESTATION_MARKER}issue={issue['number']} event={event_id} label={expected_label} body_sha256={digest} -->"
    comments = _get_pages(token, f"{api_root}/comments")
    durable = any(
        marker in (comment.get("body") or "")
        and (comment.get("user") or {}).get("login") == "github-actions[bot]"
        for comment in comments
    )
    fresh_label_event = (
        event.get("action") == "labeled"
        and (event.get("label") or {}).get("name") == expected_label
        and hashlib.sha256(((event.get("issue") or {}).get("body") or "").encode("utf-8")).hexdigest() == digest
    )
    if not durable and fresh_label_event:
        comment_body = marker + "\nOIO records that this authorized GitHub account applied the matching origin attestation to this exact issue body. This verifies account action only; shared credentials do not identify who typed a prompt. Editing the issue invalidates this attestation."
        status, response = _api(token, "POST", f"{api_root}/comments", {"body": comment_body})
        if status not in {201, 200}:
            raise RuntimeError(f"could not persist account attestation: HTTP {status}: {response}")
        durable = True
    if not durable:
        return None
    return {"label": expected_label, "actor": actor, "event_id": event_id, "body_sha256": digest}


def _current_issue(event: dict, token: str, repository: str) -> dict:
    api_root = f"https://api.github.com/repos/{repository}/issues/{event['issue']['number']}"
    status, issue = _api(token, "GET", api_root)
    if status != 200 or not isinstance(issue, dict):
        raise RuntimeError(f"could not read current issue: HTTP {status}: {issue}")
    return issue


def apply_github_labels(decision: dict, event: dict, token: str, repository: str) -> None:
    issue = event["issue"]
    api_root = f"https://api.github.com/repos/{repository}"
    status, current = _api(token, "GET", f"{api_root}/issues/{issue['number']}/labels?per_page=100")
    if status != 200:
        raise RuntimeError(f"could not read existing labels: HTTP {status}: {current}")
    current_names = {item["name"] for item in current}
    managed_prefixes = ("type:", "filer:", "queue:", "account:", "authorization:", "priority:", "impact:", "likelihood:", "exposure:", "recoverability:", "evidence:", "release:", "ontology:", "review:")
    managed_exact = {"needs-issue-type", "needs-filer-stamp", "needs-account-review", "needs-human-verification", "needs-priority-definition", "needs-impact-evidence", "needs-release-relevance", "needs-record-schema", "needs-record-context"}
    stale = [name for name in current_names if name.startswith(managed_prefixes) or name in managed_exact]
    stale.extend(name for name in current_names if name.startswith("oio-auth:") and name not in decision["labels"])
    if stale:
        for name in stale:
            status, response = _api(token, "DELETE", f"{api_root}/issues/{issue['number']}/labels/{quote(name, safe='')}")
            if status not in {200, 204}:
                raise RuntimeError(f"could not remove stale OIO label {name!r}: HTTP {status}: {response}")
    labels = decision["labels"]
    current_names -= set(stale)
    missing = [name for name in labels if name not in current_names]
    for name in missing:
        status, response = _api(token, "POST", f"{api_root}/labels", {"name": name, "color": "5319e7", "description": f"OIO classification: {name}"})
        if status not in {201, 422}:
            raise RuntimeError(f"could not ensure OIO label {name!r}: HTTP {status}: {response}")
    status, response = _api(token, "POST", f"{api_root}/issues/{issue['number']}/labels", {"labels": labels})
    if status not in {200, 201}:
        raise RuntimeError(f"could not apply OIO labels: HTTP {status}: {response}")


def main() -> int:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    token = os.environ.get("GITHUB_TOKEN")
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not event_path or not token or not repository:
        print("GITHUB_EVENT_PATH, GITHUB_TOKEN, and GITHUB_REPOSITORY are required", file=sys.stderr)
        return 2
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        repository_id = event.get("repository", {}).get("full_name")
        issue = _current_issue(event, token, repository)
        default_path = Path.cwd() / ".oio/ontology/default.json"
        project_path = Path.cwd() / ".oio/ontology/project.json"
        if not default_path.exists():
            project_path = Path.cwd() / "ontology/oio-project.json"
        project = json.loads(project_path.read_text(encoding="utf-8"))
        event["oio_attestation"] = _resolve_attestation(issue, event, project, token, repository)
        event["issue"] = issue
        decision = classify_issue(issue, Path.cwd(), repository_id, event)
        print(json.dumps(decision, indent=2))
        if not decision.get("skip"):
            apply_github_labels(decision, event, token, repository)
    except (KeyError, OSError, ValueError, json.JSONDecodeError, InstallError, RuntimeError) as exc:
        print(f"OIO triage failed closed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
