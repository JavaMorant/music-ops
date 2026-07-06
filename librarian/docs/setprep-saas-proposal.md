<!-- Strategy proposal generated 2026-07-06 via a Fable-led multi-agent pass (market + feature design + monetization + architecture + feature-set → synthesis). STATUS: DRAFT — awaiting founder decisions (see section 7). Not an approved spec; the set-prep feature becomes a formal spec + implementation plan once direction is confirmed. -->

# Librarian Cloud — Decision Memo

## 1. The bet

A metadata-only cloud service for working DJs that builds tonight's set from **what has actually worked on your floor before**. Every competitor either sequences from generic Camelot/BPM math (DJ.Studio, SetFlow, HarmonySet, Djoid), from *other people's* aggregate data (PulseDJ, VirtualDJ LiveFeedback), or doesn't sequence at all (Lexicon, Mixed In Key). Nobody ingests the DJ's own CDJ/USB play history — librarian already does, via a transition graph (`pulse_usb.follows`) no one else has or can buy. Second edge: every incumbent is tuned for 4/4 house/techno; amapiano/afrobeats/UKG/rap sets are edge cases everywhere else and first-class here — and the founder is the reference user in exactly that scene. Third edge: audio never leaves the DJ's machine — metadata-only sync means near-zero storage cost and no copyright exposure, a product shape rekordbox Cloud and DJ.Studio structurally can't match. The wedge is set-prep; the subscription is the loop (every gig synced makes next week's prep smarter); the moat is the accumulated personal transition graph, which a DJ can't take anywhere else.

## 2. Set-prep: the flagship

**Input — a GigSpec:** set length, genre journey (ordered blocks, e.g. amapiano 60% → afrobeats 40%), energy arc preset (warmup / build / peak / closing / journey), a freshness dial (0 = proven bangers, 1 = road-test new tracks), must-plays, avoids, venue label, harmonic strictness. All optional except length; JSON-serialisable — the same contract for CLI, web form, and future SaaS API.

**Ordering:** build a candidate pool from existing tags (BPM + key already parsed by `metadata.py` — no audio analysis), joined to play history by normalised artist–title. Score each candidate for each slot:

- **Harmonic** — Camelot compatibility table (same key 1.0, ±1 wheel 0.9, relative 0.85, energy-boost moves scored lower). Unknown key scores **neutral 0.5, never penalised, never guessed**.
- **BPM** — transition distance with half/double-time awareness (70 rap ↔ 140 garage is one move) + fit to the arc curve, expressed as *percentiles within the genre block* so "peak" means ~115 in amapiano and ~140 in UKG.
- **Genre fit** to the journey block, with a small editable adjacency map (amapiano ↔ afrobeats ↔ afro house, etc.).
- **Proven vs fresh** — play counts and recency, blended by the freshness dial.
- **`follows_boost`** — the differentiator: the DJ's own transition graph, applied as an **additive bonus, never an averaged component**, so missing history never drags down a harmonically perfect pick. With zero history the engine degrades gracefully into a strong harmonic/arc planner — this is also the SaaS cold-start story.
- Penalties: artist repeats, key stagnation, tracks played at this venue in the last two sets.

Must-plays become **anchors** that split the set into segments; a beam search routes each segment *toward* its anchor ("get me from here to Mnike by 10:45"). v1 ships greedy in the same code path. Sub-second on 10k tracks; explainable weights, no ML.

**Output:** an annotated setlist — per slot: track, BPM, Camelot badge, clock time, a one-line reason citing real signals ("you played this after Mnike in 3 sets"), and 3 alternates scored in context. Exports: rekordbox XML playlist (cue-safe, existing code), m3u8, markdown crib sheet. Post-gig, `setplan review` diffs plan vs what was actually played — the flywheel that tunes the weights.

## 3. What else to build

**Build with/around set-prep (Phase 1–2):**
- **Set analytics dashboard** — floor-fillers, rising/falling, per-venue patterns; refreshes every gig with zero user effort — the canonical reason to keep paying.
- **rekordbox round-trip export** — not a feature, a survival requirement; mostly already built.
- **Library health monitoring** — the existing dedup/quarantine/low-bitrate engine run continuously on synced metadata; turns a one-shot tool into a subscription.
- **Monthly recaps + December "DJ Wrapped"** — weak revenue alone, but every shared card is an ad inside the exact peer network being sold to.
- **Camelot tools** — folded into set-prep and library views, never a standalone SKU (Mixed In Key owns detection; compose on top of tags).
- **Gig object (thin slice)** — date/venue/slot on every plan; costs nothing, powers venue-aware prep and analytics.

**Later (Phase 3):** mobile as PWA only; set-structure sharing (no audio, legally clean, seeds the cross-DJ graph); b2b prep (after sharing); Serato/Traktor export.

**Skip (YAGNI):** live/next-track mode (1am-on-venue-wifi reliability bar a solo founder can't carry), crate marketplace, full promoter suite (a second product for a different buyer), native mobile apps, NL-prompt set briefs (flag-gated later at best). One reconciliation: the pricing doc put b2b crates in the top tier at launch — cut; it ships Phase 3 after sharing exists.

## 4. Architecture

The seam already exists in the code: **readers** (touch the Mac, USB sticks, rekordbox DBs) become the **desktop companion**; **pure analytics** (functions over sessions + metadata) become the **cloud brain**.

- **Companion** = existing extractors (`metadata.py`, `pulse` loader, `pulse_usb` readers, `history_archive`) + a ~200-line sync client. JSONL batches to `POST /api/v1/sync`; sessions dedupe idempotently via existing signatures; device-token auth. **No audio upload path exists in the API at all** — the legal posture is enforced in the protocol. No absolute paths synced (hashed track key + basename only). Phase 1 companion is literally `librarian sync` in the current CLI; menubar packaging (and bundling the rekordcrate/sqlcipher binaries) waits for public beta.
- **Cloud** = one FastAPI container (same framework as the current webapp) + managed Postgres + managed auth (do not build auth). Metadata is ~3 MB per DJ; no object storage, no queues, ever. The `transitions` table is `follows()` materialised as an indexed query — server-side it merges desktop + every USB stick, better than the local tool today.
- **Set-prep engine** is a pure module — `plan_set(tracks, transitions, params) → SetPlan` — shared verbatim by the local CLI and the server. First refactor: extract `librarian_core` (versions, camelot, setplan, classify_v2, pure pulse analytics); the classify cache goes global-not-per-tenant (one DJ's paid classification serves all).
- **Stays local forever:** the whole file-operation engine (plan/apply/undo, quarantine, organise, rekordbox writeback) — audio and file moves never leave the machine.
- **Flag before charging money:** AcoustID commercial licence, rekordcrate redistribution licence.

## 5. Pricing & who pays

**Segment:** gigging open-format club DJs in multi-genre diaspora scenes (amapiano, afrobeats, UKG, rap, house) — starting with the founder's own circuit. Not bedroom DJs (no play history to feed the moat — they're the free funnel), not mobile/wedding DJs (crowd-request optimisers, wrong wedge).

| Tier | Price | Gets |
|---|---|---|
| Free | $0 | Metadata sync, library/Camelot view, 3 set-prep runs/mo |
| Pro | **$12/mo** (annual discount) | Unlimited set-prep, transition-graph recs, arc controls, rekordbox export |
| Studio | **$24/mo** | Cloud backup of play history, full analytics + recaps, multi-device; b2b later |

Anchored to what DJs already pay (MIK $8, Lexicon $10–20, Serato $12). Subscription only — no lifetime SKU, no pay-per-token. **Moat:** the transition graph is non-purchasable and compounds with every gig; leaving means abandoning years of "what works for my crowd"; metadata-only structure means competitors who host audio carry cost and legal risk this never touches; opt-in anonymised cross-DJ transition priors become a second-order network effect later.

## 6. Roadmap (solo founder)

**Phase 0 — now, 1–2 weeks: set-prep for himself, local.** `camelot.py` + pool + greedy ordering + CLI + m3u8/markdown export; then beam/anchors, alternates, rekordbox XML, web tab. Dogfood at real BPM/Throwbacks gigs. **Riskiest assumption / kill criterion: the plans must beat his manual prep. If they don't, there is no product — stop here.**

**Phase 1 — ~4–6 weeks: thin cloud MVP.** Extract `librarian_core`; FastAPI + Postgres + managed auth; `librarian sync` as the companion; hosted set-prep + minimal pulse dashboard; plan lands back in rekordbox via existing XML code. Onboard ~5 beta DJs from the BPM Collective network, free. **Riskiest assumption: other DJs have usable play history (USB exports) and the artist–title identity join holds on libraries that aren't his — test with one outside library before writing any billing code.**

**Phase 2 — a quarter: charge.** Stripe + the three tiers; ship the retention bundle — analytics dashboard, library health monitoring, monthly recaps; rough packaged companion. **Riskiest assumption: DJs in this scene will pay $12/mo for prep + analytics — gate is 10 paying users from outside his immediate circle.**

**Phase 3 — expansion.** Set sharing → b2b prep → Serato export → opt-in cross-DJ transition graph; Wrapped launch timed to December. **Riskiest assumption: the network layer actually drives referrals, not just usage.**

## 7. Open questions for the founder

1. **Confirm metadata-only.** Audio never touches the cloud, so no in-app previews from user files ever (streaming-embed links only). Locked?
2. **The beta five.** Which 5 DJs from BPM Collective/Throwbacks, and do they actually gig on Pioneer gear with USB history to sync? If most are Serato, Phase 3's Serato reader moves up and Phase 1 changes shape.
3. **Brand split.** Does the local librarian stay a free/personal tool under the cloud brand, or is everything one product? And does it ship as "librarian" or a new consumer-facing name?
4. **Pricing posture.** Confirm subscription-only (no lifetime SKU) — lifetime kills the compounding-loop story but this scene is used to one-time purchases (MIK, DJ.Studio).
5. **Time budget + go/no-go gate.** Hours per week available, and agreement that Phase 1 doesn't start until Phase 0's kill criterion passes on at least 3 real gigs.
