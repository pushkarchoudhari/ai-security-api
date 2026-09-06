# Threat Model

What this system defends against, how, and — equally important — what it does
not defend against. A control list without a limitations list is marketing.

## System context

A single public endpoint (`POST /ask`) accepts a natural-language question and
an API key. An LLM classifies the question into one of three business actions;
the server then decides whether the caller is permitted to run it and executes
it against in-memory data.

The security-relevant property of the design:

> **The model proposes. The server disposes.**

The LLM's only output is a *proposed intent*. It never receives credentials,
never performs the action, and its output cannot widen the caller's permissions.
Authorization is evaluated server-side, immediately before the effect, against
the authenticated caller's role. A fully compromised model — one that has been
successfully jailbroken and is returning attacker-chosen JSON — can at most
select a different action, which is then refused by RBAC if the caller lacks it.

This is the property that makes prompt injection survivable rather than fatal.

## Trust boundaries

| Boundary | Untrusted input | Control |
|---|---|---|
| Internet → API | Request body | Pydantic schema, length and format limits |
| Caller → identity | API key | SHA-256 digest lookup, active-status check |
| User text → model | Question | Normalisation, signature scan, similarity scoring |
| Model → server | Classification JSON | Action clamped to allowlist, params sanitised |
| Model → caller | Response text | Canary and secret-shape scan |
| Server → disk | Audit entries | Hash chain, field truncation |

## Assets

1. **Employee PII** — names, departments, email addresses.
2. **Sales data** — revenue and deal figures.
3. **API keys** — the credentials themselves.
4. **The system prompt** — its contents reveal how to attack the classifier.
5. **The audit log** — the record of what happened.

## Threats and controls

### T1 — Prompt injection

*An attacker crafts input that causes the model to ignore its instructions.*

Four layers, in order. Normalisation runs first so the later layers see
canonical text: Unicode NFKC, zero-width character stripping, homoglyph folding
(Cyrillic `і` → `i`), leetspeak folding, and separator collapsing. Weighted
signature matching runs against both raw and normalised text. Character-trigram
overlap against a seed set of canonical attacks catches paraphrases. Scores
accumulate and a request is blocked above a threshold.

**Residual risk:** this is lexical, not semantic. A sufficiently novel
paraphrase that shares no vocabulary or character structure with a known attack
will pass. The mitigation is architectural rather than detective — see the model
proposes / server disposes property above. Detection is a filter, not the
boundary.

### T2 — System prompt extraction

*An attacker recovers the classifier prompt to design better attacks.*

Signature patterns cover the direct phrasings. The real control is detective: a
per-process canary token is embedded in the system prompt, and all model output
is scanned for it before being parsed. The model is never asked to emit the
canary and no legitimate classification contains it, so its appearance in output
is unambiguous evidence of leakage. When tripped, output is discarded unparsed,
a `CANARY_LEAKED` event is written at critical severity, and the request falls
through to a safe default intent.

**Residual risk:** the canary detects verbatim reproduction. A model that
*paraphrases* the system prompt without reproducing the token will not trip it.

### T3 — Privilege escalation

*A low-privilege caller reaches a high-privilege action.*

RBAC is checked server-side in `execute()`, immediately before the effect, using
the role attached to the authenticated key — never a role asserted in the
request or proposed by the model. Unknown roles fail closed. `general_answer`
requires no grant because it touches no business data.

### T4 — Bulk data exfiltration

*A caller with legitimate low-privilege access extracts more than intended.*

The employee lookup is a substring search, and `"" in anything` is `True`, so an
empty query silently meant "match every record" — the entire directory,
including email addresses, available to the lowest-privilege role. Two
independent guards now exist: the classifier refuses to emit a lookup intent for
subject-less requests ("find all employees"), and the action itself rejects
queries below a minimum length. Either alone would close the hole; both are
present because they fail differently.

### T5 — Credential compromise

*Keys are read from source, logs, or responses.*

Only SHA-256 digests appear in the key store — the plaintext keys are not in the
source tree, and a test asserts this. Output guardrails scan responses for
key-shaped strings. Audit fields are truncated to bound what attacker-influenced
text can write into the log.

SHA-256 rather than bcrypt/argon2 is deliberate: slow KDFs exist to make
*low-entropy human passwords* expensive to brute force. An API key is
machine-generated high-entropy material with no dictionary to run against it, so
a slow hash would add latency to every request and buy nothing.

**Residual risk:** the four demo keys are published in the README, because it is
a demo. There is no key rotation, expiry, or per-key rate limiting.

### T6 — Audit tampering

*An attacker edits the log to erase their activity.*

Each entry embeds the SHA-256 digest of the entry before it. Editing,
reordering, deleting, or forging an entry invalidates every digest from that
point forward. `POST /admin/audit/verify` recomputes the chain on demand;
`python -m app.security.audit audit.log` does the same from a shell.

**Residual risk:** this is *detective, not preventive*. An attacker with write
access can still delete the file wholesale or truncate the tail. What they
cannot do is quietly rewrite one line and leave a plausible-looking log. Shipping
entries to append-only external storage is the production upgrade.

### T7 — Resource exhaustion

*An attacker floods the endpoint or drains the LLM quota.*

Per-client rate limiting, with `X-Forwarded-For` honoured **only** when
`TRUST_PROXY_HEADERS` is explicitly enabled — on a directly exposed service the
header is caller-controlled and trusting it would let anyone reset their own
bucket. Live model calls are capped by a hard budget that degrades to the
deterministic classifier rather than failing. Any publicly reachable instance
is intended to run in mock mode with no API key deployed at all, so it cannot
be turned into free inference.

**Residual risk:** rate-limit state is in-process. Multiple instances each
enforce their own bucket; a shared store (Redis) is the production upgrade.

### T8 — Information disclosure through errors

*Error responses leak internal structure.*

All handlers return `{"detail": "..."}` and nothing else. Blocked requests
return a generic message — the risk score, matched signals, and nearest known
attack go to the audit log, never to the caller. Telling an attacker which rule
caught them is free tuning feedback. A test asserts the response body contains
no detection vocabulary.

## Explicitly out of scope

- **Persistence.** Everything is in memory; state is lost on restart.
- **Transport security.** TLS terminates at the platform.
- **Multi-tenancy.** One flat key namespace, no tenant isolation.
- **Indirect prompt injection.** The model reads only the user's question. A
  system that fed it retrieved documents or tool output would need injection
  analysis on those channels too — the input filter here covers one channel.
- **Model supply chain.** Provider integrity is assumed.
- **Denial of wallet at scale.** The budget cap is per-process, not global.
