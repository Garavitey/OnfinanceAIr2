// Deterministic Mermaid parse gate.
//
// Reads Mermaid source from stdin, asks mermaid.parse() whether it is
// syntactically valid, and prints exactly one JSON line to stdout:
//   {"ok":true}
//   {"ok":false,"error":"<message>"}
//
// Always exits 0 — the caller distinguishes valid/invalid via the JSON
// payload, not the process exit code. This process is spawned fresh for
// every diagram validation, so startup is kept to the minimum needed to
// get mermaid.parse() working: a jsdom document and nothing else.

import { JSDOM } from "jsdom";

async function main() {
  const source = await readStdin();

  const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>", {
    pretendToBeVisual: true,
  });

  global.window = dom.window;
  global.document = dom.window.document;
  global.Element = dom.window.Element;
  global.SVGElement = dom.window.SVGElement;
  global.HTMLElement = dom.window.HTMLElement;
  global.getComputedStyle = dom.window.getComputedStyle;

  // `navigator` is a getter-only global on modern Node; a plain assignment
  // throws "Cannot set property navigator of #<Object> which has only a
  // getter". Redefine the property instead of assigning to it.
  Object.defineProperty(global, "navigator", {
    value: dom.window.navigator,
    configurable: true,
  });

  const { default: mermaid } = await import("mermaid");

  mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });

  try {
    await mermaid.parse(source);
    process.stdout.write(JSON.stringify({ ok: true }) + "\n");
  } catch (err) {
    const message = err && err.message ? err.message : String(err);
    process.stdout.write(JSON.stringify({ ok: false, error: message }) + "\n");
  }

  process.exit(0);
}

function readStdin() {
  return new Promise((resolve, reject) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      data += chunk;
    });
    process.stdin.on("end", () => resolve(data));
    process.stdin.on("error", reject);
  });
}

main().catch((err) => {
  // Should be unreachable (the try/catch above handles parse failures),
  // but never let this process die without emitting valid JSON.
  process.stdout.write(
    JSON.stringify({ ok: false, error: `validator crashed: ${err && err.message ? err.message : String(err)}` }) + "\n"
  );
  process.exit(0);
});
