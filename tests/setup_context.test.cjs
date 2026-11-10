const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const html = fs.readFileSync(path.join(__dirname, '../provider_fallback_skill/setup.html'), 'utf8');
const source = html.match(/function contextFields\([\s\S]*?(?=function row\()/)[0];
const contextFields = vm.runInNewContext(source + ';contextFields');
const fields = (...values) => JSON.parse(JSON.stringify(contextFields(...values)));

test('blank context keeps stock routes free of added fields', () => {
  assert.deepEqual(fields('', '4096', '8192'), {});
});

test('hosted Support omits route budgets and preserves Wingman defaults', () => {
  assert.deepEqual(fields('32768', '4096', '8192', true), {});
  assert.match(html, /Wingman manages hosted Support limits/);
  assert.match(html, /these limits do not raise Core\/provider limits/);
});

test('enabled context applies defaults and preserves explicit zero headroom', () => {
  assert.deepEqual(fields('32768', '', ''), {
    context_window: 32768, max_output_tokens: 4096, tool_reserve_tokens: 8192,
  });
  assert.deepEqual(fields('32768', '2048', '0'), {
    context_window: 32768, max_output_tokens: 2048, tool_reserve_tokens: 0,
  });
});

test('invalid integers and exhausted context cannot be previewed', () => {
  for (const values of [
    ['0', '4096', '8192'], ['32768.5', '4096', '8192'],
    ['32768', '0', '8192'], ['32768', '4096', '-1'], ['32768', '4096', '1.5'],
    ['12288', '4096', '8192'], ['8192', '4096', '8192'],
  ]) assert.throws(() => fields(...values));
});

test('an unselected role does not validate or serialize its edited route', () => {
  const elements = {
    notify: {checked: true},
    'main-enabled': {checked: true}, 'main-mode': {value: 'credits_only'},
    'main-routes': {children: [{route: () => ({route_id: 'main'})}]},
    'support-enabled': {checked: false}, 'support-mode': {value: 'credits_only'},
    'support-routes': {children: [{route: () => {throw new Error('unselected');}}]},
  };
  const planSource = html.match(/^function plan\(\).*$/m)[0];
  const plan = vm.runInNewContext(planSource + ';plan', {
    document: {getElementById: id => elements[id]},
  });
  assert.deepEqual(JSON.parse(JSON.stringify(plan())).support.routes, []);
});
