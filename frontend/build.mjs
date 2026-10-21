import {build} from "esbuild";
import {mkdir, copyFile} from "node:fs/promises";
await mkdir("custom_components/bucharest_transport/www", {recursive: true});
await build({
  entryPoints: ["frontend/card.ts"],
  outfile: "custom_components/bucharest_transport/www/bucharest-transport-card.js",
  bundle: true, minify: true, format: "esm", target: "es2022",
  loader: {".css": "text", ".png": "dataurl"}, legalComments: "eof",
});
await copyFile("node_modules/leaflet/LICENSE", "custom_components/bucharest_transport/www/LEAFLET-LICENSE.txt");
await copyFile("node_modules/lit/LICENSE", "custom_components/bucharest_transport/www/LIT-LICENSE.txt");
