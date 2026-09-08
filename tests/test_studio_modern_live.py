"""Opt-in actual fee/appeal and replay proof on an owned modern Studio.

Set GL_AGENT_LAB_MODERN_LIVE=1 and GL_AGENT_LAB_MODERN_DATA_DIR to an installed
profile. Never touches the legacy stack and never accesses a public chain.
"""
import json
import os
import time
from pathlib import Path

import pytest

from genlayer_agent_lab.runtime.studio_cohort import StudioFixtureLease
from genlayer_agent_lab.runtime.studio_modern import StudioModernClient
from genlayer_agent_lab.runtime.studio_profiles import modern_profile_status

pytestmark = pytest.mark.skipif(os.getenv("GL_AGENT_LAB_MODERN_LIVE") != "1",
                               reason="Requires explicitly selected owned modern Studio")

SOURCE = b'''# v0.3.0
# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }
import genlayer as gl
from genlayer.types import bigint

class ReplayProof(gl.contract.Contract):
    count: bigint
    def __init__(self):
        self.count = 0
    @gl.public.write
    def increment(self) -> int:
        self.count += 1
        return self.count
    @gl.public.view
    def get_count(self) -> int:
        return self.count
'''


def test_actual_fee_lifecycle_appeal_and_identical_raw_replay():
    data_dir = Path(os.environ["GL_AGENT_LAB_MODERN_DATA_DIR"])
    status = modern_profile_status(data_dir)
    assert status.get("ready") and status.get("runtime_verified")
    assert status.get("network_internal") and status.get("bond_accounting")
    evidence = {"profile": status["profile"], "source_commit": status["source_commit"],
                "image_id": status["image_id"], "public_chain": False}
    with StudioFixtureLease(data_dir, subdirectory="studio-modern"), \
            StudioModernClient(status["endpoint"], timeout=180) as client:
        evidence["funding"] = client.ensure_test_balance()
        deploy = client.prepare_deploy(SOURCE, [])
        evidence["deployment_fee_quote"] = deploy["quote"]
        assert deploy["fee_value"] > 0
        tx_id = client.submit(deploy)
        assert client.envelope(tx_id)["success"] is True
        receipt = client.wait(tx_id, "finalized")
        assert receipt["execution_success"] is True, receipt
        address = receipt["contract_address"]
        assert client.verify_contract(address, SOURCE)["verified"]
        evidence["deployment"] = receipt
        assert client.read(address, "get_count", []) == 0
        before_write_balance = client.balance()
        request = client.prepare_write(address, "increment", [])
        tx_id = client.submit(request)
        assert client.submit(request) == tx_id
        receipt = client.wait(tx_id, "accepted")
        assert receipt["execution_success"] is True, receipt
        assert client.read(address, "get_count", [], finalized=False) == 1
        evidence["write_after_identical_raw_replay"] = receipt
        quote = client.appeal_quote(tx_id)
        assert quote["fee_value"] == quote["bond"] + quote["funding"]
        appeal = client.prepare_appeal(tx_id, quote=quote)
        appeal_id = client.submit(appeal)
        assert appeal_id != tx_id
        assert client.envelope(appeal_id)["success"] is True
        assert client.submit(appeal) == appeal_id
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            current = client.transaction(tx_id)
            if any("Appeal" in item["kind"] and item["kind"].endswith(("Successful", "Failed"))
                   for item in current["rounds"]):
                break
            time.sleep(0.5)
        else:
            pytest.fail("Actual Studio appeal did not complete in the bounded trial")
        final = client.wait(tx_id, "finalized")
        assert final["execution_success"] is True
        assert client.read(address, "get_count", []) == 1
        fees = final["fee_accounting"]
        assert int(fees["appeal_bonds_total"]) == quote["bond"]
        assert int(fees["appeal_funding_total"]) == quote["funding"]
        assert int(fees["total_refunded"]) >= 0
        balance_after_finalization = client.balance()
        assert before_write_balance - balance_after_finalization == (
            int(fees["paid_fee_value"]) + quote["bond"] - int(fees["total_refunded"]))
        evidence.update(appeal_quote=quote, appeal_envelope=client.envelope(appeal_id),
                        final=final, balance_before_write=before_write_balance,
                        balance_after_finalization=balance_after_finalization,
                        duplicate_write_applied_once=True, duplicate_appeal_charged_once=True)
    output = data_dir / "modern-backend-proof.json"
    output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
