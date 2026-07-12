#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const packagePath = path.join(__dirname, "..", "package.json");
const packageJson = JSON.parse(fs.readFileSync(packagePath, "utf8"));
const range = packageJson.engines && packageJson.engines.node;
const version = process.versions.node;

function parseVersion(versionText) {
  const parts = versionText.split(".").map((part) => Number.parseInt(part, 10));
  if (parts.length < 1 || parts.some((part) => Number.isNaN(part))) {
    throw new Error(`invalid Node version: ${versionText}`);
  }
  return [parts[0], parts[1] || 0, parts[2] || 0];
}

function compareVersions(leftText, rightText) {
  const left = parseVersion(leftText);
  const right = parseVersion(rightText);
  for (let i = 0; i < left.length; i += 1) {
    if (left[i] < right[i]) {
      return -1;
    }
    if (left[i] > right[i]) {
      return 1;
    }
  }
  return 0;
}

function satisfiesComparator(versionText, comparator) {
  const match = comparator.match(/^(>=|>|<=|<|=)?\s*(\d+(?:\.\d+){0,2})$/);
  if (!match) {
    throw new Error(
      `unsupported engines.node comparator ${JSON.stringify(comparator)}`
    );
  }
  const operator = match[1] || "=";
  const expected = match[2];
  const comparison = compareVersions(versionText, expected);
  switch (operator) {
    case ">=":
      return comparison >= 0;
    case ">":
      return comparison > 0;
    case "<=":
      return comparison <= 0;
    case "<":
      return comparison < 0;
    case "=":
      return comparison === 0;
    default:
      throw new Error(`unsupported engines.node operator ${operator}`);
  }
}

function satisfiesRange(versionText, rangeText) {
  return rangeText
    .split(/\s+/)
    .filter((part) => part.length > 0)
    .every((part) => satisfiesComparator(versionText, part));
}

if (!range) {
  console.error("ERROR: vscode-geno/package.json must declare engines.node");
  process.exit(1);
}

if (!satisfiesRange(version, range)) {
  console.error(
    `ERROR: Node ${version} does not satisfy vscode-geno/package.json ` +
      `engines.node (${range}). Use a supported Node runtime.`
  );
  process.exit(1);
}

console.log(`Node ${version} satisfies engines.node ${range}`);
