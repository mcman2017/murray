import asyncio

from murray.sandbox import policy
from murray.sandbox.client import LocalSandbox

GOOD = '''import numpy as np
from PIL import Image
a = np.zeros((60, 80, 3), dtype=np.uint8)
a[:, :, 0] = np.linspace(0, 255, 80).astype(np.uint8)
Image.fromarray(a).save("out.png")
'''


def test_policy_refuses_escapes():
    for bad in ["import os", "import subprocess", "open('/etc/passwd').read()", "eval('1+1')", "__import__('os')",
                "import numpy as np\nnp.load('x.npy')", "x = ().__class__.__bases__", "getattr(int, 'real')",
                "from PIL import Image\nImage.open('a.png')", "import socket", "import numpy.ctypeslib",
                "s = '__subclasses__'", "import sys\nprint(sys.argv)", "from os import path"]:
        assert policy.check(bad), bad


def test_policy_allows_ordinary_render_code():
    code = '''import numpy as np
import matplotlib.pyplot as plt
from PIL import Image
import math, cmath, colorsys
if __name__ == "__main__":
    x = np.linspace(-2, 1, 50)
    plt.plot(x, np.sin(x))
    plt.savefig("out.png", dpi=80, bbox_inches="tight")
'''
    assert policy.check(code) == []
    assert policy.check(GOOD) == []


def test_runner_produces_a_png_and_blocks_policy_violations():
    box = LocalSandbox()
    res = asyncio.run(box.run(GOOD))
    assert res.status == "ok", res.message
    assert res.files and res.files[0][0] == "out.png" and res.files[0][1].startswith(b"\x89PNG")
    blocked = asyncio.run(box.run("import os\nprint(os.environ)"))
    assert blocked.status == "policy" and not blocked.files


def test_runner_contains_runaway_scripts():
    box = LocalSandbox()
    loop = asyncio.run(box.run("while True:\n    pass\n"))
    assert loop.status == "timeout"
    hog = asyncio.run(box.run("import numpy as np\na = np.ones((70000, 70000))\nprint(a.sum())\n"))
    assert hog.status == "error"
    nothing = asyncio.run(box.run("x = 1 + 1\n"))
    assert nothing.status == "no_output"
    err = asyncio.run(box.run("import numpy as np\nprint(1/0)\n"))
    assert err.status == "error" and "ZeroDivisionError" in err.stderr_tail


def test_runner_collects_frames():
    code = '''import numpy as np
from PIL import Image
for i in range(5):
    a = np.full((40, 60, 3), i * 40, dtype=np.uint8)
    a[:, : 10 + i * 8] = 255
    Image.fromarray(a).save("frames/frame_%03d.png" % i)
'''
    res = asyncio.run(LocalSandbox().run(code, frames=True))
    assert res.status == "ok" and len(res.files) == 5
