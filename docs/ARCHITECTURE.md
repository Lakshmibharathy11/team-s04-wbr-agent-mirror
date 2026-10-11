# Architecture

Olist data -> S3 source/fixtures -> Athena allowlisted queries -> validation runner -> Bedrock reasoning -> evidence-linked weekly report

## AWS resources
- Region: us-east-2
- Source bucket: YOUR-SOURCE-BUCKET
- Athena results bucket: YOUR-ATHENA-RESULTS-BUCKET
- Glue database: s04_wbr
- Athena workgroup: S04-WBR

## Guardrails
- MCP-dedicated IAM user with least privilege
- Athena workgroup configuration enforced
- Athena query results use SSE-S3
- 100 MB per-query scan cutoff
- Athena results expire after 30 days
- incomplete multipart uploads aborted after 7 days
- no arbitrary model-generated SQL
