# Bundled Athens timezone fallback

`Europe/Athens` is an unmodified binary TZif file copied from the managed Linux
runtime's `/usr/share/zoneinfo/Europe/Athens`. Its installed IANA timezone database
identifies itself as **2026b** in `/usr/share/zoneinfo/tzdata.zi`.

SHA-256: `5c363e14151d751c901cdf06c502d9e1ac23b8e956973954763bfb39d5c53730`

IANA tz database data is in the public domain. Source and licensing information:
<https://www.iana.org/time-zones> and <https://data.iana.org/time-zones/tz-link.html>.
This file was copied from the preinstalled OS database without a network download
or modification; `cmp` verified the copy.

Python uses the system IANA database first. When it is absent (for example, on
Windows without an optional tzdata package), `server.py` loads this bundled file
using Python's standard `zoneinfo.ZoneInfo.from_file`, preserving Athens daylight
saving rules without installing third-party dependencies. Refresh this fallback
from a trusted current OS/IANA release if Greek timezone law changes. The current
Greek pilot uses Athens calendar days; per-account timezones remain future work.
