// AI Team #1 — Relay (Cloudflare Worker) · ម្ចាស់តែប៉ុណ្ណោះ (មិនផ្ញើទៅអតិថិជន)
// អតិថិជន (License AT1-…) → Relay នេះ → API key របស់ម្ចាស់ (Bayon + Google) → លទ្ធផលបកប្រែ
// key មិនដែលចេញទៅកុំព្យូទ័រអតិថិជនទេ · ការកំណត់ (Cloudflare → Worker → Settings → Variables and Secrets):
//   API_KEYS  (Secret)  key ទាំងអស់ បំបែកដោយ , ឬបន្ទាត់ថ្មី — bayon_sk_… និង/ឬ AIza…
//   REVOKED   (Text)    License ID ដែលដកសិទ្ធិ បំបែកដោយ , (ID បង្ហាញក្នុង license_admin)
//   PUBLIC_KEY(Text)    ជម្រើស — បើទទេ ប្រើតម្លៃខាងក្រោម (ដូច licensing.py)
const PUBLIC_KEY = "595f9addbea9c21acd0cbe2ad3a39c7c00a0ebf6a63dc6f30cc6f2bc4aca9be4";
const EPOCH = Date.UTC(2024, 0, 1);
const GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent";
const BAYON_URL = "https://bayongateway.com/v1/chat/completions";
const BAYON_MODELS = { "gemini-3.5-flash": "gemini-3.8-flash", "gemini-3.1-pro-preview": "gemini-3.7-pro",
                       "gemini-2.5-flash": "gemini-3-flash" };
const cooldown = new Map(); // key → ពេលដែលអាចប្រើវិញ (ms) — តាម isolate (ល្អបំផុតតាមដែលអាច)

// ---------------- License (ដូច licensing.py) ----------------
function b64url(s) {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  s += "=".repeat((4 - (s.length % 4)) % 4);
  return Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
}
const hex = (b) => [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
const unhex = (h) => Uint8Array.from(h.match(/../g).map((x) => parseInt(x, 16)));

function b32decode(code) {
  const alpha = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "", out = [];
  for (const ch of code.toUpperCase().replace(/[^A-Z2-7]/g, "")) bits += alpha.indexOf(ch).toString(2).padStart(5, "0");
  for (let i = 0; i + 8 <= bits.length; i += 8) out.push(parseInt(bits.slice(i, i + 8), 2));
  return Uint8Array.from(out);
}

async function checkLicense(lic, machineCode, env) {
  let text = (lic || "").replace(/\s+/g, "");
  if (text.toUpperCase().startsWith("AT1-")) text = text.slice(4);
  let blob;
  try { blob = b64url(text); } catch { throw new Fail(401, "License មិនត្រឹមត្រូវ"); }
  if (blob.length < 15 + 64) throw new Fail(401, "License មិនត្រឹមត្រូវ (ខ្លីពេក)");
  const payload = blob.slice(0, -64), sig = blob.slice(-64);
  const key = await crypto.subtle.importKey("raw", unhex(env.PUBLIC_KEY || PUBLIC_KEY), { name: "Ed25519" },
                                            false, ["verify"]);
  if (!(await crypto.subtle.verify({ name: "Ed25519" }, key, sig, payload)))
    throw new Fail(401, "License មិនត្រឹមត្រូវ (ហត្ថលេខាខុស)");
  const dv = new DataView(payload.buffer, payload.byteOffset);
  if (payload[0] !== 1) throw new Fail(401, "License ជាកំណែថ្មីជាង Relay");
  const id = hex(payload.slice(1, 5)), machine = payload.slice(5, 11), expires = dv.getUint16(13);
  const name = new TextDecoder().decode(payload.slice(15));
  const revoked = (env.REVOKED || "").split(/[\s,]+/).filter(Boolean);
  if (revoked.includes(id)) throw new Fail(403, "License នេះត្រូវបានដកសិទ្ធិហើយ — សូមទាក់ទងម្ចាស់កម្មវិធី");
  const today = Math.floor((Date.now() - EPOCH) / 86400000);
  if (expires && today > expires) throw new Fail(403, "License នេះផុតកំណត់ហើយ — សូមទាក់ទងម្ចាស់កម្មវិធី");
  if (machine.some((b) => b !== 0)) {
    const m = b32decode(machineCode || "");
    if (m.length !== 6 || hex(m) !== hex(machine)) throw new Fail(403, "License នេះសម្រាប់កុំព្យូទ័រផ្សេង");
  }
  return { id, name };
}

class Fail extends Error {
  constructor(status, msg) { super(msg); this.status = status; }
}

// ---------------- Upstream ----------------
function lowerSchema(s) {
  if (Array.isArray(s)) return s.map(lowerSchema);
  if (s && typeof s === "object")
    return Object.fromEntries(Object.entries(s).map(([k, v]) => [k, k === "type" && typeof v === "string"
      ? v.toLowerCase() : lowerSchema(v)]));
  return s;
}

function jsonText(t) {
  t = t.trim();
  const fence = t.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (fence) t = fence[1].trim();
  const starts = [t.indexOf("["), t.indexOf("{")].filter((i) => i >= 0);
  return starts.length ? t.slice(Math.min(...starts)) : t;
}

async function callGoogle(key, model, body) {
  const r = await fetch(GEMINI_URL.replace("{model}", encodeURIComponent(model)), {
    method: "POST", headers: { "Content-Type": "application/json", "x-goog-api-key": key }, body: JSON.stringify(body),
  });
  const text = await r.text();
  if (!r.ok) throw new Fail(r.status, `Google ${r.status}: ${text.slice(0, 300)}`);
  return JSON.parse(text);
}

async function callBayon(key, model, body) {
  const config = body.generationConfig || {};
  if ((config.responseModalities || []).includes("AUDIO")) throw new Fail(400, "Bayon មិនគាំទ្រ Gemini TTS");
  const content = [];
  for (const part of body.contents[0].parts) {
    if (part.inlineData) {
      const fmt = { "audio/wav": "wav", "audio/x-wav": "wav" }[part.inlineData.mimeType] || "mp3";
      content.push({ type: "input_audio", input_audio: { data: part.inlineData.data, format: fmt } });
    } else if (part.text !== undefined) content.push({ type: "text", text: part.text });
  }
  const wantJson = config.responseMimeType === "application/json";
  if (wantJson)
    content.push({ type: "text", text: "Reply with ONLY valid JSON (no markdown) matching this JSON schema: " +
                                       JSON.stringify(lowerSchema(config.responseSchema || {})) });
  const r = await fetch(BAYON_URL, {
    method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}` },
    body: JSON.stringify({ model: BAYON_MODELS[model] || model, messages: [{ role: "user", content }],
                           reasoning_effort: "low" }),
  });
  const text = await r.text();
  if (!r.ok) throw new Fail(r.status, `Bayon ${r.status}: ${text.slice(0, 300)}`);
  const out = JSON.parse(text).choices?.[0]?.message?.content || "";
  return { candidates: [{ content: { parts: [{ text: wantJson ? jsonText(out) : out }] } }] };
}

// key មានបញ្ហា → ឈប់ប្រើមួយរយៈ ហើយសាក key បន្ទាប់
function rest(e) {
  const m = String(e.message);
  if (e.status === 429 || /quota|rate.?limit|RESOURCE_EXHAUSTED/i.test(m)) return 60e3;
  if ([401, 402, 403].includes(e.status) || /invalid|expired|balance|insufficient|API_KEY/i.test(m)) return 3600e3;
  if (!e.status || e.status >= 500) return 20e3;
  return 0; // 400 = សំណើខុស (មិនមែន key) → ប្រាប់អតិថិជន
}

async function generate(env, model, body) {
  const keys = (env.API_KEYS || "").split(/[\s,]+/).filter(Boolean);
  if (!keys.length) throw new Fail(503, "Relay មិនទាន់មាន API key — ម្ចាស់ត្រូវដាក់ API_KEYS");
  const audio = (body.generationConfig?.responseModalities || []).includes("AUDIO");
  const now = Date.now(), start = Math.floor(Math.random() * keys.length);
  let order = keys.map((_, i) => keys[(start + i) % keys.length]).filter((k) => !audio || k.startsWith("AIza"));
  const ready = order.filter((k) => (cooldown.get(k) || 0) <= now);
  order = ready.length ? ready : order;
  const errors = [];
  for (const key of order.slice(0, 6)) { // សាកយ៉ាងច្រើន 6 key ក្នុងមួយសំណើ
    try {
      return key.startsWith("AIza") ? await callGoogle(key, model, body) : await callBayon(key, model, body);
    } catch (e) {
      errors.push(`${mask(key)}: ${reason(e)}`);
      const wait = rest(e);
      if (!wait) throw e;
      cooldown.set(key, Date.now() + wait);
    }
  }
  throw new Fail(503, `key ទាំងអស់កំពុងមានបញ្ហា — ${errors.join(" | ").slice(0, 600)}`);
}

const mask = (k) => `${k.slice(0, 8)}…${k.slice(-4)}`;

// មូលហេតុខ្លីៗ សម្រាប់ម្ចាស់ (មើលក្នុងកម្មវិធី ឬ 🩺 ពិនិត្យ)
function reason(e) {
  const m = String(e.message);
  if (e.status === 402 || /insufficient|balance|credit/i.test(m)) return "អស់ credit (បញ្ចូលលុយ)";
  if (e.status === 429 || /quota|RESOURCE_EXHAUSTED|rate.?limit/i.test(m)) return "អស់ quota / ច្រើនពេក";
  if ([401, 403].includes(e.status) || /invalid|API_KEY/i.test(m)) return "key ខុស ឬត្រូវបិទ";
  return m.slice(0, 120);
}

const reply = (status, obj) => new Response(JSON.stringify(obj), {
  status, headers: { "Content-Type": "application/json; charset=utf-8" } });

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    try {
      if (url.pathname === "/" || url.pathname === "/health") {
        const keys = (env.API_KEYS || "").split(/[\s,]+/).filter(Boolean);
        return reply(200, { ok: true, service: "AI Team #1 relay", keys: keys.length > 0,
                            bayon: keys.filter((k) => k.startsWith("bayon_sk_")).length,
                            google: keys.filter((k) => k.startsWith("AIza")).length,
                            other: keys.filter((k) => !/^(bayon_sk_|AIza)/.test(k)).length });
      }
      const who = await checkLicense(req.headers.get("X-License"), req.headers.get("X-Machine"), env);
      if (url.pathname === "/v1/check") return reply(200, { ok: true, name: who.name });
      if (url.pathname === "/v1/generate" && req.method === "POST") {
        const { model, body } = await req.json();
        if (!model || !body?.contents) throw new Fail(400, "សំណើខុស");
        return reply(200, await generate(env, model, body));
      }
      return reply(404, { error: { message: "not found" } });
    } catch (e) {
      return reply(e.status && e.status < 600 ? e.status : 500, { error: { message: String(e.message || e) } });
    }
  },
};
