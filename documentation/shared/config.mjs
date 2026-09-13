import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import { unified } from '@astrojs/markdown-remark';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { remarkDocumentationLinks } from './links.mjs';

/** Build each audience as a separate static origin with an independent search index. */
export function documentationConfig(audience, configUrl) {
  const root = fileURLToPath(new URL('.', configUrl));
  const developer = audience === 'developers';
  const title = developer ? 'Sturgis Developers' : 'Sturgis Guides';
  const port = developer ? 4322 : 4321;
  const description = developer
    ? 'Build, extend and operate Sturgis. Complete architecture, protocols, APIs and source reference.'
    : 'Learn Sturgis, from your first conversation to knowledge, connections and dependable automation.';
  return defineConfig({
    root,
    outDir: fileURLToPath(new URL(`../dist/${audience}/`, configUrl)),
    site: process.env[developer ? 'STURGIS_DEVELOPER_DOCS_ORIGIN' : 'STURGIS_USER_DOCS_ORIGIN'] || `http://localhost:${port}`,
    output: 'static',
    trailingSlash: 'always',
    markdown: { processor: unified({ remarkPlugins: [[remarkDocumentationLinks, { contentRoot: path.join(root, 'src/content/docs') }]] }) },
    integrations: [starlight({
      title, description,
      routeMiddleware: developer ? ['./route-middleware.ts'] : [],
      favicon: '/favicon.svg',
      logo: { src: './public/favicon.svg', replacesTitle: false },
      customCss: ['../shared/theme.css'],
      tableOfContents: { minHeadingLevel: 2, maxHeadingLevel: 3 },
      pagefind: true,
      credits: false,
      expressiveCode: { themes: ['github-dark', 'github-light'], styleOverrides: { borderRadius: '0.85rem', frames: { shadowColor: 'transparent' } } },
      head: [{ tag: 'meta', attrs: { name: 'theme-color', content: '#f8f7fc' } }],
      sidebar: developer ? [
        { label: 'Developer home', slug: '' },
        { label: 'Start building', items: [{ autogenerate: { directory: 'guides/getting-started' } }], collapsed: true },
        { label: 'Architecture', items: [{ autogenerate: { directory: 'guides/architecture' } }], collapsed: true },
        { label: 'Core mechanisms', items: [{ autogenerate: { directory: 'guides/core' } }], collapsed: true },
        { label: 'Native memory', items: [{ autogenerate: { directory: 'guides/memory' } }], collapsed: true },
        { label: 'Behavior and planning', items: [{ autogenerate: { directory: 'guides/behavior' } }], collapsed: true },
        { label: 'Extend and integrate', items: [{ autogenerate: { directory: 'guides/integrations' } }], collapsed: true },
        { label: 'Consumer boundary', items: [{ autogenerate: { directory: 'guides/consumer' } }], collapsed: true },
        { label: 'Model gateway', items: [{ autogenerate: { directory: 'guides/gateway' } }], collapsed: true },
        { label: 'Web client', items: [{ autogenerate: { directory: 'guides/frontend' } }], collapsed: true },
        { label: 'Operate and maintain', items: [{ autogenerate: { directory: 'guides/operations' } }], collapsed: true },
        { label: 'Complete source reference', items: [{ autogenerate: { directory: 'reference' } }], collapsed: true },
      ] : [
        { label: 'Guide home', slug: '' },
        { label: 'Start here', items: [{ autogenerate: { directory: 'getting-started' } }], collapsed: false },
        { label: 'Models and credentials', items: [{ autogenerate: { directory: 'models' } }], collapsed: true },
        { label: 'Model gateway', items: [{ autogenerate: { directory: 'guides' } }], collapsed: true },
        { label: 'Conversations', items: [{ autogenerate: { directory: 'conversations' } }], collapsed: true },
        { label: 'Work and automation', items: [{ autogenerate: { directory: 'work' } }], collapsed: true },
        { label: 'Knowledge and memory', items: [{ autogenerate: { directory: 'knowledge' } }], collapsed: true },
        { label: 'Workspace files', items: [{ autogenerate: { directory: 'files' } }], collapsed: true },
        { label: 'Connections', items: [{ autogenerate: { directory: 'connections' } }], collapsed: true },
        { label: 'Settings and recovery', items: [{ autogenerate: { directory: 'settings' } }], collapsed: true },
        { label: 'Command line', items: [{ autogenerate: { directory: 'cli' } }], collapsed: true },
        { label: 'Reference and troubleshooting', items: [{ autogenerate: { directory: 'reference' } }], collapsed: true },
      ],
    })],
    server: { host: '127.0.0.1', port },
  });
}
