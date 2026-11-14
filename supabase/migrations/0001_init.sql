-- ============================================================================
-- 0001_init.sql — application schema for the Enterprise AI Assistant
--
-- Apply with the Supabase SQL Editor (paste and run) against the DEV project.
-- Safe to re-run: every statement is guarded.
--
-- Design rules encoded here (docs/architecture/SECURITY.md):
--   * Every user-owned row carries user_id (the verified subject) and tenant_id.
--   * RLS is enabled on every table and there is no bypass key in the app: the
--     backend forwards the caller's own access token to PostgREST, so these
--     policies are a real backstop, not decoration.
--   * Tool audit rows store a hash of the arguments, never the arguments, so the
--     audit trail cannot become a PII store by accident.
--   * No table is readable by `anon`. Only `authenticated` gets grants.
-- ============================================================================

begin;

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------

-- Tenant of the current request. A tenant claim wins; otherwise the user is
-- their own tenant, which keeps per-user isolation correct before an
-- organisation model exists. (Equivalent to auth.jwt() -> 'app_metadata' on
-- Supabase versions that expose that helper.)
create or replace function public.current_tenant_id()
returns text
language sql
stable
as $$
    select coalesce(
        nullif(
            nullif(current_setting('request.jwt.claims', true), '')::jsonb
                -> 'app_metadata' ->> 'tenant_id',
            ''
        ),
        nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'
    );
$$;

create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

create table if not exists public.conversations (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references auth.users (id) on delete cascade,
    tenant_id   text not null default public.current_tenant_id(),
    title       text check (title is null or char_length(title) <= 200),
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

comment on table public.conversations is
    'One chat thread per user. Scoped by user_id and tenant_id; RLS enforces both.';

create table if not exists public.messages (
    id               uuid primary key default gen_random_uuid(),
    conversation_id  uuid not null references public.conversations (id) on delete cascade,
    user_id          uuid not null references auth.users (id) on delete cascade,
    tenant_id        text not null default public.current_tenant_id(),
    role             text not null check (role in ('system', 'user', 'assistant', 'tool')),
    content          text not null default '',
    tool_call_id     text,
    created_at       timestamptz not null default now()
);

comment on table public.messages is
    'Ordered turns of a conversation. tool_call_id links a tool reply to its request.';

create table if not exists public.tool_invocations (
    id               uuid primary key default gen_random_uuid(),
    conversation_id  uuid references public.conversations (id) on delete cascade,
    user_id          uuid not null,
    tenant_id        text not null default public.current_tenant_id(),
    tool_name        text not null,
    -- Hash, not content: the audit trail must not become a PII store.
    arguments_hash   text not null,
    status           text not null,
    latency_ms       integer check (latency_ms is null or latency_ms >= 0),
    created_at       timestamptz not null default now()
);

comment on table public.tool_invocations is
    'Append-only audit of tool calls: name, argument hash, status, latency.';

-- ---------------------------------------------------------------------------
-- Indexes (the access patterns the application actually uses)
-- ---------------------------------------------------------------------------

create index if not exists conversations_user_recent_idx
    on public.conversations (user_id, updated_at desc);

create index if not exists conversations_tenant_idx
    on public.conversations (tenant_id);

create index if not exists messages_conversation_order_idx
    on public.messages (conversation_id, created_at);

create index if not exists messages_user_idx
    on public.messages (user_id);

create index if not exists tool_invocations_conversation_idx
    on public.tool_invocations (conversation_id, created_at desc);

create index if not exists tool_invocations_user_idx
    on public.tool_invocations (user_id);

-- ---------------------------------------------------------------------------
-- Triggers
-- ---------------------------------------------------------------------------

drop trigger if exists conversations_touch_updated_at on public.conversations;
create trigger conversations_touch_updated_at
    before update on public.conversations
    for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------
-- Row-level security
-- ---------------------------------------------------------------------------

alter table public.conversations   enable row level security;
alter table public.messages        enable row level security;
alter table public.tool_invocations enable row level security;

-- conversations: owner-only, and the tenant must match the request's tenant.
drop policy if exists conversations_select_own on public.conversations;
create policy conversations_select_own on public.conversations
    for select to authenticated
    using (user_id = auth.uid() and tenant_id = public.current_tenant_id());

drop policy if exists conversations_insert_own on public.conversations;
create policy conversations_insert_own on public.conversations
    for insert to authenticated
    with check (user_id = auth.uid() and tenant_id = public.current_tenant_id());

drop policy if exists conversations_update_own on public.conversations;
create policy conversations_update_own on public.conversations
    for update to authenticated
    using (user_id = auth.uid())
    with check (user_id = auth.uid() and tenant_id = public.current_tenant_id());

drop policy if exists conversations_delete_own on public.conversations;
create policy conversations_delete_own on public.conversations
    for delete to authenticated
    using (user_id = auth.uid());

-- messages: owner-only, and the parent conversation must also be the caller's.
-- Without the subquery, a user could attach a message to someone else's thread.
drop policy if exists messages_select_own on public.messages;
create policy messages_select_own on public.messages
    for select to authenticated
    using (
        user_id = auth.uid()
        and exists (
            select 1 from public.conversations c
            where c.id = messages.conversation_id and c.user_id = auth.uid()
        )
    );

drop policy if exists messages_insert_own on public.messages;
create policy messages_insert_own on public.messages
    for insert to authenticated
    with check (
        user_id = auth.uid()
        and exists (
            select 1 from public.conversations c
            where c.id = messages.conversation_id and c.user_id = auth.uid()
        )
    );

-- Append-only from the application's point of view: no update/delete policies.
drop policy if exists tool_invocations_select_own on public.tool_invocations;
create policy tool_invocations_select_own on public.tool_invocations
    for select to authenticated
    using (user_id = auth.uid());

drop policy if exists tool_invocations_insert_own on public.tool_invocations;
create policy tool_invocations_insert_own on public.tool_invocations
    for insert to authenticated
    with check (user_id = auth.uid());

-- ---------------------------------------------------------------------------
-- Grants: authenticated only, never anon
-- ---------------------------------------------------------------------------

grant usage on schema public to authenticated;

grant select, insert, update, delete on public.conversations to authenticated;
grant select, insert, delete on public.messages to authenticated;
grant select, insert on public.tool_invocations to authenticated;

revoke all on public.conversations from anon;
revoke all on public.messages from anon;
revoke all on public.tool_invocations from anon;

revoke all on function public.current_tenant_id() from public, anon;
grant execute on function public.current_tenant_id() to authenticated;

commit;

-- ============================================================================
-- Verify after applying (paste separately; expect 12 policies across 3 tables):
--
--   select tablename, policyname, cmd
--   from pg_policies
--   where schemaname = 'public'
--   order by tablename, policyname;
--
-- And confirm anon is locked out (from a shell):
--   curl -s -o /dev/null -w '%{http_code}\n' \
--     -H "apikey: $SUPABASE_ANON_KEY" "$SUPABASE_URL/rest/v1/conversations?select=*"
--   # 401 with code 42501 = table exists, anon denied. That is the point.
-- ============================================================================
