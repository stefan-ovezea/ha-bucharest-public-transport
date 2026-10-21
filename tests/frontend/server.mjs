import {createServer} from "node:http";
import {readFile} from "node:fs/promises";
createServer(async (req, res) => {
  const path = req.url?.split("?")[0] === "/card.js" ? "custom_components/bucharest_transport/www/bucharest-transport-card.js" : "tests/frontend/fixture.html";
  try {res.setHeader("Content-Type", path.endsWith(".js") ? "text/javascript" : "text/html"); res.end(await readFile(path));}
  catch {res.writeHead(404).end();}
}).listen(4173, "127.0.0.1");
