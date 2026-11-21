import os
import tempfile

# Tests never talk to a real model: mock backend, in-process sandbox, a throwaway data directory.
_tmp = tempfile.mkdtemp(prefix="murray-test-")
os.environ["MURRAY_DATA_DIR"] = _tmp
os.environ["MURRAY_LLM_MODE"] = "mock"
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("GOOGLE_API_KEY", None)
os.environ["MURRAY_SANDBOX_URL"] = ""
os.environ["MURRAY_ADMIN_TOKEN"] = "test-admin-token"
os.environ["MURRAY_AUTOSTART"] = "off"
os.environ["MURRAY_SANDBOX_WALL_S"] = "6"
os.environ["MURRAY_SPEND_RATE_USD_PER_10MIN"] = "1000"
os.environ["MURRAY_VISITOR_RATE_USD_PER_10MIN"] = "1000"
os.environ["MURRAY_PUBLIC_DAILY_CAP_USD"] = "1000"
os.environ["MURRAY_BUILD_BUDGET_USD"] = "1000"
