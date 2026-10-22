-- People, their devices, sign-in sessions and the audit log (PLAN-office.md, sections 2 and 7).
-- Run by cp/migrate.py with search_path set to the control plane's own schema ("office"),
-- which Supabase's Data API does not expose. Row-level security is on with no policies,
-- so Supabase's anon and authenticated roles reach nothing even if the schema were exposed.

create table users (
    id          bigint generated always as identity primary key,
    email       text not null,
    name        text not null default '',
    caps        text[] not null default '{}'
                check (caps <@ array['books', 'tib', 'console', 'admin']),
    status      text not null default 'active' check (status in ('active', 'disabled')),
    google_sub  text unique,                        -- bound at the first sign-in
    created_at  timestamptz not null default now(),
    created_by  bigint references users (id)
);
create unique index users_email on users (lower(email));

create table devices (
    id                  uuid primary key default gen_random_uuid(),
    user_id             bigint not null references users (id),
    kind                text not null default 'viewer'
                        check (kind in ('office_server', 'office_client', 'viewer')),
    name                text not null default '',
    os                  text not null default '',
    app_version         text not null default '',
    sign_pub            text not null unique,       -- ECDSA P-256, SPKI, base64
    wrap_pub            text not null,              -- ECDH P-256, SPKI, base64
    fingerprint         text not null,
    status              text not null default 'pending'
                        check (status in ('pending', 'approved', 'revoked')),
    approval            text,                       -- the exact text the approver signed
    approval_sig        text,                       -- ECDSA P-256 over it, raw r||s, base64
    approved_by_device  uuid references devices (id),
    approved_at         timestamptz,
    revoked_at          timestamptz,
    first_seen          timestamptz not null default now(),
    last_seen           timestamptz not null default now(),
    last_ip             text not null default ''
);
create index devices_user on devices (user_id);

create table sessions (
    id          bigint generated always as identity primary key,
    token_hash  bytea not null unique,              -- sha256 of the cookie; the cookie itself is never stored
    user_id     bigint not null references users (id),
    device_id   uuid references devices (id),
    challenge   text not null,                      -- what the device signs to attach itself
    created_at  timestamptz not null default now(),
    last_used   timestamptz not null default now(),
    expires_at  timestamptz not null,
    revoked_at  timestamptz,
    ip          text not null default '',
    user_agent  text not null default ''
);
create index sessions_user on sessions (user_id);
create index sessions_device on sessions (device_id);

create table audit (
    id         bigint generated always as identity primary key,
    at         timestamptz not null default now(),
    user_id    bigint references users (id),
    device_id  uuid references devices (id),
    action     text not null,
    detail     jsonb not null default '{}',
    ip         text not null default ''
);
create index audit_at on audit (at desc);

alter table users    enable row level security;
alter table devices  enable row level security;
alter table sessions enable row level security;
alter table audit    enable row level security;
