import test from 'node:test';
import assert from 'node:assert/strict';
import {deliveryPhase,isTestProject,recoveryMessage,safeWebsiteUrl,relativeChange} from '../app/q/js/workflow.js';
import {shapeProject,store} from '../app/q/js/core.js';

test('historical runs keep their recorded completion when a playbook gains stages',()=>{
  store.playbooks.website_build={stages:Array.from({length:14},(_,i)=>({id:`stage${i}`}))};
  store.runs=[{project_id:'historic',state:'completed',done:11,total:11}];
  const card=shapeProject({id:'historic',playbook:'website_build',stage:11});
  assert.equal(card.total,11);assert.equal(card.done,11);assert.equal(card.pct,100);
  assert.equal(card.phase,'launch review');
  store.runs=[];store.playbooks={};
});

test('completing a build does not label it as published',()=>{
  assert.equal(deliveryPhase({p:{playbook:'website_build'},finished:true}),'launch');
  assert.equal(deliveryPhase({p:{playbook:'website_build',status:'live'},finished:true}),'grow');
});
test('build checks and launch approval land in the correct lanes',()=>{
  for(const id of ['self_audit','visual_qa','verify_repair'])assert.equal(deliveryPhase({p:{},curStage:{id}}),'build');
  assert.equal(deliveryPhase({p:{},curStage:{id:'review_gate'}}),'launch');
});
test('test projects are detectable without confusing ordinary client names',()=>{
  for(const name of ['ZZ E2E Test - Ember & Oak','No URL Test','ZZ-selftest'])assert.equal(isTestProject({name}),true);
  for(const name of ['Northgate Plumbing','Latest Architecture'])assert.equal(isTestProject({name}),false);
});
test('billing failures explain recovery without exposing diagnostics',()=>{
  const r=recoveryMessage("stage failed — LLMError HTTP 402 credits exhausted");
  assert.equal(r.title,'Provider credits exhausted');
  assert.match(r.detail,/Completed steps are saved/);
});
test('website links reject executable URLs and malformed input',()=>{
  assert.equal(safeWebsiteUrl('javascript:alert(1)'),'');
  assert.equal(safeWebsiteUrl('data:text/html,hello'),'');
  assert.equal(safeWebsiteUrl('not a URL'),'');
  assert.equal(safeWebsiteUrl('https://example.com'),'https://example.com/');
});
test('growth change has no fabricated percent for absent baseline',()=>{
  assert.equal(relativeChange(12,0),null);
  assert.equal(relativeChange(12,undefined),null);
  assert.equal(relativeChange(12,10),20);
  assert.equal(relativeChange(5,10),-50);
});
