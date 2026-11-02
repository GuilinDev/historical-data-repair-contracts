// CPU-only calibration of a public, already-fixed signature compatibility defect.
// No network requests, production secrets, SDK tool execution, or model inference.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const out = path.join(root, 'results/second_calibration/approval_signature_17493');
const variants = ['upstream_base', 'upstream_fixed', 'reject_all_legacy', 'unguarded_legacy', 'reject_control_chars'];
const signatureRel = 'packages/ai/src/generate-text/tool-approval-signature.ts';
const canonicalRel = 'packages/ai/src/util/canonical-hash.ts';
const utilsRel = 'packages/provider-utils/src/uint8-utils.ts';
const sha = x => createHash('sha256').update(x).digest('hex');
const read = p => fs.readFileSync(p, 'utf8');
const load = p => JSON.parse(read(p));
const save = (p, value) => {fs.mkdirSync(path.dirname(p), {recursive:true}); fs.writeFileSync(p, JSON.stringify(value, null, 2));};
const baseline = {approvalId:'approval_alpha',toolCallId:'call_alpha',toolName:'inspect_record',input:{record:'alpha',options:{limit:3,enabled:true}}};
const secret = 'public-synthetic-calibration-key-not-a-production-credential';
const copy = x => structuredClone(x);
const fields = ['approvalId','toolCallId','toolName'];

function replaceOnce(text, before, after) {
  assert.equal(text.split(before).length - 1, 1, `Expected one adapter/mutation target: ${before.slice(0,50)}`);
  return text.replace(before, after);
}

function prepare() {
  assert(!fs.existsSync(path.join(out,'plan.json')), 'A frozen case already exists');
  const manifests = load(path.join(out,'merged_source_manifest.json'));
  const sources = {};
  for (const r of manifests) {
    const file = path.join(out,'merged_upstream',r.variant,r.path);
    assert.equal(sha(fs.readFileSync(file)), r.sha256);
  }
  const guard = "!approvalId.includes('\\n') &&\n    !toolCallId.includes('\\n') &&\n    !toolName.includes('\\n')";
  for (const variant of variants) {
    const upstream = variant === 'upstream_base' ? 'base' : 'fixed';
    const dir = path.join(out,'executable',variant);
    fs.mkdirSync(dir,{recursive:true});
    fs.writeFileSync(path.join(dir,'package.json'),'{"type":"module"}');
    let signer = read(path.join(out,'merged_upstream',upstream,signatureRel)).replaceAll('\r\n','\n');
    let canonical = read(path.join(out,'merged_upstream',upstream,canonicalRel)).replaceAll('\r\n','\n');
    signer = replaceOnce(signer,"from '@ai-sdk/provider-utils'", "from './uint8-utils.ts'");
    signer = replaceOnce(signer,"from '../util/canonical-hash'", "from './canonical-hash.ts'");
    canonical = replaceOnce(canonical,"from '@ai-sdk/provider-utils'", "from './uint8-utils.ts'");
    if (variant === 'reject_all_legacy') signer = replaceOnce(signer,guard,'false');
    if (variant === 'unguarded_legacy') signer = replaceOnce(signer,guard,'true');
    if (variant === 'reject_control_chars') {
      const index = signer.indexOf('export async function verifyToolApprovalSignature');
      const head = signer.slice(0,index), tail = signer.slice(index);
      signer = head + replaceOnce(tail, '  const key = await importKey(secret);',
        '  if ([approvalId, toolCallId, toolName].some(value => /[\\u0000-\\u001f]/.test(value))) return false;\n  const key = await importKey(secret);');
    }
    fs.writeFileSync(path.join(dir,'tool-approval-signature.ts'), signer);
    fs.writeFileSync(path.join(dir,'canonical-hash.ts'), canonical);
    fs.copyFileSync(path.join(out,'merged_upstream',upstream,utilsRel),path.join(dir,'uint8-utils.ts'));
    sources[variant] = Object.fromEntries(['tool-approval-signature.ts','canonical-hash.ts','uint8-utils.ts','package.json']
      .map(name=>[name,sha(fs.readFileSync(path.join(dir,name)))]));
  }
  const cases = [];
  function add(id,group,producer,tuple,expected,verify=tuple,wrongSecret=false) {
    cases.push({id,group,producer,signed:copy(tuple),verify:copy(verify),expected,wrongSecret,
      eligible_continuation:expected});
  }
  add('H_plain','historical','base',baseline,true);
  add('F_plain','fresh','fixed',baseline,true);
  for (const [label,char] of [['LF','\n'],['CR','\r'],['TAB','\t'],['NUL','\0'],['QUOTE','"'],['BACKSLASH','\\']]) {
    for (const field of fields) {
      const tuple = {...copy(baseline),[field]:'before'+char+'after'};
      add(`H_${label}_${field}`,'historical','base',tuple,label!=='LF');
      add(`F_${label}_${field}`,'fresh','fixed',tuple,true);
    }
  }
  const collisionPairs = [
    [{...copy(baseline),toolCallId:'call_alpha',toolName:'inspect_record\narchive_record'},
     {...copy(baseline),toolCallId:'call_alpha\ninspect_record',toolName:'archive_record'}],
    [{...copy(baseline),approvalId:'approval_alpha',toolCallId:'call_alpha\nextra'},
     {...copy(baseline),approvalId:'approval_alpha\ncall_alpha',toolCallId:'extra'}]
  ];
  collisionPairs.forEach(([a,b],i)=>{
    add(`H_retuple_${i}`,'historical','base',a,false,b);
    add(`F_retuple_${i}`,'fresh','fixed',a,false,b);
  });
  for (const producer of ['base','fixed']) {
    const group = producer==='base'?'historical':'fresh';
    for (const field of fields) add(`${group}_tamper_${field}`,group,producer,baseline,false,{...copy(baseline),[field]:'different'});
    add(`${group}_tamper_input`,group,producer,baseline,false,{...copy(baseline),input:{record:'beta'}});
    add(`${group}_wrong_key`,group,producer,baseline,false,baseline,true);
    const reordered = {...copy(baseline),input:{options:{enabled:true,limit:3},record:'alpha'}};
    add(`${group}_key_order`,group,producer,baseline,true,reordered);
  }
  save(path.join(out,'fixtures.json'),cases);
  save(path.join(out,'plan.json'),{
    created_utc:new Date().toISOString(),kind:'Additional signature-layer acceptance-boundary calibration',
    original_multi_solution_gate:'Not satisfied or claimed. Explicitly a second class of compatibility/security boundary.',
    upstream_fixed:'7fa85b2ca52da19b56b0809614cc8485edb42a0d',upstream_parent:'34c53c0ff19b1567e79d45130f85c612006eacf6',
    source_manifest_sha256:sha(fs.readFileSync(path.join(out,'merged_source_manifest.json'))),
    executable_hashes:sources,fixture_sha256:sha(fs.readFileSync(path.join(out,'fixtures.json'))),
    runner_sha256:sha(fs.readFileSync(fileURLToPath(import.meta.url))),variants,case_count:cases.length,
    expected_basis:'Published v7 fix: accept delimiter-free legacy signatures, reject LF-bearing legacy identifiers including their original tuple, preserve full tuple/input binding, accept current signatures even for LF-bearing identifiers, normalize JSON object-key ordering.',
    stages:['initial signature verification','same persisted records verified in a fresh Node process','accepted records reissued using that candidate signer, saved, and verified in a second fresh Node process'],
    scope:'Finite JSON-compatible input domain. Synthetic keys and inert tool-name strings; no actual tool invocation, approval UI, production account, model call, or full SDK execution.',
    adaptation:'Only module import specifiers and line endings adapted in upstream modules. Official uint8-utils source used without algorithm changes. Negative controls are explicit host-authored verifier mutations.',
    version_limit:'Legacy acceptance is part of this v7 upgrade contract; intentional removal in v8 is not classified as a defect.',
    collision_pairs:collisionPairs,paid_calls:0
  });
  console.log(JSON.stringify({prepared:true,cases:cases.length,variants:variants.length,paid_calls:0}));
}

async function moduleFor(variant) {
  return import(pathToFileURL(path.join(out,'executable',variant,'tool-approval-signature.ts')).href);
}

async function child(variant,inputFile,outputFile) {
  const mod = await moduleFor(variant), rows = load(inputFile), observed = [];
  for (const row of rows) {
    try {
      const accepted = await mod.verifyToolApprovalSignature({secret:row.wrongSecret?secret+'-wrong':secret,
        signature:row.signature,...row.verify});
      observed.push({id:row.id,accepted,execution_complete:true});
    } catch(error) {
      observed.push({id:row.id,execution_complete:false,error:String(error)});
    }
  }
  save(outputFile,observed);
}

function fresh(variant,inputFile,outputFile) {
  const run = spawnSync(process.execPath,[fileURLToPath(import.meta.url),'child',variant,inputFile,outputFile],
    {encoding:'utf8',timeout:30000,windowsHide:true});
  assert.equal(run.status,0,run.stderr);
  return load(outputFile);
}

async function run() {
  const plan = load(path.join(out,'plan.json'));
  assert.equal(plan.runner_sha256,sha(fs.readFileSync(fileURLToPath(import.meta.url))));
  assert.equal(plan.fixture_sha256,sha(fs.readFileSync(path.join(out,'fixtures.json'))));
  for (const [variant, files] of Object.entries(plan.executable_hashes))
    for (const [name,digest] of Object.entries(files)) assert.equal(digest,sha(fs.readFileSync(path.join(out,'executable',variant,name))));
  const producers = {base:await moduleFor('upstream_base'),fixed:await moduleFor('upstream_fixed')};
  const fixtures = load(path.join(out,'fixtures.json'));
  const signed = [];
  for (const row of fixtures) signed.push({...row,signature:await producers[row.producer].signToolApproval({secret,...row.signed})});
  save(path.join(out,'persisted_original_approvals.json'),signed);
  const collisions = [];
  for (const [a,b] of plan.collision_pairs) {
    const oldA = await producers.base.signToolApproval({secret,...a}), oldB = await producers.base.signToolApproval({secret,...b});
    const newA = await producers.fixed.signToolApproval({secret,...a}), newB = await producers.fixed.signToolApproval({secret,...b});
    assert.equal(oldA,oldB); assert.notEqual(newA,newB);
    collisions.push({a,b,old_signature:oldA,old_signatures_equal:oldA===oldB,new_signatures_equal:newA===newB});
  }
  save(path.join(out,'collision_observations.json'),collisions);
  const results = [];
  for (const variant of variants) {
    const mod = await moduleFor(variant), folder = path.join(out,'observations',variant);
    const initialFile = path.join(folder,'initial.json');
    await child(variant,path.join(out,'persisted_original_approvals.json'),initialFile);
    const initial = load(initialFile), restarted = fresh(variant,path.join(out,'persisted_original_approvals.json'),path.join(folder,'fresh_process.json'));
    const continued = [];
    for (const row of signed.filter(x=>x.eligible_continuation)) {
      continued.push({...row,signature:await mod.signToolApproval({secret,...row.verify}),signed:row.verify,wrongSecret:false,expected:true});
    }
    const continuedFile = path.join(folder,'persisted_reissued_approvals.json');
    save(continuedFile,continued);
    const restartContinued = fresh(variant,continuedFile,path.join(folder,'reissued_fresh_process.json'));
    const records = signed.map((row,i)=>{
      const a = initial[i], b = restarted[i], c = restartContinued.find(x=>x.id===row.id);
      assert.equal(a.id,row.id); assert.equal(b.id,row.id);
      const done = a.execution_complete && b.execution_complete && (!row.eligible_continuation || c?.execution_complete);
      return {id:row.id,group:row.group,expected:row.expected,initial:a.accepted,restarted:b.accepted,
        continuation_required:row.eligible_continuation,continued:c?.accepted??null,execution_complete:done,
        passed:!!done && a.accepted===row.expected && b.accepted===row.expected && (!row.eligible_continuation || c.accepted===true)};
    });
    save(path.join(folder,'score.json'),{variant,records});
    results.push({variant,total:records.length,passed:records.filter(r=>r.passed).length,
      completed:records.filter(r=>r.execution_complete).length,
      groups:Object.fromEntries(['historical','fresh'].map(g=>[g,{total:records.filter(r=>r.group===g).length,passed:records.filter(r=>r.group===g&&r.passed).length}])),
      failed_ids:records.filter(r=>!r.passed).map(r=>r.id)});
  }
  assert.equal(results.find(r=>r.variant==='upstream_fixed').passed,fixtures.length);
  assert(results.every(r=>r.completed===fixtures.length));
  assert(results.filter(r=>r.variant!=='upstream_fixed').every(r=>r.passed<fixtures.length));
  save(path.join(out,'summary.json'),{completed:true,node_version:process.version,plan_sha256:sha(fs.readFileSync(path.join(out,'plan.json'))),
    frozen_before_execution:true,results,collision_pairs:collisions.length,paid_calls:0,
    independent_review:'pending',limitations:plan.scope+' '+plan.version_limit});
  console.log(JSON.stringify({completed:true,results,paid_calls:0}));
}

if (process.argv[2]==='prepare') prepare();
else if (process.argv[2]==='run') await run();
else if (process.argv[2]==='child') await child(...process.argv.slice(3));
else throw new Error('Expected prepare, run, or child');
