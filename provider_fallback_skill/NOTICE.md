# Source and dependency notices

Copyright (C) 2026 Wingman API Waterfall contributors.
Licensed under GPL-3.0-or-later; see LICENSE.

The owned loopback listener lifecycle adapts this project's existing speech bridge.
Wingman provides the public v3 Skill, secret and configuration interfaces; no
installed Core file is patched. The routing core is independent of Star Citizen,
other custom skills and Crankcase.

The package imports Wingman's existing runtime dependencies rather than bundling
their source or executables: HTTPX (BSD-3-Clause), FastAPI (MIT), Starlette
(BSD-3-Clause) and Uvicorn (BSD-3-Clause). Their respective supplier licenses and
Wingman's own GPL license remain applicable. No model weights, credentials,
provider accounts, private configuration or media ship in this package.
