# Extraction Tuning Log

> **Note:** the entries below are from the source project this engine was cloned from
> (a beginner-investing niche). The extraction disciplines they encode (no fabricated
> quotes, conservative pain points, sensitive-context separation) are already carried
> into the Fiboprana extraction prompt; keep them as methodology reference and start
> new Fiboprana entries above this line.

Running log of patterns noticed during observation extractions and resulting prompt changes.
Entries are chronological (newest at top). Each entry notes what was edited in the AI output, what pattern it revealed, and what (if anything) was changed in the system prompt as a result.

---

## 2026-04-24 — Prompt update (4 refinements)

Made four refinements to the extraction system prompt based on patterns observed across observations 5-13:

1. **`product-insight` tag** added explicitly to the tags section. The AI was consistently missing this convention because it wasn't documented in the prompt. Added clear criteria for when to apply it (Stackivate-actionable feature implications) vs. when not to (generic education suggestions, content ideas, out-of-scope topics).

2. **`community-validation-seeking` pattern** added to pain_points awareness. This has now appeared in 4+ observations (33yo rebalance, 22yo ChatGPT, EU MSCI World investor, others). Encoded into the prompt with a tag and pattern recognition guidance.

3. **Pain point conservatism** strengthened to address an over-stating pattern caught in observation #4 (25yo self-employed) and observation #11 (22yo ChatGPT). The AI was occasionally asserting pain points that went beyond what the source supported. Added explicit guidance to prefer fewer well-supported claims over speculative padding.

4. **Quote truncation** addressed. In observation #12 (India $10K), the AI pulled the setup of a key sentence but truncated before the resolution. Added guidance to prefer complete passages when meaning depends on it.

No structural changes to the prompt — all four refinements are additions to existing sections.

---

## 2026-04-23 — Observation #11 (22yo asked ChatGPT for investing curriculum)

**What I edited in the AI output:**
- Edited or deleted one pain point that editorialized beyond what the source supported: "Conflates financial literacy with a path to financial independence, without understanding how those connect in practice." OP didn't conflate these — he stated both as goals but lacked a framework between them. Rephrased or removed.
- Added `product-insight`, `chatgpt-curriculum`, and `resistance-to-simple-answer` tags manually.

**Pattern reinforced:**
- The "AI-mentor misalignment" pattern surfaced clearly: a beginner asked ChatGPT for an investing curriculum and got an over-engineered 24-month plan more suited to financial-analyst training than learning to invest savings. Worth tracking in future observations as `chatgpt-curriculum` or `ai-generated-advice`. This is a 2026-specific pattern (beginners using AI tools for financial education and getting plans miscalibrated for their actual goal).
- "Resistance to simple answers" is now a clear recurring pattern across 4+ observations (5-year-red, IRA-provider, this one, others). Beginners hear "just index funds" and resist it because they want to feel sophisticated or take "calculated risks." Tag this consistently going forward.

**Pattern noticed (second occurrence):**
- The AI continues to occasionally over-state pain points by reading subtext as fact. First seen in observation #4 (25yo self-employed, "overcomplicating with diversification"). Now seen here. Two occurrences = watch carefully. Three or more = consider tightening the pain points instructions in the system prompt to be more conservative about claims not directly supported by source text.

**No prompt change this round** — pattern not yet at threshold.

---

## 2026-04-23 — Observation #8 (Roth IRA set-it-and-forget-it beginner)

**What I edited in the AI output:**
- Added `product-insight` tag manually — AI did not apply it, as expected since this convention isn't in the system prompt yet
- Otherwise saved as-is; extraction quality was strong

**Pattern validated:**
- AI correctly identified the sharpest insight: OP "treats opening the account as the finish line" — the distinction between account-wrapper comprehension and investment-selection comprehension was named precisely
- Anti-fabrication fix continues holding: three real quotes returned from a short source rather than padding to five
- Feature implications were unusually product-actionable — multiple specific buildable Stackivate features were named (passive investor quiz, market-timing cost visualizer, HYSA-vs-long-term-comparison)

**Pattern observed (meta):**
Project-specific tag conventions (like `product-insight`) don't propagate to the AI unless they're in the system prompt. For now I'll apply this tag manually. Once I've manually tagged 3-5 observations with `product-insight`, I'll have enough signal to write a sharp prompt instruction for when it applies — premature to add it now from one data point.

**No prompt change this round** — deliberately waiting for more observations before encoding `product-insight` logic into the prompt.

---

## 2026-04-22 — Observation #5 (Texas IRA provider question)

**What I edited in the AI output:**
- Edited or deleted one mildly speculative pain point about Roth vs Traditional decision-making — the OP did provide shorthand reasoning and the extraction characterized it as "vague reasoning" without clear evidence
- Everything else saved as-is

**Pattern validated:**
- Anti-fabrication prompt fix held on first real test: three verbatim quotes returned, none invented, even though the source had many quotable lines that could have tempted padding
- AI correctly did not speculate beyond source evidence on the "diverse 8 funds" question (whether OP actually diversified) — good restraint
- Caught a subtle psychological pattern: "constrained-menu comfort" (OP found the 8-fund 401k menu easy but is overwhelmed by open-ended IRA options). Watch if this pattern recurs; could become a consistent tag.

**Cross-observation pattern emerging:**
Five observations in, the distinct audience archetypes are starting to separate: (1) hype-driven young beginners, (2) saving/investing conflators, (3) caregivers/family decision-makers, (4) intermediate-with-gaps investors, (5) self-made earners without formal framework, (6) functional-but-overwhelmed fund-selection types. These are audience segments worth naming for future content.

**No prompt change needed this round.**

## 2026-04-22 — Observation #4 (25yo self-employed high earner)

**What I edited in the AI output:**
- Deleted a hallucinated quote from notable_quotes: "Will so some research abt it appreciate it" — this quote did not appear in the source content. The AI appears to have fabricated it to pad the quotes section (the source post was short with limited quotable material).
- Deleted/rephrased a speculative pain point: "May be overcomplicating with income diversification while missing foundational retirement basics" — the source didn't actually support this claim, the AI was extrapolating.

**Pattern noticed (important):**
When the source content is short, the AI may fabricate quotes rather than returning fewer real ones. This is the first clear hallucination caught — worth addressing in the prompt.

**Pattern reinforced:**
The "saving vs. investing conflation" pain point now appears in three out of four good observations (gap-year saver, 18yo helping mom, this 25yo self-employed earner). This is a recurring pattern in the target audience. Consider tagging future instances with a consistent label like `saving-vs-investing-conflation` so they can be grouped and used together for content.

**Prompt change made:**
Added a no-fabrication instruction to the `notable_quotes` section of the system prompt. Key wording: "Never fabricate quotes. Only use verbatim text that appears in the source content... If the source has fewer than 2 quotable lines, return only what's actually there — even if that means 0 or 1 quotes. Returning fewer quotes is always correct; fabricating to hit a target count is never correct." The explicit "0 or 1 is acceptable" phrasing was chosen to resolve a potential conflict with the existing "2-5 verbatim quotes" target — without it, the model might treat the range as a hard floor and fabricate to hit it.

## 2026-04-22 — Observation #3 (Boglehead helping aging parents with 25-year advisor)

**What I edited in the AI output:**
- Saved substantially as-is; optionally rephrased the "advisor loyalty" pain point to be about the general pattern rather than this specific OP's situation
- No hooks, quotes, or feature implications removed

**Pattern validated:**
- Recent prompt update (separate investing content from sensitive personal context) worked as intended — AI correctly did NOT add `sensitive-context` tag despite thread containing personal context (aging parents, long-standing advisor relationship). The prompt update is distinguishing personal/emotional context from genuinely sensitive context appropriately.
- AI correctly identified OP as an intermediate investor (self-described Boglehead) rather than flattening into "another beginner," showing the segment_guess instruction is handling sophistication levels well
- AI caught a buried knowledge gap surfaced mid-thread in an OP reply (RMD contribution rules / earned-income requirement for IRA contributions) — the prompt's instruction to mine OP's follow-up replies is working

**New observations worth noting:**
- The phrase "Unsure whether complexity signals a strategy they don't understand or a strategy that doesn't exist" emerged as a genuinely insightful pain point framing. Watch for this pattern — "suspicion without vocabulary to diagnose" — in future observations. If it recurs, consider adding as an explicit tag category (e.g., `suspicion-without-diagnosis`).
- Content hooks on this extraction hit 4-for-4 quality. Either the prompt's hook instructions are working well, the source material was hook-rich, or both. Worth tracking hook hit rates across observations to see if this holds.

**No prompt change needed this round.**

## 2026-04-22 — Observations #2 (18yo helping mother with investing)

**What I edited in the AI output:**
- Removed pain point bullet about "outsized financial and logistical responsibility for a parent at 18" — it was about life circumstances, not investing knowledge gaps
- Removed content hook "What to do when you're 18 and you're the most financially literate person in your household" — leverages sensitive family dynamics as a marketing angle
- Added `sensitive-context` tag so this observation can be filtered out of content-generation searches later

**Pattern noticed:**
When source threads contain personal/sensitive context (mental health, caregiving, family dysfunction), the AI tends to weave that context into pain points and hooks. It shouldn't — pain points should be about investing knowledge, hooks should be about investing concepts.

**Prompt change made:**
Added "Separate investing content from life context" principle to the system prompt, with specific guidance for each affected field. Added the `sensitive-context` tag guidance to the tags instructions.
