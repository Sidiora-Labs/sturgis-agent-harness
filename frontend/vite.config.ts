import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath } from "node:url";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  writeFileSync,
} from "node:fs";
import { dirname, isAbsolute, join, relative } from "node:path";
const projectRoot = fileURLToPath(new URL("..", import.meta.url));

export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  plugins: [
    react(),
    tailwindcss(),
    {
      name: "sturgis-build-provenance",
      generateBundle(_options, bundle) {
        const chunks = Object.fromEntries(
          Object.values(bundle)
            .filter((chunk) => chunk.type === "chunk")
            .map((chunk) => [
              chunk.fileName,
              {
                moduleIds: chunk.moduleIds.map((id) =>
                  isAbsolute(id) ? relative(projectRoot, id) : id,
                ),
                isEntry: chunk.isEntry,
                isDynamicEntry: chunk.isDynamicEntry,
              },
            ]),
        );
        const directory = fileURLToPath(new URL("../.build", import.meta.url));
        mkdirSync(directory, { recursive: true });
        writeFileSync(
          `${directory}/web-modules.json`,
          JSON.stringify(chunks, null, 2),
        );
        // Portable wheels retain the licenses of bundled libraries and fonts.
        const packageRoots = new Set<string>([
          join(projectRoot, "node_modules/@fontsource-variable/roboto-flex"),
        ]);
        for (const chunk of Object.values(chunks)) {
          for (const module of chunk.moduleIds) {
            if (!module.startsWith("node_modules/")) continue;
            let directory = dirname(join(projectRoot, module.split("?")[0]));
            while (directory.startsWith(join(projectRoot, "node_modules"))) {
              const manifest = join(directory, "package.json");
              if (
                existsSync(manifest) &&
                JSON.parse(readFileSync(manifest, "utf8")).name
              ) {
                packageRoots.add(directory);
                break;
              }
              directory = dirname(directory);
            }
          }
        }
        const notices = [...packageRoots].sort().map((directory) => {
          const manifest = JSON.parse(
            readFileSync(join(directory, "package.json"), "utf8"),
          );
          const licenses = readdirSync(directory)
            .filter((name) => /^(?:licen[cs]e|copying)(?:[.-]|$)/i.test(name))
            .sort();
          const texts = licenses.map((name) =>
            readFileSync(join(directory, name), "utf8"),
          );
          // This release omitted its upstream MIT license from the npm tarball.
          if (
            !texts.length &&
            manifest.name === "react-remove-scroll-bar" &&
            manifest.version === "2.3.8"
          )
            texts.push(
              readFileSync(
                join(
                  projectRoot,
                  "frontend/licenses/react-remove-scroll-bar.LICENSE",
                ),
                "utf8",
              ),
            );
          if (!texts.length)
            throw new Error(
              `Bundled package lacks license text: ${manifest.name}`,
            );
          return `${manifest.name}@${manifest.version}\n${texts.join("\n")}\n`;
        });
        this.emitFile({
          type: "asset",
          name: "third-party-notices.txt",
          source: notices.join("\n---\n\n"),
        });
      },
    },
  ],
  build: {
    outDir: "../consumer/web",
    emptyOutDir: true,
    sourcemap: "hidden",
    target: "es2022",
    assetsInlineLimit: 0,
    chunkSizeWarningLimit: 350,
    rolldownOptions: {
      input: {
        app: fileURLToPath(new URL("./index.html", import.meta.url)),
        gateway: fileURLToPath(new URL("./gateway.html", import.meta.url)),
      },
      output: {
        codeSplitting: {
          includeDependenciesRecursively: false,
          groups: [
            {
              name: "react",
              test: /node_modules\/(?:react|react-dom|scheduler)\//,
              priority: 30,
            },
            {
              name: "primitives",
              test: /node_modules\/(?:@radix-ui|radix-ui)\//,
              priority: 20,
            },
            {
              name: "icons",
              test: /node_modules\/lucide-react\//,
              priority: 10,
            },
          ],
        },
      },
    },
  },
  server: { host: "127.0.0.1", proxy: { "/api": "http://127.0.0.1:8787" } },
});
