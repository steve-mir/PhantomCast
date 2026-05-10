#!/usr/bin/env node
/**
 * One-shot migration: rotate license_key prefix from DLC- → PC- on the
 * Phantom Cast Firestore `licenses` collection.
 *
 * Why: pre-rebrand keys were minted with DLC- prefix. After the rebrand, new
 * keys are PC-. This script aligns existing rows with the new prefix so
 * every license carries a consistent brand.
 *
 * Backwards compat: the v1_activate Cloud Function tolerates either prefix
 * (alt-prefix fallback in src/index.ts), so existing customers continue to
 * activate fine even *without* this migration. Run it only if you want
 * uniform PC- prefixes across the whole inventory — and remember to email
 * affected customers their new keys, since their copies still say DLC-.
 *
 * --------------------------------------------------------------------
 * Setup:
 *   1. From repo root: `cd backend/functions && npm install` (if you
 *      haven't already; uses the existing firebase-admin dep).
 *   2. Auth a service account with Firestore write access:
 *        export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa-key.json
 *      …or run from a machine where `gcloud auth application-default
 *      login` has been done against the diivix1 project.
 *   3. Dry-run first to inspect the change set:
 *        node backend/tools/migrate_license_prefix.js --dry-run
 *   4. When the printed mapping looks right:
 *        node backend/tools/migrate_license_prefix.js --commit
 *
 * Optional flags:
 *   --only PC-XXXX,DLC-YYYY    only migrate these specific keys
 *   --project diivix1          override the Firebase project ID
 * --------------------------------------------------------------------
 */
"use strict";

const admin = require("firebase-admin");

const argv = process.argv.slice(2);
const flag = (name) => argv.includes(name);
const value = (name) => {
  const i = argv.indexOf(name);
  return i >= 0 && i + 1 < argv.length ? argv[i + 1] : null;
};

const COMMIT = flag("--commit");
const DRY = !COMMIT || flag("--dry-run");
const PROJECT_ID = value("--project") || process.env.FIREBASE_PROJECT_ID || "diivix1";
const ONLY = (value("--only") || "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

if (!COMMIT && !flag("--dry-run")) {
  console.error("Refusing to run without --dry-run or --commit.");
  console.error("  dry-run: prints the change set without writing");
  console.error("  commit:  applies the migration in a single batched write");
  process.exit(2);
}

admin.initializeApp({ projectId: PROJECT_ID });
const db = admin.firestore();

(async () => {
  const snap = await db.collection("licenses").get();
  const candidates = [];

  snap.forEach((doc) => {
    const data = doc.data();
    const oldKey = data.license_key;
    if (typeof oldKey !== "string" || !oldKey.startsWith("DLC-")) return;
    if (ONLY.length && !ONLY.includes(oldKey)) return;
    const newKey = "PC-" + oldKey.slice(4);
    candidates.push({ id: doc.id, oldKey, newKey, plan: data.plan, status: data.status });
  });

  console.log(`Project:   ${PROJECT_ID}`);
  console.log(`Mode:      ${DRY ? "DRY-RUN (no writes)" : "COMMIT"}`);
  console.log(`Filter:    ${ONLY.length ? ONLY.join(", ") : "all DLC- keys"}`);
  console.log(`Candidates: ${candidates.length}`);
  console.log("");

  if (candidates.length === 0) {
    console.log("Nothing to migrate. Exiting.");
    process.exit(0);
  }

  for (const c of candidates) {
    console.log(`  ${c.oldKey}  →  ${c.newKey}    (license_id=${c.id}, plan=${c.plan}, status=${c.status})`);
  }

  if (DRY) {
    console.log("");
    console.log("Dry-run only. Re-run with --commit to apply.");
    process.exit(0);
  }

  // Single batched write so the whole migration is atomic.
  const batch = db.batch();
  for (const c of candidates) {
    batch.update(db.collection("licenses").doc(c.id), {
      license_key: c.newKey,
      previous_license_key: c.oldKey,
      prefix_migrated_at: admin.firestore.FieldValue.serverTimestamp(),
    });
  }
  await batch.commit();
  console.log("");
  console.log(`✓ Migrated ${candidates.length} license(s).`);
  console.log("Remember to email each affected customer their new PC- key.");
  process.exit(0);
})().catch((err) => {
  console.error("Migration failed:", err);
  process.exit(1);
});
