import path from 'node:path';

/** Keep authored Markdown portable while publishing canonical, extensionless site routes. */
export function remarkDocumentationLinks({ contentRoot }) {
  return (tree, file) => {
    const visit = (node) => {
      if ((node.type === 'link' || node.type === 'definition') && typeof node.url === 'string') {
        const match = node.url.match(/^([^?#]+)\.(md|mdx)([?#].*)?$/);
        if (match && !/^(?:[a-z]+:|\/\/)/i.test(match[1])) {
          const current = file.path || file.history?.[0];
          const target = match[1].startsWith('/')
            ? match[1].slice(1)
            : path.relative(contentRoot, path.resolve(path.dirname(current), match[1]));
          if (target.startsWith('..')) throw new Error(`Documentation link escapes its audience: ${node.url} in ${current}`);
          const slug = target.replaceAll(path.sep, '/').replace(/(?:^|\/)index$/, '');
          node.url = `/${slug.replace(/\/$/, '')}${slug ? '/' : ''}${match[3] || ''}`;
        }
      }
      for (const child of node.children || []) visit(child);
    };
    visit(tree);
  };
}
