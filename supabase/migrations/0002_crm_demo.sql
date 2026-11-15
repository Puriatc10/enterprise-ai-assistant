-- ============================================================================
-- 0002_crm_demo.sql — CRM demo domain (accounts, deals, activities)
--
-- Apply AFTER 0001, with the Supabase SQL Editor (paste and run) or
--   docker exec -i <pg> psql -U postgres -d <db> -v ON_ERROR_STOP=1 < this-file
-- Safe to re-run: every statement is guarded or a no-op.
--
-- WHAT THIS IS
--   The CRM domain the assistant answers questions about — the caller's account, deals
--   and open pipeline — over a *direct* PostgreSQL connection, not through PostgREST.
--
-- WHY THE READER ROLE IS SELECT-ONLY
--   A direct connection bypasses row-level security: `crm_reader` sees every row its
--   GRANT allows, and the backend's `WHERE auth_subject = $1` is what narrows that to one
--   customer (see apps/api/app/infrastructure/connectors/postgres/crm.py). The role is
--   therefore the narrowest thing that can still answer questions: USAGE on the schema
--   and SELECT on its tables. No INSERT/UPDATE/DELETE, no ownership, no BYPASSRLS, and no
--   access to the `public` or `auth` schemas.
--
-- RLS IS DELIBERATELY NOT ENABLED ON `crm`
--   Enabling it without a policy would deny every read to the non-owner reader role, and
--   enabling it with `using (true)` would only look like a control. For this path the
--   statement's WHERE clause is the control, and the migration that owns `public` (0001)
--   is where the PostgREST policies live. As a backstop, `anon` and `authenticated` are
--   revoked from the `crm` schema so these tables are not reachable through PostgREST at
--   all.
--
-- OPERATOR STEPS
--   1. Replace the placeholder password below, or create `crm_reader` first in
--      Dashboard → Database → Roles and re-run this file. Never commit the real password.
--   2. Put the resulting DSN in CONNECTOR_POSTGRES_URL (or your CRM-specific variable):
--      postgresql://crm_reader:<password>@<host>:5432/<database>
--   3. Set CRM_SOURCE=postgres for the API. Verify with the queries at the bottom.
--   4. Optional: seed a real login subject with
--      `python scripts/seed_crm.py --dsn <admin-dsn> --subject <auth-uuid>`
-- ============================================================================

begin;

-- ---------------------------------------------------------------------------
-- Schema
-- ---------------------------------------------------------------------------

create schema if not exists crm;

comment on schema crm is
    'Demo CRM domain (accounts, deals, activities). Read by the backend over a direct '
    'connection with a SELECT-only role; RLS does not apply on that path.';

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

create table if not exists crm.accounts (
    id            uuid primary key default gen_random_uuid(),
    -- The verified JWT subject this account belongs to. This column is the only thing
    -- that scopes a query, so it is unique and NOT NULL.
    auth_subject  text not null unique,
    name          text not null,
    industry      text not null,
    owner_name    text not null,
    created_at    timestamptz not null default now()
);

comment on table crm.accounts is
    'One CRM account per authenticated subject. auth_subject is the scoping key for every '
    'read; there is no other tenancy column.';
comment on column crm.accounts.auth_subject is
    'Verified JWT subject. Bound as $1 by the connector; never supplied by the model.';

create table if not exists crm.deals (
    id              uuid primary key default gen_random_uuid(),
    account_id      uuid not null references crm.accounts (id) on delete cascade,
    title           text not null,
    -- Same vocabulary as app/domain/models/crm.py DEAL_STAGES. 'won' and 'lost' are
    -- closed; everything else counts as open pipeline.
    stage           text not null
                    check (stage in ('lead', 'qualified', 'proposal', 'won', 'lost')),
    amount_toman    bigint not null check (amount_toman >= 0),
    currency        text not null default 'IRT',
    expected_close  text,
    updated_at      timestamptz not null default now()
);

comment on table crm.deals is
    'Sales opportunities. Read only through a join to the owning account; no query here '
    'filters on a deal id supplied by a caller.';

create table if not exists crm.activities (
    id           uuid primary key default gen_random_uuid(),
    deal_id      uuid not null references crm.deals (id) on delete cascade,
    kind         text not null,
    summary      text not null,
    occurred_at  timestamptz not null default now()
);

comment on table crm.activities is
    'Timeline entries per deal. Present so the schema is realistic; no tool reads it yet.';

-- ---------------------------------------------------------------------------
-- Indexes (the access patterns the connector actually uses)
-- ---------------------------------------------------------------------------

create index if not exists accounts_auth_subject_idx
    on crm.accounts (auth_subject);

create index if not exists deals_account_idx
    on crm.deals (account_id);

create index if not exists deals_account_recent_idx
    on crm.deals (account_id, updated_at desc);

create index if not exists activities_deal_idx
    on crm.activities (deal_id, occurred_at desc);

-- ---------------------------------------------------------------------------
-- The reader role: USAGE + SELECT, nothing else
-- ---------------------------------------------------------------------------

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'crm_reader') then
        begin
            -- PLACEHOLDER. Replace before use, or create the role in the dashboard first.
            create role crm_reader login password 'CHANGE_ME_crm_reader_password';
        exception
            when insufficient_privilege then
                raise notice
                    'crm_reader was NOT created (current role cannot CREATE ROLE). '
                    'Create it in Dashboard -> Database -> Roles, then re-run this file.';
        end;
    end if;

    if exists (select 1 from pg_roles where rolname = 'crm_reader') then
        execute 'revoke all on schema crm from crm_reader';
        execute 'grant usage on schema crm to crm_reader';
        execute 'grant select on all tables in schema crm to crm_reader';
        -- Tables added later (a new migration) are readable without another GRANT.
        execute 'alter default privileges in schema crm grant select on tables to crm_reader';
        raise notice 'crm_reader granted USAGE + SELECT on schema crm.';
    else
        raise notice 'crm_reader missing: no grants applied. See the notice above.';
    end if;

    -- Keep the PostgREST path out of this schema entirely. `anon` and `authenticated`
    -- only exist on Supabase, so this is guarded rather than assumed.
    if exists (select 1 from pg_roles where rolname = 'anon') then
        execute 'revoke all on schema crm from anon';
        execute 'revoke all on all tables in schema crm from anon';
    end if;

    if exists (select 1 from pg_roles where rolname = 'authenticated') then
        execute 'revoke all on schema crm from authenticated';
        execute 'revoke all on all tables in schema crm from authenticated';
    end if;
end
$$;

-- ---------------------------------------------------------------------------
-- Demo seed
--
-- Two subjects on purpose: 'demo-subject' proves a populated pipeline, and
-- 'other-subject' exists so isolation is provable by query rather than asserted.
-- Re-running never duplicates rows and never overwrites an edited demo account.
-- ---------------------------------------------------------------------------

insert into crm.accounts (auth_subject, name, industry, owner_name, created_at)
values
    ('demo-subject', 'شرکت داده‌پردازی آریا', 'فناوری اطلاعات', 'سارا محمدی',
     timestamptz '2025-11-02 09:00:00+03:30'),
    ('other-subject', 'گروه صنعتی پارس', 'تولید صنعتی', 'امیر رضایی',
     timestamptz '2025-10-19 11:30:00+03:30')
on conflict (auth_subject) do nothing;

-- demo-subject: 5 deals, mixed stages — 3 open (proposal, qualified, lead),
-- 1 won, 1 lost. Total open pipeline: 2,350,000,000 toman.
insert into crm.deals
    (account_id, title, stage, amount_toman, currency, expected_close, updated_at)
select a.id, v.title, v.stage, v.amount_toman, v.currency, v.expected_close, v.updated_at
  from crm.accounts a
 cross join (values
        ('تمدید قرارداد پشتیبانی سالانه',    'proposal',  780000000::bigint, 'IRT',
         '1404/12/15', timestamptz '2026-01-12 10:15:00+03:30'),
        ('استقرار سامانه باشگاه مشتریان',     'qualified', 1250000000::bigint, 'IRT',
         '1405/02/01', timestamptz '2026-01-09 14:40:00+03:30'),
        ('خرید لایسنس داشبورد تحلیل فروش',   'lead',       320000000::bigint, 'IRT',
         '1405/03/10', timestamptz '2026-01-05 09:05:00+03:30'),
        ('بازطراحی وبسایت شرکتی',            'won',         450000000::bigint, 'IRT',
         '1404/11/20', timestamptz '2025-12-28 16:20:00+03:30'),
        ('پروژه یکپارچه‌سازی با درگاه پرداخت', 'lost',        610000000::bigint, 'IRT',
         '1404/10/05', timestamptz '2025-12-15 11:00:00+03:30')
       ) as v(title, stage, amount_toman, currency, expected_close, updated_at)
 where a.auth_subject = 'demo-subject'
   and not exists (select 1 from crm.deals d where d.account_id = a.id);

-- other-subject: 2 deals — 1 open, 1 won. Never visible to demo-subject.
insert into crm.deals
    (account_id, title, stage, amount_toman, currency, expected_close, updated_at)
select a.id, v.title, v.stage, v.amount_toman, v.currency, v.expected_close, v.updated_at
  from crm.accounts a
 cross join (values
        ('خط تولید هوشمند — فاز دوم',  'qualified', 3400000000::bigint, 'IRT',
         '1405/04/01', timestamptz '2026-01-11 08:45:00+03:30'),
        ('قرارداد نگهداری تجهیزات',    'won',         900000000::bigint, 'IRT',
         '1404/12/01', timestamptz '2025-12-20 13:10:00+03:30')
       ) as v(title, stage, amount_toman, currency, expected_close, updated_at)
 where a.auth_subject = 'other-subject'
   and not exists (select 1 from crm.deals d where d.account_id = a.id);

-- A few activities so the timeline table is populated with realistic Persian text.
insert into crm.activities (deal_id, kind, summary, occurred_at)
select d.id, v.kind, v.summary, v.occurred_at
  from crm.deals d
  join crm.accounts a on a.id = d.account_id
  join (values
        ('تمدید قرارداد پشتیبانی سالانه',  'call',
         'تماس با مدیر فنی درباره شرایط تمدید',
         timestamptz '2026-01-12 09:30:00+03:30'),
        ('تمدید قرارداد پشتیبانی سالانه',  'email',
         'ارسال پیشنهاد قیمت بازنگری‌شده',
         timestamptz '2026-01-11 17:05:00+03:30'),
        ('استقرار سامانه باشگاه مشتریان',   'meeting',
         'جلسه نیازسنجی با تیم بازاریابی',
         timestamptz '2026-01-08 10:00:00+03:30'),
        ('خط تولید هوشمند — فاز دوم',      'meeting',
         'بازدید از کارخانه و بررسی پیش‌نیازها',
         timestamptz '2026-01-10 09:00:00+03:30')
       ) as v(deal_title, kind, summary, occurred_at)
    on v.deal_title = d.title
 where a.auth_subject in ('demo-subject', 'other-subject')
   and not exists (select 1 from crm.activities x where x.deal_id = d.id);

commit;

-- ============================================================================
-- Verify after applying (paste separately).
--
-- Shape and grants (expect: crm_reader cannot_do anything but SELECT):
--   select table_name, privilege_type
--   from information_schema.role_table_grants
--   where grantee = 'crm_reader' and table_schema = 'crm'
--   order by table_name, privilege_type;
--
-- Demo data and the open-pipeline totals per subject:
--   select a.auth_subject,
--          count(d.id)                                              as deals,
--          count(d.id) filter (where d.stage not in ('won','lost'))  as open_deals,
--          coalesce(sum(d.amount_toman)
--                   filter (where d.stage not in ('won','lost')), 0) as open_toman
--     from crm.accounts a
--     left join crm.deals d on d.account_id = a.id
--    group by a.auth_subject
--    order by a.auth_subject;
--   -- expected: demo-subject 5 / 3 / 2350000000, other-subject 2 / 1 / 3400000000
--
-- Isolation, as the reader role sees it (from a shell):
--   psql "postgresql://crm_reader:<password>@<host>:5432/<db>" -c \
--     "select count(*) from crm.deals d join crm.accounts a on a.id = d.account_id
--       where a.auth_subject = 'other-subject';"
--   -- 2 rows: the role can read the schema, the WHERE clause is what limits it to one
--   -- customer. Writing must fail:
--   psql "postgresql://crm_reader:<password>@<host>:5432/<db>" \
--     -c "insert into crm.accounts (auth_subject, name, industry, owner_name)
--         values ('x','x','x','x');"
--   -- ERROR: permission denied for table accounts
--
-- And confirm PostgREST is locked out of this schema (expect 401/404, never rows):
--   curl -s -o /dev/null -w '%{http_code}\n' \
--     -H "apikey: $SUPABASE_ANON_KEY" \
--     "$SUPABASE_URL/rest/v1/deals?select=*"
-- ============================================================================
