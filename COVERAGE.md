# Singapore employer coverage

The tracker has 62 direct employer boards covering 61 employers. It monitors Singapore technical and quantitative internships and graduate / entry-level roles. Direct coverage does not imply that matching openings exist today.

Your complete 63-company target list and supplied career links are in [EMPLOYERS.md](EMPLOYERS.md), with machine-readable entries in [employers.json](employers.json). Of those employers, 41 have direct monitoring and 22 currently depend on optional Adzuna searches.

## Direct employer monitoring

| Connection | Employers |
| --- | --- |
| lever | Binance, CSIT, CoinGecko, Crypto.com, Palantir |
| ashby | Airwallex, BlockTech, Simular, Venti Technologies |
| greenhouse | Adyen, Cloudflare, Coinbase, Datadog, GitLab, HRT, Jane Street, Jump Trading, Marshall Wace, MongoDB, OKX, Optiver, Point72, Pure Storage, QRT, Ripple, Rubrik, SimplifyNext, Squarepoint, Stripe, Virtu, Workato |
| smartrecruiters | Bosch, Foodpanda, Grab, NCS, SAP, ServiceNow, Wise |
| amazon | Amazon / AWS |
| google | Google |
| workday | Adobe, Autodesk, BlackRock, Broadcom, Cisco, Citi, CrowdStrike, HP, HPE, Intel, Manulife, Mastercard, Micron, NVIDIA, PayPal, Red Hat, Salesforce, Samsung, Workday, Zendesk |
| recruitee | OGP |

Workday and Amazon feeds are paginated. Workday details verify actual and secondary locations. Google uses public early-career search pages. Recruitee supplies OGP roles. Greenhouse employment metadata supplies internship and new-graduate evidence when it is missing from the title. Failed or incomplete feeds preserve existing statuses.

## Optional Adzuna employer searches

These run only when both `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` are set as GitHub Actions secrets. The `adzuna_companies` list includes:

Microsoft, Meta, Apple, TikTok, ByteDance, Shopee, Sea, Lazada, Alibaba, Tencent, Oracle, IBM, Dell, AMD, Qualcomm, ServiceNow, Netflix, Spotify, Snap, Snowflake, Databricks, Salesforce, Adobe, NVIDIA, Amazon, Google, Samsung, Micron, Broadcom, Citadel, LinkedIn, Sea Limited, Government Technology Agency, GIC, Goldman Sachs, JPMorgan Chase, Morgan Stanley, UBS, Visa, Revolut, DBS, OCBC, UOB, Standard Chartered.

Adzuna coverage is incomplete, employer names may differ, and truncated descriptions can fail eligibility checks. Employers marked optional are not directly monitored. Each role query and each employer search uses one API request per scan; manage the lists and frequency according to your account quota. The `company` parameter is documented in [Adzuna API reference](https://developer.adzuna.com/activedocs).

## LinkedIn Jobs

See [LINKEDIN.md](LINKEDIN.md) for Singapore search links and native daily alerts. These alerts arrive through LinkedIn/email and are not imported into this bot.

## Schedule

Scans run every two hours, with new/reopened job alerts and a periodic summary every 24 hours. GitHub may delay runs. Push local changes to the default branch and run the workflow once to activate them.
