create extension if not exists pgcrypto;

create table if not exists public.licenses (
  id uuid primary key default gen_random_uuid(),
  license_key_hash text unique not null,
  label text,
  status text not null default 'active' check (status in ('active', 'revoked', 'expired')),
  expires_at timestamptz,
  max_machines integer not null default 1 check (max_machines > 0),
  created_at timestamptz not null default now(),
  revoked_at timestamptz,
  notes text
);

create table if not exists public.license_activations (
  id uuid primary key default gen_random_uuid(),
  license_id uuid not null references public.licenses(id) on delete cascade,
  machine_fingerprint_hash text not null,
  machine_label text,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  last_ip text,
  unique (license_id, machine_fingerprint_hash)
);

create table if not exists public.license_events (
  id uuid primary key default gen_random_uuid(),
  license_id uuid references public.licenses(id) on delete set null,
  event_type text not null,
  machine_fingerprint_hash text,
  created_at timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb
);

alter table public.licenses enable row level security;
alter table public.license_activations enable row level security;
alter table public.license_events enable row level security;

create index if not exists license_activations_license_id_idx
  on public.license_activations (license_id);

create index if not exists license_events_license_id_created_at_idx
  on public.license_events (license_id, created_at desc);

create index if not exists license_events_type_created_at_idx
  on public.license_events (event_type, created_at desc);
