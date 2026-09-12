import assert from 'node:assert/strict';
import test from 'node:test';
import { onRequest, repairSourceReferenceNavigation } from '../../documentation/developers/route-middleware.ts';
import { stripHtmlExtension, ensureTrailingSlash } from '../../documentation/node_modules/@astrojs/starlight/dist/utils/path.js';

function link(label, href, extra = {}) {
  return { type: 'link', label, href, attrs: {}, badge: undefined, isCurrent: false, ...extra };
}

function formattedSource(label, base = '') {
  // Exercise the installed formatter's real extension/trailing-slash helpers.
  return ensureTrailingSlash(stripHtmlExtension(`${base}/reference/files/${label}/`));
}

test('repairs installed Starlight HTML normalization in nested sidebar and independent pagination', () => {
  const frontend = link('frontend/index.html', formattedSource('frontend/index.html'), { isCurrent: true });
  const consumer = link('consumer/web/index.html', formattedSource('consumer/web/index.html'));
  const future = link('examples/custom.report.html', formattedSource('examples/custom.report.html', '/developer-docs'));
  const data = {
    sidebar: [{ type: 'group', label: 'Source reference', entries: [frontend, { type: 'group', label: 'Other', entries: [consumer, future] }], collapsed: true }],
    pagination: { prev: structuredClone(consumer), next: structuredClone(future) },
  };
  assert.equal(frontend.href, '/reference/files/frontend/index/');
  repairSourceReferenceNavigation(data);
  assert.equal(frontend.href, '/reference/files/frontend/index.html/');
  assert.equal(consumer.href, '/reference/files/consumer/web/index.html/');
  assert.equal(future.href, '/developer-docs/reference/files/examples/custom.report.html/');
  assert.equal(data.pagination.prev.href, consumer.href);
  assert.equal(data.pagination.next.href, future.href);
  assert.equal(frontend.isCurrent, true);
  assert.equal(frontend.label, 'frontend/index.html');
  const once = structuredClone(data);
  repairSourceReferenceNavigation(data);
  assert.deepEqual(data, once);
});

test('leaves ordinary pages, other source types, external links and mismatched labels unchanged', () => {
  const sidebar = [
    link('guide.html', '/guides/guide/'),
    link('frontend/index.html', '/reference/files/another/index/'),
    link('frontend/index.html', '/reference/files/frontend/index.html/'),
    link('frontend/app.tsx', '/reference/files/frontend/app.tsx/'),
    link('frontend/index.html', 'https://elsewhere.example/reference/files/frontend/index/'),
    link('frontend/index.html', '//elsewhere.example/reference/files/frontend/index/'),
    link('../index.html', '/reference/files/../index/'),
    link('/index.html', '/reference/files//index/'),
    link('frontend/index.html', '/reference/files/frontend/index/?query=1'),
    link('frontend/#index.html', '/reference/files/frontend/#index/'),
  ];
  const data = { sidebar, pagination: { prev: undefined, next: undefined } };
  const before = structuredClone(data);
  repairSourceReferenceNavigation(data);
  assert.deepEqual(data, before);
});

test('public route middleware repairs after later middleware supplies navigation', async () => {
  const context = { locals: { starlightRoute: { sidebar: [], pagination: {} } } };
  let nextCalled = false;
  await onRequest(context, async () => {
    nextCalled = true;
    context.locals.starlightRoute.sidebar.push(link('pages/about.html', formattedSource('pages/about.html')));
  });
  assert.equal(nextCalled, true);
  assert.equal(context.locals.starlightRoute.sidebar[0].href, '/reference/files/pages/about.html/');
});
