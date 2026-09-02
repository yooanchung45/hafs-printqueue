import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { Readable } from "node:stream";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import ts from "typescript";
import bodyStreams from "next/dist/server/body-streams.js";
import bytes from "next/dist/compiled/bytes/index.js";

function loadSource(path, globals = {}) {
  const source = readFileSync(new URL(path, import.meta.url), "utf8");
  const output = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const exports = {};
  runInNewContext(output, { exports, process, ...globals });
  return exports;
}

const config = loadSource("../next.config.ts").default;
const { uploadSizeError } = loadSource("../lib/upload-limits.ts");
const MB = 1024 * 1024;

// Exercise the installed Next.js body cloning code used by external rewrites.
// Compare the entire multipart payload, including its final boundary.
for (const sizes of [[12], [100], [40, 60], [30, 30, 40]]) {
  test(`multipart upload survives the proxy: ${sizes.join(" + ")}MB`, async () => {
    const chunk = Buffer.alloc(MB, 0x61);
    const expected = createHash("sha256");
    let expectedBytes = 0;
    async function* multipart() {
      for (const [index, size] of sizes.entries()) {
        yield Buffer.from(`--test-boundary\r\nContent-Disposition: form-data; name="files"; filename="model-${index}.stl"\r\nContent-Type: application/octet-stream\r\n\r\n`);
        for (let i = 0; i < size; i++) yield chunk;
        yield Buffer.from("\r\n");
      }
      yield Buffer.from("--test-boundary--\r\n");
    }
    async function* tracked() {
      for await (const part of multipart()) {
        expected.update(part);
        expectedBytes += part.length;
        yield part;
      }
    }
    const input = Readable.from(tracked());
    input.url = "/api/upload/stl-preview";
    const body = bodyStreams.getCloneableBody(input, bytes.parse(config.experimental.proxyClientMaxBodySize));
    const actual = createHash("sha256");
    let actualBytes = 0;
    for await (const part of body.cloneBodyStream()) {
      actual.update(part);
      actualBytes += part.length;
    }
    // The local proxy must include room for the multipart envelope.
    assert.ok(expectedBytes < bytes.parse(config.experimental.proxyClientMaxBodySize));
    assert.equal(actualBytes, expectedBytes);
    assert.equal(actual.digest("hex"), expected.digest("hex"));
  });
}

test("upload validation allows size boundaries and rejects oversized files or batches", () => {
  const file = (size) => ({ name: "model.stl", size });
  assert.equal(uploadSizeError([file(12 * MB)]), null);
  assert.equal(uploadSizeError([file(100 * MB)]), null);
  assert.match(uploadSizeError([file(100 * MB + 1)]), /100MB/);
  const batch = Array.from({ length: 2 }, () => file(50 * MB));
  assert.equal(uploadSizeError(batch), null);
  assert.match(uploadSizeError([...batch, file(1)]), /100MB/);
});

test("non-JSON proxy 413 gets a useful message and backend details are preserved", async () => {
  for (const [response, expected] of [
    [new Response("<html>Too large</html>", { status: 413 }), /업로드 용량이 서버 제한을 초과/],
    [Response.json({ detail: "파일은 100MB를 넘을 수 없습니다" }, { status: 413 }), /^파일은 100MB를 넘을 수 없습니다$/],
    [new Response("Bad gateway", { status: 502 }), /^요청을 처리하지 못했습니다\.$/],
  ]) {
    const { api } = loadSource("../lib/api.ts", { fetch: async () => response });
    await assert.rejects(api("/api/upload"), (error) => {
      assert.equal(error.status, response.status);
      assert.match(error.message, expected);
      return true;
    });
  }
});
