-- Radar Reuniology : schéma Supabase.
-- À coller une fois dans Supabase › SQL Editor › New query, puis Run.

-- Membres de l'équipe autorisés (un email par personne).
create table if not exists public.members (
  email   text primary key,
  name    text not null,
  user_id uuid unique
);

-- Toutes les données du Radar, rangées par collection (signals, accounts, contacts...).
create table if not exists public.docs (
  collection text not null,
  id         text not null,
  data       jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  primary key (collection, id)
);

alter table public.members enable row level security;
alter table public.docs    enable row level security;

create or replace function public.is_member() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.members m where lower(m.email) = lower(auth.jwt() ->> 'email'));
$$;

drop policy if exists "members read" on public.members;
create policy "members read" on public.members for select to authenticated using (public.is_member());

drop policy if exists "docs read"   on public.docs;
drop policy if exists "docs insert" on public.docs;
drop policy if exists "docs update" on public.docs;
drop policy if exists "docs delete" on public.docs;
create policy "docs read"   on public.docs for select to authenticated using (public.is_member());
create policy "docs insert" on public.docs for insert to authenticated with check (public.is_member());
create policy "docs update" on public.docs for update to authenticated using (public.is_member()) with check (public.is_member());
create policy "docs delete" on public.docs for delete to authenticated using (public.is_member());

-- Fusionne des champs dans un document (comme « update ») sans écraser les autres.
create or replace function public.doc_merge(p_collection text, p_id text, p_patch jsonb) returns void
language plpgsql security invoker set search_path = public as $$
begin
  insert into public.docs(collection, id, data, updated_at) values (p_collection, p_id, p_patch, now())
  on conflict (collection, id) do update set data = public.docs.data || excluded.data, updated_at = now();
end $$;

-- À la première connexion, rattache le compte à la ligne « members » (pour afficher les prénoms).
create or replace function public.claim_member() returns void
language sql security definer set search_path = public as $$
  update public.members set user_id = auth.uid() where lower(email) = lower(auth.jwt() ->> 'email') and user_id is null;
$$;

-- Mises à jour en direct dans la page.
do $$ begin
  alter publication supabase_realtime add table public.docs;
exception when duplicate_object then null; end $$;

-- Équipe : remplacer les emails puis exécuter.
-- insert into public.members(email, name) values
--   ('vincent@exemple.fr', 'Vincent'),
--   ('louis@exemple.fr', 'Louis'),
--   ('samar@exemple.fr', 'Samar'),
--   ('olivier@exemple.fr', 'Olivier'),
--   ('jo@exemple.fr', 'Jo'),
--   ('philippe@exemple.fr', 'Philippe')
-- on conflict (email) do update set name = excluded.name;
