CREATE TABLE IF NOT EXISTS commands (
  id SERIAL,
  channel_id INT,
  name VARCHAR(50),
  data JSONB,
  author TEXT,
  text TEXT,
  discord BOOLEAN,
  twitch BOOLEAN,
  CONSTRAINT uniq_name_in_channel UNIQUE (channel_id, name)
);
CREATE TABLE IF NOT EXISTS channels (
  id SERIAL,
  channel_id INT,
  discord_guild_id varchar(50),
  discord_command_prefix varchar(10),
  twitch_channel_name varchar(50),
  twitch_command_prefix varchar(10),
  twitch_events TEXT,
  twitch_bot TEXT,
  twitch_throttle INT,
  discord_allowed_channels TEXT
);
CREATE TABLE IF NOT EXISTS twitch_bots (
  id SERIAL,
  channel_name TEXT,
  api_app_id TEXT,
  api_app_secret TEXT,
  api_url TEXT,
  api_port INT,
  auth_token TEXT,
  refhesh_token TEXT
);
CREATE TABLE IF NOT EXISTS variables (
  channel_id INT,
  name varchar(100),
  value TEXT,
  category varchar(100),
  expires INT,
  CONSTRAINT uniq_variable UNIQUE (channel_id, name, category)
);
CREATE TABLE IF NOT EXISTS texts (
  id SERIAL PRIMARY KEY,
  channel_id INT NOT NULL,
  value TEXT,
  CONSTRAINT uniq_text_value UNIQUE (channel_id, value)
);
CREATE TABLE IF NOT EXISTS tags (
  id SERIAL PRIMARY KEY,
  channel_id INT NOT NULL,
  value varchar(100),
  CONSTRAINT uniq_tag_value UNIQUE (channel_id, value)
);
CREATE TABLE IF NOT EXISTS text_tags (
  tag_id INT REFERENCES tags (id) ON DELETE CASCADE,
  text_id INT REFERENCES texts (id) ON DELETE CASCADE,
  value TEXT,
  CONSTRAINT uniq_text_tag UNIQUE (tag_id, text_id)
);