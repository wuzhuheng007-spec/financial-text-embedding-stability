# Third-party dependency inventory (release review)

The version-pinned Python list is [requirements.lock](../../apps/quant_portal/requirements.lock). [Installed package metadata receipt](../../results/quant_portal_20261116/dependency_inventory.json) records the exact versions, lockfile SHA-256 and each distribution's declared license classifier/expression from the isolated local environment. These are metadata declarations, not an independent legal review. No dependency is copied into the Git repository by the application source commit.

| Package | Version | Installed metadata license | Review note |
| --- | ---: | --- | --- |
| Django | 5.2.19 | BSD-3-Clause | Retain license text in deployable distribution review. |
| asgiref | 3.13.0 | BSD-3-Clause | Transitive Django dependency. |
| gunicorn | 27.0.1 | MIT | Linux runtime server. |
| psycopg | 3.3.9 | LGPL-3.0-only | Review redistribution and dynamic-link obligations before an image release. |
| psycopg-binary | 3.3.9 | LGPL-3.0-only | Includes bundled binaries; review their notices and any transitive native code. |
| sqlparse | 0.6.1 | BSD License classifier; no SPDX expression in installed metadata | Confirm exact text from the upstream distribution before release. |
| typing_extensions | 4.16.0 | PSF-2.0 | Transitive dependency. |
| tzdata | 2026.6 | Apache-2.0 | Time-zone data package. |
| whitenoise | 6.12.0 | MIT | Static-file serving. |

The proposed container images are version/digest-pinned in [compose.yaml](../../apps/quant_portal/compose.yaml), [compose.prod.yaml](../../apps/quant_portal/compose.prod.yaml) and [Dockerfile](../../apps/quant_portal/Dockerfile). The Postgres, Caddy, Python and Debian base-image layer licenses and full software bill of materials remain **PENDING** because no container build could run locally. Do not describe the app or repository as already granted Apache-2.0; see [open-source proposal](open_source_review.md). No dataset, market feed, news body, private Site asset or model weight is bundled.
