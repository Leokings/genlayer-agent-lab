import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from genlayer_agent_lab.client import LabClient
from genlayer_agent_lab.project_bindings import load_project_binding, normalize_project_arguments
from genlayer_agent_lab.project_scenarios import (
    approve_project_scenario,
    prediction_scenario_template,
    scenario_digest,
    validate_project_scenario,
)
from genlayer_agent_lab.project_wire import (
    INTEGER_ENCODING,
    SAFE_INTEGER,
    decode_project_wire,
    encode_project_wire,
)

ROOT = Path(__file__).parents[1]
BIG = 2**256 - 1


@pytest.mark.parametrize("value", [0, SAFE_INTEGER, -SAFE_INTEGER, SAFE_INTEGER + 1,
                                  -SAFE_INTEGER - 1, BIG, -BIG])
def test_integer_boundaries_round_trip_exactly(value):
    wire = encode_project_wire({"fee": value})
    if abs(value) > SAFE_INTEGER:
        assert wire["fee"] == {"$lab_integer": str(value)}
    else:
        assert type(wire["fee"]) is int
    assert decode_project_wire(json.loads(json.dumps(wire))) == {"fee": value}


def test_domain_strings_booleans_and_reserved_literal_objects_preserved():
    original = {"numeric_string": str(BIG), "boolean": True, "fraction": 1.25,
                "literal_integer_tag": {"$lab_integer": "123"},
                "literal_object_tag": {"$lab_object": {"$lab_integer": "456"}},
                "literal_malformed_tag": {"$lab_object": "opaque domain value"},
                "mixed_tag": {"$lab_integer": "2", "ordinary": "3"},
                "array": [BIG, {"$lab_integer": str(BIG)}]}
    result = decode_project_wire(encode_project_wire(original))
    assert result == original
    assert type(result["numeric_string"]) is str and type(result["boolean"]) is bool


@pytest.mark.parametrize("number", ["01", "-0", "+1", " 1", "1.0", "1e20", "0xff", "9" * 257, True, 42])
def test_malformed_or_unbounded_explicit_integer_tags_rejected(number):
    with pytest.raises(ValueError, match="canonical bounded"):
        decode_project_wire({"$lab_integer": number})


def test_cycles_depth_and_malformed_escape_rejected():
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ValueError, match="cycle"):
        encode_project_wire(cycle)
    deep = None
    for _ in range(50):
        deep = [deep]
    with pytest.raises(ValueError, match="structural"):
        decode_project_wire(deep)
    with pytest.raises(ValueError, match="escaped"):
        decode_project_wire({"$lab_object": {"ordinary": 3}})


def test_fee_policy_and_review_digest_survive_browser_wire_round_trip():
    draft = prediction_scenario_template(load_project_binding(ROOT / "examples/projects/prediction/project.yaml"))
    draft["policy"].update(max_fee=BIG, max_total_fee=BIG)
    draft["policy"]["operations"]["record"]["max_fee"] = BIG - 1
    original_digest = scenario_digest(draft)
    reviewed = approve_project_scenario(draft, reviewer="developer", expected_sha256=original_digest)
    restored = decode_project_wire(json.loads(json.dumps(encode_project_wire(reviewed))))
    assert validate_project_scenario(restored) == reviewed
    assert scenario_digest(restored) == original_digest
    assert type(restored["policy"]["max_fee"]) is int


def test_argument_decimals_normalize_only_in_declared_integer_slots():
    snapshot = load_project_binding(ROOT / "examples/projects/prediction/project.yaml")
    assert normalize_project_arguments(snapshot, "record", {"expected_revision": str(BIG), "outcome": "yes"}) == {
        "expected_revision": BIG, "outcome": "yes",
    }
    assert normalize_project_arguments(snapshot, "record", {"expected_revision": {"$lab_integer": str(BIG)}, "outcome": "yes"})["expected_revision"] == BIG
    assert normalize_project_arguments(snapshot, "resolve", {"evidence": str(BIG)})["evidence"] == str(BIG)
    for invalid in ("01", "1e18", " 3", 1.5, True):
        with pytest.raises(ValueError):
            normalize_project_arguments(snapshot, "record", {"expected_revision": invalid, "outcome": "yes"})


def test_python_http_client_uses_wire_for_projects_and_retains_internal_integers():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content) if request.content else None)
        return httpx.Response(200, json={"integer_encoding": INTEGER_ENCODING,
                                       "fee": {"$lab_integer": str(BIG)}, "domain": str(BIG)})

    with LabClient("http://127.0.0.1:8765", "test-token", transport=httpx.MockTransport(handler)) as client:
        response = client.workflow_invoke("project-test", "record", {"amount": BIG}, "once")
        assert response["fee"] == BIG and type(response["fee"]) is int
        assert type(response["domain"]) is str
    assert requests[0]["arguments"]["amount"] == {"$lab_integer": str(BIG)}


def test_python_legacy_client_does_not_reinterpret_tag_shaped_domain_objects():
    literal = {"$lab_integer": "42"}
    with LabClient("http://127.0.0.1:8765", "test-token",
                   transport=httpx.MockTransport(lambda request: httpx.Response(200, json=literal))) as client:
        assert client.observe("legacy-run") == literal


def test_python_client_creates_marked_export_without_losing_reviewed_integer():
    captured = []
    spec = {"schema_version": 2, "policy": {"max_fee": BIG}, "literal": {"$lab_integer": "1"}}
    marked = {"integer_encoding": INTEGER_ENCODING, **encode_project_wire(spec)}
    def handler(request):
        captured.append(decode_project_wire(json.loads(request.content)))
        return httpx.Response(200, json={"run_id": "project-test"})
    with LabClient("http://127.0.0.1:8765", "test-token", transport=httpx.MockTransport(handler)) as client:
        client.workflow_create(marked)
    assert captured == [{"spec": spec}]


def _node(script):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    result = subprocess.run([node, "--input-type=module", "-"], input=script, text=True,
                            cwd=ROOT, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_actual_typescript_client_bigints_and_raw_json_import_do_not_round():
    _node("""
import assert from 'node:assert/strict';
import {LabClient, parseProjectJson, stringifyProjectJson} from './examples/typescript/client.ts';
const big=(1n<<256n)-1n;
assert.equal(parseProjectJson('{"fee":'+big+'}').fee,big);
assert.equal(parseProjectJson('{"fee":{"$lab_integer":"'+big+'"}}').fee,big);
assert.equal(parseProjectJson('{"memo":"'+big+'"}').memo,big.toString());
const literal={$lab_integer:'123'};
assert.deepEqual(parseProjectJson(stringifyProjectJson(literal)),literal);
assert.throws(()=>stringifyProjectJson({fee:Number(big)}),/BigInt/);
let submitted;
globalThis.fetch=async (_url,options)=>{
  submitted=JSON.parse(options.body);
  return new Response(JSON.stringify({integer_encoding:'lab-tagged-decimal-v1',fee:{$lab_integer:big.toString()}}));
};
const client=new LabClient('http://127.0.0.1:8765','test-token');
const result=await client.workflowInvoke('project-test','record',{amount:big},'once');
assert.equal(result.fee,big);
assert.deepEqual(submitted.arguments.amount,{$lab_integer:big.toString()});
""")


def test_actual_dashboard_codec_preserves_large_imported_budgets():
    _node("""
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync('src/genlayer_agent_lab/assets/workflows.js','utf8');
const html=fs.readFileSync('src/genlayer_agent_lab/assets/workflows.html','utf8');
// Run the full production script with a minimal DOM, so this check follows its
// registered upload and submit handlers without a browser dependency in pytest.
class Element {
  constructor() {
    this.value=''; this.hidden=false; this.checked=false; this.disabled=false;
    this.textContent=''; this.dataset={}; this.children=[];
    this.events=new Map(); this.attributes=new Map();
    this.classList={toggle(){},add(){},remove(){}};
  }
  addEventListener(type,handler) { this.events.set(type,handler); }
  setAttribute(name,value) { this.attributes.set(name,value); }
  removeAttribute(name) { this.attributes.delete(name); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children=children; }
  focus() {}
  scrollIntoView() {}
}
const elements=new Map([...html.matchAll(/id="([^"]+)"/g)].map(match=>[match[1],new Element()]));
const getElement=id=>{
  assert(elements.has(id),'Unknown dashboard element: '+id);
  return elements.get(id);
};
const big=((1n<<256n)-1n).toString(), total=((1n<<256n)-2n).toString();
const raw=JSON.stringify({title:'Exact budget upload',task:'Read the public task',
  timeout_seconds:600,project_snapshot:{definition:{contracts:{example:{}}}},
  context:{memo:big},fixtures:{},expectations:{rules:[]},
  policy:{max_fee:'RAW_MAX',max_total_fee:'RAW_TOTAL',allow_appeal:false,operations:{}}})
  .replace('"RAW_MAX"',big).replace('"RAW_TOTAL"',total);
const marked=JSON.stringify({integer_encoding:'lab-tagged-decimal-v1',
  ...JSON.parse(raw),policy:{max_fee:{$lab_integer:big},max_total_fee:{$lab_integer:total},
    allow_appeal:false,operations:{}}});
const submissions=[];
const scope=vm.createContext({URLSearchParams,TextEncoder,AbortSignal,
  document:{getElementById:getElement,createElement:()=>new Element()},
  location:{origin:'http://127.0.0.1:8765',hash:''},
  sessionStorage:{getItem:()=>null},setInterval:()=>0,
  window:{createQuickTests:()=>({})},
  fetch:async (url,options)=>{
    assert.equal(url,'/v1/onboarding/preview');
    assert.equal(options.method,'POST');
    submissions.push(JSON.parse(options.body));
    // A raw numeric response also exercises the production HTTP parser before
    // the reviewed configuration is serialized back into the dashboard.
    return {ok:true,status:200,text:async()=>'{"spec":'+raw+',"digest":"fixture",'
      +'"summary":[],"rules":[],"warnings":[]}'};
  },
});
vm.runInContext(source,scope);
for (const contents of [raw,marked]) {
  const file={name:'scenario.json',size:Buffer.byteLength(contents),text:async()=>contents};
  await getElement('load-spec').events.get('change')({target:{files:[file]}});
  assert.equal(getElement('import-error').hidden,true);
  const loaded=JSON.parse(getElement('spec').value);
  assert.deepEqual(loaded.policy.max_fee,{$lab_integer:big});
  assert.deepEqual(loaded.policy.max_total_fee,{$lab_integer:total});
  assert.equal(loaded.context.memo,big);
  assert(!Object.hasOwn(loaded,'integer_encoding'));
  const previous=submissions.length;
  getElement('advanced-form').events.get('submit')({preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(submissions.length,previous+1);
  assert.deepEqual(submissions.at(-1).spec.policy.max_fee,{$lab_integer:big});
  assert.deepEqual(submissions.at(-1).spec.policy.max_total_fee,{$lab_integer:total});
  assert(!Object.hasOwn(submissions.at(-1).spec,'integer_encoding'));
  assert.equal(getElement('review-panel').hidden,false);
  assert.equal(getElement('review-confirmed').checked,false);
  const reviewed=JSON.parse(getElement('review-spec').textContent).spec;
  assert.deepEqual(reviewed.policy.max_fee,{$lab_integer:big});
  assert.deepEqual(reviewed.policy.max_total_fee,{$lab_integer:total});
  assert.equal(reviewed.context.memo,big);
}
assert.equal(submissions.length,2);
""")
