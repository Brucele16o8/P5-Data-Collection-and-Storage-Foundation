CREATE TABLE IF NOT EXISTS staging.stg_events (
    event_type VARCHAR(100),
    event_time TIMESTAMP,
    ip VARCHAR(64),
    product_id VARCHAR(100),
    viewing_product_id VARCHAR(100),
    current_url VARCHAR(2000),
    referrer_url VARCHAR(2000),
    source_file VARCHAR(500),
    ingested_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS analytics.dim_location (
    ip VARCHAR(64),
    country VARCHAR(100),
    region VARCHAR(100),
    city VARCHAR(100),
    latitude DECIMAL(9,6),
    longitude DECIMAL(9,6),
    processed_at TIMESTAMP,
    status VARCHAR(50),
    error_message VARCHAR(1000)
);

CREATE TABLE IF NOT EXISTS analytics.dim_product (
    product_id VARCHAR(100),
    source_url VARCHAR(2000),
    product_name VARCHAR(500),
    category VARCHAR(255),
    price DECIMAL(12,2),
    currency VARCHAR(10),
    active BOOLEAN,
    scraped_at TIMESTAMP,
    status VARCHAR(50),
    error_message VARCHAR(1000)
);
