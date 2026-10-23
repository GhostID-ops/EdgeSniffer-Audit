# FinalBot Licensing

This is a lightweight trust-gate for friends running their own local bot with their own `.env`.
It is not strong DRM: anyone with source code can bypass local checks if they are determined.

## Supabase Setup

1. Create a Supabase project.
2. Open the SQL editor and run `supabase/license_schema.sql`.
3. Copy the project URL and service-role key.
4. Choose a long random `LICENSE_ADMIN_TOKEN`.
5. Choose a long random `LICENSE_HASH_SECRET`.

Run the license API:

```powershell
$env:SUPABASE_URL="https://YOUR_PROJECT.supabase.co"
$env:SUPABASE_SERVICE_ROLE_KEY="YOUR_SERVICE_ROLE_KEY"
$env:LICENSE_ADMIN_TOKEN="long-random-admin-token"
$env:LICENSE_HASH_SECRET="long-random-hash-secret"
$env:LICENSE_API_HOST="127.0.0.1"
$env:LICENSE_API_PORT="8787"
npm run license-api
```

For production, set `LICENSE_API_HOST=0.0.0.0` behind HTTPS on a small VPS or server platform. Do not expose
`SUPABASE_SERVICE_ROLE_KEY`, `LICENSE_ADMIN_TOKEN`, or `LICENSE_HASH_SECRET` to users.

## Bot User Setup

Friends only need:

```env
LICENSE_KEY=FBOT-...
LICENSE_API_URL=https://your-license-api.example.com
LICENSE_CACHE_TTL_HOURS=24
```

The bot validates before decoding `PRIVATE_KEY_BASE58`. A successful validation writes
`.license-cache.json`, which is ignored by Git. If the license API is temporarily down, the bot
can start from a fresh cache for up to 24 hours.

## Admin Commands

Set these locally before running admin commands:

```powershell
$env:LICENSE_API_URL="https://your-license-api.example.com"
$env:LICENSE_ADMIN_TOKEN="long-random-admin-token"
$env:LICENSE_HASH_SECRET="long-random-hash-secret"
```

Create a license:

```powershell
npm run license:create -- --label "Friend Name" --expires 2026-06-20
```

List licenses:

```powershell
npm run license:list
```

Revoke a license:

```powershell
npm run license:revoke -- --id LICENSE_UUID
```

Reset a friend after a computer change:

```powershell
npm run license:reset-machine -- --id LICENSE_UUID
```
