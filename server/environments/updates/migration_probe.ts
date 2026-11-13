/** Trusted wrapper around the admitted v1 candidate's existing readonly producer. */
export const PYTHON_SCHEMA_STATUS_PROBE = String.raw`
import json, pathlib, sys, os
root = pathlib.Path(sys.argv[1]).resolve()
assert sys.version.split()[0] == sys.argv[2], sys.version
for path in (sys.executable, sys.prefix, sys.base_prefix, os.__file__):
    pathlib.Path(path).resolve().relative_to(root)
from planner.environments import update_install
pathlib.Path(update_install.__file__).resolve().relative_to(root)
try:
    pending = update_install.schema_change_pending(pathlib.Path(sys.argv[3]), root)
except ValueError as error:
    print(json.dumps({"error": str(error)}))
else:
    print(json.dumps({"schema_change_pending": pending}))
`;
