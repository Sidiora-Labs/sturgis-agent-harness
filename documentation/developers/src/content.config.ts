import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { docsSchema } from '@astrojs/starlight/schema';
// Python entry modules and repository dotfiles are public reference pages here.
// Starlight's default loader intentionally omits names beginning with an underscore.
export const collections = { docs: defineCollection({
  loader: glob({ base: new URL('./content/docs/', import.meta.url), pattern: ['**/*.{md,mdx}', '**/.*.{md,mdx}'] }),
  schema: docsSchema(),
}) };
