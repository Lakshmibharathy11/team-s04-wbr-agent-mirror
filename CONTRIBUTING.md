# Contributing

## Branch workflow
1. Create one branch per task from `main`.
2. Keep commits focused and reviewable.
3. Open a pull request into `main`.
4. Another teammate reviews before merge.
5. Prefer squash merge.

## Security and data rules
Never commit:
- AWS credentials or access keys
- `.env` files or secrets
- raw Olist datasets
- generated Athena result files
- production/customer data

Use only public Olist data and clearly labeled synthetic fixtures.

## Definition of done
A change is complete only when:
- relevant tests pass;
- evidence/metric semantics are preserved;
- failure paths do not fabricate completion;
- README/docs are updated if behavior changed.
