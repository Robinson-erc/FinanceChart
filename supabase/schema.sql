-- FinanceChart — database schema
--
-- Run this once in the Supabase SQL editor (Dashboard → SQL Editor → New query).
-- It is idempotent: re-running it is safe, including after a partial failure.
--
-- The privacy guarantees this app makes are enforced here, in row-level
-- security policies, not in the browser. The frontend is public code served
-- from GitHub Pages and cannot be trusted to keep anything secret. Every rule
-- below is applied by Postgres against the caller's authenticated user id.
--
-- Ordering matters and is deliberate: every table exists before any policy is
-- written, because Postgres resolves the tables a policy expression names at
-- the moment the policy is created. Policies on `profiles` refer to
-- `connections`, so the tables are all created up front.
--
--   1. tables
--   2. functions the policies depend on
--   3. row-level security policies
--   4. triggers
--   5. admin export, invitations, and account deletion

create extension if not exists "pgcrypto";


-- ============================================================ 1. tables

-- One row per account. `analytics_key` is a pseudonym: it lets the admin
-- export group rows by household without the export carrying a real user id,
-- an email, or a name.
create table if not exists public.profiles (
  id            uuid primary key references auth.users (id) on delete cascade,
  display_name  text not null default '',
  is_admin      boolean not null default false,
  analytics_key uuid not null default gen_random_uuid(),
  created_at    timestamptz not null default now()
);

-- A link between two accounts. Sharing is opt-in and one-directional per side:
-- accepting a connection does not reveal your finances, it only lets you
-- choose to. Each side controls its own flag.
--
-- The relationship label is per-side for the same reason. A relationship is
-- rarely symmetric in words: one person's girlfriend is the other's boyfriend,
-- a parent's child calls them a parent. One shared string forced both people to
-- read whichever label the inviter happened to pick. So each column holds what
-- *that* person calls the other, and each person may only write their own.
create table if not exists public.connections (
  id                     uuid primary key default gen_random_uuid(),
  requester_id           uuid not null references auth.users (id) on delete cascade,
  addressee_id           uuid not null references auth.users (id) on delete cascade,
  requester_relationship text,
  addressee_relationship text,
  status                 text not null default 'pending'
                           check (status in ('pending', 'accepted', 'declined')),
  requester_shares       boolean not null default false,
  addressee_shares       boolean not null default false,
  created_at             timestamptz not null default now(),
  constraint no_self_link check (requester_id <> addressee_id),
  constraint one_link_per_pair unique (requester_id, addressee_id)
);

-- Projects created before the label was split carry a single `relationship`
-- column. Move it to the requester's side — that is who wrote it — and leave
-- the addressee's own label empty for them to fill in.
--
-- Deliberately not auto-reciprocated: turning "Girlfriend" into "Boyfriend"
-- means inferring someone's gender from a word they did not choose, which is
-- the bug this migration exists to fix.
alter table public.connections
  add column if not exists requester_relationship text,
  add column if not exists addressee_relationship text;

do $$
begin
  if exists (select 1 from information_schema.columns
              where table_schema = 'public'
                and table_name = 'connections'
                and column_name = 'relationship') then
    update public.connections
       set requester_relationship = coalesce(requester_relationship, relationship);
    alter table public.connections drop column relationship;
  end if;

  if not exists (select 1 from pg_constraint
                  where conname = 'connections_label_length_check') then
    alter table public.connections add constraint connections_label_length_check
      check (length(coalesce(requester_relationship, '')) <= 40
         and length(coalesce(addressee_relationship, '')) <= 40);
  end if;
end $$;

create table if not exists public.bills (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users (id) on delete cascade,
  name       text not null check (length(trim(name)) > 0),
  amount     numeric(12, 2) not null check (amount > 0),
  category   text not null default 'Other',
  due_day    smallint not null default 1 check (due_day between 1 and 31),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Income carries a frequency because most people are not paid monthly.
--
-- `pay_day` (and `pay_day_2`) describe pay tied to dates in the month;
-- `anchor_date` describes pay on a fixed cycle, and holds one known payday
-- that the rest are counted from. The distinction matters for the monthly
-- figure: fortnightly pay is 26 packets a year, not 24, so treating it as
-- "twice a month" understates a year's income by roughly 8%.
create table if not exists public.income (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users (id) on delete cascade,
  name        text not null check (length(trim(name)) > 0),
  amount      numeric(12, 2) not null check (amount > 0),
  frequency   text not null default 'monthly'
                check (frequency in ('monthly', 'semimonthly', 'biweekly', 'weekly')),
  pay_day     smallint not null default 1 check (pay_day between 1 and 31),
  pay_day_2   smallint check (pay_day_2 between 1 and 31),
  anchor_date date,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

-- Existing projects predate the frequency columns.
alter table public.income
  add column if not exists frequency text not null default 'monthly',
  add column if not exists pay_day_2 smallint,
  add column if not exists anchor_date date;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'income_frequency_check') then
    alter table public.income add constraint income_frequency_check
      check (frequency in ('monthly', 'semimonthly', 'biweekly', 'weekly'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'income_schedule_check') then
    -- A cycle-based schedule is meaningless without the date it counts from.
    alter table public.income add constraint income_schedule_check
      check (frequency in ('monthly', 'semimonthly') or anchor_date is not null);
  end if;
  if not exists (select 1 from pg_constraint where conname = 'income_pay_day_2_check') then
    alter table public.income add constraint income_pay_day_2_check
      check (pay_day_2 is null or pay_day_2 between 1 and 31);
  end if;
end $$;

-- Bills carry a frequency for the same reason income does: a yearly premium
-- entered as a flat amount would read as that much every month. Monthly bills
-- use due_day; everything on a longer or shorter cycle counts from anchor_date.
alter table public.bills
  add column if not exists frequency text not null default 'monthly',
  add column if not exists anchor_date date;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'bills_frequency_check') then
    alter table public.bills add constraint bills_frequency_check
      check (frequency in ('monthly', 'weekly', 'biweekly',
                           'quarterly', 'semiannual', 'annual'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'bills_schedule_check') then
    alter table public.bills add constraint bills_schedule_check
      check (frequency = 'monthly' or anchor_date is not null);
  end if;
end $$;

-- Invitation attempts, kept only so they can be rate-limited.
--
-- Inviting someone requires resolving their email to an account, and anything
-- that answers "does this address have an account?" is an oracle worth
-- throttling. Rows are written by the invite function alone: no role is granted
-- anything here, and row-level security is on with no policies, so only the
-- SECURITY DEFINER function can see or write it.
create table if not exists public.invite_attempts (
  id         bigint generated always as identity primary key,
  user_id    uuid not null references auth.users (id) on delete cascade,
  created_at timestamptz not null default now()
);

create index if not exists invite_attempts_user_idx
  on public.invite_attempts (user_id, created_at desc);

create index if not exists bills_user_idx on public.bills (user_id);
create index if not exists income_user_idx on public.income (user_id);
create index if not exists connections_requester_idx on public.connections (requester_id);
create index if not exists connections_addressee_idx on public.connections (addressee_id);

alter table public.profiles        enable row level security;
alter table public.connections     enable row level security;
alter table public.bills           enable row level security;
alter table public.income          enable row level security;
alter table public.invite_attempts enable row level security;

-- Table privileges are a separate layer from row-level security, and both are
-- required. GRANT decides whether a role may touch the table at all; RLS then
-- decides which rows it sees. Newer Supabase projects no longer grant to these
-- roles automatically, so it is spelled out here.
--
-- Only `authenticated` is granted. Nothing in this app is reachable while
-- signed out, so `anon` gets schema usage and nothing more. `profiles` allows
-- no insert or delete: rows are created by the signup trigger and removed by
-- the cascade from auth.users.
grant usage on schema public to anon, authenticated;

grant select, insert, update, delete on public.connections to authenticated;
grant select, insert, update, delete on public.bills       to authenticated;
grant select, insert, update, delete on public.income      to authenticated;

-- profiles is deliberately narrower. A row-level policy can only say "you may
-- update this row" — it cannot say which columns. Granting UPDATE on the whole
-- table would let anyone set is_admin on their own row and hand themselves the
-- export. The privilege is therefore restricted to the one column a user owns.
grant select on public.profiles to authenticated;
revoke update on public.profiles from authenticated;
grant update (display_name) on public.profiles to authenticated;


-- ========================================================= 2. functions

-- Does `owner` currently share their finances with the calling user?
--
-- SECURITY DEFINER so it can look at the connection row without the caller
-- needing to read it. It returns only a boolean about the caller, so it
-- cannot be used to enumerate anyone else's relationships.
create or replace function public.shares_with_me(owner uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from public.connections c
    where c.status = 'accepted'
      and (
        (c.requester_id = owner and c.addressee_id = auth.uid() and c.requester_shares)
        or
        (c.addressee_id = owner and c.requester_id = auth.uid() and c.addressee_shares)
      )
  );
$$;

-- Is the caller connected to `other` at all? Used so a profile name can be
-- shown on an invitation, without exposing the wider user list.
create or replace function public.is_linked_to_me(other uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from public.connections c
    where (c.requester_id = other and c.addressee_id = auth.uid())
       or (c.addressee_id = other and c.requester_id = auth.uid())
  );
$$;


-- ========================================================== 3. policies

-- ---- profiles ----
-- Your own row, plus the name of anyone you are linked to (so invitations and
-- the sharing list can say who they are from). Nothing else.

drop policy if exists "read own profile" on public.profiles;
create policy "read own profile" on public.profiles
  for select using (id = auth.uid());

drop policy if exists "read linked profiles" on public.profiles;
create policy "read linked profiles" on public.profiles
  for select using (public.is_linked_to_me(id));

-- Superseded by "read linked profiles"; dropped so re-running upgrades cleanly.
drop policy if exists "read connected profiles" on public.profiles;
drop policy if exists "read pending counterpart profiles" on public.profiles;

drop policy if exists "update own profile" on public.profiles;
create policy "update own profile" on public.profiles
  for update using (id = auth.uid()) with check (id = auth.uid());

-- ---- connections ----

drop policy if exists "read own connections" on public.connections;
create policy "read own connections" on public.connections
  for select using (requester_id = auth.uid() or addressee_id = auth.uid());

drop policy if exists "send own invitations" on public.connections;
create policy "send own invitations" on public.connections
  for insert with check (requester_id = auth.uid());

drop policy if exists "update own connections" on public.connections;
create policy "update own connections" on public.connections
  for update using (requester_id = auth.uid() or addressee_id = auth.uid())
  with check (requester_id = auth.uid() or addressee_id = auth.uid());

drop policy if exists "delete own connections" on public.connections;
create policy "delete own connections" on public.connections
  for delete using (requester_id = auth.uid() or addressee_id = auth.uid());

-- ---- bills and income ----
-- Read your own rows, or those of someone who has chosen to share with you.
-- Writes are always restricted to your own rows: sharing is read-only.

drop policy if exists "read own or shared bills" on public.bills;
create policy "read own or shared bills" on public.bills
  for select using (user_id = auth.uid() or public.shares_with_me(user_id));

drop policy if exists "insert own bills" on public.bills;
create policy "insert own bills" on public.bills
  for insert with check (user_id = auth.uid());

drop policy if exists "update own bills" on public.bills;
create policy "update own bills" on public.bills
  for update using (user_id = auth.uid()) with check (user_id = auth.uid());

drop policy if exists "delete own bills" on public.bills;
create policy "delete own bills" on public.bills
  for delete using (user_id = auth.uid());

drop policy if exists "read own or shared income" on public.income;
create policy "read own or shared income" on public.income
  for select using (user_id = auth.uid() or public.shares_with_me(user_id));

drop policy if exists "insert own income" on public.income;
create policy "insert own income" on public.income
  for insert with check (user_id = auth.uid());

drop policy if exists "update own income" on public.income;
create policy "update own income" on public.income
  for update using (user_id = auth.uid()) with check (user_id = auth.uid());

drop policy if exists "delete own income" on public.income;
create policy "delete own income" on public.income
  for delete using (user_id = auth.uid());


-- ========================================================== 4. triggers

-- Column ownership on connections.
--
-- The row-level policy lets either party update a connection they are part of,
-- but it cannot express *which columns* each of them owns. Without this guard,
-- the addressee can set `requester_shares` — forcing the other person to share
-- their finances without consent, which is the one thing this app promises
-- cannot happen. Column-level GRANTs cannot express it either: which flag you
-- may write depends on the row, not just your role. So it is a trigger.
create or replace function public.guard_connection_update()
returns trigger
language plpgsql
as $$
declare
  me uuid := auth.uid();
begin
  -- No JWT means the service role or the SQL editor, which is trusted.
  if me is null then
    return new;
  end if;

  if new.id <> old.id
     or new.requester_id <> old.requester_id
     or new.addressee_id <> old.addressee_id
     or new.created_at <> old.created_at then
    raise exception 'a connection''s identity cannot be changed';
  end if;

  if new.requester_shares is distinct from old.requester_shares
     and me is distinct from old.requester_id then
    raise exception 'only that person may change their own sharing';
  end if;

  if new.addressee_shares is distinct from old.addressee_shares
     and me is distinct from old.addressee_id then
    raise exception 'only that person may change their own sharing';
  end if;

  -- Each side owns the word it uses for the other. Without this the addressee
  -- could rewrite how the requester's own list describes them, which is the
  -- same class of hole as writing someone else's sharing flag.
  if new.requester_relationship is distinct from old.requester_relationship
     and me is distinct from old.requester_id then
    raise exception 'only that person may change their own label for the other';
  end if;

  if new.addressee_relationship is distinct from old.addressee_relationship
     and me is distinct from old.addressee_id then
    raise exception 'only that person may change their own label for the other';
  end if;

  if new.status is distinct from old.status then
    if me is distinct from old.addressee_id then
      raise exception 'only the person invited may answer an invitation';
    end if;
    if old.status <> 'pending' then
      raise exception 'this invitation has already been answered';
    end if;
  end if;

  return new;
end;
$$;

drop trigger if exists connections_guard on public.connections;
create trigger connections_guard before update on public.connections
  for each row execute function public.guard_connection_update();

-- Give every new account a profile automatically.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, coalesce(new.raw_user_meta_data ->> 'display_name', ''))
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- Anyone who signed up before this ran still needs a profile.
insert into public.profiles (id, display_name)
select id, coalesce(raw_user_meta_data ->> 'display_name', '')
from auth.users
on conflict (id) do nothing;

create or replace function public.touch_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists bills_touch on public.bills;
create trigger bills_touch before update on public.bills
  for each row execute function public.touch_updated_at();

drop trigger if exists income_touch on public.income;
create trigger income_touch before update on public.income
  for each row execute function public.touch_updated_at();


-- ============ 5. admin export, invitations, account deletion

-- The export carries amounts and categories only — never names, emails, or the
-- free-text description of a bill (which is often identifying on its own:
-- "Aspen estate mortgage" names a household).
--
-- It is *pseudonymised*, not anonymised, and the distinction is legal as much
-- as technical. `analytics_key` is stable per household, so rows can be linked
-- back to a person by anyone holding the profiles table. Under GDPR that makes
-- the export personal data still, with all the duties that follow. Genuine
-- anonymisation would mean no stable key at all, and the grouping this export
-- exists to provide would go with it.
--
-- These are SECURITY DEFINER so they can read across users, and they check the
-- admin flag themselves. Without that check they would be a way for any signed
-- in account to read everyone's finances.

create or replace function public.export_bills()
returns table (
  household     uuid,
  category      text,
  amount        numeric,
  due_day       smallint,
  created_month date
)
language plpgsql
stable
security definer
set search_path = public
as $$
begin
  if not exists (select 1 from public.profiles p
                 where p.id = auth.uid() and p.is_admin) then
    raise exception 'not authorised: admin only';
  end if;

  return query
    select p.analytics_key,
           b.category,
           b.amount,
           b.due_day,
           date_trunc('month', b.created_at)::date
    from public.bills b
    join public.profiles p on p.id = b.user_id;
end;
$$;

create or replace function public.export_income()
returns table (
  household     uuid,
  amount        numeric,
  pay_day       smallint,
  created_month date
)
language plpgsql
stable
security definer
set search_path = public
as $$
begin
  if not exists (select 1 from public.profiles p
                 where p.id = auth.uid() and p.is_admin) then
    raise exception 'not authorised: admin only';
  end if;

  return query
    select p.analytics_key,
           i.amount,
           i.pay_day,
           date_trunc('month', i.created_at)::date
    from public.income i
    join public.profiles p on p.id = i.user_id;
end;
$$;

-- Invitations are by email, but the email column on auth.users must not be
-- readable by everyone — that would turn the app into an address book.
--
-- The earlier version of this resolved an address to a user id and handed it
-- back, which made it an enumeration oracle: any signed-in account could ask
-- "is alice@example.com registered here?" as fast as it liked and be answered
-- truthfully. Knowing someone holds an account with a budgeting app is not
-- nothing, and the answer was free and unlimited.
--
-- So the lookup no longer exists on its own. It happens inside the invitation,
-- the id never leaves the database, and the answer is identical whether or not
-- the address is registered — the same shape as a password-reset form that
-- says "if that address has an account, we've emailed it".
--
-- 'self', 'exists' and 'incoming' do confirm an account exists, and that is
-- deliberate: each of them can only be reached for your own address or for
-- someone already in your connection list, so they tell the caller nothing
-- they could not already see.
drop function if exists public.find_user_by_email(text);

create or replace function public.invite_by_email(lookup_email text,
                                                  label text default null)
returns text
language plpgsql
volatile
security definer
set search_path = public
as $$
declare
  me       uuid := auth.uid();
  target   uuid;
  attempts integer;
  existing record;
begin
  if me is null then
    raise exception 'not authorised';
  end if;

  -- Throttle before looking anything up, and count every attempt rather than
  -- only the ones that resolve. Counting hits alone would leave probing for
  -- addresses that are *not* registered completely unlimited, which is exactly
  -- the direction an enumeration sweep goes.
  insert into public.invite_attempts (user_id) values (me);
  select count(*) into attempts
    from public.invite_attempts
   where user_id = me and created_at > now() - interval '1 hour';
  if attempts > 15 then
    raise exception 'too many invitations in the last hour — try again later';
  end if;

  select id into target from auth.users
   where lower(email) = lower(trim(lookup_email)) limit 1;

  if target = me then
    return 'self';
  end if;

  if target is not null then
    select c.id, c.status, c.requester_id into existing
      from public.connections c
     where (c.requester_id = me and c.addressee_id = target)
        or (c.requester_id = target and c.addressee_id = me)
     limit 1;

    if existing.id is not null then
      if existing.status = 'pending' and existing.requester_id = target then
        return 'incoming';
      end if;
      return 'exists';
    end if;

    insert into public.connections (requester_id, addressee_id,
                                    requester_relationship)
    values (me, target, coalesce(nullif(trim(label), ''), 'Partner'));
  end if;

  return 'sent';
end;
$$;

-- Erase your own account and everything attached to it.
--
-- Deleting the auth.users row is the whole operation: profiles, bills, income,
-- connections and invite attempts all reference it with `on delete cascade`,
-- so they go with it. A user cannot reach auth.users directly, hence the
-- SECURITY DEFINER wrapper — which is careful to delete only auth.uid().
create or replace function public.delete_my_account()
returns void
language plpgsql
volatile
security definer
set search_path = public
as $$
declare
  me uuid := auth.uid();
begin
  if me is null then
    raise exception 'not authorised';
  end if;
  delete from auth.users where id = me;
end;
$$;

revoke all on function public.export_bills()           from public, anon;
revoke all on function public.export_income()          from public, anon;
revoke all on function public.invite_by_email(text, text) from public, anon;
revoke all on function public.delete_my_account()      from public, anon;
grant execute on function public.export_bills()             to authenticated;
grant execute on function public.export_income()            to authenticated;
grant execute on function public.invite_by_email(text, text) to authenticated;
grant execute on function public.delete_my_account()        to authenticated;

-- PostgREST caches the schema; nudge it so the new tables and functions are
-- visible to the API immediately rather than after its next reload.
notify pgrst, 'reload schema';
