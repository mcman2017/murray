"""Murray: one cognitive agent built from four Gemini 4 models.

Hydrogen = salience gate, subconscious workers, speech (Broca).
Neon     = frontoparietal executive: the 4-slot Global Workspace.
Argon    = posterior visual cortex: the closed-loop visual critic.
Carbon   = default mode network: sleep-time memory consolidation.
"""

import os

# the image tag, stamped at build time (scripts/build.sh); "dev" in a source checkout
__version__ = os.environ.get("MURRAY_VERSION") or "dev"
