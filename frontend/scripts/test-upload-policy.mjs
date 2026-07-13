import assert from "node:assert/strict";
import { rmSync } from "node:fs";
import {
  formatUploadLimit,
  uploadPolicyFromCapabilities,
  validateComposerUpload,
} from "../.upload-test-build/components/composer/uploadPolicy.js";

const policy = uploadPolicyFromCapabilities({
  features: {},
  calls: {},
  security: {},
  limits: { max_upload_bytes: 10 * 1024 * 1024 },
  media: {
    allowed_extensions: ["png", ".pdf"],
    allowed_mime_types: ["image/png", "application/pdf"],
  },
});

assert.equal(policy.maxBytes, 10 * 1024 * 1024);
assert.equal(policy.maxParallelUploads, 3);
assert.deepEqual(policy.allowedExtensions, ["png", "pdf"]);
assert.equal(formatUploadLimit(10 * 1024 * 1024), "10 MB");

assert.deepEqual(
  validateComposerUpload({ name: "photo.png", size: 1024, type: "image/png" }, policy),
  { valid: true },
);
assert.match(
  validateComposerUpload({ name: "large.png", size: 11 * 1024 * 1024, type: "image/png" }, policy).message || "",
  /10 MB upload limit/i,
);
assert.match(
  validateComposerUpload({ name: "script.exe", size: 1024, type: "application/x-msdownload" }, policy).message || "",
  /not supported/i,
);
assert.match(
  validateComposerUpload({ name: "empty.pdf", size: 0, type: "application/pdf" }, policy).message || "",
  /empty/i,
);

rmSync(new URL("../.upload-test-build", import.meta.url), { recursive: true, force: true });
console.log("Upload policy core tests passed.");
