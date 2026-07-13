import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../.call-test-build", import.meta.url));

function walk(directory) {
  for (const entry of readdirSync(directory)) {
    const fullPath = join(directory, entry);
    if (statSync(fullPath).isDirectory()) {
      walk(fullPath);
      continue;
    }
    if (!fullPath.endsWith(".js")) continue;
    const source = readFileSync(fullPath, "utf8");
    const patched = source.replace(/(from\s+["'])(\.\.?\/[^"']+?)(["'])/g, (_match, prefix, specifier, suffix) => {
      if (/\.[a-z0-9]+$/i.test(specifier)) return `${prefix}${specifier}${suffix}`;
      return `${prefix}${specifier}.js${suffix}`;
    });
    writeFileSync(fullPath, patched);
  }
}

walk(root);
