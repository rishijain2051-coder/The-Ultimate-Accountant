-- Web Push: each device's subscription and what it wants; and the office's settings that
-- aren't secret (the big-transaction threshold), read by the office server with the roster.

create table push_subscriptions (
  id          bigserial primary key,
  device_id   uuid not null references devices (id) on delete cascade,
  endpoint    text not null unique,
  p256dh      text not null,
  auth        text not null,
  wants       jsonb not null default '{"overdue": true, "big": false, "admin": true}',
  created_at  timestamptz not null default now(),
  last_ok_at  timestamptz
);
create index push_subscriptions_device on push_subscriptions (device_id);
alter table push_subscriptions enable row level security;

create table office_settings (
  key    text primary key,
  value  jsonb not null
);
alter table office_settings enable row level security;
insert into office_settings (key, value) values ('big_threshold_paise', '10000000');   -- ₹1,00,000
