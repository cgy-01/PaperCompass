import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
const load = async name => import("data:text/javascript;base64," + Buffer.from(await readFile(new URL('../src/paper_firehose/system/frontend/' + name, import.meta.url))).toString('base64'));
const { groupSources, discoverHref } = await load('catalog-data.js');
const { bindSearch } = await load('search.js');
const { workspaceHref, authMessage } = await load('auth-navigation.js');
test('native workspace entries round-trip their destination through the login gate', () => {
  for (const target of ['#home', '#discover?theme=AI+Infra', '#paper/'+'a'.repeat(64), '#library?mode=read', '#docs']) {
    const link = new URL(workspaceHref(target), 'https://papercompass.cn');
    assert.equal(link.pathname, '/auth/github/login');
    assert.equal(link.searchParams.get('return_to'), target);
  }
  assert.equal(workspaceHref('https://other.test'),workspaceHref('#home'));
  assert.equal(workspaceHref('#home\r\n'),workspaceHref('#home'));
  assert.equal(authMessage('provider-untrusted-text'), '');
});

test('source families group editions without changing exact API names', () => {
  const items = [{name:'CVPR 2025',type:'会议',count:3},{name:'CVPR 2026',type:'会议',count:4},
    {name:'arXiv cs.AI',type:'预印本',count:2},{name:'arXiv cs.CL',type:'预印本',count:1},
    {name:'Journal 2025',type:'期刊',count:1}];
  const groups=groupSources(items);
  assert.equal(groups.length,3);
  assert.equal(groups[0].count,7);
  assert.deepEqual(groups[0].items.map(x=>x.name),['CVPR 2026','CVPR 2025']);
  assert.equal(groups.find(x=>x.name==='arXiv').count,3);
  assert.equal(groups.find(x=>x.name==='Journal 2025').type,'期刊');
  const link=discoverHref({type:'会议',source:'CVPR 2026',theme:'视觉与图形'});
  const params=new URLSearchParams(link.split('?')[1]);
  assert.equal(params.get('source'),'CVPR 2026');
  assert.equal(params.get('theme'),'视觉与图形');
  assert.equal(groupSources([]).length,0);
});

class Input extends EventTarget { value=''; form=new EventTarget(); }
const event=(name,fields={})=>Object.assign(new Event(name,{cancelable:true}),fields);
test('typing and completing IME composition wait for form submission', () => {
  const input = new Input(), values = [];
  bindSearch(input, value => values.push(value));
  input.dispatchEvent(event('compositionstart'));
  input.value = 'pin';
  input.dispatchEvent(event('input', {isComposing: true}));
  input.dispatchEvent(event('keydown', {key: 'Enter', isComposing: true}));
  input.value = '强化学习';
  input.dispatchEvent(event('compositionend'));
  input.dispatchEvent(event('input'));
  assert.deepEqual(values, []);
  input.form.dispatchEvent(event('submit'));
  assert.deepEqual(values, ['强化学习']);
});

test('explicit form submit trims the query and can clear the active search', () => {
  const input = new Input(), values = [];
  bindSearch(input, value => values.push(value));
  input.value = '  KV cache  ';
  input.form.dispatchEvent(event('submit'));
  input.value = '';
  input.dispatchEvent(event('input'));
  assert.deepEqual(values, ['KV cache']);
  input.form.dispatchEvent(event('submit'));
  assert.deepEqual(values, ['KV cache', '']);
});
