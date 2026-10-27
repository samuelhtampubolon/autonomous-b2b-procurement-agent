/**
 * Client-side port of the backend's deterministic rules.
 *
 * - computeMatch        <-> src/services/matching_engine.py::compute_match
 * - assessUntrustedText <-> src/security.py::assess_untrusted_text
 * - searchClauses       <-> src/services/contract_rag.py (TF-IDF cosine)
 * - ledger helpers      <-> src/services/audit_ledger.py (SHA-256 hash chain)
 *
 * tests/test_docs_parity.py runs this file under Node and asserts it agrees with the
 * Python implementation, so the showcase cannot silently drift from the real engine.
 */
(function (root) {
  "use strict";

  const PA = (root.PA = root.PA || {});
  const S = () =>
    PA.SETTINGS || { varianceTolerancePct: 2.0, highValueThresholdUsd: 10000, cpoApprovalThresholdUsd: 25000 };

  const round = (value, digits) => {
    const f = Math.pow(10, digits);
    return Math.round((value + Number.EPSILON) * f) / f;
  };
  const usd = (value) =>
    "$" + value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  // -------------------------------------------------------------------------
  // Deterministic 3-way match
  // -------------------------------------------------------------------------
  const NON_INJECTION_FLAGS = ["duplicate_billing_suspected", "inactive_vendor"];

  function computeMatch(input) {
    const settings = S();
    const orderedQty = Number(input.orderedQty);
    const billedQty = Number(input.billedQty);
    const agreedPrice = Number(input.agreedPrice);
    const billedPrice = Number(input.billedPrice);
    const daysLate = Math.max(0, Math.floor(Number(input.daysLate) || 0));
    const flags = input.riskFlags || [];

    const qtyMatched = orderedQty === billedQty;
    const variancePct = agreedPrice > 0 ? round(((billedPrice - agreedPrice) / agreedPrice) * 100, 4) : 0;
    const subtotal = round(billedQty * billedPrice, 2);
    const appliedPenaltyPct = Math.min(Number(input.maxCap), daysLate * Number(input.dailyRate));
    const slaPenalty = round(subtotal * appliedPenaltyPct, 2);
    const finalPayable = round(Math.max(0, subtotal - slaPenalty), 2);

    const reasons = [];
    const injectionFlags = flags.filter((f) => !NON_INJECTION_FLAGS.includes(f));
    if (injectionFlags.length) {
      reasons.push(
        "Security hold: untrusted vendor notes triggered prompt-injection firewall (" + injectionFlags.join(", ") + ")"
      );
    }
    if (Math.abs(variancePct) > settings.varianceTolerancePct) {
      const sign = variancePct >= 0 ? "+" : "-";
      reasons.push(
        `Price variance ${sign}${Math.abs(variancePct).toFixed(2)}% exceeds threshold of ±${settings.varianceTolerancePct.toFixed(1)}%`
      );
    }
    if (subtotal > settings.highValueThresholdUsd) {
      reasons.push(
        `High-value invoice (${usd(subtotal)}) exceeds human approval threshold of ${usd(settings.highValueThresholdUsd)}`
      );
    }
    if (!qtyMatched) {
      reasons.push(`Quantity mismatch: Billed ${billedQty} vs Ordered ${orderedQty}`);
    }
    if (flags.includes("duplicate_billing_suspected")) {
      reasons.push("Possible duplicate billing: this PO already has a settled invoice");
    }
    if (flags.includes("inactive_vendor")) {
      reasons.push("Vendor master record is inactive");
    }

    return {
      qtyMatched,
      variancePct,
      priceMatched: Math.abs(variancePct) <= 0.001,
      subtotal,
      daysLate,
      appliedPenaltyPct,
      slaPenalty,
      finalPayable,
      reasons,
      routingStatus: reasons.length ? "HITL_PENDING" : "AUTO_APPROVED",
      routingReason: reasons.length
        ? reasons.join("; ")
        : "Deterministic 3-way match passed: price variance, quantity, and contract terms verified",
    };
  }

  // -------------------------------------------------------------------------
  // Prompt-injection firewall
  // -------------------------------------------------------------------------
  const INJECTION_REDACTION = "[REDACTED_PROMPT_INJECTION_ATTEMPT]";
  const MARKUP_REDACTION = "[REDACTED_MARKUP]";
  const MAX_UNTRUSTED_TEXT_CHARS = 4000;

  const INJECTION_RULES = [
    ["instruction_override", /\b(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier|preceding)\s+(instructions?|rules|prompts?|context|directions)/gi],
    ["instruction_override", /\bdisregard\s+the\s+above/gi],
    ["role_spoofing", /\b(system|developer|assistant)\s*:/gi],
    ["chat_template_tokens", /<\|\s*(im_start|im_end|system|user|assistant|endoftext)\s*\|>/gi],
    ["chat_template_tokens", /\[\/?INST\]|<<\/?SYS>>|###\s*(system|instruction)s?\b/gi],
    ["persona_override", /\byou\s+are\s+now\s+(in\s+)?(developer\s+mode|dan\b|jailbroken|unrestricted|an?\s+admin)/gi],
    ["persona_override", /\b(act|pretend|behave)\s+as\s+(an?\s+|the\s+)?(admin(istrator)?|system|approver|cpo|root)\b/gi],
    ["guardrail_bypass", /\bbypass\s+(all\s+|the\s+|any\s+)?(rules|guidelines|checks|controls|validation|approvals?|guardrails)/gi],
    ["approval_override", /\bauto[- ]?approve\b(\s+always)?/gi],
    ["approval_override", /\boverride\s+(the\s+)?(decision|policy|approval|routing|threshold)/gi],
    ["approval_override", /\b(skip|avoid)\s+(the\s+)?(human|hitl|manual)\s+(review|approval)/gi],
    ["approval_override", /\bmark\s+(this\s+invoice\s+|it\s+)?as\s+approved/gi],
    ["new_instructions", /\b(new|updated|revised)\s+instructions?\s*:/gi],
    ["secret_exfiltration", /\b(reveal|print|show|output|leak|send)\s+(me\s+)?(the\s+|your\s+)?(system\s+prompt|secret|api[\s_-]*key|password|credentials?|token)/gi],
  ];
  const MARKUP_RULES = [
    ["html_markup", /<\s*\/?\s*(script|iframe|object|embed|svg|img|style|link|meta|form)\b[^>]*>?/gi],
    ["script_uri", /\b(javascript|vbscript)\s*:|\bdata\s*:\s*text\/html/gi],
    ["event_handler", /\bon[a-z]{3,}\s*=/gi],
  ];

  // Character classes are assembled from code points so this source file itself never
  // contains invisible or bidi-override characters (Trojan Source hygiene).
  const ch = (n) => String.fromCharCode(n);
  const range = (a, b) => ch(a) + "-" + ch(b);
  const CONTROL_RE = new RegExp("[" + range(0x00, 0x08) + ch(0x0b) + ch(0x0c) + range(0x0e, 0x1f) + range(0x7f, 0x9f) + "]", "g");
  const INVISIBLE_RE = new RegExp(
    "[" + range(0x200b, 0x200f) + range(0x202a, 0x202e) + range(0x2060, 0x2064) + range(0x2066, 0x2069) + ch(0xfeff) + "]",
    "g"
  );

  function assessUntrustedText(text) {
    if (!text) return { sanitized: "", flags: [] };
    const normalized = String(text).slice(0, MAX_UNTRUSTED_TEXT_CHARS).normalize("NFKC");
    let cleaned = normalized.replace(CONTROL_RE, "").replace(INVISIBLE_RE, "");
    const flags = [];
    if (cleaned.length !== normalized.length) flags.push("hidden_characters");

    for (const [name, pattern] of INJECTION_RULES) {
      pattern.lastIndex = 0;
      if (pattern.test(cleaned)) {
        flags.push(name);
        pattern.lastIndex = 0;
        cleaned = cleaned.replace(pattern, INJECTION_REDACTION);
      }
    }
    for (const [name, pattern] of MARKUP_RULES) {
      pattern.lastIndex = 0;
      if (pattern.test(cleaned)) {
        flags.push(name);
        pattern.lastIndex = 0;
        cleaned = cleaned.replace(pattern, MARKUP_REDACTION);
      }
    }
    cleaned = cleaned.split("```").join("'''").split("<|").join("").split("|>").join("");
    return { sanitized: cleaned.trim(), flags: Array.from(new Set(flags)).sort() };
  }

  // -------------------------------------------------------------------------
  // Contract RAG: TF-IDF cosine similarity (identical math to contract_rag.py)
  // -------------------------------------------------------------------------
  const STOPWORDS = new Set(
    "a an and are as at be by for from has have if in into is it its of on or per the to up upon what when which with within will".split(" ")
  );

  function tokenize(text) {
    const matches = String(text).toLowerCase().match(/\b[a-z0-9_-]{2,}\b/g) || [];
    return matches.filter((t) => !STOPWORDS.has(t));
  }

  function tfidfVector(tokens, vocab, idf) {
    const vec = new Float64Array(vocab.size);
    for (const t of tokens) {
      if (vocab.has(t)) vec[vocab.get(t)] += idf.get(t) || 1;
    }
    let norm = 0;
    for (const v of vec) norm += v * v;
    norm = Math.sqrt(norm);
    if (norm > 0) for (let i = 0; i < vec.length; i++) vec[i] /= norm;
    return vec;
  }

  function searchClauses(clauses, query, topK, vendorLookup) {
    const safeQuery = assessUntrustedText(query).sanitized;
    const queryTokens = tokenize(safeQuery);
    if (!queryTokens.length || !clauses.length) return [];

    const docs = clauses.map((c) => tokenize(`${c.category.split("_").join(" ")} ${c.title} ${c.content}`));
    const vocab = new Map();
    const df = new Map();
    docs.forEach((tokens) => {
      new Set(tokens).forEach((t) => df.set(t, (df.get(t) || 0) + 1));
      tokens.forEach((t) => vocab.has(t) || vocab.set(t, vocab.size));
    });
    queryTokens.forEach((t) => vocab.has(t) || vocab.set(t, vocab.size));
    const n = docs.length;
    const idf = new Map();
    vocab.forEach((_, t) => idf.set(t, Math.log((n + 1) / ((df.get(t) || 0) + 1)) + 1));

    const q = tfidfVector(queryTokens, vocab, idf);
    const qSet = new Set(queryTokens);
    return clauses
      .map((clause, i) => {
        const d = tfidfVector(docs[i], vocab, idf);
        let sim = 0;
        for (let k = 0; k < q.length; k++) sim += q[k] * d[k];
        return {
          clause,
          vendor: vendorLookup ? vendorLookup(clause.vendorId) : "",
          score: round(Math.max(0, sim), 4),
          matchedTerms: Array.from(new Set(docs[i].filter((t) => qSet.has(t)))),
        };
      })
      .sort((a, b) => b.score - a.score)
      .slice(0, topK);
  }

  // -------------------------------------------------------------------------
  // SHA-256 (synchronous, dependency-free; works on file:// and in Node)
  // -------------------------------------------------------------------------
  const K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ]);

  function sha256Hex(message) {
    const bytes = new TextEncoder().encode(message);
    const bitLen = bytes.length * 8;
    const padded = new Uint8Array(((bytes.length + 9 + 63) >> 6) << 6);
    padded.set(bytes);
    padded[bytes.length] = 0x80;
    const view = new DataView(padded.buffer);
    view.setUint32(padded.length - 8, Math.floor(bitLen / 0x100000000));
    view.setUint32(padded.length - 4, bitLen >>> 0);

    const H = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
    const W = new Uint32Array(64);
    const rotr = (x, n) => (x >>> n) | (x << (32 - n));
    for (let off = 0; off < padded.length; off += 64) {
      for (let t = 0; t < 16; t++) W[t] = view.getUint32(off + t * 4);
      for (let t = 16; t < 64; t++) {
        const s0 = rotr(W[t - 15], 7) ^ rotr(W[t - 15], 18) ^ (W[t - 15] >>> 3);
        const s1 = rotr(W[t - 2], 17) ^ rotr(W[t - 2], 19) ^ (W[t - 2] >>> 10);
        W[t] = (W[t - 16] + s0 + W[t - 7] + s1) >>> 0;
      }
      let [a, b, c, d, e, f, g, h] = H;
      for (let t = 0; t < 64; t++) {
        const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        const chv = (e & f) ^ (~e & g);
        const t1 = (h + S1 + chv + K[t] + W[t]) >>> 0;
        const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        const maj = (a & b) ^ (a & c) ^ (b & c);
        const t2 = (S0 + maj) >>> 0;
        h = g; g = f; f = e; e = (d + t1) >>> 0;
        d = c; c = b; b = a; a = (t1 + t2) >>> 0;
      }
      H[0] += a; H[1] += b; H[2] += c; H[3] += d; H[4] += e; H[5] += f; H[6] += g; H[7] += h;
    }
    return Array.from(H, (x) => x.toString(16).padStart(8, "0")).join("");
  }

  // -------------------------------------------------------------------------
  // Tamper-evident ledger (same canonical encoding as audit_ledger.py)
  // -------------------------------------------------------------------------
  const GENESIS_HASH = "0".repeat(64);

  function asciiJson(value) {
    return JSON.stringify(value).replace(/[^\x00-\x7f]/g, (c) => "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"));
  }

  function canonicalTimestamp(date) {
    return date.toISOString().slice(0, 23) + "000";
  }

  function computeEntryHash(prevHash, entry) {
    const payload = {
      action_taken: entry.action,
      actor: entry.actor || "",
      compute_cost_usd: Number(entry.computeCost || 0).toFixed(8),
      created_at: canonicalTimestamp(entry.createdAt),
      details: entry.details,
      invoice_id: entry.invoiceId == null ? null : entry.invoiceId,
      labor_saved_usd: Number(entry.laborSaved || 0).toFixed(8),
      tenant_id: entry.tenant,
      tokens_used: Math.trunc(entry.tokens || 0),
    };
    return sha256Hex(prevHash + asciiJson(payload));
  }

  function appendEntry(ledger, entry) {
    const prevHash = ledger.length ? ledger[ledger.length - 1].entryHash : GENESIS_HASH;
    const sealed = Object.assign({ id: ledger.length + 1 }, entry, { prevHash });
    sealed.entryHash = computeEntryHash(prevHash, sealed);
    ledger.push(sealed);
    return sealed;
  }

  function verifyLedger(ledger) {
    let expectedPrev = GENESIS_HASH;
    for (let i = 0; i < ledger.length; i++) {
      const e = ledger[i];
      if (e.prevHash !== expectedPrev) {
        return { valid: false, verified: i, total: ledger.length, firstInvalidId: e.id, reason: "Broken link: an entry was removed or reordered" };
      }
      if (computeEntryHash(expectedPrev, e) !== e.entryHash) {
        return { valid: false, verified: i, total: ledger.length, firstInvalidId: e.id, reason: "Hash mismatch: entry content was modified after it was sealed" };
      }
      expectedPrev = e.entryHash;
    }
    return { valid: true, verified: ledger.length, total: ledger.length, firstInvalidId: null, headHash: expectedPrev, reason: "All entries verified: hash chain intact" };
  }

  const api = {
    computeMatch,
    assessUntrustedText,
    tokenize,
    searchClauses,
    sha256Hex,
    computeEntryHash,
    appendEntry,
    verifyLedger,
    GENESIS_HASH,
    INJECTION_REDACTION,
  };
  PA.engine = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
