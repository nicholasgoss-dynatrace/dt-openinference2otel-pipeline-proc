#!/usr/bin/env python3
"""
Sets up a Dynatrace OpenPipeline processor that converts OpenInference
attributes to OpenTelemetry gen_ai.* attributes for AI Observability.

Usage:
    export DT_ENDPOINT=https://<your-env>.live.dynatrace.com
    export DT_API_TOKEN=<token-with-settings-write-scope>
    python setup_pipeline.py

    # Dry-run (print the API payload without applying it):
    python setup_pipeline.py --dry-run

    # Tear down the pipeline and routing rule:
    python setup_pipeline.py --delete

Required token scopes:
    - settings.read
    - settings.write
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests
import yaml
from dotenv import load_dotenv

load_dotenv()

PIPELINE_SCHEMA_ID = "builtin:openpipeline.spans.pipelines"
ROUTING_SCHEMA_ID = "builtin:openpipeline.spans.routing"
CONFIG_FILE = Path(__file__).parent / "openpipeline_config.yaml"

ROUTING_MATCHER = "isNotNull(openinference.span.kind)"
ROUTING_DESCRIPTION = "OpenInference (Arize Phoenix) → gen_ai.* normalization"


def load_config() -> dict[str, Any]:
    with CONFIG_FILE.open() as f:
        return yaml.safe_load(f)


def build_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Api-Token {token}",
        "Content-Type": "application/json",
    }


def get_settings_objects(
    endpoint: str, token: str, schema_id: str
) -> list[dict[str, Any]]:
    url = f"{endpoint}/api/v2/settings/objects"
    params = {"schemaIds": schema_id, "pageSize": 100}
    resp = requests.get(url, headers=build_headers(token), params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("items", [])


def find_existing_object(
    objects: list[dict[str, Any]], external_id: str
) -> dict[str, Any] | None:
    for obj in objects:
        if obj.get("value", {}).get("customId") == external_id:
            return obj
    return None


def build_pipeline_payload(config: dict[str, Any]) -> dict[str, Any]:
    """Convert the YAML config into the Settings 2.0 value object."""
    processors = []
    for proc in config["processing"]["processors"]:
        p: dict[str, Any] = {
            "id": proc["id"],
            "enabled": proc["enabled"],
            "matcher": proc["matcher"],
            "type": proc["type"],
            "description": proc.get("description", ""),
        }
        ptype = proc["type"]
        if ptype == "dql":
            p["dql"] = {"script": proc["dql"]["script"]}
        elif ptype == "fieldsAdd":
            p["fieldsAdd"] = proc["fieldsAdd"]
        elif ptype == "fieldsRename":
            p["fieldsRename"] = proc["fieldsRename"]
        elif ptype == "fieldsRemove":
            p["fieldsRemove"] = proc["fieldsRemove"]
        processors.append(p)

    return {
        "customId": config["customId"],
        "displayName": config["displayName"],
        "processing": {"processors": processors},
    }


def upsert_pipeline(
    endpoint: str, token: str, pipeline_value: dict[str, Any], dry_run: bool
) -> str | None:
    """Create or update the pipeline. Returns the objectId (or None for dry-run)."""
    payload = [
        {
            "schemaId": PIPELINE_SCHEMA_ID,
            "scope": "environment",
            "value": pipeline_value,
        }
    ]

    if dry_run:
        print("=== Pipeline payload (dry-run) ===")
        print(json.dumps(payload, indent=2))
        return None

    objects = get_settings_objects(endpoint, token, PIPELINE_SCHEMA_ID)
    existing = find_existing_object(objects, pipeline_value["customId"])

    if existing:
        obj_id = existing["objectId"]
        url = f"{endpoint}/api/v2/settings/objects/{obj_id}"
        resp = requests.put(
            url,
            headers=build_headers(token),
            json={"value": pipeline_value},
            timeout=30,
        )
        resp.raise_for_status()
        print(f"Updated pipeline '{pipeline_value['customId']}' (objectId={obj_id})")
    else:
        url = f"{endpoint}/api/v2/settings/objects"
        resp = requests.post(
            url, headers=build_headers(token), json=payload, timeout=30
        )
        resp.raise_for_status()
        created = resp.json()
        obj_id = created[0].get("objectId", "unknown") if created else "unknown"
        print(f"Created pipeline '{pipeline_value['customId']}' (objectId={obj_id})")

    return obj_id


def upsert_routing_rule(
    endpoint: str, token: str, pipeline_obj_id: str, dry_run: bool
) -> None:
    """Add or update the routing entry that directs OpenInference spans to the pipeline.

    The routing schema stores a single object per environment whose value contains
    a `routingEntries` list. Each entry identifies the target pipeline by its
    Settings objectId (not its customId).
    """
    new_entry = {
        "enabled": True,
        "pipelineType": "custom",
        "pipelineId": pipeline_obj_id,
        "matcher": ROUTING_MATCHER,
        "description": ROUTING_DESCRIPTION,
    }

    if dry_run:
        print("=== Routing entry payload (dry-run) ===")
        print(json.dumps(new_entry, indent=2))
        return

    try:
        objects = get_settings_objects(endpoint, token, ROUTING_SCHEMA_ID)
    except requests.HTTPError as exc:
        print(
            f"Warning: could not fetch routing configuration ({exc.response.status_code}). "
            "Add the routing rule manually in the Dynatrace UI:\n"
            f"  OpenPipeline → Traces/Spans → Routing → Add rule\n"
            f"  Matcher : {ROUTING_MATCHER}\n"
            f"  Pipeline: (select '{ROUTING_DESCRIPTION}')"
        )
        return

    if not objects:
        print(
            "Warning: no routing configuration object found. "
            "Add the routing rule manually in the Dynatrace UI."
        )
        return

    routing_obj = objects[0]
    obj_id = routing_obj["objectId"]
    value = routing_obj.get("value", {})
    entries: list[dict[str, Any]] = value.get("routingEntries", [])

    existing_idx = next(
        (i for i, e in enumerate(entries) if e.get("pipelineId") == pipeline_obj_id),
        None,
    )
    if existing_idx is not None:
        entries[existing_idx] = new_entry
        action = "Updated"
    else:
        entries.insert(0, new_entry)
        action = "Added"

    value["routingEntries"] = entries

    url = f"{endpoint}/api/v2/settings/objects/{obj_id}"
    resp = requests.put(url, headers=build_headers(token), json={"value": value}, timeout=30)
    resp.raise_for_status()
    print(f"{action} routing entry → pipeline objectId '{pipeline_obj_id}'")


def delete_pipeline(endpoint: str, token: str, pipeline_id: str) -> None:
    objects = get_settings_objects(endpoint, token, PIPELINE_SCHEMA_ID)
    existing = find_existing_object(objects, pipeline_id)
    if not existing:
        print(f"Pipeline '{pipeline_id}' not found — nothing to delete.")
        return
    obj_id = existing["objectId"]
    url = f"{endpoint}/api/v2/settings/objects/{obj_id}"
    resp = requests.delete(url, headers=build_headers(token), timeout=30)
    resp.raise_for_status()
    print(f"Deleted pipeline '{pipeline_id}' (objectId={obj_id})")


def delete_routing_rule(endpoint: str, token: str, pipeline_obj_id: str) -> None:
    try:
        objects = get_settings_objects(endpoint, token, ROUTING_SCHEMA_ID)
    except requests.HTTPError:
        print("Could not fetch routing configuration — skipping routing cleanup.")
        return

    if not objects:
        print("No routing configuration found — nothing to delete.")
        return

    routing_obj = objects[0]
    obj_id = routing_obj["objectId"]
    value = routing_obj.get("value", {})
    entries: list[dict[str, Any]] = value.get("routingEntries", [])
    original_count = len(entries)
    value["routingEntries"] = [
        e for e in entries if e.get("pipelineId") != pipeline_obj_id
    ]

    if len(value["routingEntries"]) == original_count:
        print("Routing entry not found — nothing to delete.")
        return

    url = f"{endpoint}/api/v2/settings/objects/{obj_id}"
    resp = requests.put(url, headers=build_headers(token), json={"value": value}, timeout=30)
    resp.raise_for_status()
    print("Removed routing entry.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set up Dynatrace OpenPipeline for OpenInference → gen_ai.* mapping"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the API payloads without applying them",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Remove the pipeline and routing rule instead of creating them",
    )
    parser.add_argument(
        "--skip-routing",
        action="store_true",
        help="Only manage the pipeline; skip the routing rule",
    )
    args = parser.parse_args()

    endpoint = os.environ.get("DT_ENDPOINT", "").rstrip("/")
    token = os.environ.get("DT_API_TOKEN", "")

    if not endpoint or not token:
        print(
            "Error: DT_ENDPOINT and DT_API_TOKEN environment variables must be set.",
            file=sys.stderr,
        )
        sys.exit(1)

    config = load_config()
    pipeline_id = config["customId"]

    if args.delete:
        objects = get_settings_objects(endpoint, token, PIPELINE_SCHEMA_ID)
        existing = find_existing_object(objects, pipeline_id)
        pipeline_obj_id = existing["objectId"] if existing else None
        delete_pipeline(endpoint, token, pipeline_id)
        if not args.skip_routing and pipeline_obj_id:
            delete_routing_rule(endpoint, token, pipeline_obj_id)
        return

    pipeline_value = build_pipeline_payload(config)
    pipeline_obj_id = upsert_pipeline(endpoint, token, pipeline_value, dry_run=args.dry_run)

    if not args.skip_routing:
        upsert_routing_rule(endpoint, token, pipeline_obj_id or "", dry_run=args.dry_run)

    if not args.dry_run:
        print("Done. OpenInference spans will now be normalized to gen_ai.* attributes.")


if __name__ == "__main__":
    main()
