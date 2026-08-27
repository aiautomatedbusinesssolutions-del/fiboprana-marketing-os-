# content/

Per-platform generators for Fiboprana marketing copy, all driven by the weekly
research digest and the business wiki (`wiki/`), all founder-gated before
anything ships.

- **Video chain** (news + feature lanes): `news_ideas_run` → `facts_run`
  (claims verified against primary sources) → `script_run` (faceless
  voice-over scripts with Quiet Earth visual beats) → `video_artifacts_run`
  (storyboard, thumbnail prompts, upload package). Production itself lives in
  the sibling repo `Fiboprana Marketing/` (narrate → stills → animate →
  Remotion render).
- **Shorts**: `shorts_run` judges a folder of cut clips, packages captions,
  mints tracked links, schedules the lanes.
- **Posts**: `x_run` / `x_post_prompt` (weekly X batch), TikTok + LinkedIn
  prompt files.
- **Email**: `email_run` drafts the weekly "Notice" issue; `fleet/email_send`
  delivers via Resend after approval.

Compliance floor everywhere: the wellness line (never cure/treat/diagnose, no
health-outcome claims, no scores/grades), no em dashes, no invented facts.
