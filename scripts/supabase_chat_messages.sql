-- Run in Supabase SQL Editor (Dashboard → SQL) after creating your project.
-- Stores chat turns for signed-in users (server inserts with service role).

create table if not exists public.chat_messages (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  role text not null check (role in ('user', 'assistant')),
  content text not null,
  created_at timestamptz not null default now()
);

create index if not exists chat_messages_user_id_created_at_idx
  on public.chat_messages (user_id, created_at desc);

alter table public.chat_messages enable row level security;

-- Users can read their own rows (optional; service role bypasses RLS for inserts).
create policy "Users read own chat_messages"
  on public.chat_messages
  for select
  to authenticated
  using (auth.uid() = user_id);
