# Fleet — Security Notes

## Known interim exposure (H1): the agent shares the public anon key

**Status: accepted interim posture, not blocking.** ⚠️ Confirmed 2026-06-25: this project
uses Supabase's **modern asymmetric keys**, so the `mint_hermes_token.py` HS256 path below
does **not** work here (a self-signed JWT returns `401 PGRST301`). Use the secret-key-bound-
to-a-custom-role path noted in step 1 of "Apply it" instead. The scoped-role migration is
unchanged; only the credential method differs.

The agent authenticates to Supabase with `SUPABASE_ANON_KEY`. That anon key is the
**same public key the SaaS app ships to browsers**, and the `marketing` schema's
RLS policies are `using(true) / with check(true)`. So anyone who extracts the anon
key from your frontend can, against the `marketing` schema only:

- **read** every research run (`reasoning`, `outcome_note`, `content_pull` strategy), and
- **overwrite** any row via PATCH. Withholding DELETE does *not* protect history —
  a full-row UPDATE blanks `digest_md` / `reasoning` just as well.

Blast radius is confined to the `marketing` schema (auth/billing live under separate
grants the agent never touches). But it includes write access to the agent's own
training signal, which is why we don't leave it open indefinitely.

Until the fix below is applied, the content-pull prompt treats both the digest and
the stored verdict notes as **untrusted data** (they could be poisoned through this
exposure) — see `content_pull_prompt.py` v1.4 and the fenced user message in
`research_agent.py`.

## The fix (staged): a dedicated, scoped `hermes` role

`supabase/migrations/20260625000001_scope_hermes_role.sql` creates a `hermes`
Postgres role, grants it only what the agent needs, and **removes anon/authenticated
access to the `marketing` schema entirely**. It also makes the ledgers write-once
(the agent can INSERT and fill `outcome_*`, but cannot rewrite a past digest or
reasoning). After it's applied, a leaked anon key has no access here at all.

The code is already wired for it: `fleet/supabase.py` sends `SUPABASE_HERMES_JWT`
as the bearer token when present, and falls back to the anon key when it isn't — so
nothing changes until you opt in.

### Apply it (≈15 min, order matters)

The agent **breaks** if you apply the migration before it holds a `hermes`
credential. Do these in order:

1. **Mint the credential.** ⚠️ **This project uses asymmetric signing keys**, so
   you **cannot** self-sign a JWT — confirmed 2026-06-25 by a live `401 PGRST301`
   (`None of the keys was able to decode the JWT`). Create a Supabase **secret**
   API key bound to a custom `hermes` role in the dashboard (Settings → API), and
   use that string as `SUPABASE_HERMES_JWT`. The migration's role + policies below
   are identical either way.

   <details><summary>Legacy HS256 projects only — NOT this one</summary>

   If a project still exposes a legacy HS256 "JWT Secret" under Settings → API →
   JWT Settings, you can self-sign instead. Get that secret, then:
   ```bash
   # Git Bash:
   SUPABASE_JWT_SECRET="<your-jwt-secret>" python -m fleet.mint_hermes_token
   ```
   ```powershell
   # PowerShell:
   $env:SUPABASE_JWT_SECRET="<your-jwt-secret>"; python -m fleet.mint_hermes_token
   ```
   It prints a long-lived JWT whose `role` claim is `hermes`. Note:
   `mint_hermes_token.py` only works on projects using a shared (HS256) JWT
   secret — if the Fiboprana project is configured with asymmetric signing
   keys, mint the token another way.

   </details>

2. **Set the env var** in your local `.env` *and* the Railway dashboard:
   `SUPABASE_HERMES_JWT=<token>`.

3. **Sanity check** (still on anon grants — should pass): `python -m fleet.check --write`.

4. **Apply the migration**: Supabase Studio → SQL Editor → paste
   `20260625000001_scope_hermes_role.sql` → Run.

5. **Confirm**: `python -m fleet.check --write` should still pass (now as `hermes`).
   To prove anon is locked out, temporarily unset `SUPABASE_HERMES_JWT` and re-run —
   it should fail with a permission/RLS error. Re-set it afterward.

### Rollback

If anything misbehaves, the agent keeps working the moment you unset
`SUPABASE_HERMES_JWT` **and** restore anon access. To restore anon access, re-grant
and re-create the permissive policies from `20260624000001_init_marketing_schema.sql`
(the "Table privileges" + "Row Level Security" sections), or keep a small
`down`-style snippet handy. The `hermes` role itself is harmless to leave in place.

## Other notes

- **Never** put `SUPABASE_SERVICE_ROLE_KEY` in the agent's runtime environment — it
  bypasses RLS on the entire database. service_role is your admin / break-glass key.
- `.env` and `*.db` are git-ignored and docker-ignored; secrets live only in `.env`
  locally and the Railway dashboard in the cloud.
