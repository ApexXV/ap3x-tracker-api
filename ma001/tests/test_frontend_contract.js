#!/usr/bin/env node

"use strict";

const fs = require("fs");
const path = require("path");

const packageRoot = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(packageRoot, "payload", "local-ops-canary.js"), "utf8");

function requireText(fragment, message) {
  if (!source.includes(fragment)) throw new Error(message || `missing: ${fragment}`);
}

requireText('workloads.contract === "MA001"', "MA001 contract discriminator missing");
requireText("workloads.display?.title", "backend display title is not consumed");
requireText("workloads.display?.detail", "backend display detail is not consumed");
requireText('setText("workloads-title", "MEDIA PARTIAL")', "partial state wording missing");
requireText('setText("workloads-title", "MEDIA STALE")', "stale state wording missing");
requireText('setText("workloads-title", workloads.active ? "WORKLOADS ACTIVE"', "legacy fallback removed");
requireText('safeFetch("/api/ops/workloads")', "same-origin workload endpoint changed");
requireText("renderWorkloads(data.workloads)", "workload render call changed");

if ((source.match(/workloads\.contract === "MA001"/g) || []).length !== 1) {
  throw new Error("MA001 renderer must appear exactly once");
}

console.log("PASS: Local Ops MA001 renderer contract");
