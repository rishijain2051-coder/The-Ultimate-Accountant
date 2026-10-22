-- Office PCs signing in with their own keys, and the Keeper's data key wrapped for each device
-- (PLAN-office.md, sections 2.2, 2.4 and 4).

-- A PC asks for a token by signing a time; each must be later than the last, so a captured
-- request can't be sent again.
alter table devices add column last_token_ts bigint not null default 0;

-- The data key (DEK), wrapped by the office server for one device: ECDH with a fresh key
-- against the device's wrap_pub, HKDF-SHA256, AES-256-GCM. Ciphertext only.
create table wrapped_keys (
    device_id   uuid not null references devices (id) on delete cascade,
    kid         text not null,
    epk         text not null,          -- the fresh P-256 public key, SPKI, base64
    nonce       text not null,          -- base64
    ct          text not null,          -- the DEK under the derived key, with its tag, base64
    created_at  timestamptz not null default now(),
    primary key (device_id, kid)
);

-- Which key id the office server encrypts with now. Older ones stay for older files.
create table key_ring (
    kid         text primary key,
    created_at  timestamptz not null default now(),
    is_current  boolean not null default false
);

alter table wrapped_keys enable row level security;
alter table key_ring     enable row level security;
