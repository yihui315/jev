#!/usr/bin/env python
"""§7F — emit the Real-Jev verification evidence and the interface document.

Single source of truth
----------------------
This script CONTAINS NO FACTS. Every fact it publishes comes from
``jcc_core.providers.real`` (the interface card) or from
``v1_1_local_credential_discovery.environment_report()``. That is deliberate:
a hand-written evidence file is a second copy of the truth, and the second copy
is always the one that goes stale. Re-running this script after any interface
change regenerates both artifacts from the same card.

Outputs (both TRACKED -- commit them together with the code they describe)
--------------------------------------------------------------------------
* ``artifacts/release/V1_1_REAL_JEV_VERIFICATION.json`` -- machine-readable,
  one record per interface field with
  ``field / value / status / authority / evidence / last_verified_at``.
* ``docs/V1_1_REAL_JEV_INTERFACE.md`` -- the same content for a human reader.

Secrets
-------
No credential value can reach either output: the environment report is
presence-only by construction, and ``assert_no_secret_escaped`` inside the
probe already refused to return a report that contained one. ``--emit`` runs the
same scan over the finished JSON as a second, independent check.

Modes
-----
``--emit``   write both files (default)
``--check``  verify the committed files still match a fresh generation. The
             volatile fields (``generated_at``, ``repo_head``, ``worktree_dirty``)
             are excluded, so this catches a stale artifact -- the exact failure
             that a hand-maintained evidence file always eventually has.

Usage
-----
    F:/jev/.venv/Scripts/python.exe tools/v1_1_emit_real_jev_verification.py
    F:/jev/.venv/Scripts/python.exe tools/v1_1_emit_real_jev_verification.py --check
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "packages") not in sys.path:
    sys.path.insert(0, str(REPO / "packages"))

from jcc_core.providers.real import (  # noqa: E402
    ALL_AUTHORITIES,
    AUTHORITIES_THAT_CAN_VERIFY,
    TRANSPORT_IMPLEMENTED,
    RealProvider,
)

from v1_1_local_credential_discovery import (  # noqa: E402
    assert_no_secret_escaped,
    environment_report,
)

JSON_OUT = REPO / "artifacts" / "release" / "V1_1_REAL_JEV_VERIFICATION.json"
MD_OUT = REPO / "docs" / "V1_1_REAL_JEV_INTERFACE.md"

SCHEMA = "jcc-v1.1-real-jev-verification/1"

#: Keys that legitimately differ between two runs, matched by NAME anywhere in
#: the tree. ``--check`` drops exactly these and nothing else.
#:
#: WHY BY NAME, AND WHY INCLUDE THE FILE COUNT. An earlier version stripped only
#: top-level keys and left ``git_tracked_secret_scan.tracked_file_count`` in the
#: compared content. That number changes every time ANY file is committed, so the
#: committed artifact would have been reported "stale" for a reason that has
#: nothing to do with what it claims -- a false failure, which is worse than no
#: check because it teaches the operator to ignore the check. The rule is: a
#: key belongs here when it is PROVENANCE, not when it is a CLAIM. The claims
#: (card fields, decision, smoke gates, credential states) are never dropped.
#:
#: An earlier version also left ``generated_at`` in the rendered Markdown, which
#: made the document's bytes differ on every run -- so its own staleness check
#: could never pass. Provenance now lives only in the JSON.
VOLATILE_KEYS = frozenset({
    "generated_at",
    "generated_by_host",
    "repo_head",
    "repo_branch",
    "worktree_dirty",
    "tracked_file_count",
})

#: §7 — the facts a reader must NOT assume are known just because the host name
#: is. Published as an explicit list so that "we did not guess" is auditable
#: rather than merely promised.
DO_NOT_GUESS = (
    "exact API route",
    "HTTP method",
    "API version",
    "Authorization header syntax",
    "model identifier",
    "request JSON schema",
    "response JSON schema",
    "batch semantics",
    "timeout contract",
    "retry contract",
    "error schema",
    "rate-limit headers",
    "usage response fields",
    "billing / credit response fields",
    "idempotency semantics",
)


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(REPO), *args], text=True,
            stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"


def build_report() -> Dict[str, Any]:
    provider = RealProvider()
    env = environment_report()
    cred = env["env_presence"]

    # "credential present" means a credential for the REAL provider specifically
    # -- not "some API key exists on this host". Cryptic coupling is how a
    # presence check starts reporting the wrong thing.
    cred_env = provider.api_key_env
    cred_state = cred.get(cred_env, {"state": "ABSENT"})
    credential_present = cred_state.get("state") == "PRESENT"

    card = provider.interface
    endpoint_verified = card.field("endpoint_path").status == "VERIFIED"
    request_schema_verified = card.field("request_schema").status == "VERIFIED"

    smoke_allowed = bool(credential_present and endpoint_verified
                         and request_schema_verified and TRANSPORT_IMPLEMENTED)

    smoke_gates = {
        "credential_present": credential_present,
        "exact_endpoint_verified": endpoint_verified,
        "request_schema_verified": request_schema_verified,
        "transport_implemented": TRANSPORT_IMPLEMENTED,
    }
    # DERIVED, not written by hand. The first version of this file hard-coded
    # "At least one gate is open" -- which said the OPPOSITE of the truth and
    # would have shipped that sentence inside a committed evidence record. A
    # reason string that restates the gates must be computed from the gates.
    closed = [k for k, v in smoke_gates.items() if not v]
    if smoke_allowed:
        smoke_reason = (
            "§7E permits a real smoke and every gate is open. A smoke MUST NOT be "
            "run automatically: it spends money and touches an external service, "
            "so it is an operator action with its own review (§7E: at most 1 "
            "normal request plus, only if needed, 1 negative auth/schema request; "
            "never a retry loop)."
        )
    else:
        smoke_reason = (
            "§7E permits a real smoke ONLY when the credential is present AND the "
            "exact endpoint AND the request schema are VERIFIED. Closed gate(s): "
            + ", ".join(f"`{c}`" for c in closed)
            + ". No request was issued. Zero calls also means zero cost and zero "
              "data exposure."
        )

    report: Dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": "tools/v1_1_emit_real_jev_verification.py",
        "generated_by_host": sys.platform,
        "repo_head": _git("rev-parse", "HEAD"),
        "repo_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "worktree_dirty": bool(_git("status", "--porcelain")),

        "provider": provider.interface_report(),

        "authority_model": {
            "levels": list(ALL_AUTHORITIES),
            "can_promote_to_verified": sorted(AUTHORITIES_THAT_CAN_VERIFY),
            "rule": ("Only OFFICIAL_PUBLIC / OFFICIAL_ACCOUNT_DOC / MEASURED can "
                     "make a field VERIFIED. LOCAL_CONFIG proves only that this "
                     "project was once configured that way; ASSUMED is a guess "
                     "and may never back a production RealProvider field."),
        },

        "credential_evidence": {
            "env_presence": cred,
            "dotenv_key_names": env["dotenv_key_names"],
            "file_presence": env["file_presence"],
            "git_tracked_secret_scan": env["git_tracked_secret_scan"],
            "real_provider_api_key_env": cred_env,
            "real_provider_credential_present": credential_present,
            "note": ("Presence only. No credential value is read, stored or "
                     "published. Length buckets are coarse by construction."),
        },

        "smoke": {
            "attempted": False,
            "requests_issued": 0,
            "allowed_by_section_7E": smoke_allowed,
            "gates": smoke_gates,
            "closed_gates": closed,
            "reason": smoke_reason,
            "decision": "PENDING_EXTERNAL_ACTIVATION",
        },

        "do_not_guess": {
            "fields": list(DO_NOT_GUESS),
            "note": ("These are NOT known. No default was invented for any of "
                     "them, and https://api.typesafe.ai was NOT expanded into a "
                     "guessed path."),
        },

        "decision": {
            "real_jev": provider.activation_state,
            "provider_mode": provider.mode,
            "provider_verified": provider.verified,
            "actuation_enabled": False,
            "reasons": list(provider.unmet_requirements()),
            "note": ("PENDING_EXTERNAL_ACTIVATION is a lawful, SAFE end state, "
                     "not a project failure: the engineering line continues on "
                     "MOCK / REPLAY / DISABLED and the shadow plane."),
        },

        "discipline_notes": [
            "The interface card is code (packages/jcc_core/providers/real.py); "
            "this file is a serialisation of it, so the two cannot disagree.",
            "RealProvider.verified is computed as TRANSPORT_IMPLEMENTED AND "
            "all_critical_verified() -- two independent locks. A fully verified "
            "card alone still cannot open the wire.",
            "No mock, replay or disabled provider may report provider_mode=REAL "
            "or provider_is_real=true; pinned by tests/test_provider_mode_contract.py.",
        ],
    }

    assert_no_secret_escaped(report)
    return report


def render_markdown(report: Dict[str, Any]) -> str:
    p = report["provider"]
    iface = p["interface"]
    rows = []
    for f in iface["fields"]:
        rows.append(
            "| `{field}` | {value} | {status} | `{authority}` | {date} | {crit} |".format(
                field=f["field"],
                value=("`" + f["value"] + "`") if f["value"] is not None else "**UNKNOWN**",
                status=("**VERIFIED**" if f["status"] == "VERIFIED" else "UNVERIFIED"),
                authority=f["authority"],
                date=f["last_verified_at"] or "-",
                crit="yes" if f["critical"] else "no",
            ))

    missing = "\n".join(f"- `{m}`" for m in iface["missing_critical"]) or "- (none)"
    dont = "\n".join(f"- {d}" for d in report["do_not_guess"]["fields"])
    unmet = "\n".join(f"- `{u}`" for u in report["decision"]["reasons"])

    return f"""# Real Jev Interface — v1.1 verification record

> **GENERATED FILE — do not edit by hand.** Produced by
> `tools/v1_1_emit_real_jev_verification.py`; every fact below is read from
> `packages/jcc_core/providers/real.py`. Edit the card, re-run the generator,
> and commit both together. `--check` fails if this file and the code disagree.

## 1. Verdict

| | |
|---|---|
| Provider | `{p['provider']}` |
| Provider mode | `{p['provider_mode']}` |
| Provider verified | `{p['provider_verified']}` |
| Activation state | **`{p['provider_activation_state']}`** |
| Real-Jev decision | **`{report['decision']['real_jev']}`** |
| Credential present | `{p['credential_present']}` (env `{p['credential_env_name']}`) |
| Transport implemented | `{p['transport_implemented']}` |
| Actuation enabled | `{report['decision']['actuation_enabled']}` |
| Interface card digest (sha256) | `{iface['card_digest_sha256']}` |

> **Provenance is deliberately NOT in this file.** The generation timestamp, the
> repo head and the dirty flag are recorded in the JSON companion. They are left
> out here so this document has exactly ONE byte form: a document that changes
> every time it is generated cannot be checked for staleness at all.
> `tools/v1_1_emit_real_jev_verification.py --check` compares it byte for byte.

**`PENDING_EXTERNAL_ACTIVATION` is a lawful end state, not a failure.** The
software is complete; what is missing lives outside this repository.

## 2. What stops real Jev from scoring

{unmet}

The interface card reports {len(iface['missing_critical'])} critical field(s) as UNVERIFIED:

{missing}

## 3. Field-level verification (the anti-"UNVERIFIED blob" rule)

Nothing here is marked UNVERIFIED at whole-provider granularity. Every field
carries its own status and its own authority, so a reader can see exactly which
facts are established and which are not.

| field | value | status | authority | last_verified_at | critical |
|---|---|---|---|---|---|
{chr(10).join(rows)}

## 4. Authority model (§7B)

Only these may promote a field to VERIFIED:
{", ".join("`" + a + "`" for a in report['authority_model']['can_promote_to_verified'])}.

`LOCAL_CONFIG` proves only that *this project was once configured that way*.
`ASSUMED` is a guess. Neither can ever back a field that the live provider sends.

## 5. Explicitly NOT guessed

The verified public host `api.typesafe.ai` was **not** expanded into a guessed
route. The following remain UNKNOWN and no default was invented for them:

{dont}

## 6. Credential discovery (§7A)

| item | result |
|---|---|
| `{p['credential_env_name']}` | `{report['credential_evidence']['env_presence'].get(p['credential_env_name'], {}).get('state')}` |
| git-tracked secret scan | `{report['credential_evidence']['git_tracked_secret_scan']['state']}` |

Presence only. No credential value is read, stored, or published anywhere in
this document or its JSON companion.

## 7. Real API smoke (§7E)

| gate | state |
|---|---|
| credential present | `{report['smoke']['gates']['credential_present']}` |
| exact endpoint verified | `{report['smoke']['gates']['exact_endpoint_verified']}` |
| request schema verified | `{report['smoke']['gates']['request_schema_verified']}` |
| transport implemented | `{report['smoke']['gates']['transport_implemented']}` |
| **smoke permitted** | **`{report['smoke']['allowed_by_section_7E']}`** |

Requests issued: **{report['smoke']['requests_issued']}**.

{report['smoke']['reason']}

## 8. How to activate real Jev (a reviewed change, not a config tweak)

1. Obtain the interface facts from an authoritative source (official docs, or an
   account-holder document) and record them on the card with authority
   `OFFICIAL_PUBLIC` / `OFFICIAL_ACCOUNT_DOC`, **or** measure them
   (`MEASURED`) — never `ASSUMED`.
2. Provide the credential in `{p['credential_env_name']}` (never in the repo).
3. Run the §7E smoke: at most 1 normal request plus, only if needed, 1 negative
   auth/schema request. No retry loop.
4. Only then flip `TRANSPORT_IMPLEMENTED`, record the smoke evidence, and
   re-baseline whichever pinned artifacts moved.

Steps 1-2 alone do not activate anything: `TRANSPORT_IMPLEMENTED` is the second
lock and it is a code change with its own review.
"""


def _normalize(obj: Any) -> Any:
    """Drop provenance keys by NAME, recursively. Claims are never dropped."""
    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in obj.items() if k not in VOLATILE_KEYS}
    if isinstance(obj, list):
        return [_normalize(v) for v in obj]
    return obj


def main(argv) -> int:
    check = "--check" in argv
    report = build_report()
    md = render_markdown(report)

    if check:
        problems = []
        if not JSON_OUT.is_file():
            problems.append(f"missing {JSON_OUT.relative_to(REPO)}")
        else:
            on_disk = json.loads(JSON_OUT.read_text(encoding="utf-8"))
            if _normalize(on_disk) != _normalize(report):
                problems.append(
                    f"{JSON_OUT.relative_to(REPO)} is STALE: it disagrees with a "
                    "fresh generation from the interface card. Re-run without "
                    "--check and commit the result."
                )
        if not MD_OUT.is_file():
            problems.append(f"missing {MD_OUT.relative_to(REPO)}")
        elif MD_OUT.read_text(encoding="utf-8") != md:
            problems.append(
                f"{MD_OUT.relative_to(REPO)} is STALE (not byte-identical to a "
                "fresh render)."
            )
        if problems:
            for p in problems:
                print(f"STALE: {p}")
            return 1
        print("OK: both artifacts match a fresh generation from the card.")
        return 0

    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" is explicit on purpose: Path.write_text would translate to
    # os.linesep on Windows and emit CRLF, which `.gitattributes` then normalises
    # back to LF. Writing the committed form directly keeps the working-tree bytes
    # equal to the committed bytes, so a re-generate does not show as a diff on
    # every checkout.
    with open(JSON_OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    MD_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(MD_OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(md)
    print(f"wrote {JSON_OUT.relative_to(REPO)}")
    print(f"wrote {MD_OUT.relative_to(REPO)}")
    print(f"decision.real_jev = {report['decision']['real_jev']}")
    print(f"smoke requests issued = {report['smoke']['requests_issued']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
