# YouTube Description Format (locked)

> Fiboprana version (2026-08-26), adapted from the engine's locked format. Every
> description carries the video's content (SEO/AEO terms front-loaded) AND bridges to
> Fiboprana. Calm, wellness-education-framed, with a not-medical-advice disclaimer and
> the synthetic-media disclosure (we disclose AI visuals even though the stylized lane
> is disclosure-exempt — trust is the moat).

## Structure (in order)
1. **Above-the-fold hook** (1–2 lines) — only the first ~2 lines show in
   search/suggested; front-load the keywords + a curiosity gap.
2. **What the video covers** — the plain substance, with searchable terms (HRV,
   meditation, sleep score, nervous system...).
3. **The catch / tension** — the nuance; what the study or story can't say. Sources
   named here when the video leans on research.
4. **The bigger point** — zoom out (the over-optimization backlash, the missing mind
   layer); respectful to the tools, never a takedown.
5. **The turn inward** — noticing over grading; you decide what it means.
6. **— About Fiboprana —** — basic info (the mind-state layer for the wearables you
   already own; ~30-second check-ins; your practice and your body's signals on one
   screen, over weeks; no scores, no grades; your data stays yours; free during beta)
   + how this video's theme connects + a TRACKED link. Mint it (idempotent):
   `python -m attribution.autolink https://fiboprana.com --source youtube --medium description --campaign video-<slug>`
   and paste the printed `fiboprana.com/r/<code>` URL. Never paste a bare link —
   clicks from it are invisible to attribution. (Until the public /r/ redirect ships
   in fiboprana-site, use the full UTM link from the sibling repo's `build-link.mjs`.)
7. **CTA** — subscribe (one-line value prop) + a comment question that seeds the
   reply loop.
8. **Disclosure + disclaimer** — "Visuals are AI-generated illustration; narration is
   the founder's own cloned voice." + "General wellness education, not medical advice.
   Nothing here diagnoses or treats anything — talk to a professional about your
   health."
9. **Hashtags** — ~6–8 relevant tags incl. #Fiboprana (e.g. #meditation #HRV
   #nervoussystem #mindbody #wearables #burnout).

## Rules
- Front-load keywords in the first 2 lines.
- Wellness-line clean: no outcome claims, no "measures your stress," no scores/grades
  framing, no capability claims that don't ship yet.
- Every research claim in the description matches a source the video actually cites.
- Always bridge to Fiboprana + how the topic connects.
- Keep it tight (~200–300 words + hashtags).

## Deferred
- **Chapters / timestamps** — add once the flow is proven (masters are only 3–5 min).
