/** Exercise both built documentation sites with real browser navigation and local search. */
import { createServer } from 'node:http';
import { readFile, stat, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import AxeBuilder from '@axe-core/playwright';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const output = path.join(root, 'evidence/sturgis-docs');
const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2', '.wasm': 'application/wasm' };

async function serve(audience) {
  const base = path.join(root, 'documentation/dist', audience);
  const server = createServer(async (request, response) => {
    try {
      let target = path.resolve(base, '.' + decodeURIComponent(new URL(request.url, 'http://localhost').pathname));
      if (target !== base && !target.startsWith(base + path.sep)) throw new Error('Outside static root');
      if ((await stat(target)).isDirectory()) target = path.join(target, 'index.html');
      const body = await readFile(target);
      response.writeHead(200, { 'Content-Type': types[path.extname(target)] || 'application/octet-stream', 'Content-Length': body.length });
      response.end(body);
    } catch {
      response.writeHead(404); response.end('Not found');
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return { server, origin: `http://127.0.0.1:${server.address().port}` };
}

const servers = [];
const report = { status: 'running', audiences: {}, accessibility: [], errors: [] };
let browser;
try {
  await mkdir(output, { recursive: true });
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  for (const audience of ['users', 'developers']) {
    const hosted = await serve(audience); servers.push(hosted.server);
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, permissions: ['clipboard-read', 'clipboard-write'] });
    context.setDefaultTimeout(20000);
    context.setDefaultNavigationTimeout(30000);
    const page = await context.newPage();
    console.log(`${audience}: home, keyboard and theme`);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const response = await page.goto(hosted.origin + '/');
    assert.equal(response.status(), 200);
    await page.locator('main h1').waitFor();
    assert.match(await page.title(), audience === 'users' ? /Sturgis|Learn/ : /Sturgis|Build/);
    await page.keyboard.press('Tab');
    const focused = await page.evaluate(() => ({ tag: document.activeElement.tagName, text: document.activeElement.textContent }));
    assert.equal(focused.tag, 'A');
    assert.match(focused.text, /skip/i);
    await page.keyboard.press('Enter');
    const theme = page.getByRole('combobox', { name: /theme/i });
    await theme.selectOption('dark');
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
    await page.reload();
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
    await theme.selectOption('light');
    await page.screenshot({ path: path.join(output, `${audience}-desktop.png`), fullPage: false });
    let axe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
    report.accessibility.push({ audience, view: 'desktop-home', violations: axe.violations });
    assert.equal(axe.violations.length, 0, `${audience} desktop accessibility: ${JSON.stringify(axe.violations)}`);

    console.log(`${audience}: search`);
    await page.keyboard.press('Control+k');
    const search = page.getByRole('textbox', { name: 'Search', exact: true });
    await search.waitFor();
    const query = audience === 'users' ? 'connect model' : 'GoalService';
    await search.fill(query);
    const result = page.locator('.pagefind-ui__result-link').first();
    await result.waitFor({ timeout: 20000 });
    const resultHref = await result.getAttribute('href');
    assert.ok(resultHref && !resultHref.includes('undefined'));
    await result.click();
    await page.locator('main h1').waitFor();
    assert.notEqual(new URL(page.url()).pathname, '/');
    const searchedPage = new URL(page.url()).pathname;

    const guide = audience === 'users' ? '/getting-started/self-hosted/' : '/guides/getting-started/first-mechanisms/';
    // The declared example route is supplied by the authored page manifest, never a synthetic test-only page.
    const manifest = JSON.parse(await readFile(path.join(root, `documentation/coverage/${audience}.json`), 'utf8'));
    let codeRoute = guide;
    for (const row of manifest.pages) {
      let rel = row.path;
      if (audience === 'developers' && !rel.startsWith('guides/')) rel = 'guides/' + rel;
      const source = await readFile(path.join(root, `documentation/${audience}/src/content/docs`, rel), 'utf8');
      if (/```(?:bash|sh|python|json)/.test(source)) {
        codeRoute = '/' + rel.replace(/\.md$/, '').replace(/\/index$/, '') + '/';
        break;
      }
    }
    const codeResponse = await page.goto(hosted.origin + codeRoute);
    console.log(`${audience}: code example ${codeRoute}`);
    assert.equal(codeResponse.status(), 200, codeRoute);
    const copy = page.locator('.expressive-code .copy button').first();
    await copy.waitFor();
    await copy.click();
    const clipboard = await page.evaluate(() => navigator.clipboard.readText());
    assert.ok(clipboard.trim().length > 5, 'Code copy must transfer actual example text');
    await page.screenshot({ path: path.join(output, `${audience}-guide.png`), fullPage: false });

    if (audience === 'developers') {
      const sourceResponse = await page.goto(hosted.origin + '/reference/files/agent/protocol/__init__.py/#L10');
      assert.equal(sourceResponse.status(), 200);
      const sourceLine = page.locator('[id="L10"]');
      await sourceLine.waitFor();
      assert.ok((await sourceLine.textContent()).trim().length > 0);
      await page.screenshot({ path: path.join(output, 'developer-source-reference.png'), fullPage: false });
    }

    await page.setViewportSize({ width: 390, height: 844 });
    console.log(`${audience}: mobile navigation`);
    await page.goto(hosted.origin + '/');
    await page.getByRole('button', { name: /menu/i }).click();
    const nav = page.locator('nav').filter({ has: page.locator('a') });
    assert.ok(await nav.count());
    await page.getByRole('button', { name: /menu/i }).click();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `${audience} mobile page overflows`);
    axe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
    report.accessibility.push({ audience, view: 'mobile-home', violations: axe.violations });
    assert.equal(axe.violations.length, 0, `${audience} mobile accessibility: ${JSON.stringify(axe.violations)}`);
    await page.screenshot({ path: path.join(output, `${audience}-mobile.png`), fullPage: false });
    assert.deepEqual(errors, [], `${audience} browser errors`);
    report.audiences[audience] = { home: 'passed', keyboard_skip: 'passed', theme_persistence: 'passed', search: { query, result: searchedPage },
      copied_example_route: codeRoute, copied_characters: clipboard.length, mobile_navigation: 'passed', overflow: false, browser_errors: errors };
    await context.close();
  }
  report.status = 'passed';
  report.completed_at = new Date().toISOString();
  console.log(JSON.stringify(report, null, 2));
} catch (error) {
  report.status = 'failed'; report.errors.push(String(error.stack || error));
  console.error(error);
  process.exitCode = 1;
} finally {
  await writeFile(path.join(output, 'browser.json'), JSON.stringify(report, null, 2) + '\n');
  await browser?.close();
  for (const server of servers) await new Promise(resolve => server.close(resolve));
}
