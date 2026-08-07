/*
 * Your Supabase project's connection details.
 *
 * Both values are safe to publish. The anon key is designed to sit in public
 * frontend code — it identifies the project, it does not grant access. What
 * a signed-in account may read and write is decided by the row-level security
 * policies in supabase/schema.sql, which run inside Postgres.
 *
 * Never put the *service role* key here. That one bypasses every policy.
 *
 * Find these under: Supabase Dashboard → Project Settings → API
 */
export const SUPABASE_URL = "https://lbrudglqapguvzihtncf.supabase.co";
export const SUPABASE_ANON_KEY = "sb_publishable_7oC4oxifui5dhvT3qfN4fg_vmmx5CU9";

export const isConfigured = () =>
  !SUPABASE_URL.includes("YOUR-PROJECT-REF") &&
  !SUPABASE_ANON_KEY.includes("YOUR-ANON");
