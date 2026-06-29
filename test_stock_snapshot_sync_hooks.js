const fs = require("fs");
const path = require("path");
const test = require("node:test");
const assert = require("node:assert/strict");

function extractFunction(source, name) {
  const needle = `async def ${name}(`;
  const start = source.indexOf(needle);
  if (start === -1) throw new Error(`${name} not found`);
  const next = source.indexOf("\n    async def ", start + needle.length);
  return source.slice(start, next === -1 ? source.length : next);
}

function indentation(line) {
  const match = line.match(/^ */);
  return match ? match[0].length : 0;
}

test("analytics stocks captures daily stock snapshot while DB session is active", () => {
  const source = fs.readFileSync(path.resolve(__dirname, "src", "sync_manager.py"), "utf8");
  const fn = extractFunction(source, "sync_analytics_stocks");
  const lines = fn.split(/\r?\n/);
  const snapshotLine = lines.find((line) => line.includes("await self._capture_stock_daily_snapshot(session)"));
  assert.ok(snapshotLine, "sync_analytics_stocks must capture stock_daily_snapshots");

  const sessionLine = lines.find((line) => line.includes("async with db_manager.session() as session:"));
  assert.ok(sessionLine, "sync_analytics_stocks must use db session");
  assert.ok(
    indentation(snapshotLine) >= indentation(sessionLine) + 4,
    "snapshot capture must be indented inside the active session context body",
  );
});

test("fbs warehouse stock sync captures daily stock snapshot after FBS update", () => {
  const source = fs.readFileSync(path.resolve(__dirname, "src", "sync_manager.py"), "utf8");
  const fn = extractFunction(source, "sync_fbs_warehouse_stocks");
  const lines = fn.split(/\r?\n/);
  const snapshotLine = lines.find((line) => line.includes("await self._capture_stock_daily_snapshot(session)"));
  assert.ok(snapshotLine, "sync_fbs_warehouse_stocks must refresh stock_daily_snapshots");

  const sessionLine = lines.find((line) => line.includes("async with db_manager.session() as session:"));
  assert.ok(sessionLine, "sync_fbs_warehouse_stocks must use db session");
  assert.ok(
    indentation(snapshotLine) >= indentation(sessionLine) + 4,
    "snapshot capture must be indented inside the active session context body",
  );
});
