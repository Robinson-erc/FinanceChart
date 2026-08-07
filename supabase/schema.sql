-- FinanceChart — database schema
--
-- Run this once in the Supabase SQL editor (Dashboard → SQL Editor → New query).
-- It is idempotent: re-running it is safe.
--
-- The privacy guarantees this app makes are enforced here, in row-level
-- security policies, not in the browser. The frontend is public code served
-- from GitHub Pages and cannot be trusted to keep anything secret. Every rule
-- below is applied by Postgres against the caller's authenticated user id.

create extension if not exists "pgcrypto";


-- ---------------------------------------------------------------- profiles

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

alter table public.profiles enable row level security;

drop policy if exists "read own profile" on public.profiles;
create policy "read own profile" on public.profiles
  for select using (id = auth.uid());

drop policy if exists "read connected profiles" on public.profiles;
create policy "read connected profiles" on public.profiles
  for select using (
    exists (
      select 1 from public.connections c
      where c.status = 'accepted'
        and ((c.requester_id = profiles.id and c.addressee_id = auth.uid())
          or (c.addressee_id = profiles.id and c.requester_id = auth.uid()))
    )
  );

-- A pending invitation has to be able to name the person who sent it.
drop policy if exists "read pending counterpart profiles" on public.profiles;
create policy "read pending counterpart profiles" on public.profiles
  for select using (
    exists (
      select 1 from public.connections c
      where c.status = 'pending'
        and ((c.requester_id = profiles.id and c.addressee_id = auth.uid())
          or (c.addressee_id = profiles.id and c.requester_id = auth.uid()))
    )
  );

drop policy if exists "update own profile" on public.profiles;
create policy "update own profile" on public.profiles
  for update using (id = auth.uid()) with check (id = auth.uid());

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


-- ------------------------------------------------------------- connections

-- A link between two accounts. Sharing is opt-in and one-directional per side:
-- accepting a connection does not reveal your finances, it only lets you
-- choose to. Each side controls its own flag.
create table if not exists public.connections (
  id               uuid primary key default gen_random_uuid(),
  requester_id     uuid not null references auth.users (id) on delete cascade,
  addressee_id     uuid not null references auth.users (id) on delete cascade,
  relationship     text not null default 'Partner',
  status           text not null default 'pending'
                     check (status in ('pending', 'accepted', 'declined')),
  requester_shares boolean not null default false,
  addressee_shares boolean not null default false,
  created_at       timestamptz not null default now(),
  constraint no_self_link check (requester_id <> addressee_id),
  constraint one_link_per_pair unique (requester_id, addressee_id)
);

alter table public.connections enable row level security;

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


-- Does `owner` currently share their finances with the calling user?
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


-- -------------------------------------------------------- bills and income

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

create table if not exists public.income (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users (id) on delete cascade,
  name       text not null check (length(trim(name)) > 0),
  amount     numeric(12, 2) not null check (amount > 0),
  pay_day    smallint not null default 1 check (pay_day between 1 and 31),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists bills_user_idx on public.bills (user_id);
create index if not exists income_user_idx on public.income (user_id);

alter table public.bills enable row level security;
alter table public.income enable row level security;

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


-- ------------------------------------------------------------ admin export

-- The app promises that exported data is anonymised: amounts and categories
-- only, never names, emails, or the free-text description of a bill (which is
-- often identifying — "Aspen estate mortgage" names a household).
--
-- These are SECURITY DEFINER so they can read across users, and they check the
-- admin flag themselves. Without that check they would be a way for any signed
-- in account to read everyone's finances.

create or replace function public.export_bills()
returns table (
  household   uuid,
  category    text,
  amount      numeric,
  due_day     smallint,
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

revoke all on function public.export_bills() from public, anon;
revoke all on function public.export_income() from public, anon;
grant execute on function public.export_bills() to authenticated;
grant execute on function public.export_income() to authenticated;


-- --------------------------------------------------------- finding people

-- Invitations are by email, but the email column on auth.users must not be
-- readable by everyone — that would turn the app into an address book. This
-- resolves one address at a time and returns only an id.
create or replace function public.find_user_by_email(lookup_email text)
returns uuid
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  found uuid;
begin
  if auth.uid() is null then
    raise exception 'not authorised';
  end if;
  select id into found from auth.users
   where lower(email) = lower(trim(lookup_email)) limit 1;
  return found;
end;
$$;

revoke all on function public.find_user_by_email(text) from public, anon;
grant execute on function public.find_user_by_email(text) to authenticated;
