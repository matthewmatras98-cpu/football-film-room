CREATE TABLE IF NOT EXISTS snapshots (
  snapshot_ts TEXT NOT NULL,
  game_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  book TEXT NOT NULL,
  market TEXT NOT NULL,
  side TEXT NOT NULL,
  point TEXT NOT NULL,
  price INTEGER NOT NULL,
  last_update TEXT NOT NULL,
  shot_kind TEXT NOT NULL,
  PRIMARY KEY (snapshot_ts, game_id, book, market, side)
);

CREATE TABLE IF NOT EXISTS shots (
  shot_key TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  window_kickoff TEXT,
  snapshot_ts TEXT,
  credits_remaining INTEGER,
  status TEXT NOT NULL
);
