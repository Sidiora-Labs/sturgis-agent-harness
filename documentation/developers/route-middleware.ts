import {
  defineRouteMiddleware,
  type StarlightRouteData,
} from '@astrojs/starlight/route-data';

type Navigation = Pick<StarlightRouteData, 'sidebar' | 'pagination'>;
type SidebarEntry = StarlightRouteData['sidebar'][number];
type SidebarLink = Extract<SidebarEntry, { type: 'link' }>;

/** Preserve HTML source-file suffixes stripped by Starlight's directory formatter. */
function repairSourceLink(link: SidebarLink | undefined): void {
  if (!link) return;
  const { label, href } = link;
  // Generated source labels are exact repository paths. Match both that label
  // and its source-reference URL, so ordinary docs and external links stay intact.
  if (
    !label.endsWith('.html') ||
    /[?#\\]/.test(label) ||
    label.split('/').some((part) => !part || part === '.' || part === '..') ||
    !href.startsWith('/') ||
    href.startsWith('//')
  ) return;

  const formattedSuffix = `/reference/files/${label.slice(0, -5)}/`;
  if (!href.endsWith(formattedSuffix)) return;
  const base = href.slice(0, -formattedSuffix.length);
  link.href = `${base}/reference/files/${label}/`;
}

/** Repair rendered navigation without changing collection IDs or published routes. */
export function repairSourceReferenceNavigation(data: Navigation): void {
  const visit = (entries: SidebarEntry[]): void => {
    for (const entry of entries) {
      if (entry.type === 'group') visit(entry.entries);
      else repairSourceLink(entry);
    }
  };
  visit(data.sidebar);
  // Pagination is computed before route middleware and may contain separate
  // link objects, so repair it explicitly as well as the sidebar tree.
  repairSourceLink(data.pagination.prev);
  repairSourceLink(data.pagination.next);
}

export const onRequest = defineRouteMiddleware(async (context, next) => {
  await next();
  repairSourceReferenceNavigation(context.locals.starlightRoute);
});
