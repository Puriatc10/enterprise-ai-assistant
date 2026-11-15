/**
 * The single Supabase client instance.
 *
 * `createClient` throws when handed an empty URL, which would turn a missing
 * environment variable into a white screen. So the client is created only when
 * both values exist; callers get `null` and render a configuration notice
 * instead. No token or session object is ever logged from here.
 */

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

import { env, isAuthConfigured } from "../lib/env";

function createSupabaseClient(): SupabaseClient | null {
  if (!isAuthConfigured || env.supabaseUrl === null || env.supabaseAnonKey === null) {
    return null;
  }

  return createClient(env.supabaseUrl, env.supabaseAnonKey, {
    auth: {
      // Keep the session in localStorage so a reload does not sign the user out,
      // and refresh it in the background: the backend verifies short-lived JWTs.
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
    },
  });
}

export const supabase: SupabaseClient | null = createSupabaseClient();
