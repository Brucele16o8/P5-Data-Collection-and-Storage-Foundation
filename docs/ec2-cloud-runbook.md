# EC2 Cloud Runbook

This project is designed so local development and cloud execution use the same Python code and the same MongoDB connection string:

```text
mongodb://localhost:27017
```

That works because MongoDB Docker and the Python project run on the same machine in both environments.

## Stakeholder Execution Checklist

Use this order when setting up the cloud environment from zero:

```text
1. Create S3 bucket/prefixes
2. Upload raw MongoDB dump and IP2Location BIN to S3
3. Create VPC
4. Create public subnet
5. Attach internet gateway
6. Create route table
7. Confirm or configure network ACL
8. Create EC2 security group
9. Create EC2 IAM role for S3 access
10. Launch EC2 in the public subnet
11. Install Docker, Python, uv, AWS CLI, MongoDB Database Tools
12. Clone GitHub repository
13. Start Docker MongoDB
14. Download dump from S3
15. Restore dump into MongoDB
16. Run cloud sample test
17. Run full extraction, crawler, and IP geolocation
18. Upload outputs to S3
19. Stop or terminate EC2
```

The sections below explain each infrastructure component and why it exists.

## Architecture Decision

For Project 05, use one temporary EC2 instance for the full cloud execution:

```text
S3 raw MongoDB dump
        |
        v
EC2 instance
├── Git repository clone
├── uv Python environment
├── Docker MongoDB container
│   └── restored full countly.summary collection
├── product extraction and crawler scripts
└── AWS CLI / boto3
        |
        v
S3 product-information output
```

Your Mac remains the development environment:

```text
Mac
├── PyCharm
├── uv / .venv
├── Docker MongoDB
├── small MongoDB sample
└── local test outputs
```

GitHub is the transfer and version-control layer:

```text
Mac -> git push -> GitHub -> git clone / git pull -> EC2
```

This avoids a cloud networking mismatch because Python does not connect to a remote MongoDB service. On EC2, Python connects to the local Docker MongoDB container through `localhost`.

## Is The Recommendation Good?

The local-first recommendation is good for development, but it is not the final Project 05 execution design.

Use this split:

```text
Development:
  Mac + PyCharm + local Docker MongoDB sample

Final Project 05 cloud run:
  S3 dump -> EC2 -> Docker MongoDB + Python -> S3 output

Future production upgrade:
  S3 crawl list -> AWS Batch/Fargate crawler jobs -> S3 output
```

Do not start with Batch/Fargate for this project unless required. EC2 is simpler and matches the VM + MongoDB requirement.

## Cloud Infrastructure Design

This section explains the AWS infrastructure that must exist before running the Python pipeline on EC2.

For this project, the EC2 instance is a temporary processing machine. It downloads a MongoDB dump from S3, restores it into Docker MongoDB, runs the Python scripts, uploads output to S3, and can then be stopped or terminated.

Recommended Project 05 infrastructure:

```text
AWS account / region
└── VPC: p5-glamira-vpc
    ├── Internet Gateway
    ├── Public subnet: p5-public-subnet-a
    │   └── EC2: p5-glamira-runner
    │       ├── Docker MongoDB bound to 127.0.0.1:27017
    │       ├── Python project cloned from GitHub
    │       ├── AWS CLI / boto3
    │       └── IAM role for S3 read/write
    ├── Optional private subnet: p5-private-subnet-a
    ├── Route table for public subnet
    ├── Network ACL
    └── Security group for EC2
```

### Why This Design Is Acceptable For Project 05

The original requirement asks for:

```text
VM setup
MongoDB on VM
security policies
Python data processing
IP geolocation
product information collection
file outputs
GitHub repository
```

The AWS version maps that to:

```text
GCP VM             -> AWS EC2
GCS                -> Amazon S3
MongoDB on VM      -> Docker MongoDB on EC2
security policies  -> VPC, route table, NACL, security group, IAM role
```

MongoDB does not need to be reachable from the internet. Python runs on the same EC2 machine as MongoDB, so the connection stays:

```text
mongodb://localhost:27017
```

This avoids opening database ports and avoids configuring a remote database connection.

### Public Subnet vs Private Subnet

AWS subnets are classified by their routing. A public subnet has a route to an internet gateway. A private subnet does not have a direct route to an internet gateway.

For this project, use one of these two designs:

| Option | Recommended for | Design |
| --- | --- | --- |
| Simple project design | Project 05 assessment and manual testing | EC2 in a public subnet, SSH restricted to your IP, MongoDB closed to internet |
| More industry design | Production-style environments | EC2 in a private subnet, access through Systems Manager or a bastion host, NAT or VPC endpoints for outbound access |

Use the simple project design first unless your instructor explicitly requires a private subnet.

The simple design is still controlled:

- EC2 is in a public subnet only so you can SSH to it.
- SSH is restricted to your own IP address.
- MongoDB port `27017` is not opened in the security group.
- Docker binds MongoDB to `127.0.0.1`, so only programs on the EC2 instance can access it.
- S3 access uses an IAM role, not access keys.

## VPC Setup

Create a dedicated VPC for this project.

Recommended values:

| Setting | Value |
| --- | --- |
| Name | `p5-glamira-vpc` |
| IPv4 CIDR | `10.50.0.0/16` |
| IPv6 | Not required |
| DNS resolution | Enabled |
| DNS hostnames | Enabled |

Why:

- A dedicated VPC keeps this project isolated from other AWS work.
- `10.50.0.0/16` gives enough private IP space without conflicting with common home networks.
- DNS settings help EC2 and AWS services resolve names normally.

## Subnet Setup

Create at least one public subnet.

Recommended public subnet:

| Setting | Value |
| --- | --- |
| Name | `p5-public-subnet-a` |
| Availability Zone | Any one AZ in your selected region |
| IPv4 CIDR | `10.50.1.0/24` |
| Auto-assign public IPv4 | Enabled |

Optional private subnet:

| Setting | Value |
| --- | --- |
| Name | `p5-private-subnet-a` |
| Availability Zone | Same or different AZ |
| IPv4 CIDR | `10.50.11.0/24` |
| Auto-assign public IPv4 | Disabled |

For Project 05, the EC2 runner can be launched in the public subnet. The private subnet is included for industry understanding and future improvement, but it is not required for the current run.

## Internet Gateway

Create and attach an internet gateway:

| Setting | Value |
| --- | --- |
| Name | `p5-igw` |
| Attached VPC | `p5-glamira-vpc` |

Why:

- The EC2 instance needs outbound internet access to install packages, clone GitHub code, and crawl Glamira product pages.
- If the EC2 instance is in a public subnet, the internet gateway is also what allows SSH access from your own IP.

## Route Tables

Create a public route table.

Recommended public route table:

| Setting | Value |
| --- | --- |
| Name | `p5-public-rt` |
| VPC | `p5-glamira-vpc` |
| Associated subnet | `p5-public-subnet-a` |

Routes:

```text
10.50.0.0/16     local
0.0.0.0/0        p5-igw
```

The `0.0.0.0/0 -> internet gateway` route is what makes the subnet public.

Optional private route table:

| Setting | Value |
| --- | --- |
| Name | `p5-private-rt` |
| Associated subnet | `p5-private-subnet-a` |

Routes without NAT:

```text
10.50.0.0/16     local
```

Routes with NAT, if you later build a private-subnet design:

```text
10.50.0.0/16     local
0.0.0.0/0        NAT gateway
```

For the simple Project 05 design, NAT gateway is not required.

## Network ACL Setup

Network ACLs operate at the subnet level. Security groups operate at the EC2 instance level.

Recommended for this project:

```text
Use the default VPC network ACL, or create a simple project NACL that allows standard outbound/inbound return traffic.
Use the EC2 security group as the main security control.
```

Why:

- NACLs are stateless, so both inbound and outbound rules must be correct.
- Overly strict NACLs are a common cause of SSH, package install, S3, or web crawling failures.
- For a single temporary EC2 runner, security groups are easier and less error-prone.

If creating a custom public subnet NACL, use rules like this:

Inbound:

| Rule | Type | Protocol | Port | Source | Allow/Deny | Purpose |
| --- | --- | --- | --- | --- | --- | --- |
| 100 | SSH | TCP | 22 | Your public IP `/32` | Allow | SSH access |
| 110 | Ephemeral | TCP | 1024-65535 | `0.0.0.0/0` | Allow | Return traffic for outbound requests |
| * | All traffic | All | All | `0.0.0.0/0` | Deny | Default deny |

Outbound:

| Rule | Type | Protocol | Port | Destination | Allow/Deny | Purpose |
| --- | --- | --- | --- | --- | --- | --- |
| 100 | HTTPS | TCP | 443 | `0.0.0.0/0` | Allow | S3, GitHub, package downloads, web crawling |
| 110 | HTTP | TCP | 80 | `0.0.0.0/0` | Allow | Some package/web redirects |
| 120 | SSH response | TCP | 1024-65535 | Your public IP `/32` | Allow | Return traffic for SSH |
| * | All traffic | All | All | `0.0.0.0/0` | Deny | Default deny |

For many student projects, leaving the default NACL is acceptable and using a strict security group is clearer.

## Security Group Setup

Create one EC2 security group:

| Setting | Value |
| --- | --- |
| Name | `p5-ec2-runner-sg` |
| VPC | `p5-glamira-vpc` |
| Description | Security group for temporary Project 05 EC2 runner |

Inbound rules:

| Type | Protocol | Port | Source | Reason |
| --- | --- | --- | --- | --- |
| SSH | TCP | 22 | Your public IP `/32` | Allows you to connect to EC2 |

Do not add:

| Port | Reason |
| --- | --- |
| `27017` | MongoDB must not be publicly reachable |
| `80` / `443` inbound | The EC2 instance is not hosting a website |
| `0.0.0.0/0` SSH | Too open; avoid this |

Outbound rules:

| Type | Protocol | Port | Destination | Reason |
| --- | --- | --- | --- | --- |
| All traffic or HTTPS/HTTP | TCP | 443, 80 | `0.0.0.0/0` | GitHub, S3, package install, Glamira crawl |

For a stricter setup, allow outbound `443` and `80` only. For simpler project setup, default outbound allow-all is acceptable.

## IAM Role And S3 Permissions

Create an IAM role for EC2. Do not put AWS access keys on the instance.

Recommended role:

```text
p5-ec2-s3-role
```

Trusted entity:

```text
AWS service: EC2
```

Attach a least-privilege policy for the project bucket.

Example policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListProjectBucket",
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket"
      ],
      "Resource": "arn:aws:s3:::glamira-data-lake-20260611",
      "Condition": {
        "StringLike": {
          "s3:prefix": [
            "raw/*",
            "samples/*",
            "product-information/*"
          ]
        }
      }
    },
    {
      "Sid": "ReadRawAndSamples",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject"
      ],
      "Resource": [
        "arn:aws:s3:::glamira-data-lake-20260611/raw/*",
        "arn:aws:s3:::glamira-data-lake-20260611/samples/*"
      ]
    },
    {
      "Sid": "WriteProductInformation",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:AbortMultipartUpload"
      ],
      "Resource": "arn:aws:s3:::glamira-data-lake-20260611/product-information/*"
    }
  ]
}
```

Attach this role to the EC2 instance during launch, or attach it to the instance afterward.

On EC2, verify:

```bash
aws sts get-caller-identity
aws s3 ls s3://glamira-data-lake-20260611/
```

## S3 Layout

Recommended project bucket layout:

```text
s3://glamira-data-lake-20260611/
├── raw/
│   ├── source=mongodb_dump/
│   │   └── dataset=glamira_countly/
│   │       └── dump/
│   │           └── countly/
│   │               ├── summary.bson
│   │               └── summary.metadata.json
│   └── reference/
│       └── ip2location/
│           └── IP-COUNTRY-REGION-CITY.BIN
├── samples/
│   └── source=mongodb_sample/
└── product-information/
    └── run_date=YYYY-MM-DD/
```

Purpose:

| Prefix | Purpose |
| --- | --- |
| `raw/` | Original source files and reference files |
| `samples/` | Optional MongoDB-native test samples |
| `product-information/` | Final Project 05 outputs |

## EC2 Instance Setup

Launch one EC2 instance.

Recommended launch settings:

| Setting | Recommendation |
| --- | --- |
| Name | `p5-glamira-runner` |
| AMI | Ubuntu LTS or Amazon Linux 2023 |
| Instance type | Start with `t3.large`, `t3.xlarge`, `m6i.large`, or larger depending on dump size |
| VPC | `p5-glamira-vpc` |
| Subnet | `p5-public-subnet-a` |
| Auto-assign public IP | Enabled |
| IAM role | `p5-ec2-s3-role` |
| Security group | `p5-ec2-runner-sg` |
| EBS volume | Enough for dump + restored MongoDB + outputs |

Disk sizing rule:

```text
EBS size >= downloaded dump + restored MongoDB + indexes + outputs + working space
```

If the dump is 40 GB, do not use a 40 GB disk. Use significantly more space.

Recommended starting point when unsure:

```text
EBS gp3, 150-300 GB
```

After launch, connect by SSH:

```bash
ssh -i /path/to/key.pem ubuntu@<ec2-public-ip>
```

For Amazon Linux:

```bash
ssh -i /path/to/key.pem ec2-user@<ec2-public-ip>
```

Do not open MongoDB to your laptop. SSH is only for shell access.

## Files To Upload To GitHub

Commit source code and configuration templates:

```text
scripts/
src/
tests/
docs/
docker-compose.yml
pyproject.toml
uv.lock
README.md
.env.example
.gitignore
```

Do not commit:

```text
.env
.venv/
data/
outputs/
samples/
*.bson
*.jsonl
AWS credentials
MongoDB database files
large dump files
```

## EC2 Requirements

Choose an EC2 instance with enough disk for:

```text
downloaded dump
+ restored MongoDB database
+ MongoDB indexes and working files
+ crawler output
```

If the dump is large, allocate significantly more EBS storage than the dump size.

Install on EC2:

```text
Git
Docker and Docker Compose
Python
uv
AWS CLI
MongoDB Database Tools
```

MongoDB itself runs in Docker through `docker-compose.yml`. IAM and security group setup are covered in the detailed sections above.

## EC2 Setup Commands

Clone the project:

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
cd YOUR_REPOSITORY
```

Install Python dependencies:

```bash
uv sync
```

Start MongoDB Docker:

```bash
docker compose up -d mongodb
docker ps
```

Check MongoDB:

```bash
docker exec -it mongo-explore mongosh
```

## Download Dump From S3

Create a local data folder on EC2:

```bash
mkdir -p "$HOME/data/glamira-dump"
```

Download the raw MongoDB dump:

```bash
aws s3 cp \
  "s3://glamira-data-lake-20260611/raw/source=mongodb_dump/dataset=glamira_countly/dump/" \
  "$HOME/data/glamira-dump/" \
  --recursive
```

## Restore Dump Into Docker MongoDB

Restore the full dump:

```bash
mongorestore \
  --uri "mongodb://localhost:27017" \
  --drop \
  "$HOME/data/glamira-dump/"
```

Verify the restored collection:

```bash
docker exec -it mongo-explore mongosh countly --eval 'db.summary.countDocuments({})'
```

Expected database and collection:

```text
countly.summary
```

## Cloud Sample Test

Create a 100,000-record random MongoDB sample on EC2:

```bash
uv run python scripts/create_mongodb_sample.py \
  --uri "mongodb://localhost:27017" \
  --source-db countly \
  --source-collection summary \
  --target-db countly_samples \
  --target-collection summary_random_100000 \
  --size 100000 \
  --mode random
```

This keeps the sample inside MongoDB as native BSON documents. It does not convert the sample to JSONL.

To create a smaller 1,000-record sample for quick cloud validation, change the target collection and size:

```bash
uv run python scripts/create_mongodb_sample.py \
  --uri "mongodb://localhost:27017" \
  --source-db countly \
  --source-collection summary \
  --target-db countly_samples \
  --target-collection summary_random_1000 \
  --size 1000 \
  --mode random
```

## Save A MongoDB Sample To S3

If you want to move a MongoDB-native sample to S3 for later cloud checking, use `mongodump`. This preserves BSON/native MongoDB types and does not convert records to JSONL.

Dump the 1,000-record sample:

```bash
mkdir -p data/sample-dumps

docker exec mongo-explore mongodump \
  --uri "mongodb://localhost:27017" \
  --db countly_samples \
  --collection summary_random_1000 \
  --out /data/import/sample-dumps/

docker cp \
  mongo-explore:/data/import/sample-dumps/countly_samples \
  data/sample-dumps/countly_samples
```

Upload the sample dump to S3:

```bash
aws s3 cp \
  "data/sample-dumps/countly_samples/" \
  "s3://glamira-data-lake-20260611/samples/source=mongodb_sample/dataset=countly_samples/collection=summary_random_1000/" \
  --recursive
```

Restore that sample later on EC2:

```bash
mkdir -p "$HOME/data/sample-dumps/countly_samples"

aws s3 cp \
  "s3://glamira-data-lake-20260611/samples/source=mongodb_sample/dataset=countly_samples/collection=summary_random_1000/" \
  "$HOME/data/sample-dumps/countly_samples/" \
  --recursive

mongorestore \
  --uri "mongodb://localhost:27017" \
  --drop \
  "$HOME/data/sample-dumps/"
```

Extract product targets from the cloud sample:

```bash
MONGO_URI="mongodb://localhost:27017" \
MONGO_DATABASE="countly_samples" \
MONGO_COLLECTION="summary_random_100000" \
OUTPUT_DIRECTORY="outputs/aws-test" \
uv run python scripts/extract_product_targets.py
```

For the quick 1,000-record sample test, use:

```bash
MONGO_URI="mongodb://localhost:27017" \
MONGO_DATABASE="countly_samples" \
MONGO_COLLECTION="summary_random_1000" \
OUTPUT_DIRECTORY="outputs/aws-test-1000" \
uv run python scripts/extract_product_targets.py
```

Crawl a small validation batch first:

```bash
OUTPUT_DIRECTORY="outputs/aws-test" \
uv run python scripts/crawl_products.py \
  --limit 100 \
  --timeout-seconds 20
```

Check the result:

```bash
head -n 3 outputs/aws-test/product_information.jsonl
```

The crawler should:

```text
try original MongoDB URL
try /catalog/product/view/id/{product_id}
prefer English-language stores first
parse var react_data
write one active product record per product_id
record failed attempts without crashing
```

## IP2Location From S3

Store the IP2Location BIN file in S3:

```text
s3://glamira-data-lake-20260611/raw/reference/ip2location/IP-COUNTRY-REGION-CITY.BIN
```

Process distinct IP addresses from the sample:

```bash
MONGO_URI="mongodb://localhost:27017" \
MONGO_DATABASE="countly_samples" \
MONGO_COLLECTION="summary_random_1000" \
OUTPUT_DIRECTORY="outputs/aws-test" \
IP2LOCATION_DB_URI="s3://glamira-data-lake-20260611/raw/reference/ip2location/IP-COUNTRY-REGION-CITY.BIN" \
uv run python scripts/process_ip_locations.py
```

Process distinct IP addresses from the full collection:

```bash
MONGO_URI="mongodb://localhost:27017" \
MONGO_DATABASE="countly" \
MONGO_COLLECTION="summary" \
OUTPUT_DIRECTORY="outputs/full-run" \
IP2LOCATION_DB_URI="s3://glamira-data-lake-20260611/raw/reference/ip2location/IP-COUNTRY-REGION-CITY.BIN" \
uv run python scripts/process_ip_locations.py
```

The script downloads the BIN file to a temporary local path on EC2, performs lookups, and writes:

```text
outputs/aws-test/ip_locations.jsonl
outputs/full-run/ip_locations.jsonl
countly_enriched.ip_locations
```

## Full Cloud Run

Extract targets from the full collection:

```bash
MONGO_URI="mongodb://localhost:27017" \
MONGO_DATABASE="countly" \
MONGO_COLLECTION="summary" \
OUTPUT_DIRECTORY="outputs/full-run" \
uv run python scripts/extract_product_targets.py
```

Crawl full product target set:

```bash
OUTPUT_DIRECTORY="outputs/full-run" \
uv run python scripts/crawl_products.py \
  --timeout-seconds 20
```

Process IP locations for the full collection:

```bash
MONGO_URI="mongodb://localhost:27017" \
MONGO_DATABASE="countly" \
MONGO_COLLECTION="summary" \
OUTPUT_DIRECTORY="outputs/full-run" \
IP2LOCATION_DB_URI="s3://glamira-data-lake-20260611/raw/reference/ip2location/IP-COUNTRY-REGION-CITY.BIN" \
uv run python scripts/process_ip_locations.py
```

Upload results to S3:

```bash
RUN_DATE="$(date +%F)"

aws s3 cp \
  "outputs/full-run/" \
  "s3://glamira-data-lake-20260611/product-information/run_date=${RUN_DATE}/" \
  --recursive
```

## Updating Code On EC2

After making local changes:

```bash
git add .
git commit -m "Update Project 05 cloud pipeline"
git push
```

On EC2:

```bash
git pull
uv sync
```

Then rerun the sample test or full run.

## When To Stop Or Terminate EC2

This EC2 instance is a temporary processing machine:

```text
launch EC2
download dump
restore MongoDB
run sample test
run full pipeline
upload outputs to S3
stop or terminate EC2
```

Stop the instance if you may rerun soon. Terminate it if the run is finished and the outputs are safely in S3.
