CREATE TABLE pro_wms.revision (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL,
    wave_seq BIGINT NOT NULL DEFAULT 1,
    users JSONB NOT NULL DEFAULT '{"operator":"operator","supervisor":"supervisor","admin":"admin"}'
);
INSERT INTO pro_wms.revision (id, version) VALUES (1, 1);
CREATE TABLE pro_wms.warehouses (id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE pro_wms.skus (id TEXT PRIMARY KEY, payload JSONB NOT NULL);
CREATE TABLE pro_wms.locations (
    id TEXT PRIMARY KEY,
    warehouse TEXT NOT NULL REFERENCES pro_wms.warehouses(id),
    UNIQUE (warehouse, id)
);
CREATE TABLE pro_wms.lots (
    id TEXT PRIMARY KEY,
    sku TEXT NOT NULL REFERENCES pro_wms.skus(id),
    batch_no TEXT NOT NULL,
    expiry DATE,
    received_at DATE NOT NULL
);
CREATE TABLE pro_wms.stock (
    warehouse TEXT NOT NULL,
    location TEXT NOT NULL,
    lot_id TEXT NOT NULL REFERENCES pro_wms.lots(id),
    qty BIGINT NOT NULL CHECK (qty >= 0),
    reserved BIGINT NOT NULL CHECK (reserved >= 0 AND reserved <= qty),
    version BIGINT NOT NULL CHECK (version > 0),
    PRIMARY KEY (warehouse, location, lot_id),
    FOREIGN KEY (warehouse, location) REFERENCES pro_wms.locations(warehouse, id)
);
CREATE TABLE pro_wms.documents (
    kind TEXT NOT NULL CHECK (kind IN ('inbounds','outbounds','waves','stocktakes')),
    id TEXT NOT NULL,
    warehouse TEXT NOT NULL REFERENCES pro_wms.warehouses(id),
    status TEXT NOT NULL,
    lines JSONB NOT NULL,
    meta JSONB NOT NULL,
    PRIMARY KEY (kind, id)
);
CREATE TABLE pro_wms.ledger (
    id BIGINT PRIMARY KEY,
    sku TEXT NOT NULL REFERENCES pro_wms.skus(id),
    warehouse TEXT NOT NULL REFERENCES pro_wms.warehouses(id),
    ref_type TEXT NOT NULL,
    ref_id TEXT NOT NULL,
    qty_delta BIGINT NOT NULL,
    note TEXT NOT NULL
);
CREATE TABLE pro_wms.requests (
    key TEXT PRIMARY KEY CHECK (length(key) BETWEEN 1 AND 128),
    fingerprint TEXT NOT NULL,
    response JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ledger_sku_warehouse ON pro_wms.ledger (sku, warehouse, id);
CREATE INDEX documents_warehouse_status ON pro_wms.documents (warehouse, status);
