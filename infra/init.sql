-- Proposed physical model (selyla owns the real migrations).
-- Optimistic stock.version is the concurrency token; Redis is not the source of truth.

CREATE TABLE IF NOT EXISTS warehouses (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS locations (
  id TEXT PRIMARY KEY,
  warehouse_id TEXT NOT NULL REFERENCES warehouses(id),
  zone TEXT NOT NULL,
  shelf TEXT NOT NULL,
  bin TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skus (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  shelf_life_days INT
);

CREATE TABLE IF NOT EXISTS lots (
  id TEXT PRIMARY KEY,
  sku_id TEXT NOT NULL REFERENCES skus(id),
  batch_no TEXT NOT NULL,
  expiry DATE,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS stock (
  id BIGSERIAL PRIMARY KEY,
  warehouse_id TEXT NOT NULL REFERENCES warehouses(id),
  location_id TEXT NOT NULL REFERENCES locations(id),
  lot_id TEXT NOT NULL REFERENCES lots(id),
  qty NUMERIC(18, 3) NOT NULL,
  version INT NOT NULL DEFAULT 1,
  UNIQUE (warehouse_id, location_id, lot_id)
);

CREATE TABLE IF NOT EXISTS ledger (
  id BIGSERIAL PRIMARY KEY,
  at TIMESTAMPTZ NOT NULL DEFAULT now(),
  sku_id TEXT NOT NULL,
  warehouse_id TEXT NOT NULL,
  ref_type TEXT NOT NULL,
  ref_id TEXT NOT NULL,
  qty_delta NUMERIC(18, 3) NOT NULL,
  note TEXT
);

CREATE INDEX IF NOT EXISTS stock_sku_wh_idx ON stock (warehouse_id, lot_id);

-- Optimistic consume (him): version is the concurrency token.
-- UPDATE stock
--    SET qty = qty - :take, version = version + 1
--  WHERE warehouse_id = :wh AND location_id = :loc AND lot_id = :lot
--    AND version = :version_seen AND qty >= :take
-- RETURNING id, version;
-- 0 rows => StockConflict (or treat as InsufficientStock when qty < take).
-- Hot-path optional: SELECT ... FOR UPDATE of the candidate rows in the same txn
-- before planning, then still bump version so readers detect lost updates.

CREATE INDEX IF NOT EXISTS stock_wh_loc_lot_version_idx
  ON stock (warehouse_id, location_id, lot_id, version);

CREATE INDEX IF NOT EXISTS lots_expiry_idx ON lots (expiry NULLS LAST, received_at);
CREATE INDEX IF NOT EXISTS lots_received_idx ON lots (received_at);
