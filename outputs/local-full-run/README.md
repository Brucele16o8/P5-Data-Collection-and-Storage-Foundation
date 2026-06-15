# Local Full Run Output

This folder contains the Project 5 local full-run deliverables that are small
enough to keep in GitHub.

The full product information dataset is stored as:

```text
product_information.jsonl.zst
```

Restore it locally with:

```bash
zstd -d product_information.jsonl.zst -o product_information.jsonl
```

Run summary:

- Target products: 19,417
- Successful product records: 18,423
- Failed targets: 994
- Success rate: 94.88%
- Runtime: 1h 28m 48s

The uncompressed `product_information.jsonl` file is about 2.9 GB and is kept
out of git because GitHub blocks normal repository files larger than 100 MiB.
