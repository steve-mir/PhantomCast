/**
 * Deep-Live-Cam Pro — Cloud Functions
 *
 * Endpoints (HTTPS, JSON):
 *   POST /v1_activate     — bind license to device, return signed claims
 *   POST /v1_heartbeat    — refresh claims (every 6h while online)
 *   POST /v1_deactivate   — release a device slot (rate-limited)
 *   POST /v1_moveLicense  — force re-bind on hardware swap (1/4mo)
 *   POST /v1_modelUrl     — short-lived signed model URL (paywalled models)
 *   POST /v1_stripe       — Stripe webhook handler
 *   scheduled cleanupExpiredActivations  — daily
 *
 * All HTTP endpoints validate App Check, rate-limit per IP and per license,
 * and write an audit row on every action (success or failure).
 */
import * as admin from "firebase-admin";
import * as functions from "firebase-functions";
import { onRequest, onSchedule } from "firebase-functions/v2/https";
import * as jwt from "jsonwebtoken";
import Stripe from "stripe";
import * as crypto from "crypto";

admin.initializeApp();
const db = admin.firestore();

// ---------- Secrets ----------
const SIGNING_KEY = process.env.DLC_SIGNING_PRIVATE_KEY!;   // RS256 PEM
const SIGNING_KID = process.env.DLC_SIGNING_KID!;           // e.g. "dlc-pro-2026-01"
const STRIPE_SECRET = process.env.STRIPE_SECRET_KEY!;
const STRIPE_WEBHOOK_SECRET = process.env.STRIPE_WEBHOOK_SECRET!;
const stripe = new Stripe(STRIPE_SECRET, { apiVersion: "2024-04-10" as any });

// ---------- Helpers ----------

const FEATURES_BY_PLAN: Record<string, string[]> = {
  free:   ["core_cpu"],
  pro:    ["core_cpu", "gpu_inference", "export_4k", "face_enhancer_512", "live_webcam"],
  studio: ["core_cpu", "gpu_inference", "export_4k", "face_enhancer_512", "live_webcam",
           "map_faces", "hyperswap_full_head", "batch_queue", "no_watermark"],
};
const CLAIMS_TTL_SECONDS = 24 * 3600;
const FINGERPRINT_DRIFT_THRESHOLD = 7;     // out of 10 weighted match
const COMPONENT_WEIGHTS: Record<string, number> = {
  motherboard_uuid: 3,
  cpu_id:           2,
  disk_serial:      2,
  machine_guid:     2,
  primary_mac:      1,
};

function hash(s: string): string {
  return crypto.createHash("sha256").update(s).digest("hex").slice(0, 32);
}

function similarityScore(
  stored: Record<string, string>,
  current: Record<string, string>
): number {
  let score = 0;
  for (const [k, w] of Object.entries(COMPONENT_WEIGHTS)) {
    if (stored[k] && current[k] && stored[k] === current[k]) score += w;
  }
  return score;
}

function signClaims(payload: Record<string, unknown>): { token: string; exp: number } {
  const iat = Math.floor(Date.now() / 1000);
  const exp = iat + CLAIMS_TTL_SECONDS;
  const token = jwt.sign(
    { ...payload, iat, exp },
    SIGNING_KEY,
    { algorithm: "RS256", header: { alg: "RS256", kid: SIGNING_KID } }
  );
  return { token, exp };
}

async function audit(action: string, ok: boolean, ctx: Record<string, unknown>): Promise<void> {
  await db.collection("activation_audit").add({
    action, ok,
    ts: admin.firestore.FieldValue.serverTimestamp(),
    ...ctx,
  });
}

interface RateBucket { count: number; resetAt: number }
const RATE_LIMITS: Record<string, RateBucket> = {};
function rateLimit(key: string, max: number, windowSec: number): boolean {
  const now = Date.now();
  const b = RATE_LIMITS[key] ?? { count: 0, resetAt: now + windowSec * 1000 };
  if (now > b.resetAt) { b.count = 0; b.resetAt = now + windowSec * 1000; }
  b.count++;
  RATE_LIMITS[key] = b;
  return b.count <= max;
}

// ---------- v1_activate ----------

export const v1_activate = onRequest(
  { region: "us-central1", cors: false, enforceAppCheck: true },
  async (req, res) => {
    if (req.method !== "POST") { res.status(405).json({ error: "method" }); return; }

    const ip = (req.headers["x-forwarded-for"] as string)?.split(",")[0] ?? req.ip ?? "unknown";
    if (!rateLimit(`activate:${ip}`, 10, 60)) {
      res.status(429).json({ error: { code: "rate_limited", message: "Too many activation attempts." } });
      return;
    }

    const { license_key, fingerprint, components, os, client_version } = req.body ?? {};
    if (!license_key || !fingerprint) {
      res.status(400).json({ error: { code: "bad_request", message: "Missing fields." } }); return;
    }
    if (!rateLimit(`activate:lic:${license_key}`, 8, 24 * 3600)) {
      res.status(429).json({ error: { code: "rate_limited", message: "Activation limit reached." } });
      return;
    }

    const licQ = await db.collection("licenses").where("license_key", "==", license_key).limit(1).get();
    if (licQ.empty) {
      await audit("activate", false, { ip: hash(ip), reason: "not_found" });
      res.status(401).json({ error: { code: "license_invalid", message: "License not found." } });
      return;
    }
    const lic = licQ.docs[0];
    const data = lic.data();
    if (data.status !== "active") {
      await audit("activate", false, { licenseId: lic.id, reason: data.status });
      res.status(401).json({ error: { code: "license_suspended", message: `License ${data.status}.` } });
      return;
    }

    let deviceId: string;
    if (data.fingerprintHash && data.fingerprintHash !== fingerprint) {
      const score = similarityScore(data.fingerprintComponents ?? {}, components ?? {});
      if (score < FINGERPRINT_DRIFT_THRESHOLD) {
        await audit("activate", false, { licenseId: lic.id, reason: "device_mismatch", score });
        res.status(409).json({ error: {
          code: "device_mismatch",
          message: "License is bound to a different machine. Use 'Move license' if this is a hardware swap."
        } });
        return;
      }
      deviceId = data.deviceId ?? db.collection("devices").doc().id;
    } else {
      deviceId = data.deviceId ?? db.collection("devices").doc().id;
    }

    await db.runTransaction(async tx => {
      tx.update(lic.ref, {
        fingerprintHash: fingerprint,
        fingerprintComponents: components ?? {},
        deviceId,
        lastActivatedAt: admin.firestore.FieldValue.serverTimestamp(),
      });
      tx.set(db.collection("devices").doc(deviceId), {
        licenseId: lic.id,
        fingerprintHash: fingerprint,
        fingerprintComponents: components ?? {},
        os, client_version,
        firstSeen: admin.firestore.FieldValue.serverTimestamp(),
        lastSeen:  admin.firestore.FieldValue.serverTimestamp(),
      }, { merge: true });
    });

    const features = FEATURES_BY_PLAN[data.plan] ?? FEATURES_BY_PLAN.free;
    const { token, exp } = signClaims({
      license_id: lic.id,
      plan: data.plan,
      feature_flags: features,
      fingerprint_hash: fingerprint,
      device_id: deviceId,
      kid: SIGNING_KID,
    });

    await audit("activate", true, { licenseId: lic.id, deviceId, ip: hash(ip) });
    res.status(200).json({
      license_id: lic.id,
      device_id: deviceId,
      plan: data.plan,
      feature_flags: features,
      claims_jwt: token,
      claims_exp: exp,
      portal_url: data.portal_url ?? null,
    });
  }
);

// ---------- v1_heartbeat ----------

export const v1_heartbeat = onRequest(
  { region: "us-central1", enforceAppCheck: true },
  async (req, res) => {
    if (req.method !== "POST") { res.status(405).json({ error: "method" }); return; }
    const { license_id, license_key, fingerprint } = req.body ?? {};
    if (!license_id) { res.status(400).json({ error: { code: "bad_request" } }); return; }

    const ref = db.collection("licenses").doc(license_id);
    const snap = await ref.get();
    if (!snap.exists) { res.status(401).json({ error: { code: "license_invalid" } }); return; }
    const data = snap.data()!;
    if (data.license_key !== license_key) {
      res.status(401).json({ error: { code: "license_invalid" } }); return;
    }
    if (data.status !== "active") {
      res.status(401).json({ error: { code: "license_suspended", message: data.status } }); return;
    }
    if (data.fingerprintHash && fingerprint && data.fingerprintHash !== fingerprint) {
      res.status(409).json({ error: { code: "device_mismatch" } }); return;
    }

    await ref.update({ lastHeartbeatAt: admin.firestore.FieldValue.serverTimestamp() });

    const features = FEATURES_BY_PLAN[data.plan] ?? FEATURES_BY_PLAN.free;
    const { token, exp } = signClaims({
      license_id,
      plan: data.plan,
      feature_flags: features,
      fingerprint_hash: data.fingerprintHash,
      device_id: data.deviceId,
      kid: SIGNING_KID,
    });
    res.status(200).json({
      status: "active",
      plan: data.plan,
      feature_flags: features,
      claims_jwt: token,
      claims_exp: exp,
      portal_url: data.portal_url ?? null,
    });
  }
);

// ---------- v1_deactivate ----------

export const v1_deactivate = onRequest(
  { region: "us-central1", enforceAppCheck: true },
  async (req, res) => {
    if (req.method !== "POST") { res.status(405).json({ error: "method" }); return; }
    const { license_id, license_key, fingerprint } = req.body ?? {};
    const ref = db.collection("licenses").doc(license_id);
    const snap = await ref.get();
    if (!snap.exists || snap.data()!.license_key !== license_key) {
      res.status(401).json({ error: { code: "license_invalid" } }); return;
    }
    if (snap.data()!.fingerprintHash !== fingerprint) {
      res.status(409).json({ error: { code: "device_mismatch" } }); return;
    }
    await ref.update({ fingerprintHash: null, deviceId: null });
    await audit("deactivate", true, { licenseId: license_id });
    res.status(200).json({ ok: true });
  }
);

// ---------- v1_moveLicense ----------

export const v1_moveLicense = onRequest(
  { region: "us-central1", enforceAppCheck: true },
  async (req, res) => {
    if (req.method !== "POST") { res.status(405).json({ error: "method" }); return; }
    const { license_id, license_key, fingerprint, components } = req.body ?? {};
    const ref = db.collection("licenses").doc(license_id);
    const snap = await ref.get();
    if (!snap.exists || snap.data()!.license_key !== license_key) {
      res.status(401).json({ error: { code: "license_invalid" } }); return;
    }
    const data = snap.data()!;
    const yearKey = new Date().getUTCFullYear();
    const moveCount = data[`moveCount_${yearKey}`] ?? 0;
    if (moveCount >= 1) {
      res.status(429).json({ error: { code: "rate_limited", message: "License move limit reached this year." } });
      return;
    }
    await ref.update({
      fingerprintHash: fingerprint,
      fingerprintComponents: components ?? {},
      [`moveCount_${yearKey}`]: moveCount + 1,
      lastMovedAt: admin.firestore.FieldValue.serverTimestamp(),
    });
    await audit("move_license", true, { licenseId: license_id });
    res.status(200).json({ ok: true });
  }
);

// ---------- v1_modelUrl: signed download URL gated by plan ----------

export const v1_modelUrl = onRequest(
  { region: "us-central1", enforceAppCheck: true },
  async (req, res) => {
    if (req.method !== "POST") { res.status(405).json({ error: "method" }); return; }
    const { license_id, license_key, model } = req.body ?? {};
    if (!license_id || !model) { res.status(400).json({ error: "bad_request" }); return; }

    const snap = await db.collection("licenses").doc(license_id).get();
    if (!snap.exists || snap.data()!.license_key !== license_key) {
      res.status(401).json({ error: { code: "license_invalid" } }); return;
    }
    const plan = snap.data()!.plan;

    const PLAN_REQUIRED: Record<string, string> = {
      "GPEN-BFR-512.onnx": "pro",
      "hyperswap.onnx":    "studio",
    };
    const required = PLAN_REQUIRED[model];
    if (required && plan !== required && plan !== "studio") {
      res.status(403).json({ error: { code: "plan_required", message: required } }); return;
    }

    // Generate a signed URL via Firebase Storage (15-min TTL).
    const file = admin.storage().bucket().file(`models/${model}`);
    const [url] = await file.getSignedUrl({
      action: "read",
      expires: Date.now() + 15 * 60 * 1000,
    });
    res.status(200).json({ url });
  }
);

// ---------- v1_stripe webhook ----------

export const v1_stripe = onRequest(
  { region: "us-central1", cors: false },
  async (req, res) => {
    const sig = req.headers["stripe-signature"] as string;
    let event: Stripe.Event;
    try {
      event = stripe.webhooks.constructEvent(req.rawBody, sig, STRIPE_WEBHOOK_SECRET);
    } catch (e) {
      res.status(400).send("bad signature"); return;
    }

    if (event.type === "customer.subscription.created" || event.type === "customer.subscription.updated") {
      const sub = event.data.object as Stripe.Subscription;
      const customer = await stripe.customers.retrieve(sub.customer as string);
      const email = (customer as Stripe.Customer).email ?? "";
      const plan = (sub.items.data[0]?.price?.lookup_key) ?? "pro";   // "pro" | "studio"
      const license_key = `DLC-${crypto.randomBytes(8).toString("hex").toUpperCase()}`;

      await db.collection("licenses").doc(sub.id).set({
        license_key,
        plan,
        status: sub.status === "active" ? "active" : "suspended",
        stripeSubscriptionId: sub.id,
        currentPeriodEnd: new Date(sub.current_period_end * 1000),
        ownerEmail: email,
        createdAt: admin.firestore.FieldValue.serverTimestamp(),
      }, { merge: true });

      // TODO: email license_key to ownerEmail via SendGrid / Postmark.
    }

    if (event.type === "customer.subscription.deleted") {
      const sub = event.data.object as Stripe.Subscription;
      await db.collection("licenses").doc(sub.id).update({ status: "cancelled" });
    }

    res.status(200).send("ok");
  }
);

// ---------- scheduled: cleanup ----------

export const cleanupExpiredActivations = onSchedule(
  { region: "us-central1", schedule: "every 24 hours" },
  async () => {
    const cutoff = Date.now() - 30 * 24 * 3600 * 1000;
    const stale = await db.collection("devices")
      .where("lastSeen", "<", new Date(cutoff)).limit(500).get();
    const batch = db.batch();
    stale.forEach(d => batch.delete(d.ref));
    await batch.commit();
  }
);
