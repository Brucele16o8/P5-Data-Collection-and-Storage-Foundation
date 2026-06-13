# AWS Architecture

The Project 05 AWS implementation uses two separate environments.

```text
LOCAL DEVELOPMENT ENVIRONMENT
Your Mac
├── PyCharm + AI support
├── Python source code
├── Docker MongoDB
│   └── small/sample Glamira data
└── local test output
        |
        | git push
        v
GitHub repository
        |
        | git clone / git pull
        v
AWS CLOUD ENVIRONMENT
EC2 instance
├── Python source code from GitHub
├── Docker MongoDB
│   └── full Glamira data restored from S3
├── crawler execution
└── output uploaded to S3
```

Local development:

```text
Python on Mac
  -> mongodb://localhost:27017
  -> Docker MongoDB sample collection
  -> outputs/local-test
```

AWS execution:

```text
Python on EC2
  -> mongodb://localhost:27017
  -> Docker MongoDB full collection
  -> outputs/aws-test, then outputs/full-run
  -> S3 product-information/
```

The connection string can stay as `mongodb://localhost:27017` in both places because Python and Docker MongoDB run on the same machine in each environment.

Use the same Docker container name in both places when possible:

```text
mongo-explore
```

Sample-first execution order:

```text
local sample
  -> local tests
  -> push code
  -> EC2 cloud sample
  -> EC2 full collection
  -> S3 upload
```

Full-data local execution is allowed when the Mac has enough resources, but EC2 remains the required cloud validation environment:

```text
S3 raw MongoDB dump
  -> EC2 downloads dump
  -> mongorestore into Docker MongoDB on EC2
  -> Python extracts product targets from countly.summary
  -> Python crawls product pages
  -> S3 product-information output
```

EC2 should use an IAM role:

```text
EC2 role
  -> read s3://glamira-data-lake-20260611/raw/*
  -> write s3://glamira-data-lake-20260611/product-information/*
```

Do not copy local AWS SSO profiles, admin credentials, MongoDB database files, dump files, or crawler outputs into GitHub.

The Python code stays environment-independent:

```text
MONGO_URI
MONGO_DATABASE
MONGO_COLLECTION
OUTPUT_DIRECTORY
```

Changing those values switches between:

```text
countly_samples.summary_random_100000
countly.summary
```

and between:

```text
outputs/local-test
outputs/aws-test
outputs/full-run
```

Glue, Redshift, Batch, ECS, and Fargate remain possible future upgrades, but they are not required for the VM + MongoDB objective.
