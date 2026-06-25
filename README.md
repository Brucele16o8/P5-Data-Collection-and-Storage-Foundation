# Project 05: MongoDB + Python Data Collection Foundation

This project collects and prepares Glamira product and IP-location data from a MongoDB event source. It is designed to run the same way on a laptop or on one EC2 instance:

- MongoDB runs in Docker.
- Python runs from this repository.
- Input dumps/reference files live on local disk or EBS.
- S3 is only used outside the app as optional file storage/transfer.

No managed ETL, warehouse, or BI service is required for Project 5.

## What This Produces

After a successful run, stakeholders should expect these files:

```text
outputs/<run-name>/
  product_targets.csv
  product_targets_summary.json
  product_information.jsonl
  product_information_summary.json
  product_failed_targets.csv
  product_information_warehouse.csv
  product_react_data.jsonl
  ip_locations.jsonl
  ip_locations_summary.json
```

Important outputs:

- `product_information_warehouse.csv`: flat product fields for later analysis.
- `product_react_data.jsonl`: raw crawled `react_data` archive for debugging/replay.
- `ip_locations.jsonl`: IP geolocation lookup results.
- `*_summary.json`: run counts, status counts, elapsed time, and validation evidence.

## Prerequisites

Install these before running:

- Python 3.10+
- `uv`
- Docker Desktop locally, or Docker Engine on EC2
- MongoDB dump files available on local disk or EBS
- Optional: IP2Location `.BIN` file for real geolocation enrichment

On EC2, first copy data from S3 to EBS if needed, for example:

```bash
aws s3 sync s3://your-bucket/path/to/mongodb-dump/ /data/glamira/mongodb-dump/
aws s3 cp s3://your-bucket/reference/IP-COUNTRY-REGION-CITY.BIN /data/glamira/reference/
```

The Python scripts read local paths after that copy.

## Setup

Install dependencies:

```bash
uv sync --extra dev
```

Start MongoDB:

```bash
docker compose up -d
```

Check MongoDB is reachable:

```bash
docker exec -it mongo-explore mongosh --eval "db.runCommand({ ping: 1 })"
```

Create `.env` from the example:

```bash
cp .env.example .env
```

Edit `.env` for your run:

```text
MONGO_URI=mongodb://localhost:27017
MONGO_DATABASE=countly_samples
MONGO_COLLECTION=summary_random_100000
OUTPUT_DIRECTORY=outputs/local-test
IP2LOCATION_DB_PATH=/path/to/IP-COUNTRY-REGION-CITY.BIN
```

## Load MongoDB Data

If you have a BSON dump, restore it into Docker MongoDB. Example:

```bash
docker exec -i mongo-explore mongorestore \
  --drop \
  --db countly \
  --collection summary \
  /data/import/countly_samples/summary_random_1000.bson
```

The `docker-compose.yml` mounts local `./data` to container path `/data/import`.

For a full dump directory, use the appropriate dump path:

```bash
docker exec -it mongo-explore mongorestore --drop /data/import/<dump-directory>
```

Confirm the collection has data:

```bash
docker exec -it mongo-explore mongosh countly --eval "db.summary.countDocuments()"
```

## Optional: Create a Sample Collection

Use this when the full collection is large and you want a smaller stakeholder demo:

```bash
uv run python scripts/create_mongodb_sample.py \
  --source-db countly \
  --source-collection summary \
  --target-db countly_samples \
  --target-collection summary_random_100000 \
  --size 100000 \
  --mode random
```

For a deterministic first-N sample:

```bash
uv run python scripts/create_mongodb_sample.py \
  --source-db countly \
  --source-collection summary \
  --target-db countly_samples \
  --target-collection summary_first_1000 \
  --size 1000 \
  --mode first
```

## Run The Pipeline

Choose a run folder:

```bash
export RUN_DIR=outputs/local-test
mkdir -p "$RUN_DIR"
```

Set the database and collection:

```bash
export MONGO_DATABASE=countly_samples
export MONGO_COLLECTION=summary_random_100000
```

### 1. Extract Product Crawl Targets

```bash
uv run python scripts/extract_product_targets.py \
  --database "$MONGO_DATABASE" \
  --collection "$MONGO_COLLECTION" \
  --output "$RUN_DIR/product_targets.csv" \
  --summary-output "$RUN_DIR/product_targets_summary.json"
```

Expected result:

- `product_targets.csv`
- `product_targets_summary.json`

Quick check:

```bash
head "$RUN_DIR/product_targets.csv"
cat "$RUN_DIR/product_targets_summary.json"
```

### 2. Crawl Product Information

For a quick demo, start with a small limit:

```bash
uv run python scripts/crawl_products.py \
  --input "$RUN_DIR/product_targets.csv" \
  --output "$RUN_DIR/product_information.jsonl" \
  --failed-output "$RUN_DIR/product_failed_targets.csv" \
  --summary-output "$RUN_DIR/product_information_summary.json" \
  --limit 100 \
  --workers 6 \
  --timeout-seconds 20
```

For a full run, remove `--limit 100`.

To resume a partially completed run:

```bash
uv run python scripts/crawl_products.py \
  --input "$RUN_DIR/product_targets.csv" \
  --output "$RUN_DIR/product_information.jsonl" \
  --failed-output "$RUN_DIR/product_failed_targets.csv" \
  --summary-output "$RUN_DIR/product_information_summary.json" \
  --resume \
  --workers 6 \
  --timeout-seconds 20
```

Expected result:

- `product_information.jsonl`
- `product_information_summary.json`
- `product_failed_targets.csv`

Quick check:

```bash
wc -l "$RUN_DIR/product_information.jsonl"
cat "$RUN_DIR/product_information_summary.json"
```

### 3. Split Product Output

```bash
uv run python scripts/export_product_warehouse.py \
  --input "$RUN_DIR/product_information.jsonl" \
  --warehouse-output "$RUN_DIR/product_information_warehouse.csv" \
  --react-data-output "$RUN_DIR/product_react_data.jsonl" \
  --warehouse-format csv
```

Expected result:

- `product_information_warehouse.csv`
- `product_react_data.jsonl`

Quick check:

```bash
head "$RUN_DIR/product_information_warehouse.csv"
wc -l "$RUN_DIR/product_react_data.jsonl"
```

### 4. Process IP Locations

If you have an IP2Location BIN file:

```bash
uv run python scripts/process_ip_locations.py \
  --database "$MONGO_DATABASE" \
  --collection "$MONGO_COLLECTION" \
  --ip2location-db-path "$IP2LOCATION_DB_PATH" \
  --output "$RUN_DIR/ip_locations.jsonl" \
  --summary-output "$RUN_DIR/ip_locations_summary.json" \
  --skip-mongodb-write
```

If you do not have the BIN file yet, run without it. The script will still validate IPs and mark lookups as not configured:

```bash
uv run python scripts/process_ip_locations.py \
  --database "$MONGO_DATABASE" \
  --collection "$MONGO_COLLECTION" \
  --output "$RUN_DIR/ip_locations.jsonl" \
  --summary-output "$RUN_DIR/ip_locations_summary.json" \
  --skip-mongodb-write
```

Expected result:

- `ip_locations.jsonl`
- `ip_locations_summary.json`

Quick check:

```bash
wc -l "$RUN_DIR/ip_locations.jsonl"
cat "$RUN_DIR/ip_locations_summary.json"
```

## Stakeholder Demo: 100 Product Records

This is the recommended demo flow:

```bash
export RUN_DIR=outputs/stakeholder-demo-100
mkdir -p "$RUN_DIR"

uv run python scripts/extract_product_targets.py \
  --database countly_samples \
  --collection summary_random_100000 \
  --output "$RUN_DIR/product_targets.csv" \
  --summary-output "$RUN_DIR/product_targets_summary.json"

uv run python scripts/crawl_products.py \
  --input "$RUN_DIR/product_targets.csv" \
  --output "$RUN_DIR/product_information.jsonl" \
  --failed-output "$RUN_DIR/product_failed_targets.csv" \
  --summary-output "$RUN_DIR/product_information_summary.json" \
  --limit 100 \
  --workers 6

uv run python scripts/export_product_warehouse.py \
  --input "$RUN_DIR/product_information.jsonl" \
  --warehouse-output "$RUN_DIR/product_information_warehouse.csv" \
  --react-data-output "$RUN_DIR/product_react_data.jsonl"
```

Review:

```bash
cat "$RUN_DIR/product_targets_summary.json"
cat "$RUN_DIR/product_information_summary.json"
head "$RUN_DIR/product_information_warehouse.csv"
```

## Validation And Tests

Run automated tests:

```bash
uv run --extra dev pytest
```

Run a Python compile check:

```bash
find src scripts tests -name '*.py' -print | sort | xargs python3 -m py_compile
```

The test suite covers:

- product URL extraction rules
- product crawler parsing
- URL fallback strategy
- product target exporting
- product output splitting
- crawl checkpoint/resume behavior
- IP validation

## Troubleshooting

MongoDB connection failed:

```bash
docker compose ps
docker logs mongo-explore --tail 50
```

No product targets:

- Confirm the selected collection has event records.
- Confirm records contain `collection`, `product_id` or `viewing_product_id`, and `current_url` or `referrer_url`.

Product crawl is slow:

- Use `--limit 100` for demos.
- Increase or decrease `--workers` depending on network stability.
- Use `--resume` after interruption.

IP geolocation shows `lookup_not_configured`:

- Set `IP2LOCATION_DB_PATH` to a local `.BIN` file.
- Or accept this status for a demo that only validates pipeline mechanics.

## Design Pattern Use

Patterns are used only where they reduce coupling:

- Strategy: product target filtering and product crawl URL attempt policy.
- Factory Method: output writer creation for CSV/JSONL.
- Adapter: CSV/JSONL sinks behind a common writer interface.
- Facade: product target and product export services.
- Observer/Composite: progress/log/audit event boundaries.
- Iterator: streaming JSONL and MongoDB-derived records instead of loading all rows.
- Memento-like checkpointing: crawler resume state in `crawl_state.py`.

This keeps the project modular without forcing all 23 Gang of Four patterns into places where they would add noise.
